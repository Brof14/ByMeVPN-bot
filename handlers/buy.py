"""
VPN purchase flow:
  buy_vpn → select type → select period → payment method → invoice/link
"""
import time
import logging

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery, LabeledPrice, PreCheckoutQuery, Message,
)

from constants import PRICE_CONFIG, PERIOD_LABELS, TRIAL_DAYS
from config import PRICE_1_MONTH, DAYS_1M
from states import BuyFlow
from keyboards import tariff_selection_kb, payment_kb
from payments import create_yookassa_payment
from subscription import ask_config_name
from database import ensure_user, add_referral_earning, get_referrer, add_payment
from utils import send_with_photo, safe_answer

logger = logging.getLogger(__name__)
router = Router()


# ---------------------------------------------------------------------------
# Step 1: Choose plan type
# ---------------------------------------------------------------------------

@router.callback_query(F.data == "buy_vpn")
async def cb_buy_vpn(callback: CallbackQuery, bot: Bot, state: FSMContext):
    await safe_answer(callback)
    await state.set_state(BuyFlow.choosing_period)
    
    # Check if user has active promo code
    data = await state.get_data()
    promo_discount = data.get("promo_discount", 0)
    
    await send_with_photo(
        bot, callback,
        "<b>Выберите срок подписки</b>\n\nЧем дольше срок, тем ниже стоимость одного месяца.\n\nВсе тарифы включают до 5 устройств одновременно.",
        tariff_selection_kb(discount_percent=promo_discount),
    )


# ---------------------------------------------------------------------------
# Step 2: Choose period
# ---------------------------------------------------------------------------

@router.callback_query(F.data.startswith("tariff_"))
async def cb_select_tariff(callback: CallbackQuery, bot: Bot, state: FSMContext):
    await safe_answer(callback)
    months = int(callback.data.split("_", 1)[1])
    
    price_rub, days = PRICE_CONFIG.get(months, PRICE_CONFIG[1])
    
    # Check for promo discount
    data = await state.get_data()
    promo_discount = data.get("promo_discount", 0)
    promo_code = data.get("promo_code", "")
    original_price = price_rub
    
    logger.info(f"Selecting tariff: months={months}, original_price={original_price}, promo_discount={promo_discount}, promo_code={promo_code}")
    
    if promo_discount > 0:
        price_rub = int(price_rub * (100 - promo_discount) / 100)
        promo_text = f" (скидка {promo_discount}% применена)"
        logger.info(f"Promo applied: new_price={price_rub}, discount={promo_discount}%")
    else:
        promo_text = ""
    
    await state.update_data(months=months, price_rub=price_rub, days=days, original_price=original_price)

    period_name = PERIOD_LABELS.get(months, f"{months} мес.")

    text = (
        f"<b>Вы покупаете доступ на {days} дней.</b>\n\n"
        f"Стоимость: <b>{price_rub} ₽</b>{promo_text}\n"
    )
    
    if promo_discount > 0:
        text += f"<s>Без скидки: {original_price} ₽</s>\n"
    
    text += (
        f"Устройств: до 5 одновременно\n\n"
        "Оплачивая подписку, вы соглашаетесь с <a href='https://telegra.ph/POLITIKA-KONFIDENCIALNOSTI-ByMeVPN-03-12'>политикой обработки персональных данных</a>, с <a href='https://telegra.ph/DOGOVOR-PUBLICHNOJ-OFERTY-ByMyVPN-03-12'>договором оферты</a> и с <a href='https://telegra.ph/SOGLASHENIE-O-REGULYARNYH-REKURRENTNYH-PLATEZHAH-ByMeVPN-03-12'>соглашением о присоединении к рекуррентной системе платежей</a>.\n\n"
        "Все подписки продлеваются автоматически. Отмена подписки возможна в любой момент.\n\n"
        "После оплаты бот отправит вам ключ для приложения и подробную инструкцию по установке.\n\n"
        "<b>Выберите способ оплаты:</b>"
    )
    
    await send_with_photo(bot, callback, text, payment_kb(price_rub, days))


# ---------------------------------------------------------------------------
# Step 3: Choose payment method
# ---------------------------------------------------------------------------

# Legacy handler for old period_* callbacks
@router.callback_query(F.data.startswith("period_"))
async def cb_select_period_legacy(callback: CallbackQuery, bot: Bot, state: FSMContext):
    await safe_answer(callback)
    # Redirect to new tariff selection
    await send_with_photo(
        bot, callback,
        "<b>Выберите срок подписки</b>\n\nЧем дольше срок, тем ниже стоимость одного месяца.\n\nВсе тарифы включают до 5 устройств одновременно.",
        tariff_selection_kb(),
    )


# ---------------------------------------------------------------------------
# Payment: Telegram Stars  (Stars = rubles, 1:1, intentional)
# ---------------------------------------------------------------------------

@router.callback_query(F.data == "pay_stars")
async def cb_pay_stars(callback: CallbackQuery, bot: Bot, state: FSMContext):
    await safe_answer(callback)
    data = await state.get_data()
    price_rub: int = data.get("price_rub", PRICE_1_MONTH)
    days: int = data.get("days", DAYS_1M)
    months: int = data.get("months", 1)
    # All plans now support up to 5 devices
    devices: int = 5
    user_id = callback.from_user.id

    # Stars amount = rubles (1:1) — intentional, to cover Telegram commission
    stars = price_rub
    # Encode months in payload so we can recover if FSM is lost
    payload = f"stars_{user_id}_{days}_{months}_{int(time.time())}"

    try:
        # Отправляем инвойс сразу без промежуточных сообщений
        await bot.send_invoice(
            chat_id=user_id,
            title="ByMeVPN — подписка",
            description=f"Доступ к VPN на {days} дней (VLESS + Reality)",
            payload=payload,
            provider_token="",  # empty string for Telegram Stars
            currency="XTR",
            prices=[LabeledPrice(label=f"VPN на {days} дней", amount=stars)],
        )
    except Exception as e:
        logger.error("Stars invoice error for user %d: %s", user_id, e)
        # Отправляем сообщение об ошибке, если инвойс не удался
        try:
            await bot.send_message(
                chat_id=user_id,
                text="❌ Ошибка при создании платежа. Попробуйте ещё раз.",
                parse_mode="HTML"
            )
        except Exception as msg_error:
            logger.error("Failed to send error message to user %d: %s", user_id, msg_error)


# ---------------------------------------------------------------------------
# Payment: YooKassa
# ---------------------------------------------------------------------------

@router.callback_query(F.data == "pay_yookassa")
async def cb_pay_yookassa(callback: CallbackQuery, bot: Bot, state: FSMContext):
    await safe_answer(callback)
    data = await state.get_data()
    price_rub: int = data.get("price_rub", PRICE_1_MONTH)
    days: int = data.get("days", DAYS_1M)
    months: int = data.get("months", 1)
    # All plans now support up to 5 devices
    devices: int = 5
    user_id = callback.from_user.id

    url = await create_yookassa_payment(
        price_rub, f"ByMeVPN {days} дней", user_id, days, devices,
    )

    if not url:
        await safe_answer(
            callback,
            "ЮKassa временно недоступна. Пожалуйста, выберите оплату через Telegram Stars.",
            alert=True,
        )
        return

    # Показываем сообщение с кнопкой для перехода на страницу оплаты
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"💳 Оплатить {price_rub} ₽", url=url)],
        [InlineKeyboardButton(text="Назад", callback_data="back_to_menu")]
    ])
    
    await send_with_photo(
        bot, callback,
        f"💳 <b>Оплата через ЮKassa</b>\n\n"
        f"Нажмите кнопку ниже для перехода на страницу оплаты.\n\n"
        f"Сумма: <b>{price_rub} ₽</b>\n"
        f"Срок: {days} дней\n"
        f"Устройств: до 5 одновременно\n\n"
        f"После оплаты вы получите ключ автоматически.",
        kb,
    )


# ---------------------------------------------------------------------------
# Pre-checkout confirmation (required by Telegram for Stars)
# ---------------------------------------------------------------------------

@router.pre_checkout_query()
async def pre_checkout(pre: PreCheckoutQuery):
    await pre.answer(ok=True)


# ---------------------------------------------------------------------------
# Successful Stars payment → ask config name → deliver key
# ---------------------------------------------------------------------------

@router.message(F.successful_payment)
async def on_successful_payment(message: Message, bot: Bot, state: FSMContext):
    user_id = message.from_user.id
    await ensure_user(user_id)

    payment = message.successful_payment
    payload = payment.invoice_payload
    stars = payment.total_amount
    currency = payment.currency  # XTR

    # Parse days and months from payload: "stars_{user_id}_{days}_{months}_{ts}"
    parts = payload.split("_")
    try:
        days = int(parts[2])
    except Exception:
        days = DAYS_1M
    try:
        # New format has months at index 3; old format (4 parts) falls back to FSM
        months_from_payload = int(parts[3]) if len(parts) >= 5 else None
    except Exception:
        months_from_payload = None

    # Retrieve months from FSM; payload value is authoritative fallback if FSM is gone
    data = await state.get_data()
    fsm_months = data.get("months")
    months = fsm_months or months_from_payload or 1
    
    # All paid plans support up to 5 devices
    devices = 5

    try:
        await message.delete()
    except Exception:
        pass

    # Добавляем платёж в базу данных
    payment_id = await add_payment(
        user_id=user_id,
        amount=stars,
        currency=currency,
        method="stars",
        days=days,
        payload=payload,
        tariff=f"{months} мес",
        devices=5
    )

    # Начисляем бонус рефереалу за первую оплату (80₽)
    try:
        referrer_id = await get_referrer(user_id)
        if referrer_id:
            bonus_added = await add_referral_earning(referrer_id, user_id, 80, payment_id)
            if bonus_added:
                logger.info("Referral bonus 80₽ added for referrer %d from user %d payment", referrer_id, user_id)
                # Отправляем уведомление рефереалу
                try:
                    await bot.send_message(
                        referrer_id,
                        f"🎉 <b>Получен бонус!</b>\n\n"
                        f"Ваш друг (ID: {user_id}) оформил подписку.\n"
                        f"Начислено: +80 ₽\n"
                        f"Текущий баланс обновлён в партнёрской программе."
                    )
                except Exception as notify_error:
                    logger.error("Failed to notify referrer %d: %s", referrer_id, notify_error)
    except Exception as e:
        logger.error("Error processing referral bonus for user %d: %s", user_id, e)

    # Ask for config name before delivering key
    await ask_config_name(
        bot, message, state,
        context={
            "days": days,
            "prefix": "stars",
            "is_paid": True,
            "amount": stars,
            "currency": currency,
            "method": "stars",
            "payload": payload,
        }
    )

# YooKassa payments are now processed automatically in webhook.py
# No manual delivery needed anymore


# ---------------------------------------------------------------------------
# Promo Code Command
# ---------------------------------------------------------------------------

@router.message(F.text.startswith("/promo"))
async def cmd_promo(message: Message, state: FSMContext):
    """Activate promo code."""
    try:
        parts = message.text.split(maxsplit=1)
        if len(parts) < 2:
            await message.answer(
                "🎁 <b>Использование промокода</b>\n\n"
                "Введите: <code>/promo КОД</code>\n\n"
                "Например: <code>/promo SALE20</code>\n\n"
                "Промокод даст скидку при следующей покупке VPN.",
                parse_mode="HTML"
            )
            return
        
        code = parts[1].strip().upper()
        user_id = message.from_user.id
        
        # Validate promo code
        from database import validate_promo_code, use_promo_code
        
        promo = await validate_promo_code(code)
        if not promo:
            await message.answer(
                "❌ <b>Промокод не действителен</b>\n\n"
                "Возможные причины:\n"
                "• Код истёк\n"
                "• Достигнут лимит использований\n"
                "• Код не существует",
                parse_mode="HTML"
            )
            return
        
        # Mark promo code as used
        success = await use_promo_code(code, user_id)
        if not success:
            await message.answer(
                "❌ <b>Вы уже использовали этот промокод</b>\n\n"
                "Каждый промокод можно использовать только один раз.",
                parse_mode="HTML"
            )
            return
        
        # Save discount to state for next purchase
        discount_percent = promo["discount_percent"]
        await state.update_data(promo_discount=discount_percent, promo_code=code)
        
        logger.info(f"Promo code {code} activated for user {user_id} with discount {discount_percent}%")
        
        await message.answer(
            f"✅ <b>Промокод активирован!</b>\n\n"
            f"🎁 Код: <code>{code}</code>\n"
            f"💰 Скидка: <b>{discount_percent}%</b>\n\n"
            f"При следующей покупке VPN стоимость будет автоматически уменьшена.\n\n"
            f"👉 Нажмите /buy чтобы купить VPN со скидкой!",
            parse_mode="HTML"
        )
        
    except Exception as e:
        logger.error("Promo code error: %s", e)
        await message.answer("❌ Произошла ошибка. Попробуйте позже.")
