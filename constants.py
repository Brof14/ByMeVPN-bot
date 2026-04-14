"""
Константы и общие настройки для ByMeVPN бота.
"""
from datetime import datetime

# Временные константы
TRIAL_DAYS = 3
SECONDS_PER_DAY = 86400
CAPTION_LIMIT = 1024

# URL и ссылки
LOGO_URL = "https://i.ibb.co/rG9F5PCS/logo.jpg"
SUPPORT_URL_TEMPLATE = "https://t.me/ByMeVPN_support_bot?text={}"

# Цены и сроки (в днях)
PRICE_CONFIG = {
    1:  (149, 30),   # 1 месяц - 149 руб, 30 дней
    3:  (436, 120),  # 3 месяца + 1 месяц подарок - 436 руб, 120 дней
    6:  (632, 240),  # 6 месяцев + 2 месяца подарок - 632 руб, 240 дней
    12: (885, 450),  # 1 год + 3 месяца подарок - 885 руб, 450 дней
}

# Метки для периодов
PERIOD_LABELS = {
    1:  "1 мес.",
    3:  "3 мес.",
    6:  "6 мес.",
    12: "1 год",
}

# Допустимые лимиты устройств
VALID_DEVICE_LIMITS = (1, 2, 5)

def get_price_for_months(months: int) -> tuple[int, int]:
    """Получить цену и количество дней для указанного количества месяцев."""
    return PRICE_CONFIG.get(months, (149, 30))

def get_period_label(months: int) -> str:
    """Получить метку периода для указанного количества месяцев."""
    return PERIOD_LABELS.get(months, f"{months} мес.")

def validate_device_limit(limit: int) -> int:
    """Валидировать и нормализовать лимит устройств."""
    return limit if limit in VALID_DEVICE_LIMITS else 1

def format_timestamp(ts: int) -> str:
    """Отформатировать timestamp в дату."""
    try:
        return datetime.fromtimestamp(ts).strftime("%d.%m.%Y")
    except Exception:
        return "—"

def format_days_left(expiry: int) -> str:
    """Отформатировать оставшиеся дни."""
    import time
    left = expiry - int(time.time())
    if left <= 0:
        return "истёк"
    d = left // SECONDS_PER_DAY
    if d > 0:
        return f"{d} дн."
    h = left // 3600
    return f"{h} ч."
