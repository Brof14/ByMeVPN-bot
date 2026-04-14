"""Background expiry notification scheduler."""
import asyncio
import logging
from datetime import datetime

from aiogram import Bot
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from database import get_keys_nearing_expiry

logger = logging.getLogger(__name__)


def get_day_word(days: int) -> str:
    """Return correct form of 'day' word in Russian."""
    if days % 10 == 1 and days % 100 != 11:
        return "день"
    elif 2 <= days % 10 <= 4 and (days % 100 < 10 or days % 100 >= 20):
        return "дня"
    else:
        return "дней"


async def _send_expiry_notifications(bot: Bot) -> None:
    keys = await get_keys_nearing_expiry(days_min=1, days_max=3)
    for item in keys:
        try:
            date_str = datetime.fromtimestamp(item["expiry"]).strftime("%d.%m.%Y")
            import time
            days_left = max(1, int((item["expiry"] - int(time.time())) / 86400))
            text = (
                f"⏳ <b>Ваша подписка скоро закончится</b>\n\n"
                f"📅 Дата окончания: <b>{date_str}</b>\n"
                f"🔔 Осталось: <b>{days_left} {get_day_word(days_left)}</b>\n\n"
                f"⚡ Чтобы не потерять доступ к YouTube, Telegram и другим сервисам — продлите подписку прямо сейчас!\n\n"
                f"💡 <b>Партнёрская программа:</b> Приглашайте друзей и получайте +5 дней VPN за каждого!"
            )
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔄 Продлить подписку", callback_data="buy_vpn")],
                [InlineKeyboardButton(text="🎁 Пригласить друга", callback_data="partner")]
            ])
            await bot.send_message(item["user_id"], text, parse_mode="HTML", reply_markup=kb)
        except Exception as e:
            logger.debug("Notification error for user %d: %s", item["user_id"], e)


async def start_notification_scheduler(bot: Bot) -> None:
    """Run expiry notifications once per day at ~10:00."""
    logger.info("Notification scheduler started")
    while True:
        try:
            await _send_expiry_notifications(bot)
        except Exception as e:
            logger.error("Scheduler error: %s", e)
        # Wait 24 hours using asyncio.sleep (non-blocking)
        await asyncio.sleep(86400)
