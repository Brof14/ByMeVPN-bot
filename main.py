import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

from config import BOT_TOKEN
from database import init_db, close_db
from notifications import start_notification_scheduler
from webhook import start_webhook_server
from xui import close_session
from async_utils import preload_static_data

from handlers import (
    start_router, buy_router, keys_router, partner_router,
    guide_router, legal_router, admin_router, auth_router, fallback_router
)
from subscription import router as subscription_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s:%(lineno)d | %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)

logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("aiogram").setLevel(logging.INFO)

logger = logging.getLogger(__name__)


async def main() -> None:
    if not BOT_TOKEN:
        logger.critical("BOT_TOKEN is not set in .env")
        sys.exit(1)

    # Предзагрузка статических данных для мгновенных ответов
    await preload_static_data()
    logger.info("Static data preloaded")

    await init_db()
    logger.info("Database ready")

    from xui import test_xui_connection
    xui_connected, xui_message = await test_xui_connection()
    if xui_connected:
        logger.info("3x-UI connection: %s", xui_message)
    else:
        logger.error("3x-UI connection failed: %s", xui_message)

    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher(storage=MemoryStorage())

    routers = [
        start_router, buy_router, subscription_router, keys_router,
        partner_router, guide_router, legal_router,
        admin_router, auth_router, fallback_router
    ]
    
    for router in routers:
        dp.include_router(router)

    logger.info("All routers registered")
    await bot.delete_webhook(drop_pending_updates=True)

    scheduler_task = asyncio.create_task(start_notification_scheduler(bot))
    # webhook_task = asyncio.create_task(start_webhook_server(bot))  # Disabled: YooKassa webhook not used
    logger.info("Bot is running. Press Ctrl+C to stop.")

    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        scheduler_task.cancel()
        # webhook_task.cancel()  # Disabled along with webhook server
        await bot.session.close()
        await close_db()
        await close_session()
        logger.info("Bot stopped")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nBot stopped by user")
