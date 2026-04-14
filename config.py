import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
SUPPORT_USERNAME = os.getenv("SUPPORT_USERNAME", "@ByMeVPN_support_bot")
MENU_PHOTO = os.getenv("MENU_PHOTO", "")
DB_FILE = os.getenv("DB_FILE", "vpnbot.db")

ADMIN_IDS = [
    int(x.strip())
    for x in os.getenv("ADMIN_IDS", os.getenv("ADMIN_ID", "0")).split(",")
    if x.strip()
]
ADMIN_ID = ADMIN_IDS[0] if ADMIN_IDS else 0

XUI_HOST = os.getenv("XUI_HOST", "")
XUI_USERNAME = os.getenv("XUI_USERNAME", "")
XUI_PASSWORD = os.getenv("XUI_PASSWORD", "")
INBOUND_ID = int(os.getenv("INBOUND_ID", "5"))

REALITY_HOST = os.getenv("REALITY_HOST", "")
REALITY_PORT = int(os.getenv("REALITY_PORT", "443"))
REALITY_SNI = os.getenv("REALITY_SNI", "www.microsoft.com")
REALITY_FP = os.getenv("REALITY_FP", "chrome")
REALITY_PBK = os.getenv("REALITY_PBK", "")
REALITY_SID = os.getenv("REALITY_SID", "")

YOOKASSA_SHOP_ID = os.getenv("YOOKASSA_SHOP_ID", "")
YOOKASSA_SECRET_KEY = os.getenv("YOOKASSA_SECRET_KEY", "")

# SMTP settings for email authentication
SMTP_HOST = os.getenv("SMTP_HOST", "")
SMTP_PORT = int(os.getenv("SMTP_PORT", "465"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")

WEBHOOK_HOST = os.getenv("WEBHOOK_HOST") or "0.0.0.0"
WEBHOOK_PORT = int(os.getenv("WEBHOOK_PORT") or "8080")

TRIAL_DAYS = 3
TRIAL_PRICE = 0
REF_BONUS_DAYS = int(os.getenv("REF_BONUS_DAYS", "3"))

PRICE_1_MONTH = 149
PRICE_3_MONTHS = 436
PRICE_6_MONTHS = 632
PRICE_12_MONTHS = 885

DAYS_1M = 30
DAYS_3M = 120
DAYS_6M = 240
DAYS_12M = 450

import logging as _logging
_logging.getLogger(__name__).info(
    "3x-UI config → host=%s user=%s inbound_id=%d",
    XUI_HOST, XUI_USERNAME, INBOUND_ID
)
