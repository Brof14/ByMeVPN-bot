"""
Core subscription logic: ask config name → create VPN key → deliver.
"""
import asyncio
import logging
import time
from datetime import datetime

from aiogram import Bot, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery

from config import ADMIN_ID
from utils import LOGO_URL, send_with_photo, safe_answer
from database import add_key, get_referrer, add_payment, set_trial_used
from xui import create_client, get_subscription_url
from keyboards import after_key_kb, cancel_kb
from states import BuyFlow

logger = logging.getLogger(__name__)

# Create router for subscription handlers
router = Router()

# Referral bonus: 15 days for referrer when referred makes first paid purchase
REF_BONUS_DAYS = 15


async def ask_config_name(
    bot: Bot,
    target: "Message | CallbackQuery",
    state: FSMContext,
    context: dict,
) -> None:
    """Deliver key or extend existing one without asking user for config name."""
    user_id = target.from_user.id if hasattr(target, 'from_user') else target.message.from_user.id
    chat_id = target.message.chat.id if hasattr(target, 'message') else target.chat.id
    
    days = context.get("days", 30)
    is_paid = context.get("is_paid", False)
    limit_ip = 5
    amount = context.get("amount", 0)
    currency = context.get("currency", "RUB")
    method = context.get("method", "unknown")
    payload = context.get("payload", "")
    
    await state.clear()
    
    # Check if user has existing keys to extend
    from database import get_user_keys, extend_key, add_payment
    import time
    
    existing_keys = await get_user_keys(user_id)
    current_time = int(time.time())
    
    # Find active or expired keys (extend even expired keys within 7 days grace period)
    extendable_keys = [k for k in existing_keys if k.get("expiry", 0) > current_time - 7*86400]
    
    if extendable_keys:
        # Sort by expiry (most recent first) and extend the first one
        extendable_keys.sort(key=lambda k: k.get("expiry", 0), reverse=True)
        key_to_extend = extendable_keys[0]
        key_id = key_to_extend["id"]
        old_expiry = key_to_extend["expiry"]
        
        # Extend the key
        success = await extend_key(key_id, days)
        
        if success:
            # Add payment record
            if is_paid and amount > 0:
                tariff_name = f"Продление {days} дней (до 5 устройств)"
                await add_payment(
                    user_id, amount, currency, method, days, payload,
                    status="success", tariff=tariff_name, devices=limit_ip
                )
            
            # Calculate new expiry for display
            from constants import format_timestamp
            new_expiry = old_expiry + days * 86400
            
            # Get subscription URL
            from xui import get_subscription_url
            sub_url = get_subscription_url(key_to_extend.get("short_id", "")) if key_to_extend.get("short_id") else key_to_extend.get("key", "")
            
            text = (
                f"✅ <b>Подписка продлена!</b>\n\n"
                f"🔑 <b>Ключ #{key_id}</b> продлен на <b>{days} дней</b>\n"
                f"📅 Новый срок: до <b>{format_timestamp(new_expiry)[:10]}</b>\n\n"
                f"🔗 <code>{sub_url}</code>\n\n"
                f"Название сервера осталось прежним — всё работает как раньше!"
            )
            
            from keyboards import after_key_kb
            from utils import send_with_photo, LOGO_URL
            await bot.send_photo(
                chat_id=chat_id, photo=LOGO_URL,
                caption=text, parse_mode="HTML", reply_markup=after_key_kb(),
            )
            
            # Process referral bonuses for paid extensions
            if is_paid and amount > 0:
                try:
                    from referral_system import process_payment_referral_bonus
                    await process_payment_referral_bonus(user_id, amount, bot)
                except Exception as e:
                    logger.error("Referral bonus error: %s", e)
            
            return
    
    # No existing keys - create new one
    prefix = context.get("prefix", "vpn")
    config_name = f"{prefix}_user_{user_id}"
    
    await deliver_key_with_generated_name(
        bot=bot,
        target=target,
        state=state,
        context=context,
        config_name=config_name,
    )


async def deliver_key_with_generated_name(
    bot: Bot,
    target: "Message | CallbackQuery",
    state: FSMContext,
    context: dict,
    config_name: str,
) -> None:
    """Deliver key using automatically generated name."""
    user_id = target.from_user.id if hasattr(target, 'from_user') else target.message.from_user.id
    chat_id = target.message.chat.id if hasattr(target, 'message') else target.chat.id
    
    days = context.get("days", 30)
    is_paid = context.get("is_paid", False)
    limit_ip = 5  # All plans (trial and paid) support up to 5 devices
    amount = context.get("amount", 0)
    currency = context.get("currency", "RUB")
    method = context.get("method", "unknown")
    payload = context.get("payload", "")
    trial_uid = context.get("_trial_user_id")
    yk_payment_id = context.get("_yk_payment_id")

    await state.clear()

    success = await deliver_key(
        bot=bot, user_id=user_id, chat_id=chat_id,
        config_name=config_name, days=days, limit_ip=limit_ip,
        is_paid=is_paid, amount=amount, currency=currency,
        method=method, payload=payload,
    )

    # Clean up YooKassa pending record after successful delivery
    if success and yk_payment_id:
        from database import delete_pending_yookassa_payment
        try:
            await delete_pending_yookassa_payment(yk_payment_id)
        except Exception as e:
            logger.error("Could not delete pending yk payment %s: %s", yk_payment_id, e)

    # Unmark trial if delivery failed (allow user to retry)
    if not success and trial_uid:
        try:
            # Trial was already marked as used, we can't reset it for security
            logger.warning("Trial delivery failed for user %d, trial remains used", trial_uid)
        except Exception as e:
            logger.error("Could not handle trial reset for %d: %s", trial_uid, e)


async def deliver_key(
    bot: Bot,
    user_id: int,
    chat_id: int,
    config_name: str,
    days: int,
    limit_ip: int = 1,
    is_paid: bool = False,
    amount: int = 0,
    currency: str = "RUB",
    method: str = "trial",
    payload: str = "",
) -> bool:
    """
    Create 3x-UI client, store in DB, send key to user.
    limit_ip — number of simultaneous device connections (1, 2 or 5).
    Returns True on success.
    """
    from xui import validate_device_limit
    
    # Validate device limit
    limit_ip = validate_device_limit(limit_ip)
    
    client_uuid = None
    client_short_id = None
    try:
        logger.info("deliver_key: user=%d name='%s' days=%d limit_ip=%d method=%s", user_id, config_name, days, limit_ip, method)

        client_result = await create_client(user_id, days, limit_ip=limit_ip)
        if not client_result:
            await bot.send_message(
                chat_id,
                "❌ <b>Ошибка создания ключа</b>\n\n"
                "Не удалось создать ключ в панели 3x-UI.\n"
                "Пожалуйста, попробуйте позже или напишите в поддержку.\n\n"
                "📞 Поддержка: @ByMeVPN_support_bot",
                parse_mode="HTML",
            )
            return False

        client_uuid = client_result["uuid"]
        client_short_id = client_result["short_id"]
        sub_url = get_subscription_url(client_short_id)
        
        # Log the generated key for debugging
        logger.info("Subscription URL for user %d: %s", user_id, sub_url)
        
        # Run database operations in parallel for better performance
        db_tasks = []
        db_tasks.append(add_key(user_id, sub_url, config_name, client_uuid, days, limit_ip, short_id=client_short_id))
        
        # Save payment record only after successful key creation
        if is_paid and amount > 0:
            # All paid plans now support up to 5 devices
            tariff_name = f"{days} дней (до 5 устройств)"
            db_tasks.append(add_payment(
                user_id, amount, currency, method, days, payload,
                status="success", tariff=tariff_name, devices=limit_ip
            ))

        # Execute database operations in parallel
        await asyncio.gather(*db_tasks)

        text = (
            f"Подписка активирована! Спасибо, что выбрали нас❤️\n\n"
            f"Скопируйте ссылку на подписку и посмотрите инструкцию подключения:\n"
            f"<code>{sub_url}</code>"
        )
        await bot.send_photo(
            chat_id=chat_id, photo=LOGO_URL,
            caption=text, parse_mode="HTML", reply_markup=after_key_kb(),
        )

        # Referral bonus: give referrer 15 days on first paid purchase
        if is_paid:
            # Import enhanced referral system
            from referral_system import process_payment_referral_bonus
            
            # Process referral bonus automatically
            await process_payment_referral_bonus(user_id, amount, bot)

        logger.info("Key delivered: user=%d uuid=%s name='%s' days=%d", user_id, client_uuid, config_name, days)
        return True

    except Exception as e:
        logger.exception("deliver_key FAILED for user=%d name='%s': %s", user_id, config_name, e)
        
        # Cleanup: if we created a client but failed later, try to delete it
        if client_uuid:
            try:
                from xui import delete_client
                await delete_client(client_uuid)
                logger.info("Cleaned up orphaned client %s for user %d", client_uuid, user_id)
            except Exception as cleanup_error:
                logger.error("Failed to cleanup orphaned client %s: %s", client_uuid, cleanup_error)
        
        # For paid payments, notify admin about the failure
        if is_paid:
            try:
                await bot.send_message(
                    ADMIN_ID,
                    f"🚨 <b>Ошибка создания ключа после оплаты!</b>\n\n"
                    f"👤 User: <code>{user_id}</code>\n"
                    f"📝 Имя: {config_name}\n"
                    f"⏳ Дней: {days}\n"
                    f"💰 Сумма: {amount} {currency}\n"
                    f"🔧 Метод: {method}\n"
                    f"🎫 Payload: {payload}\n"
                    f"❌ <code>{str(e)[:300]}</code>\n\n"
                    f"⚠️ Необходимо вернуть деньги или выдать ключ вручную!",
                    parse_mode="HTML",
                )
            except Exception:
                pass
        
        try:
            await bot.send_message(
                chat_id,
                "❌ <b>Ошибка создания VPN ключа</b>\n\n"
                "Не удалось создать ключ в панели управления.\n"
                "Если вы оплатили, деньги будут возвращены автоматически.\n"
                "Пожалуйста, напишите в поддержку — мы поможем!\n\n"
                "📞 Поддержка: @ByMeVPN_support_bot",
                parse_mode="HTML",
            )
        except Exception:
            pass
        return False


async def _notify_referral_bonus(bot: Bot, referrer_id: int, new_user_id: int) -> None:
    """
    Notify referrer: their referral just paid → bonus 15 days available.
    Referrer must press a button and enter a config name to activate.
    """
    try:
        from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(
                text=f"🎁 Активировать +{REF_BONUS_DAYS} дней",
                callback_data=f"ref_bonus_activate:{new_user_id}",
            )
        ]])
        await bot.send_message(
            referrer_id,
            f"🎁 <b>Ваш реферал оформил подписку!</b>\n\n"
            f"Вам начислено <b>+{REF_BONUS_DAYS} дней</b> бесплатно.\n\n"
            "Для активации нажмите кнопку ниже и введите название конфига:",
            parse_mode="HTML",
            reply_markup=kb,
        )
    except Exception as e:
        logger.error("Failed to notify referrer %d: %s", referrer_id, e)


# ---------------------------------------------------------------------------
# Message handlers for config name input
# ---------------------------------------------------------------------------

