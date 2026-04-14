"""
/start, main menu, trial, back_to_menu, config-name FSM handler.

Menu states:
  new      — never had key, trial not used → show trial button
  referred — arrived via ref link + trial available → single "Забрать" button
  expired  — had key/trial but no active sub → "Подписка закончилась" + existing menu
  active   — has active sub → existing menu
"""
import asyncio
import logging
import time

from aiogram import Bot, F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    Message, CallbackQuery,
    InlineKeyboardMarkup, InlineKeyboardButton,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from config import TRIAL_DAYS
from database import (
    ensure_user, get_referrer, set_referrer,
    has_trial_used, try_claim_trial,
    has_active_subscription, has_paid_subscription,
    has_ever_had_key, get_user_keys, add_key,
)
from xui import get_subscription_url
from keyboards import main_menu_new_user, main_menu_existing, main_menu_with_keys, back_to_menu, cancel_kb
from utils import send_with_photo, safe_answer, LOGO_URL
from subscription import ask_config_name, deliver_key
from states import BuyFlow
from async_utils import monitor_performance, batch_execute
from cache import cache_subscription_data

logger = logging.getLogger(__name__)
router = Router()

# Bonus days referrer gets when referral makes first paid purchase
REF_BONUS_DAYS = 15


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@monitor_performance("user_state_check")
@cache_subscription_data
async def _user_state(user_id: int) -> str:
    """Определить состояние пользователя с использованием кэша."""
    # Выполняем все проверки параллельно для максимальной скорости
    tasks = [
        has_active_subscription(user_id),
        has_ever_had_key(user_id),
        has_trial_used(user_id),
    ]
    
    active, ever_had, trial_used = await asyncio.gather(*tasks, return_exceptions=True)
    
    # Обрабатываем возможные исключения
    if isinstance(active, Exception):
        active = False
    if isinstance(ever_had, Exception):
        ever_had = False
    if isinstance(trial_used, Exception):
        trial_used = False
    
    if active:
        return "active"
    if ever_had or trial_used:
        return "expired"
    return "new"


@monitor_performance("clean_chat")
async def _clean_chat(bot: Bot, chat_id: int, anchor_msg_id: int, count: int = 50) -> None:
    """
    Delete the last `count` messages up to and including anchor_msg_id.
    Runs all deletes concurrently and swallows errors (already deleted, too old, etc).
    """
    # Get all message IDs to delete
    ids = [mid for mid in range(anchor_msg_id, anchor_msg_id - count, -1) if mid > 0]
    
    # Create batches for faster deletion (Telegram API limit)
    batch_size = 10
    tasks = []
    
    for i in range(0, len(ids), batch_size):
        batch = ids[i:i + batch_size]
        # Create concurrent tasks for this batch
        batch_tasks = [bot.delete_message(chat_id, mid) for mid in batch]
        tasks.extend(batch_tasks)
    
    # Execute all tasks concurrently for instant deletion
    await asyncio.gather(*tasks, return_exceptions=True)


def _referral_welcome_kb() -> InlineKeyboardMarkup:
    """Beautiful welcome keyboard for referral users."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎁 Получить 3 дня бесплатно", callback_data="trial_ref")],
    ])


async def _send_main_menu(
    bot: Bot,
    target: "Message | CallbackQuery",
    user_id: int,
    user_name: str,
    *,
    is_new_referral: bool = False,
) -> None:
    """
    Send the correct menu screen based on user state.
    is_new_referral=True → show special single-button referral welcome screen.
    """
    state = await _user_state(user_id)

    if is_new_referral and state == "new":
        # Referral landing - fire bonus text (when someone clicks referral link)
        text = (
            "🔥 Нормальный VPN сейчас найти сложно — либо дорогой, либо не работает.\n\n"
            "🎁 У вас уже есть доступ — 3 дня бесплатно (без карты)\n\n"
            "Что получите сразу:\n"
            "• Telegram и YouTube работают без ограничений\n"
            "• Instagram, TikTok, сайты — открываются\n"
            "• До 5 устройств по одной подписке\n"
            "• Быстрое подключение за 30 секунд\n\n"
            "━━━━━━━━━━━━━━━━━━━━━\n"
            "💰 Дальше — от 59 ₽/мес\n"
            "(в 2–3 раза дешевле большинства VPN)\n"
            "━━━━━━━━━━━━━━━━━━━━━\n\n"
            "⏳ Бесплатный доступ ограничен — лучше проверить сейчас\n\n"
            "👇 Нажмите кнопку и подключитесь"
        )
        kb = _referral_welcome_kb()
    elif state == "new":
        # Main menu - standard welcome text
        text = (
            "Здравствуйте, ByMeVPN!\n\n"
            "Этот бот поможет вам получить доступ к быстрому и безопасному VPN, который работает, обходя любые блокировки.\n\n"
            "Любой из наших тарифов, включая пробный тариф на 3 дня, даёт полный доступ к интернету, для 5 устройств.\n\n"
            "Наши приложения доступны для:\n"
            "<a href='https://apps.apple.com/us/app/happ-proxy-utility/id6504287215'>iOS</a>, "
            "<a href='https://play.google.com/store/apps/details?id=com.happproxy&pcampaignid=web_share'>Android</a>, "
            "<a href='https://github.com/Happ-proxy/happ-desktop/releases/latest/download/setup-Happ.x64.exe'>Windows</a>, "
            "<a href='https://apps.apple.com/ru/app/happ-proxy-utility-plus/id6746188973'>macOS</a> и "
            "<a href='https://github.com/Happ-proxy/happ-desktop/releases/latest/download/Happ.linux.x64.deb'>Linux</a>.\n\n"
            "После оплаты, бот пришлёт вам ключ, который нужно будет вставить в наше приложение."
        )
        kb = main_menu_new_user()
    elif state == "expired":
        text = (
            f"<b>Здравствуйте, {user_name}!</b>\n\n"
            "Ваша подписка закончилась.\n\n"
            "Вы можете продлить VPN и дальше пользоваться сервисом без ограничений.\n\n"
            "Любой из наших тарифов даёт полный доступ к интернету для 5 устройств.\n\n"
            "Чем дольше срок, тем больше вы экономите!"
        )
        # Check if user has keys to show appropriate menu (use direct DB check, not cache)
        user_keys = await get_user_keys(user_id)
        has_keys = len(user_keys) > 0
        trial_used = await has_trial_used(user_id)
        kb = main_menu_with_keys(trial_used=trial_used) if has_keys else main_menu_existing()
    else:  # active
        text = f"<b>Здравствуйте, {user_name}!</b>"
        # Check if user has keys to show appropriate menu (use direct DB check, not cache)
        user_keys = await get_user_keys(user_id)
        has_keys = len(user_keys) > 0
        trial_used = await has_trial_used(user_id)
        kb = main_menu_with_keys(trial_used=trial_used) if has_keys else main_menu_existing()

    if isinstance(target, Message):
        await bot.send_photo(
            chat_id=user_id, photo=LOGO_URL,
            caption=text, parse_mode="HTML", reply_markup=kb,
        )
    else:
        await send_with_photo(bot, target, text, kb)


# ---------------------------------------------------------------------------
# /start
# ---------------------------------------------------------------------------

@router.message(F.text.startswith("/start"))
@monitor_performance("start_command")
async def cmd_start(message: Message, bot: Bot) -> None:
    """
    /start — register or return to main menu.
    Supports referral links: /start 123456
    """
    user_id = message.from_user.id
    user_name = message.from_user.full_name or message.from_user.first_name or "Друг"

    # Register user (idempotent)
    await ensure_user(user_id)

    # Process referral link if provided
    args = message.text.split()
    is_new_referral = False
    referral_processed = False
    
    if len(args) > 1 and args[1].isdigit():
        ref_id = int(args[1])
        
        # Process referral (set referrer + give bonus +5 days)
        if ref_id != user_id:
            try:
                existing_keys = await get_user_keys(user_id)
                if not existing_keys:
                    # New referral - show welcome screen
                    is_new_referral = True
                    referral_processed = True
                    
                    # Log referral click (referral system removed)
                    logger.info("Referral click from user %s with code %s", user_id, args[1])
            except Exception as e:
                logger.error("Error processing referral: %s", e)

    # Send appropriate menu
    await _send_main_menu(
        bot, message, user_id, user_name,
        is_new_referral=is_new_referral and referral_processed
    )
    
    # Не удаляем сообщения после /start, чтобы избежать бесконечной кнопки Старт


# ---------------------------------------------------------------------------
# Back to menu
# ---------------------------------------------------------------------------

@router.callback_query(F.data == "back_to_menu")
async def cb_back_to_menu(callback: CallbackQuery, bot: Bot, state: FSMContext):
    await state.clear()
    await safe_answer(callback)
    user_id = callback.from_user.id
    name = callback.from_user.first_name or "друг"
    await _send_main_menu(bot, callback, user_id, name)


# ---------------------------------------------------------------------------
# Trial — regular (from main menu)
# ---------------------------------------------------------------------------

@router.callback_query(F.data == "trial")
async def cb_trial(callback: CallbackQuery, bot: Bot, state: FSMContext):
    await safe_answer(callback)
    user_id = callback.from_user.id

    # Atomic claim: single UPDATE, returns False if already used or has key
    claimed = await try_claim_trial(user_id)
    if not claimed:
        await safe_answer(callback, "Пробный период доступен только новым пользователям.", alert=True)
        return

    await ask_config_name(
        bot, callback, state,
        context={
            "days": TRIAL_DAYS, "prefix": "trial", "is_paid": False,
            "amount": 0, "currency": "RUB", "method": "trial",
            "payload": f"trial_{user_id}", "_trial_user_id": user_id,
            "limit_ip": 1,  # Trial is always 1 device only
        },
    )


# ---------------------------------------------------------------------------
# Trial — referral version (from referral welcome screen)
# ---------------------------------------------------------------------------

@router.callback_query(F.data == "trial_ref")
async def cb_trial_ref(callback: CallbackQuery, bot: Bot, state: FSMContext):
    """
    Referral welcome button: "Забрать 3 дня бесплатно"
    Same logic as regular trial but activated from referral welcome screen.
    """
    await safe_answer(callback)
    user_id = callback.from_user.id

    claimed = await try_claim_trial(user_id)
    if not claimed:
        # Already used — show normal menu
        name = callback.from_user.first_name or "друг"
        await _send_main_menu(bot, callback, user_id, name)
        return

    await ask_config_name(
        bot, callback, state,
        context={
            "days": TRIAL_DAYS, "prefix": "trial_ref", "is_paid": False,
            "amount": 0, "currency": "RUB", "method": "trial",
            "payload": f"trial_ref_{user_id}", "_trial_user_id": user_id,
            "limit_ip": 1,  # Trial is always 1 device only
        },
    )


# ---------------------------------------------------------------------------
# About
# ---------------------------------------------------------------------------

@router.callback_query(F.data == "about")
async def cb_about(callback: CallbackQuery, bot: Bot):
    await safe_answer(callback)
    text = (
        "ByMeVPN был создан в 2022 году.\n\n"
        "⚡️ Наш сервис работает на быстром и безопасном протоколе VLESS, поверх которого используется дополнительная маскировка трафика. Благодаря этому ByMeVPN умело обходит блокировки и работает во всех странах.\n\n"
        "👨‍💻 Мы используем специальные приложения для всех платформ. Начало работы с нашим сервисом максимально простое и не требует никаких специальных умений, не нужны никакие сложные инструкции.\n\n"
        "🔒 В нашем сервисе весь ваш трафик полностью зашифрован. Мы не храним логи и не видим, на какие сайты вы заходите. И никто не увидит.\n\n"
        "🌎 Наши сервера размещены по всему миру, и на любом из наших тарифов (даже на пробном) вы получаете полный доступ ко всем локациям. На одном сервере мы размещаем не более 10 клиентов – таким образом вы получаете максимальную скорость, до 10 Гбит/сек.\n\n"
        "👨‍👩‍👧‍👦 Количество устройств на одной подписке 5 штук. Можно делиться вашим ключом от ByMeVPN с близкими."
    )
    # Создаем клавиатуру с кнопками "Назад" и "Поддержка" в одном ряду
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Назад", callback_data="back_to_menu"), 
         InlineKeyboardButton(text="Поддержка", url="https://t.me/ByMeVPN_support_bot")]
    ])
    await send_with_photo(bot, callback, text, kb)


# ---------------------------------------------------------------------------
# My Keys
# ---------------------------------------------------------------------------

@router.callback_query(F.data == "my_keys")
async def cb_my_keys(callback: CallbackQuery, bot: Bot):
    """Show user's keys as buttons."""
    await safe_answer(callback)
    user_id = callback.from_user.id
    
    # Get user's keys
    keys = await get_user_keys(user_id)
    
    if not keys:
        await callback.message.answer(
            "У вас пока нет ключей.\n\n"
            "Нажмите 'Купить от 59 ₽ в месяц' или 'Попробовать БЕСПЛАТНО 3 дня' чтобы получить ключ.",
            reply_markup=back_to_menu()
        )
        return
    
    # Build keyboard with keys as buttons
    kb = InlineKeyboardBuilder()
    
    for i, key in enumerate(keys, 1):
        status = "✅" if key['expiry'] > int(time.time()) else "❌"
        expiry_date = time.strftime('%d.%m.%Y', time.localtime(key['expiry']))
        button_text = f"{status} Ключ #{i} (до {expiry_date})"
        kb.row(InlineKeyboardButton(text=button_text, callback_data=f"key_details:{key['id']}"))
    
    kb.row(InlineKeyboardButton(text="Назад в меню", callback_data="back_to_menu"))
    
    await callback.message.answer(
        "🔑 <b>Ваши ключи:</b>\n\n"
        "Нажмите на ключ, чтобы увидеть ссылку и инструкцию подключения.",
        reply_markup=kb.as_markup()
    )


@router.callback_query(F.data.startswith("key_details:"))
async def cb_key_details(callback: CallbackQuery, bot: Bot):
    """Show key details with link and action buttons."""
    await safe_answer(callback)
    user_id = callback.from_user.id
    
    # Extract key_id from callback data
    key_id = int(callback.data.split(":")[1])
    
    # Get all user keys and find the specific one
    keys = await get_user_keys(user_id)
    key = None
    for k in keys:
        if k['id'] == key_id:
            key = k
            break
    
    if not key:
        await callback.message.answer("Ключ не найден.", reply_markup=back_to_menu())
        return
    
    # Build detailed view
    status = "✅ Активен" if key['expiry'] > int(time.time()) else "❌ Истёк"
    expiry_date = time.strftime('%d.%m.%Y', time.localtime(key['expiry']))
    
    text = (
        f"🔑 <b>Ключ #{key_id}</b>\n\n"
        f"📱 Устройств: {key['limit_ip']}\n"
        f"📅 Срок: до {expiry_date}\n"
        f"Статус: {status}\n\n"
        f"🔗 <b>Ссылка для подключения:</b>\n"
        f"<code>{get_subscription_url(key['short_id']) if key.get('short_id') else key['key']}</code>\n\n"
        f"Нажмите на ссылку, чтобы скопировать её."
    )
    
    # Create keyboard with action buttons and back buttons
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="📋 Инструкция подключения", callback_data=f"key_instructions:{key_id}"))
    kb.row(
        InlineKeyboardButton(text="🗑 Удалить", callback_data=f"delete_key:{key_id}"),
        InlineKeyboardButton(text="➕ Продлить", callback_data=f"extend_key:{key_id}")
    )
    kb.row(InlineKeyboardButton(text="Назад к ключам", callback_data="my_keys"))
    kb.row(InlineKeyboardButton(text="В главное меню", callback_data="back_to_menu"))
    
    await callback.message.answer(text, reply_markup=kb.as_markup(), parse_mode="HTML")


@router.callback_query(F.data.startswith("key_instructions:"))
async def cb_key_instructions(callback: CallbackQuery, bot: Bot):
    """Show connection instructions with device selection."""
    await safe_answer(callback)
    user_id = callback.from_user.id
    key_id = int(callback.data.split(":")[1])
    
    # Get all user keys and find the specific one
    keys = await get_user_keys(user_id)
    key = None
    for k in keys:
        if k['id'] == key_id:
            key = k
            break
    
    if not key:
        await callback.message.answer("Ключ не найден.", reply_markup=back_to_menu())
        return
    
    sub_url = get_subscription_url(key['short_id']) if key.get('short_id') else key['key']
    
    # Show device selection guide
    text = (
        f"📋 <b>Инструкция подключения</b>\n\n"
        f"🔑 <b>Ключ #{key_id}</b>\n"
        f"🔗 <code>{sub_url}</code>\n\n"
        f"📱 <b>Выберите ваше устройство:</b>"
    )
    
    # Create custom keyboard with back to key button
    kb = InlineKeyboardBuilder()
    for name, cb in [
        ("🍎 iOS", "guide_ios"),
        ("📱 Android", "guide_android"),
        ("💻 Windows", "guide_windows"),
        ("🍏 macOS", "guide_macos"),
        ("🐧 Linux", "guide_linux"),
    ]:
        kb.row(InlineKeyboardButton(text=name, callback_data=cb))
    kb.row(InlineKeyboardButton(text="◀️ Назад к ключу", callback_data=f"key_details:{key_id}"))
    kb.row(InlineKeyboardButton(text="🏠 В главное меню", callback_data="back_to_menu"))
    
    await callback.message.answer(text, reply_markup=kb.as_markup(), parse_mode="HTML")


@router.callback_query(F.data.startswith("delete_key:"))
async def cb_delete_key(callback: CallbackQuery, bot: Bot):
    """Delete a key from DB and 3x-UI after confirmation."""
    await safe_answer(callback)
    user_id = callback.from_user.id
    key_id = int(callback.data.split(":")[1])
    
    # Verify the key belongs to the user
    from database import get_user_keys, delete_key, get_key_by_id
    from xui import delete_client
    
    keys = await get_user_keys(user_id)
    key = None
    for k in keys:
        if k['id'] == key_id:
            key = k
            break
    
    if not key:
        await callback.message.answer("Ключ не найден.", reply_markup=back_to_menu())
        return
    
    # First delete from 3x-UI panel
    deleted_from_panel = True
    if key.get('uuid'):
        deleted_from_panel = await delete_client(key['uuid'])
        if not deleted_from_panel:
            logger.warning(f"Failed to delete client {key['uuid']} from 3x-UI for key {key_id}")
    
    # Then delete from database
    deleted_from_db = await delete_key(key_id)
    
    if deleted_from_db:
        status_msg = "🗑 Ключ успешно удалён из системы."
        if not deleted_from_panel and key.get('uuid'):
            status_msg += "\n⚠️ (Не удалось удалить из панели, но ключ удалён из базы)"
        
        await callback.message.answer(
            f"{status_msg}\n\n"
            f"Если у вас больше нет ключей, нажмите 'Купить' или 'Попробовать бесплатно'.",
            reply_markup=back_to_menu()
        )
    else:
        await callback.message.answer(
            "Не удалось удалить ключ. Попробуйте позже.",
            reply_markup=back_to_menu()
        )


@router.callback_query(F.data.startswith("extend_key:"))
async def cb_extend_key(callback: CallbackQuery, bot: Bot):
    """Show extend options for a key."""
    await safe_answer(callback)
    user_id = callback.from_user.id
    key_id = int(callback.data.split(":")[1])
    
    # Verify the key belongs to the user
    from database import get_user_keys
    keys = await get_user_keys(user_id)
    key = None
    for k in keys:
        if k['id'] == key_id:
            key = k
            break
    
    if not key:
        await callback.message.answer("Ключ не найден.", reply_markup=back_to_menu())
        return
    
    # Show extend options
    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(text="+7 дней", callback_data=f"extend_confirm:{key_id}:7"),
        InlineKeyboardButton(text="+30 дней", callback_data=f"extend_confirm:{key_id}:30")
    )
    kb.row(
        InlineKeyboardButton(text="+90 дней", callback_data=f"extend_confirm:{key_id}:90"),
        InlineKeyboardButton(text="+180 дней", callback_data=f"extend_confirm:{key_id}:180")
    )
    kb.row(InlineKeyboardButton(text="Отмена", callback_data=f"key_details:{key_id}"))
    
    await callback.message.answer(
        f"🔑 <b>Продление ключа #{key_id}</b>\n\n"
        f"Выберите на сколько дней продлить ключ:\n\n"
        f"💡 После выбора вы будете перенаправлены на оплату.",
        reply_markup=kb.as_markup()
    )


@router.callback_query(F.data.startswith("extend_confirm:"))
async def cb_extend_confirm(callback: CallbackQuery, bot: Bot):
    """Handle extend confirmation - redirect to payment."""
    await safe_answer(callback)
    
    parts = callback.data.split(":")
    key_id = int(parts[1])
    days = int(parts[2])
    
    # Here we would typically redirect to payment flow
    # For now, show a message that this will be implemented with payment
    await callback.message.answer(
        f"➕ <b>Продление ключа #{key_id}</b>\n\n"
        f"Для продления на <b>{days} дней</b> перейдите в раздел «Купить от 59 ₽ в месяц».\n\n"
        f"Там вы сможете выбрать подходящий тариф и продлить ключ.",
        reply_markup=back_to_menu()
    )
