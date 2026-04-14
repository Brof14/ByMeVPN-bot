"""
YooKassa webhook server — runs alongside the Telegram bot as an asyncio task.

Security model:
  1. Receive POST /yookassa/webhook
  2. NEVER trust the incoming body alone — always re-fetch the payment from
     YooKassa API using the payment_id from the body (prevents spoofing).
  3. Check payment status == "succeeded" on the verified response.
  4. Idempotency: mark payment_id as processed in DB before delivering key,
     so a duplicate webhook never gives a second key.
  5. Validate devices value from metadata — only 1, 2, 5 are legal.

Requires: fastapi, uvicorn[standard]  (added to requirements.txt)
Config:   WEBHOOK_HOST, WEBHOOK_PORT in .env
"""
import asyncio
import base64
import logging
from typing import Optional

import httpx
import uvicorn
from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from fastapi import FastAPI, Request, Response

from config import (
    YOOKASSA_SHOP_ID, YOOKASSA_SECRET_KEY,
    WEBHOOK_HOST, WEBHOOK_PORT, ADMIN_ID,
)
from database import init_db, add_payment, is_yookassa_processed, mark_yookassa_processed, add_referral_earning, get_referrer
from subscription import deliver_key

logger = logging.getLogger(__name__)

app = FastAPI(docs_url=None, redoc_url=None)  # disable docs in production

# ---------------------------------------------------------------------------
# YooKassa API helper — verify payment by fetching it directly
# ---------------------------------------------------------------------------

async def _fetch_yookassa_payment(payment_id: str) -> Optional[dict]:
    """
    Fetch payment details from YooKassa API.
    Returns the payment dict on success, None on error.
    CRITICAL: always call this to verify — never trust the webhook body alone.
    """
    if not YOOKASSA_SHOP_ID or not YOOKASSA_SECRET_KEY:
        logger.error("YooKassa credentials not configured")
        return None

    auth = base64.b64encode(
        f"{YOOKASSA_SHOP_ID}:{YOOKASSA_SECRET_KEY}".encode()
    ).decode()
    headers = {"Authorization": f"Basic {auth}"}

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                f"https://api.yookassa.ru/v3/payments/{payment_id}",
                headers=headers,
            )
            resp.raise_for_status()
            return resp.json()
    except Exception as e:
        logger.error("Failed to fetch YooKassa payment %s: %s", payment_id, e)
        return None


# ---------------------------------------------------------------------------
# Webhook endpoint
# ---------------------------------------------------------------------------

@app.post("/yookassa/webhook")
async def yookassa_webhook(request: Request) -> Response:
    """
    Receive YooKassa payment notification.
    Always responds 200 quickly — heavy work runs as background task.
    """
    try:
        body = await request.json()
    except Exception:
        logger.warning("Webhook: invalid JSON body")
        return Response(status_code=400)

    event = body.get("event", "")
    obj = body.get("object", {})
    payment_id = obj.get("id", "")

    # We only care about succeeded payments
    if event != "payment.succeeded" or not payment_id:
        return Response(status_code=200)

    # Schedule async processing — respond immediately to YooKassa
    bot: Bot = app.state.bot
    asyncio.create_task(_process_payment(bot, payment_id))

    return Response(status_code=200)


async def _process_payment(bot: Bot, payment_id: str) -> None:
    """
    Verify and process a succeeded YooKassa payment.
    Fully idempotent — safe to call multiple times for the same payment_id.
    """
    try:
        # ── Step 1: Re-fetch from YooKassa to verify (never trust webhook body) ──
        payment = await _fetch_yookassa_payment(payment_id)
        if not payment:
            logger.error("Webhook: could not verify payment %s", payment_id)
            return

        if payment.get("status") != "succeeded":
            logger.info("Webhook: payment %s status=%s, skipping", payment_id, payment.get("status"))
            return

        # ── Step 2: Idempotency check ──
        already_processed = await is_yookassa_processed(payment_id)
        if already_processed:
            logger.info("Webhook: payment %s already processed, skipping", payment_id)
            return

        # Mark as processed BEFORE delivering to prevent race on duplicate webhooks
        marked = await mark_yookassa_payment_processed(payment_id)
        if not marked:
            # Another concurrent task beat us to it
            logger.info("Webhook: payment %s lost idempotency race, skipping", payment_id)
            return

        # ── Step 3: Extract metadata ──
        metadata = payment.get("metadata", {})
        logger.info("Webhook: payment_id=%s metadata=%s", payment_id, metadata)
        try:
            user_id = int(metadata["user_id"])
            days = int(metadata["days"])
            devices = int(metadata.get("devices", 1))
        except (KeyError, ValueError, TypeError) as e:
            logger.error("Webhook: bad metadata in payment %s: %s — %s", payment_id, metadata, e)
            await _notify_admin(bot, f"⚠️ YooKassa payment {payment_id}: bad metadata {metadata}")
            return

        # Validate devices — only 1, 2, 5 allowed; anything else → 1
        if devices not in (1, 2, 5):
            logger.warning("Webhook: invalid devices=%d in payment %s, defaulting to 1", devices, payment_id)
            devices = 1

        amount_str = payment.get("amount", {}).get("value", "0")
        try:
            amount_rub = int(float(amount_str))
        except ValueError:
            amount_rub = 0

        logger.info(
            "Webhook: processing payment %s — user=%d days=%d devices=%d amount=%d",
            payment_id, user_id, days, devices, amount_rub,
        )

        # ── Step 4: Store pending delivery for config name input ──
        import aiosqlite
        from database import DB_FILE
        
        async with aiosqlite.connect(DB_FILE) as db:
            await db.execute(
                """INSERT OR REPLACE INTO yookassa_pending 
                   (payment_id, user_id, days, devices, amount_rub) 
                   VALUES (?, ?, ?, ?, ?)""",
                (payment_id, user_id, days, devices, amount_rub)
            )
            await db.commit()
        
        logger.info("YooKassa payment stored as pending: user=%d days=%d devices=%d", 
                   user_id, days, devices)
        
        # Notify user to provide config name
        try:
            device_label = "до 5 устройств" if days > 3 else "1 устройство"  # Trial (3 days) = 1 device, paid plans = 5 devices
            text = (
                "💰 <b>Оплата успешно получена!</b>\n\n"
                f"📋 Срок: <b>{days} дней</b>\n"
                f"📱 Устройств: <b>{device_label}</b>\n"
                f"💰 Сумма: <b>{amount_rub} ₽</b>\n\n"
                "📝 <b>Теперь введите имя конфига</b>\n"
                "Это имя будет видно в вашем VPN приложении.\n\n"
                "Например: MyVPN, Work, Phone и т.д."
            )
            
            await bot.send_message(
                chat_id=user_id,
                text=text,
                parse_mode="HTML"
            )
            
            # Start config name input flow
            from subscription import ask_config_name
            from states import BuyFlow
            
            # Automatically deliver key without asking for config name
            success = await deliver_key(
                bot=bot,
                user_id=user_id,
                chat_id=user_id,
                config_name=f"YK-{user_id}",
                days=days,
                limit_ip=5 if days > 3 else 1,
                is_paid=True,
                amount=amount_rub,
                currency="RUB",
                method="yookassa",
                payload=payment_id,
            )
            
            if not success:
                logger.error("Failed to deliver key after YooKassa payment for user %d", user_id)
                await bot.send_message(
                    chat_id=user_id,
                    text="⚠️ Произошла ошибка при создании ключа. Пожалуйста, напишите в поддержку @ByMeVPN_support_bot с кодом: " + payment_id[:8]
                )
            
        except Exception as e:
            logger.error("Failed to notify user about pending key delivery: %s", e)
        
        await add_payment(
            user_id, amount_rub, "RUB", "yookassa", days, payment_id,
            status="success", tariff=f"{days} дней", devices=devices
        )
        
        # Log payment completion
        log_payment_completed(user_id, amount_rub, "yookassa", days)
        
        # Начисляем бонус рефереалу за первую оплату (80₽)
        try:
            referrer_id = await get_referrer(user_id)
            if referrer_id:
                bonus_added = await add_referral_earning(referrer_id, user_id, 80, payment_id)
                if bonus_added:
                    logger.info("Referral bonus 80₽ added for referrer %d from user %d YooKassa payment", referrer_id, user_id)
                    # Отправляем уведомление рефереалу
                    try:
                        await bot.send_message(
                            referrer_id,
                            f"🎉 <b>Получен бонус!</b>\n\n"
                            f"Ваш друг (ID: {user_id}) оформил подписку через ЮKassa.\n"
                            f"Начислено: +80 ₽\n"
                            f"Текущий баланс обновлён в партнёрской программе."
                        )
                    except Exception as notify_error:
                        logger.error("Failed to notify referrer %d: %s", referrer_id, notify_error)
        except Exception as e:
            logger.error("Error processing referral bonus for YooKassa user %d: %s", user_id, e)

    except Exception as e:
        logger.exception("Webhook: unexpected error processing payment %s: %s", payment_id, e)
        log_error("webhook_process_payment", e, {"payment_id": payment_id})
        try:
            await _notify_admin(bot, f"Webhook error for payment {payment_id}: {str(e)[:300]}")
        except Exception:
            pass


async def _notify_admin(bot: Bot, text: str) -> None:
    try:
        await bot.send_message(ADMIN_ID, text, parse_mode="HTML")
    except Exception as e:
        logger.error("Could not notify admin: %s", e)


# ---------------------------------------------------------------------------
# Server lifecycle
# ---------------------------------------------------------------------------

async def start_webhook_server(bot: Bot) -> None:
    """Start the uvicorn server as an asyncio task."""
    app.state.bot = bot
    config = uvicorn.Config(
        app,
        host=WEBHOOK_HOST,
        port=WEBHOOK_PORT,
        log_level="warning",
        access_log=False,
    )
    server = uvicorn.Server(config)
    logger.info("YooKassa webhook server starting on %s:%d", WEBHOOK_HOST, WEBHOOK_PORT)
    await server.serve()
