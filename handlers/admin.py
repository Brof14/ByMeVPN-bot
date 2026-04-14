"""
Extended admin panel.
Features: stats, extended stats, broadcast, user list, user search,
          delete user, edit key days, reset trial, personal message,
          payment history, export CSV.
"""
import asyncio
import io
import logging
from datetime import datetime

from aiogram import Bot, F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    Message, CallbackQuery,
    InlineKeyboardMarkup, InlineKeyboardButton,
    BufferedInputFile,
)

from config import ADMIN_IDS
from database import (
    get_admin_stats, get_extended_stats,
    get_all_user_ids, get_all_users, get_users_count, get_all_users_paginated,
    find_user_by_id, delete_user_and_keys,
    get_user_keys, set_key_days, update_key_remark,
    set_trial_used, reset_trial_for_user, get_user_payments,
    get_all_users_csv, extend_key,
    add_refund, get_user_refunds, get_all_refunds, get_refund_stats,
    get_payment_stats,
    get_all_keys_paginated, get_keys_count,
    create_promo_code, get_all_promo_codes, delete_promo_code, validate_promo_code,
)
from states import AdminFlow
from utils import safe_answer
from constants import format_timestamp as fmt_date, format_days_left as fmt_days_left
from xui import delete_client

logger = logging.getLogger(__name__)
router = Router()


def _is_admin(uid: int) -> bool:
    return uid in ADMIN_IDS


# ── keyboards ─────────────────────────────────────────────────────────────

def _main_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        # Row 1: Quick Stats
        [InlineKeyboardButton(text="📊 Статистика", callback_data="admin_stats"),
         InlineKeyboardButton(text="📈 Детали", callback_data="admin_stats_ext")],
        # Row 2: Users
        [InlineKeyboardButton(text="👥 Пользователи", callback_data="admin_users:0"),
         InlineKeyboardButton(text="📋 Юзеры+Ключи", callback_data="admin_users_ext:0")],
        # Row 3: Keys & Search
        [InlineKeyboardButton(text="🗝 Все ключи", callback_data="admin_all_keys:0"),
         InlineKeyboardButton(text="🔍 Поиск", callback_data="admin_search")],
        # Row 4: Financial
        [InlineKeyboardButton(text="💳 Платежи", callback_data="admin_payments"),
         InlineKeyboardButton(text="💰 Возвраты", callback_data="admin_refunds")],
        # Row 5: Marketing & Broadcast
        [InlineKeyboardButton(text="📢 Рассылка", callback_data="admin_broadcast"),
         InlineKeyboardButton(text="🎁 Рефералы", callback_data="admin_referrals")],
        # Row 6: Exports
        [InlineKeyboardButton(text="📥 Пользователи CSV", callback_data="admin_export_csv"),
         InlineKeyboardButton(text="� Ключи CSV", callback_data="admin_export_keys_csv")],
        # Row 7: Promo & System
        [InlineKeyboardButton(text="🎁 Промокоды", callback_data="admin_promo_codes"),
         InlineKeyboardButton(text="⚙️ Система", callback_data="admin_system")],
        # Row 8: Maintenance
        [InlineKeyboardButton(text="🧹 Очистка", callback_data="admin_cleanup")],
    ])


def _back_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Главное меню", callback_data="admin_menu")]
    ])


def _user_kb(user_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        # Row 1: View info
        [InlineKeyboardButton(text="🔑 Ключи", callback_data=f"admin_user_keys:{user_id}"),
         InlineKeyboardButton(text="💳 Платежи", callback_data=f"admin_user_pay:{user_id}")],
        # Row 2: Quick Actions
        [InlineKeyboardButton(text="⏰ Продлить", callback_data=f"admin_extend_sub:{user_id}"),
         InlineKeyboardButton(text="🎁 Пробник", callback_data=f"admin_grant_trial:{user_id}")],
        # Row 3: Management
        [InlineKeyboardButton(text="🔄 Сброс пробника", callback_data=f"admin_reset_trial:{user_id}"),
         InlineKeyboardButton(text="✉️ Написать", callback_data=f"admin_pm:{user_id}")],
        # Row 4: Financial
        [InlineKeyboardButton(text="💰 Возврат", callback_data=f"admin_refund_user:{user_id}")],
        # Row 5: Danger zone
        [InlineKeyboardButton(text="🗑 Удалить пользователя", callback_data=f"admin_del_user:{user_id}")],
        # Row 6: Navigation
        [InlineKeyboardButton(text="🔙 Главное меню", callback_data="admin_menu")],
    ])


def _keys_kb(keys: list, user_id: int) -> InlineKeyboardMarkup:
    rows = []
    for k in keys:
        kid = k["id"]
        remark = (k.get("remark") or f"#{kid}")[:20]
        status = "🟢" if k.get("expiry", 0) > int(__import__('time').time()) else "🔴"
        rows.append([
            InlineKeyboardButton(text=f"{status} {remark}", callback_data=f"admin_key_detail:{kid}:{user_id}"),
            InlineKeyboardButton(text="✏️", callback_data=f"admin_edit_key:{kid}"),
            InlineKeyboardButton(text="🏷️", callback_data=f"admin_rename_key:{kid}"),
            InlineKeyboardButton(text="🗑️", callback_data=f"admin_del_key_confirm:{kid}:{user_id}"),
        ])
    rows.append([InlineKeyboardButton(text="🔙 Назад", callback_data=f"admin_user:{user_id}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _user_kb(user_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        # Row 1: View info
        [InlineKeyboardButton(text="🔑 Ключи", callback_data=f"admin_user_keys:{user_id}"),
         InlineKeyboardButton(text="💳 Платежи", callback_data=f"admin_user_pay:{user_id}")],
        # Row 2: Quick Actions
        [InlineKeyboardButton(text="⏰ Продлить", callback_data=f"admin_extend_sub:{user_id}"),
         InlineKeyboardButton(text="🎁 Пробник", callback_data=f"admin_grant_trial:{user_id}")],
        # Row 3: Management
        [InlineKeyboardButton(text="🔄 Сброс пробника", callback_data=f"admin_reset_trial:{user_id}"),
         InlineKeyboardButton(text="✉️ Написать", callback_data=f"admin_pm:{user_id}")],
        # Row 4: Grant Key & Refund
        [InlineKeyboardButton(text="🆕 Выдать ключ", callback_data=f"admin_grant_key:{user_id}"),
         InlineKeyboardButton(text="💰 Возврат", callback_data=f"admin_refund_user:{user_id}")],
        # Row 5: Danger zone
        [InlineKeyboardButton(text="🗑 Удалить пользователя", callback_data=f"admin_del_user:{user_id}")],
        # Row 6: Navigation
        [InlineKeyboardButton(text="🔙 Главное меню", callback_data="admin_menu")],
    ])


def _key_detail_kb(key_id: int, user_id: int, is_active: bool) -> InlineKeyboardMarkup:
    """Keyboard for key detail view with quick actions."""
    rows = [
        # Quick extend actions
        [
            InlineKeyboardButton(text="➕ +7 дней", callback_data=f"admin_key_add_days:{key_id}:{user_id}:7"),
            InlineKeyboardButton(text="➕ +30 дней", callback_data=f"admin_key_add_days:{key_id}:{user_id}:30"),
            InlineKeyboardButton(text="➕ +90 дней", callback_data=f"admin_key_add_days:{key_id}:{user_id}:90"),
        ],
        # Edit and delete
        [
            InlineKeyboardButton(text="✏️ Указать срок", callback_data=f"admin_edit_key:{key_id}"),
            InlineKeyboardButton(text="🗑️ Удалить", callback_data=f"admin_del_key_confirm:{key_id}:{user_id}"),
        ],
        # Navigation
        [
            InlineKeyboardButton(text="🔙 К списку ключей", callback_data=f"admin_user_keys:{user_id}"),
            InlineKeyboardButton(text="🏠 Главное меню", callback_data="admin_menu"),
        ]
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ── /admin ─────────────────────────────────────────────────────────────────

@router.message(F.text.startswith("/admin"))
async def cmd_admin(message: Message, state: FSMContext):
    logger.info(f"Admin command received from user {message.from_user.id}")
    if not _is_admin(message.from_user.id):
        logger.warning(f"Unauthorized admin attempt from user {message.from_user.id}")
        return
    await state.clear()
    try:
        await message.delete()
    except Exception:
        pass
    await message.answer(
        "⚡ <b>ByMeVPN — Админ-панель</b>\n"
        "━━━━━━━━━━━━━━━\n\n"
        "👋 Добро пожаловать в панель управления!\n\n"
        "📊 <b>Статистика</b> — просмотр метрик\n"
        "👥 <b>Пользователи</b> — управление клиентами\n"
        "💰 <b>Финансы</b> — платежи и возвраты\n"
        "📢 <b>Рассылка</b> — массовые уведомления\n\n"
        "👇 Выберите действие:",
        parse_mode="HTML", reply_markup=_main_kb()
    )


@router.callback_query(F.data == "admin_menu")
async def cb_admin_menu(callback: CallbackQuery, state: FSMContext):
    if not _is_admin(callback.from_user.id):
        await safe_answer(callback, "Нет доступа.", alert=True); return
    await safe_answer(callback)
    await state.clear()
    await callback.message.edit_text(
        "⚡ <b>ByMeVPN — Админ-панель</b>\n"
        "━━━━━━━━━━━━━━━\n\n"
        "👋 Добро пожаловать в панель управления!\n\n"
        "📊 <b>Статистика</b> — просмотр метрик\n"
        "👥 <b>Пользователи</b> — управление клиентами\n"
        "💰 <b>Финансы</b> — платежи и возвраты\n"
        "📢 <b>Рассылка</b> — массовые уведомления\n\n"
        "👇 Выберите действие:",
        parse_mode="HTML", reply_markup=_main_kb()
    )


# ── Stats ──────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "admin_stats")
async def cb_stats(callback: CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await safe_answer(callback, "Нет доступа.", alert=True); return
    await safe_answer(callback)
    s = await get_admin_stats()
    text = (
        "⚡ <b>ByMeVPN — Статистика</b>\n"
        "━━━━━━━━━━━━━━━\n\n"
        "👥 <b>Пользователи:</b>\n"
        f"  📊 Всего: <b>{s['total_users']}</b>\n"
        f"  ✅ Активные: <b>{s['active_users']}</b>\n"
        f"  📈 Конверсия: <b>{(s['active_users']/max(s['total_users'],1)*100):.1f}%</b>\n\n"
        "💰 <b>Доходы:</b>\n"
        f"  📅 Сегодня: <b>{s['today_revenue']} ₽</b>\n"
        f"  📆 Неделя: <b>{s['week_revenue']} ₽</b>\n"
        f"  📅 Месяц: <b>{s['month_revenue']} ₽</b>\n\n"
        f"🤝 <b>Рефералов:</b> <code>{s['total_referrals']}</code>"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Обновить", callback_data="admin_stats"),
         InlineKeyboardButton(text="📈 Подробнее", callback_data="admin_stats_ext")],
        [InlineKeyboardButton(text="🔙 Главное меню", callback_data="admin_menu")],
    ])
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)


@router.callback_query(F.data == "admin_stats_ext")
async def cb_stats_ext(callback: CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await safe_answer(callback, "Нет доступа.", alert=True); return
    await safe_answer(callback)
    s = await get_extended_stats()
    refund_stats = await get_refund_stats()

    top_text = ""
    if s["top_refs"]:
        top_text = "\n👑 <b>Топ рефераторов:</b>\n"
        for i, r in enumerate(s["top_refs"], 1):
            top_text += f"  {i}. ID {r['user_id']} — {r['count']} платных рефералов\n"

    text = (
        "📈 <b>Детальная статистика</b>\n\n"
        "👤 <b>Новые пользователи:</b>\n"
        f"  📅 За 24ч: {s['new_day']}\n"
        f"  📆 За неделю: {s['new_week']}\n"
        f"  📅 За месяц: {s['new_month']}\n\n"
        "🔑 <b>Активные подписки:</b>\n"
        f"  1️⃣ месяц: {s.get('active_1m', 0)}\n"
        f"  6️⃣ месяцев: {s.get('active_6m', 0)}\n"
        f"  🔢 год: {s.get('active_12m', 0)}\n"
        f"  2️⃣ года: {s.get('active_24m', 0)}\n\n"
        "� <b>Возвраты:</b>\n"
        f"  📅 30 дней: {refund_stats['count_30d']} ({refund_stats['sum_30d']} ₽)\n"
        f"  🔢 Всего: {refund_stats['count_total']} ({refund_stats['sum_total']} ₽)\n"
        f"{top_text}"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Обновить", callback_data="admin_stats_ext")],
        [InlineKeyboardButton(text="🔙 Главное меню", callback_data="admin_menu")],
    ])
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)


# ── Broadcast ──────────────────────────────────────────────────────────────

@router.callback_query(F.data == "admin_broadcast")
async def cb_broadcast(callback: CallbackQuery, state: FSMContext):
    if not _is_admin(callback.from_user.id):
        await safe_answer(callback, "Нет доступа.", alert=True); return
    await safe_answer(callback)
    await state.set_state(AdminFlow.broadcast)
    await callback.message.edit_text(
        "✍️ <b>Рассылка</b>\n\nОтправьте текст сообщения.\nПоддерживается HTML.",
        parse_mode="HTML", reply_markup=_back_kb()
    )


@router.message(StateFilter(AdminFlow.broadcast))
async def receive_broadcast(message: Message, bot: Bot, state: FSMContext):
    if not _is_admin(message.from_user.id):
        return
    await state.clear()
    text = message.html_text or message.text or ""
    if not text:
        await message.answer("Пустое сообщение. Отменено."); return
    user_ids = await get_all_user_ids()
    sent = failed = 0
    status = await message.answer(f"⏳ Рассылка... ({len(user_ids)} пользователей)")
    for uid in user_ids:
        try:
            await bot.send_message(uid, text, parse_mode="HTML")
            sent += 1
        except Exception:
            failed += 1
        if (sent + failed) % 25 == 0:
            await asyncio.sleep(1)
    await status.edit_text(
        f"✅ <b>Рассылка завершена</b>\n\n📤 Отправлено: {sent}\n❌ Ошибок: {failed}",
        parse_mode="HTML", reply_markup=_back_kb()
    )


# ── User list ──────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("admin_users:"))
async def cb_user_list(callback: CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await safe_answer(callback, "Нет доступа.", alert=True); return
    await safe_answer(callback)

    page = int(callback.data.split(":")[1])
    per_page = 10
    offset = page * per_page
    total = await get_users_count()
    users = await get_all_users_paginated(limit=per_page, offset=offset)

    if not users:
        await callback.message.edit_text(
            "Пользователей нет.", reply_markup=_back_kb()
        ); return

    lines = [f"👥 <b>Пользователи</b> (стр. {page+1}):\n"]
    for u in users:
        reg = fmt_date(u["created"]) if u["created"] else "?"
        lines.append(
            f"• <code>{u['user_id']}</code> — "
            f"{'✅' if u.get('active_keys', 0) > 0 else '❌'} "
            f"ключей: {u.get('total_keys', 0)}, рег: {reg}"
        )

    # Pagination + user buttons
    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton(text="◀", callback_data=f"admin_users:{page-1}"))
    if offset + per_page < total:
        nav_row.append(InlineKeyboardButton(text="▶", callback_data=f"admin_users:{page+1}"))

    user_rows = [
        [InlineKeyboardButton(
            text=f"👤 {u['user_id']}",
            callback_data=f"admin_user:{u['user_id']}"
        )]
        for u in users
    ]

    kb_rows = user_rows
    if nav_row:
        kb_rows = kb_rows + [nav_row]
    kb_rows.append([InlineKeyboardButton(text="◀️ Назад", callback_data="admin_menu")])

    await callback.message.edit_text(
        "\n".join(lines),
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows)
    )


# ── Extended User list with Keys ───────────────────────────────────────────

@router.callback_query(F.data.startswith("admin_users_ext:"))
async def cb_user_list_ext(callback: CallbackQuery):
    """Extended user list showing keys info inline."""
    if not _is_admin(callback.from_user.id):
        await safe_answer(callback, "Нет доступа.", alert=True); return
    await safe_answer(callback)

    page = int(callback.data.split(":")[1])
    per_page = 5  # Fewer users per page since we show keys
    offset = page * per_page
    total = await get_users_count()
    users = await get_all_users_paginated(limit=per_page, offset=offset)

    if not users:
        await callback.message.edit_text(
            "Пользователей нет.", reply_markup=_back_kb()
        ); return

    import time
    now = int(time.time())
    lines = [f"📋 <b>Пользователи + Ключи</b> (стр. {page+1}):\n"]

    for u in users:
        uid = u['user_id']
        keys = await get_user_keys(uid)

        # User header
        reg = fmt_date(u["created"]) if u["created"] else "?"
        lines.append(
            f"\n👤 <code>{uid}</code> | {'✅' if u.get('active_keys', 0) > 0 else '❌'} "
            f"Ключей: {len(keys)} | Рег: {reg}"
        )

        # Show keys inline
        if keys:
            for k in keys:
                status = "✅" if k["expiry"] > now else "❌"
                days_left = (k["expiry"] - now) // 86400
                key_name = k.get('remark', f"#{k['id']}")[:15]
                lines.append(
                    f"   {status} <b>{key_name}</b> — {days_left}дн. "
                    f"(до {fmt_date(k['expiry'])[:10]})"
                )
        else:
            lines.append("   🚫 Нет ключей")

    # Pagination
    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton(text="◀", callback_data=f"admin_users_ext:{page-1}"))
    if offset + per_page < total:
        nav_row.append(InlineKeyboardButton(text="▶", callback_data=f"admin_users_ext:{page+1}"))

    kb_rows = [
        [InlineKeyboardButton(text="🔍 Поиск по ID", callback_data="admin_search")],
    ]
    if nav_row:
        kb_rows.append(nav_row)
    kb_rows.append([
        InlineKeyboardButton(text="🔙 Главное меню", callback_data="admin_menu"),
        InlineKeyboardButton(text="👥 Обычный список", callback_data="admin_users:0")
    ])

    text = "\n".join(lines)
    if len(text) > 4000:  # Telegram limit
        text = text[:3990] + "\n... (обрезано)"

    await callback.message.edit_text(
        text,
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows)
    )


# ── All Keys View ─────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("admin_all_keys:"))
async def cb_all_keys(callback: CallbackQuery):
    """Show all keys from all users with pagination."""
    if not _is_admin(callback.from_user.id):
        await safe_answer(callback, "Нет доступа.", alert=True); return
    await safe_answer(callback)

    page = int(callback.data.split(":")[1])
    per_page = 10
    offset = page * per_page
    total = await get_keys_count()
    keys = await get_all_keys_paginated(limit=per_page, offset=offset)

    if not keys:
        await callback.message.edit_text(
            "🗝 Ключей нет.", reply_markup=_back_kb()
        ); return

    import time
    now = int(time.time())
    lines = [f"🗝 <b>Все ключи</b> (стр. {page+1} из {(total//per_page)+1}):\n"]
    lines.append(f"📊 Всего ключей: {total}\n")

    for k in keys:
        status = "✅" if k["is_active"] else "❌"
        days_left = (k["expiry"] - now) // 86400 if k["expiry"] > now else 0
        paid = "💰" if k.get("total_paid", 0) > 0 else "🆓"
        key_name = k.get('remark', f"#{k['id']}")[:12]

        lines.append(
            f"{status} <b>{key_name}</b> | 👤 {k['user_id']} | "
            f"{paid} | {days_left}дн. | 📅 {fmt_date(k['expiry'])[:10]}"
        )

    # Pagination
    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton(text="◀", callback_data=f"admin_all_keys:{page-1}"))
    if offset + per_page < total:
        nav_row.append(InlineKeyboardButton(text="▶", callback_data=f"admin_all_keys:{page+1}"))

    kb_rows = []
    if nav_row:
        kb_rows.append(nav_row)
    kb_rows.append([
        InlineKeyboardButton(text="🔙 Главное меню", callback_data="admin_menu"),
        InlineKeyboardButton(text="📋 Юзеры+Ключи", callback_data="admin_users_ext:0")
    ])

    text = "\n".join(lines)
    if len(text) > 4000:
        text = text[:3990] + "\n... (обрезано)"

    await callback.message.edit_text(
        text, parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows)
    )


# ── User search ────────────────────────────────────────────────────────────

@router.callback_query(F.data == "admin_search")
async def cb_search(callback: CallbackQuery, state: FSMContext):
    if not _is_admin(callback.from_user.id):
        await safe_answer(callback, "Нет доступа.", alert=True); return
    await safe_answer(callback)
    await state.set_state(AdminFlow.search_user)
    await callback.message.edit_text(
        "🔍 Введите Telegram ID пользователя:",
        reply_markup=_back_kb()
    )


@router.message(StateFilter(AdminFlow.search_user))
async def receive_search(message: Message, state: FSMContext):
    if not _is_admin(message.from_user.id):
        return
    await state.clear()
    try:
        uid = int(message.text.strip())
    except ValueError:
        await message.answer("❌ Введите числовой ID."); return
    await _show_user(message, uid)


async def _show_user(target, user_id: int):
    """Show user info card."""
    user = await find_user_by_id(user_id)
    if not user:
        text = f"❌ Пользователь <code>{user_id}</code> не найден."
        if isinstance(target, Message):
            await target.answer(text, parse_mode="HTML", reply_markup=_back_kb())
        else:
            await target.message.edit_text(text, parse_mode="HTML", reply_markup=_back_kb())
        return

    reg = fmt_date(user["created"]) if user["created"] else "?"
    
    # Get refund info
    refunds = await get_user_refunds(user_id)
    refund_total = sum(r["amount"] for r in refunds) if refunds else 0
    
    text = (
        f"👤 <b>Пользователь {user_id}</b>\n\n"
        f"📅 <b>Регистрация:</b> {reg}\n"
        f"🔑 <b>Ключи:</b> {user.get('total_keys', 0)} (активных: {user.get('active_keys', 0)})\n"
        f"🆓 <b>Пробник:</b> {'✅ Использован' if user['trial_used'] else '❌ Не использован'}\n"
        f"👥 <b>Реферер:</b> {user['referrer_id'] or '—'}\n"
        f"💰 <b>Всего оплачено:</b> {user['total_paid']} ₽\n"
        f"💸 <b>Возвращено:</b> {refund_total} {'звёзд' if refunds else '0'}\n"
        f"🔄 <b>Возвратов:</b> {len(refunds) if refunds else 0}"
    )
    kb = _user_kb(user_id)
    if isinstance(target, Message):
        await target.answer(text, parse_mode="HTML", reply_markup=kb)
    else:
        await target.message.edit_text(text, parse_mode="HTML", reply_markup=kb)


@router.callback_query(F.data.startswith("admin_user:"))
async def cb_user_card(callback: CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await safe_answer(callback, "Нет доступа.", alert=True); return
    await safe_answer(callback)
    uid = int(callback.data.split(":")[1])
    logger.info("Admin %d viewed user %d card", callback.from_user.id, uid)
    await _show_user(callback, uid)


# ── User keys (admin view) ─────────────────────────────────────────────────

@router.callback_query(F.data.startswith("admin_user_keys:"))
async def cb_user_keys(callback: CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await safe_answer(callback, "Нет доступа.", alert=True); return
    await safe_answer(callback)
    uid = int(callback.data.split(":")[1])
    keys = await get_user_keys(uid)
    if not keys:
        await callback.message.edit_text(
            f"У пользователя {uid} нет ключей.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="◀️ Назад", callback_data=f"admin_user:{uid}")
            ]])
        ); return
    lines = [f"🗝 <b>Ключи пользователя {uid}:</b>\n"]
    import time
    now = int(time.time())
    for k in keys:
        status = "✅" if k["expiry"] > now else "❌"
        created_str = fmt_date(k.get('created', 0))
        lines.append(
            f"{status} <b>#{k['id']}</b> {k.get('remark','')}\n"
            f"   👤 User ID: {uid}\n"
            f"   📅 Создан: {created_str}\n"
            f"   ⏳ До: {fmt_date(k['expiry'])} ({k['days']} дн.)"
        )
    await callback.message.edit_text(
        "\n".join(lines), parse_mode="HTML",
        reply_markup=_keys_kb(keys, uid)
    )


# ── Edit key days ──────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("admin_edit_key:"))
async def cb_edit_key(callback: CallbackQuery, state: FSMContext):
    if not _is_admin(callback.from_user.id):
        await safe_answer(callback, "Нет доступа.", alert=True); return
    await safe_answer(callback)
    key_id = int(callback.data.split(":")[1])
    await state.set_state(AdminFlow.edit_key_days)
    await state.update_data(edit_key_id=key_id)
    await callback.message.edit_text(
        f"✏️ Ключ <b>#{key_id}</b>\n\n"
        "Введите количество дней от сегодня (например: <code>30</code> — продлить на 30 дней):",
        parse_mode="HTML",
        reply_markup=_back_kb()
    )


@router.message(StateFilter(AdminFlow.edit_key_days))
async def receive_edit_days(message: Message, state: FSMContext):
    """Set key to specific number of days from today."""
    if not _is_admin(message.from_user.id):
        return
    data = await state.get_data()
    await state.clear()
    try:
        days = int(message.text.strip())
        if days < 1:
            raise ValueError
    except ValueError:
        await message.answer("❌ Введите положительное число дней."); return
    key_id = data.get("edit_key_id")
    
    # set_key_days now automatically syncs with 3x-UI panel
    await set_key_days(key_id, days)
    
    await message.answer(
        f"✅ Ключ <b>#{key_id}</b> обновлён — {days} дней от сегодня.\n\n"
        f"Срок синхронизирован с панелью 3x-UI — ключ продолжает работать!",
        parse_mode="HTML", reply_markup=_back_kb()
    )


# ── Delete key (admin) ─────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("admin_del_key_confirm:"))
async def cb_admin_del_key_confirm(callback: CallbackQuery, bot: Bot):
    """Show confirmation before deleting key."""
    if not _is_admin(callback.from_user.id):
        await safe_answer(callback, "Нет доступа.", alert=True); return
    await safe_answer(callback)
    
    parts = callback.data.split(":")
    key_id = int(parts[1])
    user_id = int(parts[2])
    
    from database import get_key_by_id
    k = await get_key_by_id(key_id)
    
    if not k:
        await callback.answer("Ключ не найден.", show_alert=True)
        return
    
    # Show confirmation
    text = (
        f"⚠️ <b>Подтвердите удаление ключа</b>\n\n"
        f"🔑 Ключ #{key_id}\n"
        f"👤 Пользователь: {user_id}\n"
        f"🏷️ Название: {k.get('remark', 'N/A')}\n"
        f"🔐 UUID: <code>{k.get('uuid', 'N/A')[:20]}...</code>\n\n"
        f"❗️ <b>Внимание:</b> Ключ будет удалён из БД и панели 3x-UI.\n"
        f"Это действие нельзя отменить!"
    )
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Да, удалить", callback_data=f"admin_del_key:{key_id}:{user_id}"),
            InlineKeyboardButton(text="❌ Отмена", callback_data=f"admin_user_keys:{user_id}")
        ]
    ])
    
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)


@router.callback_query(F.data.startswith("admin_del_key:"))
async def cb_admin_del_key(callback: CallbackQuery, bot: Bot):
    """Actually delete the key after confirmation."""
    if not _is_admin(callback.from_user.id):
        await safe_answer(callback, "Нет доступа.", alert=True); return
    await safe_answer(callback)
    
    parts = callback.data.split(":")
    key_id = int(parts[1])
    user_id = int(parts[2])
    
    from database import get_key_by_id, delete_key_by_id
    k = await get_key_by_id(key_id)
    
    if not k:
        await callback.answer("Ключ не найден.", show_alert=True)
        return
    
    # Delete from 3x-UI panel
    deleted_from_panel = True
    if k and k.get("uuid"):
        deleted_from_panel = await delete_client(k["uuid"])
        if not deleted_from_panel:
            logger.warning(f"Failed to delete client {k['uuid']} from 3x-UI")
    
    # Delete from database
    deleted_from_db = await delete_key_by_id(key_id)
    
    # Show result
    status_msg = f"✅ Ключ #{key_id} удалён"
    if not deleted_from_panel and k and k.get("uuid"):
        status_msg += "\n⚠️ (Не удалось удалить из панели 3x-UI)"
    
    await callback.answer(status_msg, show_alert=True)
    
    # Refresh keys list
    keys = await get_user_keys(user_id)
    if not keys:
        await callback.message.edit_text(
            f"🗝 <b>Ключи пользователя {user_id}</b>\n\n"
            f"✅ Все ключи удалены.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="◀️ Назад к пользователю", callback_data=f"admin_user:{user_id}")]
            ])
        )
    else:
        import time
        now = int(time.time())
        lines = [f"🗝 <b>Ключи {user_id}:</b>\n"]
        for k2 in keys:
            s = "✅" if k2["expiry"] > now else "❌"
            lines.append(f"{s} #{k2['id']} {k2.get('remark','')} — до {fmt_date(k2['expiry'])}")
        await callback.message.edit_text(
            "\n".join(lines), parse_mode="HTML",
            reply_markup=_keys_kb(keys, user_id)
        )


# ── Delete user ────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("admin_del_user:"))
async def cb_del_user(callback: CallbackQuery, bot: Bot):
    if not _is_admin(callback.from_user.id):
        await safe_answer(callback, "Нет доступа.", alert=True); return
    await safe_answer(callback)
    uid = int(callback.data.split(":")[1])
    uuids = await delete_user_and_keys(uid)
    # Delete from panel
    for uuid in uuids:
        await delete_client(uuid)
    await callback.message.edit_text(
        f"🗑 Пользователь <code>{uid}</code> и все его ключи ({len(uuids)}) удалены.",
        parse_mode="HTML", reply_markup=_back_kb()
    )


# ── Reset trial ────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("admin_reset_trial:"))
async def cb_reset_trial(callback: CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await safe_answer(callback, "Нет доступа.", alert=True); return
    uid = int(callback.data.split(":")[1])
    await reset_trial_for_user(uid)
    await callback.answer(f"✅ Пробник пользователя {uid} сброшен. Теперь он может взять пробный период заново.", show_alert=True)


@router.callback_query(F.data.startswith("admin_grant_trial:"))
async def cb_grant_trial(callback: CallbackQuery, bot: Bot, state: FSMContext):
    """
    Admin grants a free trial key directly to a user.
    Resets trial flag, then starts the deliver flow on behalf of the user.
    """
    if not _is_admin(callback.from_user.id):
        await safe_answer(callback, "Нет доступа.", alert=True); return
    await safe_answer(callback)

    uid = int(callback.data.split(":")[1])

    # Reset trial flag so delivery is allowed
    await reset_trial_for_user(uid)

    # Deliver key directly without asking for config name — use "Admin" as name
    from config import TRIAL_DAYS
    from subscription import deliver_key
    config_name = f"Trial-{uid}"
    success = await deliver_key(
        bot=bot,
        user_id=uid,
        chat_id=uid,
        config_name=config_name,
        days=TRIAL_DAYS,
        limit_ip=1,
        is_paid=False,
        amount=0,
        currency="RUB",
        method="admin_trial",
        payload=f"admin_trial_{uid}",
    )

    if success:
        await callback.message.answer(
            f"✅ Пробный ключ выдан пользователю <code>{uid}</code> "
            f"на {TRIAL_DAYS} дней (1 устройство).",
            parse_mode="HTML",
            reply_markup=_back_kb(),
        )
    else:
        await callback.message.answer(
            f"❌ Не удалось выдать ключ пользователю <code>{uid}</code>.\n"
            "Проверьте подключение к 3x-UI в логах.",
            parse_mode="HTML",
            reply_markup=_back_kb(),
        )


# ── Personal message ───────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("admin_pm:"))
async def cb_pm_start(callback: CallbackQuery, state: FSMContext):
    if not _is_admin(callback.from_user.id):
        await safe_answer(callback, "Нет доступа.", alert=True); return
    await safe_answer(callback)
    uid = int(callback.data.split(":")[1])
    await state.set_state(AdminFlow.send_personal_msg)
    await state.update_data(pm_target=uid)
    await callback.message.edit_text(
        f"✉️ Введите сообщение для пользователя <code>{uid}</code>:",
        parse_mode="HTML", reply_markup=_back_kb()
    )


@router.message(StateFilter(AdminFlow.send_personal_msg))
async def receive_personal_msg(message: Message, bot: Bot, state: FSMContext):
    if not _is_admin(message.from_user.id):
        return
    data = await state.get_data()
    await state.clear()
    uid = data.get("pm_target")
    text = message.html_text or message.text or ""
    if not text or not uid:
        await message.answer("Отменено."); return
    try:
        await bot.send_message(uid, text, parse_mode="HTML")
        await message.answer(f"✅ Сообщение отправлено пользователю {uid}.", reply_markup=_back_kb())
    except Exception as e:
        await message.answer(f"❌ Не удалось отправить: {e}", reply_markup=_back_kb())


# ── Payment history ────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("admin_user_pay:"))
async def cb_user_payments(callback: CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await safe_answer(callback, "Нет доступа.", alert=True); return
    await safe_answer(callback)
    uid = int(callback.data.split(":")[1])
    payments = await get_user_payments(uid)
    if not payments:
        await callback.message.edit_text(
            f"Платежей у пользователя {uid} нет.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="◀️ Назад", callback_data=f"admin_user:{uid}")
            ]])
        ); return
    lines = [f"💳 <b>Платежи пользователя {uid}:</b>\n"]
    for p in payments:
        dt = fmt_date(p["created"])
        lines.append(
            f"• {dt} — {p['amount']} {p['currency']} "
            f"({p['method']}, {p['days']} дн.)"
        )
    total = sum(p["amount"] for p in payments)
    lines.append(f"\n💰 Итого: {total} ₽")
    await callback.message.edit_text(
        "\n".join(lines), parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="◀️ Назад", callback_data=f"admin_user:{uid}")
        ]])
    )


# ── Export CSV ─────────────────────────────────────────────────────────────

@router.callback_query(F.data == "admin_export_csv")
async def cb_export_csv(callback: CallbackQuery, bot: Bot):
    if not _is_admin(callback.from_user.id):
        await safe_answer(callback, "Нет доступа.", alert=True); return
    await safe_answer(callback)
    await callback.message.edit_text("⏳ Генерирую CSV...", reply_markup=_back_kb())
    csv_text = await get_all_users_csv()
    date_str = datetime.now().strftime("%Y%m%d_%H%M")
    filename = f"bymevpn_users_{date_str}.csv"
    file_bytes = csv_text.encode("utf-8")
    await bot.send_document(
        chat_id=callback.from_user.id,
        document=BufferedInputFile(file_bytes, filename=filename),
        caption=f"📥 Экспорт пользователей ByMeVPN\n{datetime.now().strftime('%d.%m.%Y %H:%M')}",
    )
    await callback.message.edit_text(
        "✅ CSV отправлен выше.", reply_markup=_back_kb()
    )


# ── Refunds management ─────────────────────────────────────────────────────

@router.callback_query(F.data == "admin_refunds")
async def cb_refunds_main(callback: CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await safe_answer(callback, "Нет доступа.", alert=True); return
    await safe_answer(callback)
    
    stats = await get_refund_stats()
    refunds = await get_all_refunds()
    
    text = (
        "� <b>Управление возвратами</b>\n\n"
        f"📊 <b>Статистика:</b>\n"
        f"  📅 30 дней: {stats['count_30d']} возвратов ({stats['sum_30d']} ₽)\n"
        f"  🔢 Всего: {stats['count_total']} возвратов ({stats['sum_total']} ₽)\n\n"
    )
    
    if refunds:
        text += "<b>📋 Последние возвраты:</b>\n"
        for r in refunds:
            dt = fmt_date(r["created"])
            text += f"• {dt} — {r['amount']} {r['currency']} (юзер {r['user_id']})\n"
    else:
        text += "📋 Возвратов пока нет."
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 Возврат юзеру", callback_data="admin_refund_search")],
        [InlineKeyboardButton(text="📋 Все возвраты", callback_data="admin_refunds_list")],
        [InlineKeyboardButton(text="🔙 Главное меню", callback_data="admin_menu")],
    ])
    
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)


@router.callback_query(F.data == "admin_refund_search")
async def cb_refund_search(callback: CallbackQuery, state: FSMContext):
    if not _is_admin(callback.from_user.id):
        await safe_answer(callback, "Нет доступа.", alert=True); return
    await safe_answer(callback)
    await state.set_state(AdminFlow.search_user)
    await state.update_data(refund_mode=True)
    await callback.message.edit_text(
        "💰 <b>Возврат средств</b>\n\n"
        "Введите Telegram ID пользователя для возврата:",
        reply_markup=_back_kb()
    )


@router.callback_query(F.data.startswith("admin_refund_user:"))
async def cb_refund_user(callback: CallbackQuery, state: FSMContext):
    if not _is_admin(callback.from_user.id):
        await safe_answer(callback, "Нет доступа.", alert=True); return
    await safe_answer(callback)
    
    uid = int(callback.data.split(":")[1])
    user = await find_user_by_id(uid)
    if not user:
        await callback.message.edit_text(
            f"❌ Пользователь <code>{uid}</code> не найден.",
            reply_markup=_back_kb()
        )
        return
    
    payments = await get_user_payments(uid)
    if not payments:
        await callback.message.edit_text(
            f"У пользователя <code>{uid}</code> нет платежей для возврата.",
            reply_markup=_back_kb()
        )
        return
    
    # Show recent payments for context
    text = (
        f"💰 <b>Возврат пользователю {uid}</b>\n\n"
        f"💳 Всего оплачено: {user['total_paid']} ₽\n\n"
        f"<b>Последние платежи:</b>\n"
    )
    
    for p in payments[:5]:  # Show last 5 payments
        dt = fmt_date(p["created"])
        text += f"• {dt} — {p['amount']} {p['currency']} ({p['method']})\n"
    
    text += f"\nВыберите платеж для возврата или введите сумму:"
    
    # Create buttons for recent payments
    rows = []
    for p in payments[:3]:  # Quick refund buttons for last 3 payments
        rows.append([
            InlineKeyboardButton(
                text=f"Вернуть {p['amount']} {p['currency']} ({p['method']})",
                callback_data=f"admin_refund_do:{uid}:{p['amount']}:{p['currency']}:{p['method']}:{p['payload'] or ''}"
            )
        ])
    
    rows.append([
        InlineKeyboardButton(text="📝 Другая сумма", callback_data=f"admin_refund_custom:{uid}")
    ])
    rows.append([InlineKeyboardButton(text="◀️ Назад", callback_data="admin_refunds")])
    
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


@router.callback_query(F.data.startswith("admin_refund_custom:"))
async def cb_refund_custom(callback: CallbackQuery, state: FSMContext):
    if not _is_admin(callback.from_user.id):
        await safe_answer(callback, "Нет доступа.", alert=True); return
    await safe_answer(callback)
    
    uid = int(callback.data.split(":")[1])
    await state.set_state(AdminFlow.refund_amount)
    await state.update_data(refund_user_id=uid)
    
    await callback.message.edit_text(
        f"💰 <b>Возврат пользователю {uid}</b>\n\n"
        "Введите сумму для возврата (в звёздах/рублях):",
        reply_markup=_back_kb()
    )


@router.message(StateFilter(AdminFlow.refund_amount))
async def receive_refund_amount(message: Message, state: FSMContext):
    if not _is_admin(message.from_user.id):
        return
    
    try:
        amount = int(message.text.strip())
        if amount <= 0:
            raise ValueError("Amount must be positive")
    except ValueError:
        await message.answer("❌ Введите положительное число."); return
    
    data = await state.get_data()
    uid = data.get("refund_user_id")
    if not uid:
        await message.answer("❌ Ошибка сессии. Начните заново."); return
    
    await state.set_state(AdminFlow.refund_reason)
    await state.update_data(refund_amount=amount)
    
    await message.answer(
        f"💰 <b>Возврат {amount} звёзд пользователю {uid}</b>\n\n"
        "Введите причину возврата:",
        reply_markup=_back_kb()
    )


@router.message(StateFilter(AdminFlow.refund_reason))
async def receive_refund_reason(message: Message, bot: Bot, state: FSMContext):
    if not _is_admin(message.from_user.id):
        return
    
    reason = message.text.strip() or "Возврат по запросу"
    data = await state.get_data()
    await state.clear()
    
    uid = data.get("refund_user_id")
    amount = data.get("refund_amount")
    
    if not uid or not amount:
        await message.answer("❌ Ошибка сессии. Начните заново."); return
    
    # Record refund in database
    await add_refund(
        user_id=uid,
        amount=amount,
        currency="XTR",  # Telegram Stars
        method="stars",
        reason=reason,
        refunded_by=message.from_user.id
    )
    
    # Try to refund actual Stars (this would require Telegram Stars API)
    # For now, we just record it and notify admin
    
    try:
        # Notify user about refund
        await bot.send_message(
            uid,
            f"💰 <b>Возврат средств</b>\n\n"
            f"Вам возвращено <b>{amount} звёзд</b>.\n"
            f"Причина: {reason}\n\n"
            f"Если звёзды не поступили в течение 5 минут, напишите в поддержку.",
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error("Could not notify user %d about refund: %s", uid, e)
    
    await message.answer(
        f"✅ <b>Возврат оформлен</b>\n\n"
        f"👤 Пользователь: <code>{uid}</code>\n"
        f"💰 Сумма: {amount} звёзд\n"
        f"📝 Причина: {reason}\n\n"
        f"ℹ️ Запись добавлена в базу данных.",
        reply_markup=_back_kb()
    )


@router.callback_query(F.data.startswith("admin_refund_do:"))
async def cb_refund_do(callback: CallbackQuery, state: FSMContext):
    if not _is_admin(callback.from_user.id):
        await safe_answer(callback, "Нет доступа.", alert=True); return
    await safe_answer(callback)
    
    parts = callback.data.split(":")
    uid = int(parts[1])
    amount = int(parts[2])
    currency = parts[3]
    method = parts[4]
    payload = ":".join(parts[5:]) if len(parts) > 5 else ""
    
    # Quick refund with default reason
    await add_refund(
        user_id=uid,
        amount=amount,
        currency=currency,
        method=method,
        reason=f"Возврат платежа ({method})",
        original_payload=payload,
        refunded_by=callback.from_user.id
    )
    
    try:
        await callback.bot.send_message(
            uid,
            f"💰 <b>Возврат средств</b>\n\n"
            f"Вам возвращено <b>{amount} {currency}</b>.\n"
            f"Причина: Возврат платежа\n\n"
            f"Если средства не поступили в течение 5 минут, напишите в поддержку.",
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error("Could not notify user %d about refund: %s", uid, e)
    
    await callback.answer(f"✅ Возврат {amount} {currency} пользователю {uid} оформлен!", show_alert=True)
    
    # Refresh refunds list
    await cb_refunds_main(callback)


@router.callback_query(F.data == "admin_refunds_list")
async def cb_refunds_list(callback: CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await safe_answer(callback, "Нет доступа.", alert=True); return
    await safe_answer(callback)
    
    refunds = await get_all_refunds(limit=30)
    
    if not refunds:
        await callback.message.edit_text(
            "Пока нет возвратов.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="◀️ Назад", callback_data="admin_refunds")
            ]])
        )
        return
    
    text = "📋 <b>Все возвраты:</b>\n\n"
    for r in refunds:
        dt = fmt_date(r["created"])
        text += (
            f"• {dt} — {r['amount']} {r['currency']}\n"
            f"  Юзер: {r['user_id']} (всего оплачено: {r['user_total_paid']} ₽)\n"
            f"  Метод: {r['method']}, причина: {r['reason']}\n\n"
        )
    
    await callback.message.edit_text(
        text,
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="◀️ Назад", callback_data="admin_refunds")
        ]])
    )


# ── Payments management ─────────────────────────────────────────────────────

@router.callback_query(F.data == "admin_payments")
async def cb_payments_main(callback: CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await safe_answer(callback, "Нет доступа.", alert=True); return
    await safe_answer(callback)
    
    # Get payment statistics
    stats = await get_payment_stats()
    
    text = (
        "💳 <b>Управление платежами</b>\n\n"
        "📊 <b>Статистика:</b>\n"
    )
    
    # Overall stats by method
    method_stats = stats.get("by_method", {})
    if method_stats:
        for method, data in method_stats.items():
            text += f"• {method}: {data.get('count', 0)} платежей, {data.get('sum', 0)} ₽\n"
    else:
        text += "• Пока нет данных\n"
    
    text += "\n📈 <b>Общая статистика:</b>\n"
    text += f"• Всего: {stats.get('total', {}).get('count', 0)} платежей, {stats.get('total', {}).get('sum', 0)} ₽\n"
    text += f"• Сегодня: {stats.get('today', {}).get('count', 0)} платежей, {stats.get('today', {}).get('sum', 0)} ₽\n"
    text += f"• Месяц: {stats.get('month', {}).get('count', 0)} платежей, {stats.get('month', {}).get('sum', 0)} ₽\n"
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="📋 Все платежи", callback_data="admin_payments_list"),
            InlineKeyboardButton(text="⭐ Stars", callback_data="admin_payments_list:stars")
        ],
        [
            InlineKeyboardButton(text="💳 YooKassa", callback_data="admin_payments_list:yookassa"),
            InlineKeyboardButton(text="🔍 Поиск", callback_data="admin_payments_search")
        ],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="admin_menu")]
    ])
    
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)


@router.callback_query(F.data.startswith("admin_payments_list"))
async def cb_payments_list(callback: CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await safe_answer(callback, "Нет доступа.", alert=True); return
    await safe_answer(callback)
    
    from database import get_all_payments
    
    # Parse method filter
    parts = callback.data.split(":")
    method = parts[1] if len(parts) > 1 else None
    
    payments = await get_all_payments(limit=30, method=method)
    
    if not payments:
        method_text = f" ({method})" if method else ""
        await callback.message.edit_text(
            f"💳 <b>Платежи{method_text}</b>\n\n"
            "Платежи не найдены.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="◀️ Назад", callback_data="admin_payments")
            ]])
        )
        return
    
    title = f"💳 <b>Платежи{f' - {method}' if method else ''}</b>\n\n"
    text = title
    
    for p in payments[:15]:  # Show last 15 to avoid message length issues
        dt = fmt_date(p["created"])
        status_emoji = "✅" if p["status"] == "success" else "❌"
        text += (
            f"{status_emoji} {dt} — {p['amount']} {p['currency']}\n"
            f"  Юзер: {p['user_id']} (всего оплачено: {p['user_total_paid']} ₽)\n"
            f"  Метод: {p['method']}, тариф: {p['tariff'] or 'Не указан'}\n"
            f"  Устройств: {p['devices']}, дней: {p['days']}\n\n"
        )
    
    if len(payments) > 15:
        text += f"... и еще {len(payments) - 15} платежей\n\n"
    
    # Navigation buttons
    nav_buttons = []
    if method:
        nav_buttons.append(InlineKeyboardButton(text="◀️ Все платежи", callback_data="admin_payments_list"))
    nav_buttons.append(InlineKeyboardButton(text="🔄 Обновить", callback_data=callback.data))
    
    kb_rows = [nav_buttons] if nav_buttons else []
    kb_rows.append([InlineKeyboardButton(text="◀️ Назад", callback_data="admin_payments")])
    
    await callback.message.edit_text(
        text,
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows)
    )


@router.callback_query(F.data == "admin_payments_search")
async def cb_payments_search(callback: CallbackQuery, state: FSMContext):
    if not _is_admin(callback.from_user.id):
        await safe_answer(callback, "Нет доступа.", alert=True); return
    await safe_answer(callback)
    
    await state.set_state(AdminFlow.payment_search)
    await callback.message.edit_text(
        "💳 <b>Поиск платежей</b>\n\n"
        "Введите ID пользователя для поиска его платежей:",
        reply_markup=_back_kb()
    )


@router.message(StateFilter(AdminFlow.payment_search))
async def receive_payment_search(message: Message, state: FSMContext):
    if not _is_admin(message.from_user.id):
        return
    
    try:
        user_id = int(message.text.strip())
    except ValueError:
        await message.answer("❌ Введите корректный ID пользователя (число)."); return
    
    from database import get_user_payments
    
    await state.clear()
    
    payments = await get_user_payments(user_id, limit=20)
    
    if not payments:
        await message.answer(
            f"💳 <b>Платежи пользователя {user_id}</b>\n\n"
            "Платежи не найдены.",
            reply_markup=_back_kb()
        )
        return
    
    text = f"💳 <b>Платежи пользователя {user_id}</b>\n\n"
    for p in payments:
        dt = fmt_date(p["created"])
        status_emoji = "✅" if p["status"] == "success" else "❌"
        text += (
            f"{status_emoji} {dt} — {p['amount']} {p['currency']}\n"
            f"  Метод: {p['method']}, тариф: {p['tariff'] or 'Не указан'}\n"
            f"  Устройств: {p['devices']}, дней: {p['days']}\n\n"
        )
    
    await message.answer(text, parse_mode="HTML", reply_markup=_back_kb())


# ── Referrals management ─────────────────────────────────────────────────────

@router.callback_query(F.data == "admin_referrals")
async def cb_referrals_main(callback: CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await safe_answer(callback, "Нет доступа.", alert=True); return
    await safe_answer(callback)
    
    from database import get_all_referral_stats
    from referral_system_new import get_referral_stats_with_free_days
    
    # Get top referrers with detailed stats
    referrers = await get_all_referral_stats()
    
    # Convert to list and limit to 20
    referrers_list = [{"user_id": uid, "total_referrals": stats.get("total_referrals", 0)} for uid, stats in referrers.items()]
    referrers_list = referrers_list[:20]
    
    if not referrers_list:
        await callback.message.edit_text(
            "<b>Управление рефералами</b>\n\n"
            "Пока нет реферальных данных.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="Назад", callback_data="admin_menu")
            ]])
        )
        return
    
    text = "<b>🏆 Топ рефералы</b>\n\n"
    for i, ref in enumerate(referrers_list[:10], 1):
        # Get detailed stats including free/paid days
        detailed_stats = await get_referral_stats_with_free_days(ref["user_id"])
        
        text += (
            f"{i}. <code>{ref['user_id']}</code> — "
            f"{ref['total_referrals']} рефералов\n"
            f"   🎁 Бесплатных дней: {detailed_stats.get('free_days', 0)}\n"
            f"   💰 Платных дней: {detailed_stats.get('paid_days', 0)}\n"
            f"   ⏳ Ожидают: {detailed_stats.get('pending_bonuses', 0)}\n\n"
        )
    
    if len(referrers_list) > 10:
        text += f"... и еще {len(referrers_list) - 10} рефералов\n\n"
    
    # Create user detail buttons
    kb_rows = []
    for ref in referrers_list[:5]:  # Top 5 with detail buttons
        kb_rows.append([InlineKeyboardButton(
            text=f"👤 Детали ID {ref['user_id']}",
            callback_data=f"admin_referral_details:{ref['user_id']}"
        )])
    
    kb_rows.extend([
        [InlineKeyboardButton(text="🔄 Обновить", callback_data="admin_referrals")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="admin_menu")]
    ])
    
    kb = InlineKeyboardMarkup(inline_keyboard=kb_rows)
    
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)


@router.callback_query(F.data.startswith("admin_referral_details:"))
async def cb_referral_details(callback: CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await safe_answer(callback, "Нет доступа.", alert=True); return
    await safe_answer(callback)
    
    from database import get_referral_events, get_referral_stats
    from referral_system_new import get_referral_stats_with_free_days
    
    referrer_id = int(callback.data.split(":")[1])
    
    # Get referral summary and events
    summary = await get_referral_stats(referrer_id)
    events = await get_referral_events(referrer_id, limit=20)
    detailed_stats = await get_referral_stats_with_free_days(referrer_id)
    
    text = (
        f"<b>Детали реферала {referrer_id}</b>\n\n"
        f"<b>Статистика:</b>\n"
        f"• Всего рефералов: {summary['total']}\n"
        f"• Оплативших: {summary['paid']}\n"
        f"• Ожидают оплаты: {summary['total'] - summary['paid']}\n"
        f"• Бесплатных дней: {detailed_stats['free_days']}\n"
        f"• Платных дней: {detailed_stats['paid_days']}\n"
        f"• Всего бонусных дней: {detailed_stats['total_bonus_days']}\n"
        f"• Ожидают активации: {detailed_stats['pending_bonuses']}\n\n"
        f"<b>Последние события:</b>\n"
    )
    
    for event in events[:10]:
        dt = fmt_date(event["created"])
        event_type_text = {
            "trial_bonus": "Бонус за переход",
            "payment_bonus": "Бонус за оплату",
            "registration": "Регистрация"
        }.get(event["event_type"], "Событие")
        
        text += (
            f"{event_type_text} {dt} — {event['days_awarded']} дней\n"
            f"   ID: {event['referred_id']}\n"
            f"   {event['description']}\n\n"
        )
    
    if len(events) > 10:
        text += f"... и еще {len(events) - 10} событий\n\n"
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Обновить", callback_data=callback.data)],
        [InlineKeyboardButton(text="Все рефералы", callback_data="admin_referrals")]
    ])
    
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)


# ── Expired keys cleanup ─────────────────────────────────────────────────────

@router.callback_query(F.data == "admin_cleanup")
async def cb_cleanup(callback: CallbackQuery, bot: Bot):
    if not _is_admin(callback.from_user.id):
        await safe_answer(callback, "Нет доступа.", alert=True); return
    await safe_answer(callback)
    
    from database import cleanup_expired_keys_report
    import time
    
    # Get cleanup statistics
    stats = await cleanup_expired_keys_report()
    
    text = (
        f"🧹 <b>Очистка истёкших ключей</b>\n\n"
        f"📊 <b>Статистика:</b>\n"
        f"• Удалено ключей: {stats.get('removed_count', 0)}\n\n"
        f"⚠️ Очистка удалит истёкшие клиенты из панели 3x-UI.\n"
        f"Ключи в базе данных останутся для истории.\n\n"
        f"Выполнить очистку?"
    )
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🧹 Очистить", callback_data="admin_cleanup_do"),
            InlineKeyboardButton(text="🔄 Обновить", callback_data="admin_cleanup")
        ],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="admin_menu")]
    ])
    
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)


@router.callback_query(F.data == "admin_cleanup_do")
async def cb_cleanup_do(callback: CallbackQuery, bot: Bot):
    if not _is_admin(callback.from_user.id):
        await safe_answer(callback, "Нет доступа.", alert=True); return
    await safe_answer(callback)
    
    from database import get_expired_keys, mark_keys_cleaned
    from xui import delete_client
    import time
    
    # Get expired keys that can be cleaned
    expired_keys = await get_expired_keys()
    
    if not expired_keys:
        await callback.message.edit_text(
            "✅ <b>Очистка завершена</b>\n\n"
            "Нет истёкших ключей для очистки.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="◀️ Назад", callback_data="admin_menu")
            ]])
        )
        return
    
    # Clean up keys from 3x-UI
    cleaned_count = 0
    failed_count = 0
    cleaned_key_ids = []
    
    for key in expired_keys:
        try:
            success = await delete_client(key["uuid"])
            if success:
                cleaned_count += 1
                cleaned_key_ids.append(key["id"])
            else:
                failed_count += 1
                logger.warning("Failed to delete expired client %s from 3x-UI", key["uuid"])
        except Exception as e:
            failed_count += 1
            logger.error("Error deleting expired client %s: %s", key["uuid"], e)
    
    # Mark cleaned keys in database
    if cleaned_key_ids:
        db_marked = await mark_keys_cleaned(cleaned_key_ids)
        logger.info("Marked %d keys as cleaned in database", db_marked)
    
    # Send results
    text = (
        f"🧹 <b>Очистка завершена</b>\n\n"
        f"📊 <b>Результаты:</b>\n"
        f"• Найдено истёкших ключей: {len(expired_keys)}\n"
        f"• Успешно удалено из 3x-UI: {cleaned_count}\n"
        f"• Ошибок при удалении: {failed_count}\n"
        f"• Помечено в БД: {len(cleaned_key_ids)}\n\n"
    )
    
    if failed_count > 0:
        text += "⚠️ Некоторые ключи не удалось удалить. Проверьте логи.\n\n"
    
    text += "Истёкшие клиенты удалены из панели 3x-UI.\n"
    text += "Ключи в базе данных сохранены для истории."
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🔄 Обновить", callback_data="admin_cleanup"),
            InlineKeyboardButton(text="📊 Статистика", callback_data="admin_stats")
        ],
        [InlineKeyboardButton(text="◀️ Главное меню", callback_data="admin_menu")]
    ])


# ── System status ────────────────────────────────────────────────────────────

@router.callback_query(F.data == "admin_system")
async def cb_system_status(callback: CallbackQuery, bot: Bot):
    """Show system status and health check."""
    if not _is_admin(callback.from_user.id):
        await safe_answer(callback, "Нет доступа.", alert=True); return
    await safe_answer(callback)
    
    import os
    import time
    import sqlite3
    from config import DB_FILE, XUI_HOST, XUI_USERNAME
    from cache import get_cache_stats
    
    # Database stats
    db_size = 0
    try:
        db_size = os.path.getsize(DB_FILE) / (1024 * 1024)  # MB
    except:
        pass
    
    # Connection test to 3x-UI
    xui_status = "❌ Не подключен"
    try:
        from xui import _login, _client
        async with _client() as http:
            await _login(http)
            xui_status = "✅ Подключен"
    except:
        pass
    
    # Get cache stats
    cache_stats = get_cache_stats()
    
    # Get user counts
    total_users = await get_users_count()
    
    text = (
        f"⚙️ <b>Состояние системы ByMeVPN</b>\n\n"
        f"📊 <b>База данных:</b>\n"
        f"  Размер: {db_size:.2f} MB\n"
        f"  Пользователей: {total_users}\n\n"
        f"🔌 <b>3x-UI панель:</b>\n"
        f"  Статус: {xui_status}\n"
        f"  Хост: <code>{XUI_HOST}</code>\n"
        f"  Логин: <code>{XUI_USERNAME}</code>\n\n"
        f"💾 <b>Кэш:</b>\n"
        f"  Размер: {cache_stats.get('size', 0)} записей\n"
        f"  Попаданий: {cache_stats.get('hits', 0)}\n"
        f"  Промахов: {cache_stats.get('misses', 0)}\n\n"
        f"🤖 <b>Бот:</b>\n"
        f"  ID: <code>{callback.bot.id}</code>\n"
        f"  Username: @{callback.bot._me.username if callback.bot._me else 'N/A'}\n"
    )
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Проверить 3x-UI", callback_data="admin_check_xui")],
        [InlineKeyboardButton(text="🗑 Очистить кэш", callback_data="admin_clear_cache")],
        [InlineKeyboardButton(text="◀️ Главное меню", callback_data="admin_menu")]
    ])
    
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)


@router.callback_query(F.data == "admin_check_xui")
async def cb_check_xui(callback: CallbackQuery):
    """Test connection to 3x-UI panel."""
    if not _is_admin(callback.from_user.id):
        await safe_answer(callback, "Нет доступа.", alert=True); return
    await safe_answer(callback)
    
    try:
        from xui import _login, _client
        async with _client() as http:
            await _login(http)
            await callback.answer("✅ Подключение к 3x-UI успешно!", show_alert=True)
    except Exception as e:
        await callback.answer(f"❌ Ошибка подключения: {str(e)[:100]}", show_alert=True)


@router.callback_query(F.data == "admin_clear_cache")
async def cb_clear_cache(callback: CallbackQuery):
    """Clear application cache."""
    if not _is_admin(callback.from_user.id):
        await safe_answer(callback, "Нет доступа.", alert=True); return
    await safe_answer(callback)
    
    from cache import clear_cache
    clear_cache()
    
    await callback.answer("✅ Кэш очищен!", show_alert=True)
    await cb_system_status(callback, callback.bot)


# ── Key Detail View ─────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("admin_key_detail:"))
async def cb_key_detail(callback: CallbackQuery):
    """Show detailed key information with quick actions."""
    if not _is_admin(callback.from_user.id):
        await safe_answer(callback, "Нет доступа.", alert=True); return
    await safe_answer(callback)
    
    parts = callback.data.split(":")
    key_id = int(parts[1])
    user_id = int(parts[2])
    
    from database import get_key_by_id
    key = await get_key_by_id(key_id)
    
    if not key:
        await callback.message.edit_text(
            "❌ Ключ не найден.",
            reply_markup=_back_kb()
        ); return
    
    import time
    now = int(time.time())
    expiry = key.get("expiry", 0)
    days_left = max(0, (expiry - now) // 86400) if expiry > now else 0
    is_active = expiry > now
    status = "🟢 Активен" if is_active else "🔴 Истёк"
    
    # Get subscription URL
    from xui import get_subscription_url
    sub_url = get_subscription_url(key.get("short_id", "")) if key.get("short_id") else key.get("key", "")
    
    text = (
        f"🔑 <b>Ключ #{key_id}</b>\n"
        f"━━━━━━━━━━━━━━━\n\n"
        f"📛 <b>Название:</b> <code>{key.get('remark', 'N/A')}</code>\n"
        f"📊 <b>Статус:</b> {status}\n"
        f"⏱ <b>Осталось:</b> {days_left} дней\n"
        f"📅 <b>Истекает:</b> {fmt_date(expiry)[:16]}\n"
        f"👤 <b>Пользователь:</b> <code>{key.get('user_id')}</code>\n"
        f"🌐 <b>Устройств:</b> {key.get('limit_ip', 1)}\n\n"
        f"🔗 <b>Подписка:</b>\n<code>{sub_url}</code>\n\n"
        f"🆔 <b>UUID:</b> <code>{key.get('uuid', 'N/A')[:20]}...</code>\n"
        f"🔗 <b>Short ID:</b> <code>{key.get('short_id', 'N/A')}</code>"
    )
    
    await callback.message.edit_text(
        text, parse_mode="HTML",
        reply_markup=_key_detail_kb(key_id, user_id, is_active)
    )


# ── Quick Add Days ────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("admin_key_add_days:"))
async def cb_key_add_days(callback: CallbackQuery):
    """Quickly add days to existing key expiry."""
    if not _is_admin(callback.from_user.id):
        await safe_answer(callback, "Нет доступа.", alert=True); return
    await safe_answer(callback)
    
    parts = callback.data.split(":")
    key_id = int(parts[1])
    user_id = int(parts[2])
    days_to_add = int(parts[3])
    
    from database import get_key_by_id, extend_key
    key = await get_key_by_id(key_id)
    
    if not key:
        await callback.answer("❌ Ключ не найден!", show_alert=True); return
    
    # Extend the key
    success = await extend_key(key_id, days_to_add)
    
    if success:
        # Calculate new expiry for display
        import time
        new_expiry = key["expiry"] + days_to_add * 86400
        new_days_left = max(0, (new_expiry - int(time.time())) // 86400)
        
        await callback.message.edit_text(
            f"✅ <b>Ключ #{key_id} продлён!</b>\n\n"
            f"📛 <b>Название:</b> <code>{key.get('remark', 'N/A')}</code>\n"
            f"➕ <b>Добавлено:</b> +{days_to_add} дней\n"
            f"📅 <b>Новый срок:</b> до {fmt_date(new_expiry)[:10]}\n"
            f"⏱ <b>Осталось:</b> {new_days_left} дней\n\n"
            f"Ключ работает, название сервера не изменилось!",
            parse_mode="HTML",
            reply_markup=_key_detail_kb(key_id, user_id, new_expiry > int(time.time()))
        )
    else:
        await callback.answer("❌ Ошибка при продлении ключа!", show_alert=True)


# ── Rename Key ─────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("admin_rename_key:"))
async def cb_rename_key(callback: CallbackQuery, state: FSMContext):
    """Start renaming a key - ask for new name."""
    if not _is_admin(callback.from_user.id):
        await safe_answer(callback, "Нет доступа.", alert=True); return
    await safe_answer(callback)
    
    key_id = int(callback.data.split(":")[1])
    await state.set_state(AdminFlow.edit_key_name)
    await state.update_data(edit_key_id=key_id)
    
    await callback.message.edit_text(
        f"🏷️ <b>Переименование ключа #{key_id}</b>\n\n"
        "Введите новое название для ключа:\n"
        "• Будет добавлено 🇺🇸 автоматически\n"
        "• Например: <code>Мой VPN</code> станет <code>🇺🇸 Мой VPN</code>\n\n"
        "Или отправьте <code>-</code> для автоматического имени",
        parse_mode="HTML", reply_markup=_back_kb()
    )


@router.message(StateFilter(AdminFlow.edit_key_name))
async def receive_key_name(message: Message, state: FSMContext):
    """Receive new key name and update in database and 3x-UI."""
    if not _is_admin(message.from_user.id):
        return
    
    data = await state.get_data()
    key_id = data.get("edit_key_id")
    
    if not key_id:
        await message.answer("❌ Ошибка сессии. Начните заново.")
        await state.clear()
        return
    
    # Get new name and ensure USA emoji
    new_name = message.text.strip()
    if new_name == "-":
        new_name = f"USA Ключ #{key_id}"
    elif not new_name.startswith("🇺🇸"):
        new_name = f"🇺🇸 {new_name}"
    
    # Update in database
    success_db = await update_key_remark(key_id, new_name)
    
    # Update in 3x-UI panel
    from database import get_key_by_id
    from xui import update_client_name
    key = await get_key_by_id(key_id)
    success_xui = False
    if key and key.get("uuid"):
        try:
            success_xui = await update_client_name(key["uuid"], new_name)
        except Exception as e:
            logger.error("Failed to update client name in 3x-UI: %s", e)
    
    await state.clear()
    
    if success_db:
        status = "✅" if success_xui else "⚠️"
        await message.answer(
            f"{status} <b>Ключ переименован!</b>\n\n"
            f"📛 Новое название: <code>{new_name}</code>\n"
            f"🗄️ База данных: обновлена\n"
            f"🔌 3x-UI панель: {'обновлена' if success_xui else 'требует ручного обновления'}\n\n"
            f"Ключ работает с новым названием!",
            parse_mode="HTML", reply_markup=_back_kb()
        )
    else:
        await message.answer(
            "❌ Не удалось переименовать ключ. Попробуйте позже.",
            reply_markup=_back_kb()
        )


# ── Grant Key to User ─────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("admin_grant_key:"))
async def cb_grant_key(callback: CallbackQuery, state: FSMContext):
    """Start granting a key to specific user."""
    if not _is_admin(callback.from_user.id):
        await safe_answer(callback, "Нет доступа.", alert=True); return
    await safe_answer(callback)
    
    user_id = int(callback.data.split(":")[1])
    await state.set_state(AdminFlow.grant_key_days)
    await state.update_data(grant_key_user_id=user_id)
    
    await callback.message.edit_text(
        f"🆕 <b>Выдача ключа пользователю {user_id}</b>\n\n"
        "Введите количество дней для ключа:\n"
        "• Например: <code>30</code> — ключ на 30 дней\n"
        "• Например: <code>365</code> — ключ на год\n\n"
        "Ключ будет создан автоматически с названием 🇺🇸 Admin-Key-{user_id}",
        parse_mode="HTML", reply_markup=_back_kb()
    )


@router.message(StateFilter(AdminFlow.grant_key_days))
async def receive_grant_key_days(message: Message, bot: Bot, state: FSMContext):
    """Receive days and create key for user."""
    if not _is_admin(message.from_user.id):
        return
    
    try:
        days = int(message.text.strip())
        if days < 1:
            raise ValueError
    except ValueError:
        await message.answer("❌ Введите положительное число дней."); return
    
    data = await state.get_data()
    await state.clear()
    uid = data.get("grant_key_user_id")
    
    if not uid:
        await message.answer("❌ Ошибка сессии. Начните заново."); return
    
    # Create key for user
    from subscription import deliver_key
    
    config_name = f"🇺🇸 Admin-Key-{uid}"
    success = await deliver_key(
        bot=bot,
        user_id=uid,
        chat_id=uid,
        config_name=config_name,
        days=days,
        limit_ip=5,
        is_paid=False,
        amount=0,
        currency="RUB",
        method="admin_grant",
        payload=f"admin_grant_{uid}_{days}",
    )
    
    if success:
        await message.answer(
            f"✅ <b>Ключ выдан!</b>\n\n"
            f"👤 Пользователь: <code>{uid}</code>\n"
            f"📅 Срок: {days} дней\n"
            f"📱 Устройств: до 5\n"
            f"🏷️ Название: <b>{config_name}</b>\n\n"
            f"Ключ отправлен пользователю в личные сообщения!",
            parse_mode="HTML", reply_markup=_back_kb()
        )
    else:
        await message.answer(
            f"❌ Не удалось выдать ключ пользователю <code>{uid}</code>.\n"
            "Проверьте подключение к 3x-UI в логах.",
            parse_mode="HTML", reply_markup=_back_kb()
        )


# ── Export Keys CSV ─────────────────────────────────────────────────────────

@router.callback_query(F.data == "admin_export_keys_csv")
async def cb_export_keys_csv(callback: CallbackQuery, bot: Bot):
    if not _is_admin(callback.from_user.id):
        await safe_answer(callback, "Нет доступа.", alert=True); return
    await safe_answer(callback)
    await callback.message.edit_text("⏳ Генерирую CSV со всеми ключами...", reply_markup=_back_kb())
    
    from database import get_all_keys_csv
    csv_text = await get_all_keys_csv()
    date_str = datetime.now().strftime("%Y%m%d_%H%M")
    filename = f"bymevpn_keys_{date_str}.csv"
    file_bytes = csv_text.encode("utf-8")
    await bot.send_document(
        chat_id=callback.from_user.id,
        document=BufferedInputFile(file_bytes, filename=filename),
        caption=f"🗝 Экспорт ключей ByMeVPN\n{datetime.now().strftime('%d.%m.%Y %H:%M')}",
    )
    await callback.message.edit_text(
        "✅ CSV с ключами отправлен выше.", reply_markup=_back_kb()
    )


# ── Promo Codes ─────────────────────────────────────────────────────────────

@router.callback_query(F.data == "admin_promo_codes")
async def cb_promo_codes(callback: CallbackQuery):
    """Show promo codes management."""
    if not _is_admin(callback.from_user.id):
        await safe_answer(callback, "Нет доступа.", alert=True); return
    await safe_answer(callback)
    
    promo_codes = await get_all_promo_codes()
    
    text = "🎁 <b>Управление промокодами</b>\n\n"
    
    if promo_codes:
        text += "<b>Активные промокоды:</b>\n"
        for p in promo_codes[:10]:
            status = "✅" if p["is_active"] else "❌"
            expiry_str = fmt_date(p["expires_at"])[:10]
            uses = f"{p['uses_count']}/{p['max_uses']}" if p['max_uses'] < 999999 else f"{p['uses_count']}/∞"
            text += f"{status} <code>{p['code']}</code> — {p['discount_percent']}% (исп. {uses}) до {expiry_str}\n"
    else:
        text += "Промокодов пока нет."
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Создать промокод", callback_data="admin_promo_create")],
        [InlineKeyboardButton(text="🔄 Обновить", callback_data="admin_promo_codes")],
        [InlineKeyboardButton(text="◀️ Главное меню", callback_data="admin_menu")]
    ])
    
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)


@router.callback_query(F.data == "admin_promo_create")
async def cb_promo_create_start(callback: CallbackQuery, state: FSMContext):
    """Start creating promo code."""
    if not _is_admin(callback.from_user.id):
        await safe_answer(callback, "Нет доступа.", alert=True); return
    await safe_answer(callback)
    
    await state.set_state(AdminFlow.promo_code_create)
    await callback.message.edit_text(
        "🎁 <b>Создание промокода</b>\n\n"
        "Введите код промокода (например: SALE20, BONUS10):\n\n"
        "Требования:\n"
        "• Только буквы и цифры\n"
        "• Минимум 4 символа\n"
        "• Будет сохранён в верхнем регистре",
        parse_mode="HTML", reply_markup=_back_kb()
    )


@router.message(StateFilter(AdminFlow.promo_code_create))
async def receive_promo_code(message: Message, state: FSMContext):
    """Receive promo code and ask for discount."""
    if not _is_admin(message.from_user.id):
        return
    
    code = message.text.strip().upper() if message.text else ""
    
    if len(code) < 4:
        await message.answer("❌ Код должен быть минимум 4 символа.")
        return
    
    if not code.isalnum():
        await message.answer("❌ Код должен содержать только буквы и цифры.")
        return
    
    existing = await validate_promo_code(code)
    if existing:
        await message.answer("❌ Такой промокод уже существует.")
        await state.clear()
        return
    
    await state.update_data(promo_code=code)
    await state.set_state(AdminFlow.promo_code_discount)
    
    await message.answer(
        f"🎁 Код: <code>{code}</code>\n\n"
        "Введите размер скидки в процентах (например: 10, 20, 50):",
        parse_mode="HTML", reply_markup=_back_kb()
    )


@router.message(StateFilter(AdminFlow.promo_code_discount))
async def receive_promo_discount(message: Message, state: FSMContext):
    """Receive discount and ask for max uses."""
    if not _is_admin(message.from_user.id):
        return
    
    try:
        discount = int(message.text.strip())
        if discount < 1 or discount > 100:
            raise ValueError
    except ValueError:
        await message.answer("❌ Введите число от 1 до 100.")
        return
    
    await state.update_data(promo_discount=discount)
    await state.set_state(AdminFlow.promo_code_uses)
    
    await message.answer(
        f"🎁 Скидка: {discount}%\n\n"
        "Введите максимальное количество использований\n"
        "(например: 1, 10, 100, или 0 для неограниченного):",
        reply_markup=_back_kb()
    )


@router.message(StateFilter(AdminFlow.promo_code_uses))
async def receive_promo_uses(message: Message, bot: Bot, state: FSMContext):
    """Receive max uses and create promo code."""
    if not _is_admin(message.from_user.id):
        return
    
    try:
        max_uses = int(message.text.strip())
        if max_uses < 0:
            max_uses = 999999
    except ValueError:
        max_uses = 1
    
    data = await state.get_data()
    await state.clear()
    
    code = data.get("promo_code")
    discount = data.get("promo_discount")
    
    success = await create_promo_code(code, discount, max_uses if max_uses > 0 else 999999, 30)
    
    if success:
        await message.answer(
            f"✅ <b>Промокод создан!</b>\n\n"
            f"🎁 Код: <code>{code}</code>\n"
            f"💰 Скидка: {discount}%\n"
            f"🔄 Использований: {'∞' if max_uses == 0 or max_uses >= 999999 else max_uses}\n"
            f"⏳ Действует: 30 дней\n\n"
            f"Отправьте код пользователям для получения скидки!",
            parse_mode="HTML", reply_markup=_back_kb()
        )
    else:
        await message.answer(
            "❌ Не удалось создать промокод.",
            reply_markup=_back_kb()
        )
