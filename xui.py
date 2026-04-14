"""
3x-UI API wrapper — direct httpx calls, SSL verification disabled.

Uses the 3x-UI REST API directly instead of py3xui to give full control
over timeouts, retries and TLS settings.
"""
import asyncio
import json
import logging
import uuid
from datetime import datetime, timedelta
from typing import Optional, List
import urllib.parse

import httpx

from config import XUI_HOST, XUI_USERNAME, XUI_PASSWORD, INBOUND_ID
from cache import cache_xui_data

logger = logging.getLogger(__name__)

_MAX_RETRIES = 2
_RETRY_DELAY = 0.5
_TIMEOUT = 8.0

# Global session for connection pooling
_session: Optional[httpx.AsyncClient] = None


def validate_xui_config() -> tuple[bool, str]:
    """
    Validate 3x-UI configuration.
    Returns (is_valid, error_message).
    """
    errors = []
    
    if not XUI_HOST:
        errors.append("XUI_HOST не указан")
    elif not (XUI_HOST.startswith("http://") or XUI_HOST.startswith("https://")):
        errors.append("XUI_HOST должен начинаться с http:// или https://")
    
    if not XUI_USERNAME:
        errors.append("XUI_USERNAME не указан")
    
    if not XUI_PASSWORD:
        errors.append("XUI_PASSWORD не указан")
    
    try:
        inbound_id = int(INBOUND_ID) if INBOUND_ID else None
        if inbound_id is None or inbound_id <= 0:
            errors.append("INBOUND_ID должен быть положительным числом")
    except (ValueError, TypeError):
        errors.append("INBOUND_ID должен быть числом")
    
    if errors:
        return False, "; ".join(errors)
    
    return True, ""


async def test_xui_connection() -> tuple[bool, str]:
    """
    Test connection to 3x-UI panel.
    Returns (is_connected, error_message).
    """
    is_valid, config_error = validate_xui_config()
    if not is_valid:
        return False, f"Ошибка конфигурации: {config_error}"
    
    try:
        async with _client() as http:
            await _login(http)
            # Test getting inbound info
            url = f"{XUI_HOST}/panel/api/inbounds/get/{INBOUND_ID}"
            resp = await http.get(url)
            if resp.status_code == 200:
                try:
                    data = resp.json()
                    if data.get("success"):
                        return True, "Соединение с 3x-UI успешно"
                    else:
                        return False, f"Ошибка API 3x-UI: {data.get('msg', 'Неизвестная ошибка')}"
                except Exception:
                    return False, "Неверный ответ от 3x-UI (не JSON)"
            else:
                return False, f"HTTP ошибка: {resp.status_code}"
    except Exception as e:
        return False, f"Ошибка подключения: {str(e)}"


async def get_session() -> httpx.AsyncClient:
    """Get or create global HTTP session for connection pooling."""
    global _session
    if _session is None or _session.is_closed:
        _session = httpx.AsyncClient(
            verify=False,
            timeout=_TIMEOUT,
            follow_redirects=True,
            limits=httpx.Limits(
                max_keepalive_connections=XUI_CONFIG['max_keepalive_connections'], 
                max_connections=XUI_CONFIG['max_connections']
            )
        )
    return _session


async def close_session():
    """Close global HTTP session."""
    global _session
    if _session and not _session.is_closed:
        await _session.aclose()
        _session = None


def _client() -> httpx.AsyncClient:
    """Create httpx client with SSL verification disabled (legacy, use get_session instead)."""
    return httpx.AsyncClient(
        verify=False,
        timeout=_TIMEOUT,
        follow_redirects=True,
    )


async def _login(client: httpx.AsyncClient) -> None:
    """Authenticate with 3x-UI panel and store session cookie."""
    url = f"{XUI_HOST}/login"
    payload = {"username": XUI_USERNAME, "password": XUI_PASSWORD}

    for content_type, body in [("json", payload), ("data", payload)]:
        try:
            resp = await client.post(url, **{content_type: body})
            if resp.status_code == 200:
                try:
                    data = resp.json()
                    if data.get("success"):
                        logger.debug("3x-UI login OK (method=%s)", content_type)
                        return
                    logger.warning("3x-UI login response: %s", data)
                except Exception:
                    if resp.cookies:
                        logger.debug("3x-UI login OK (cookie, method=%s)", content_type)
                        return
        except Exception as e:
            logger.debug("Login attempt failed (method=%s): %s", content_type, e)

    raise RuntimeError(
        f"3x-UI login failed: host={XUI_HOST} user={XUI_USERNAME}"
    )


async def _with_retry(coro_factory, retries: int = _MAX_RETRIES):
    """Call an async coroutine factory up to `retries` times."""
    last_exc = None
    for attempt in range(1, retries + 1):
        try:
            return await coro_factory()
        except Exception as e:
            last_exc = e
            if attempt < retries:
                logger.warning(
                    "Attempt %d/%d failed: %s — retrying in %.1fs",
                    attempt, retries, e, _RETRY_DELAY,
                )
                await asyncio.sleep(_RETRY_DELAY)
            else:
                logger.error("All %d attempts failed. Last error: %s", retries, e)
    raise last_exc


@cache_xui_data
async def get_clients() -> Optional[List[dict]]:
    """
    Get list of all clients from 3x-UI inbound.
    Returns list of client dicts or None on failure.
    """
    
    async def _attempt():
        http = await get_session()
        await _login(http)
        url = f"{XUI_HOST}/panel/api/inbounds/get/{INBOUND_ID}"
        resp = await http.get(url)
        logger.debug("getClients → status=%d", resp.status_code)
        resp.raise_for_status()
        try:
            data = resp.json()
            if not data.get("success"):
                raise RuntimeError(f"getClients failed: {data.get('msg', data)}")
            
            # Extract clients from the inbound settings
            inbound_obj = data.get("obj", {})
            if not inbound_obj:
                return []
            
            settings = json.loads(inbound_obj.get("settings", "{}"))
            clients = settings.get("clients", [])
            return clients
        except Exception as e:
            raise RuntimeError(f"Failed to parse clients response: {e}")
    
    try:
        return await _with_retry(_attempt)
    except Exception as e:
        logger.error("get_clients permanently failed: %s", e)
        return None


async def client_exists(email: str) -> bool:
    """
    Check if a client with the given email already exists in 3x-UI.
    Returns True if client exists, False otherwise.
    """
    clients = await get_clients()
    if clients is None:
        # If we can't get clients, assume it doesn't exist to allow creation attempt
        return False
    
    logger.info("Checking for email '%s' among %d existing clients", email, len(clients))
    
    for client in clients:
        client_email = client.get("email", "")
        logger.info("Existing client: email='%s', id='%s'", client_email, client.get("id", ""))
        
        if client_email == email:
            logger.info("Client with email '%s' already exists in 3x-UI", email)
            return True
    
    logger.info("Email '%s' not found in %d existing clients", email, len(clients))
    return False


def validate_device_limit(limit_ip: int) -> int:
    """
    Validate and normalize device limit.
    Only allows 1, 2, or 5 devices. Defaults to 1 for invalid values.
    """
    from constants import validate_device_limit as const_validate_device_limit
    return const_validate_device_limit(limit_ip)


def generate_unique_name(user_id: int, prefix: str = "") -> str:
    """
    Generate a unique client name using user_id and timestamp.
    Format: user_{user_id}_{timestamp} or {prefix}_user_{user_id}_{timestamp}
    """
    timestamp = int(datetime.now().timestamp())
    if prefix:
        return f"{prefix}_user_{user_id}_{timestamp}"
    return f"user_{user_id}_{timestamp}"


async def create_client(user_id: int, days: int, limit_ip: int = 1) -> Optional[dict]:
    """
    Create a new VLESS client in 3x-UI inbound.
    If any client exists, it will be replaced (workaround for XUI bug).
    limit_ip — max simultaneous device connections (1, 2 or 5).
    Returns dict {"uuid": ..., "short_id": ...} on success, None on failure.
    """
    import secrets
    import random
    # Server name shown in v2rayNG (must be UNIQUE in 3x-UI for 500+ users)
    # Format with emoji flag: 🇺🇸США1234 (emoji + USA in Cyrillic + digits)
    random_suffix = random.randint(1000, 9999)
    email = f"🇺🇸США{random_suffix}"
    # Comment/description for the client (shown in 3x-UI panel)
    comment = "Telegram @ByMeVPN_bot"

    logger.info("Creating client for user_id=%d with email='%s', comment='%s', days=%d, limit_ip=%d", user_id, email, comment, days, limit_ip)
    
    # Validate device limit
    limit_ip = validate_device_limit(limit_ip)

    async def _attempt():
        client_id = str(uuid.uuid4())
        # Generate short_id for subscription URL (3x-UI uses this for /sub/ endpoint)
        short_id = secrets.token_hex(8)  # 16 hex chars
        expiry_ms = int(
            (datetime.now() + timedelta(days=days)).timestamp() * 1000
        )
        client_obj = {
            "id": client_id,
            "flow": "",
            "email": email,
            "comment": comment,
            "limitIp": limit_ip,  # already validated
            "totalGB": 0,
            "expiryTime": expiry_ms,
            "enable": True,
            "tgId": str(user_id),
            "subId": short_id,
        }
        
        # WORKAROUND: Replace ALL clients instead of adding (XUI bug fix)
        payload = {
            "id": INBOUND_ID,
            "settings": json.dumps({"clients": [client_obj]}),
        }
        http = await get_session()
        await _login(http)
        url = f"{XUI_HOST}/panel/api/inbounds/addClient"
        resp = await http.post(url, json=payload)
        logger.info(
            "addClient → status=%d  body=%s",
            resp.status_code, resp.text[:300],
        )
        resp.raise_for_status()
        try:
            data = resp.json()
        except Exception:
            raise RuntimeError(f"Non-JSON response: {resp.text[:200]}")
        if not data.get("success"):
            raise RuntimeError(f"3x-UI addClient failed: {data.get('msg', data)}")
        logger.info("Client created: email=%s comment=%s uuid=%s short_id=%s days=%d limit_ip=%d", email, comment, client_id, short_id, days, limit_ip)
        # Invalidate XUI cache when client is created
        from cache import invalidate_xui_cache
        invalidate_xui_cache()
        return {"uuid": client_id, "short_id": short_id}

    try:
        return await _with_retry(_attempt)
    except Exception as e:
        logger.error(
            "create_client permanently failed for '%s': %s | "
            "XUI_HOST=%s XUI_USERNAME=%s INBOUND_ID=%d",
            email, e, XUI_HOST, XUI_USERNAME, INBOUND_ID,
        )
        return None


async def update_client_expiry(client_uuid: str, new_expiry_timestamp: int) -> bool:
    """Update client expiry date in 3x-UI. Returns True on success."""
    import json
    
    async def _attempt():
        http = await get_session()
        await _login(http)
        
        # First get current inbound settings to find the client
        url = f"{XUI_HOST}/panel/api/inbounds/get/{INBOUND_ID}"
        resp = await http.get(url)
        resp.raise_for_status()
        data = resp.json()
        
        if not data.get("success"):
            raise RuntimeError(f"Failed to get inbound: {data.get('msg')}")
        
        inbound = data.get("obj", {})
        settings = json.loads(inbound.get("settings", "{}"))
        clients = settings.get("clients", [])
        
        # Find client by UUID
        client_found = False
        for client in clients:
            if client.get("id") == client_uuid:
                # Convert timestamp to milliseconds for 3x-UI
                client["expiryTime"] = new_expiry_timestamp * 1000
                client_found = True
                break
        
        if not client_found:
            logger.warning("Client %s not found in inbound", client_uuid[:8])
            return False
        
        # Update inbound with new settings
        update_url = f"{XUI_HOST}/panel/api/inbounds/update/{INBOUND_ID}"
        payload = {
            "id": INBOUND_ID,
            "settings": json.dumps(settings)
        }
        resp = await http.post(update_url, json=payload)
        resp.raise_for_status()
        
        result = resp.json()
        if not result.get("success"):
            raise RuntimeError(f"Update failed: {result.get('msg')}")
        
        logger.info("Updated client %s expiry to %d", client_uuid[:8], new_expiry_timestamp)
        return True
    
    try:
        return await _with_retry(_attempt)
    except Exception as e:
        logger.error("update_client_expiry failed for %s: %s", client_uuid, e)
        return False


async def delete_client(client_uuid: str) -> bool:
    """Delete a client from 3x-UI by UUID. Returns True on success."""

    async def _attempt():
        http = await get_session()
        await _login(http)
        url = f"{XUI_HOST}/panel/api/inbounds/{INBOUND_ID}/delClient/{client_uuid}"
        resp = await http.post(url)
        logger.info(
            "delClient(%s) → status=%d  body=%s",
            client_uuid, resp.status_code, resp.text[:200],
        )
        resp.raise_for_status()
        try:
            data = resp.json()
            if not data.get("success"):
                raise RuntimeError(f"delClient failed: {data.get('msg', data)}")
        except Exception:
            pass  # empty body = success on some versions
        return True

    try:
        return await _with_retry(_attempt)
    except Exception as e:
        logger.error("delete_client permanently failed for %s: %s", client_uuid, e)
        return False


def build_vless_link(client_uuid: str, remark: str = "ByMeVPN_🇺🇸 США") -> str:
    """Build a VLESS connection link using config values from .env."""
    from config import (
        REALITY_HOST, REALITY_PORT,
        REALITY_SNI, REALITY_FP,
        REALITY_PBK, REALITY_SID,
    )
    
    # Log parameters for debugging (mask sensitive values)
    import logging
    logger = logging.getLogger(__name__)
    logger.info("Building VLESS link: uuid=%s, host=%s, port=%s, sni=%s", 
                client_uuid[:8] + "...", REALITY_HOST, REALITY_PORT, REALITY_SNI)
    
    params = {
        "type": "tcp",
        "security": "reality",
        "pbk": REALITY_PBK,
        "fp": REALITY_FP,
        "sni": REALITY_SNI,
        "sid": REALITY_SID,
        "flow": "",
        "encryption": "none",
    }
    qs = "&".join(
        f"{k}={urllib.parse.quote(str(v), safe='')}"
        for k, v in params.items()
        if v
    )
    # Fixed format: ByMeVPN_🇺🇸 США
    tag = "ByMeVPN_🇺🇸 США"
    return f"vless://{client_uuid}@{REALITY_HOST}:{REALITY_PORT}?{qs}#{tag}"


def get_subscription_url(short_id: str) -> str:
    """Build subscription URL for 3x-UI panel.

    Returns URL like: https://host:2096/sub/{short_id}
    Uses short_id (subId) instead of UUID — 3x-UI subscription endpoint
    expects short_id, not UUID (using UUID causes "Bad Request").
    """
    from config import XUI_HOST
    
    logger.info(f"Building subscription URL, XUI_HOST={XUI_HOST}, short_id={short_id[:8]}...")
    
    if not XUI_HOST:
        logger.error("XUI_HOST is empty! Cannot build subscription URL.")
        return ""
    
    # Extract host from XUI_HOST (remove https:// or http:// prefix)
    host = XUI_HOST.replace("https://", "").replace("http://", "").split(":")[0]
    
    if not host:
        logger.error(f"Could not extract host from XUI_HOST={XUI_HOST}")
        return ""
    
    # Default subscription port (should match 3x-UI subscription settings)
    sub_port = 2096
    
    url = f"https://{host}:{sub_port}/sub/{short_id}"
    logger.info(f"Subscription URL built: {url[:50]}...")
    return url


async def update_client_name(uuid: str, new_name: str) -> bool:
    """Update client name (remark) in 3x-UI panel.
    
    Args:
        uuid: Client UUID
        new_name: New name/remark for the client
        
    Returns:
        True if successful, False otherwise
    """
    try:
        async with _client() as http:
            await _login(http)
            
            # Get client info first
            params = {"filter": uuid}
            r = await http.get(f"{XUI_HOST}/panel/api/inbounds/list", params=params)
            r.raise_for_status()
            data = r.json()
            
            if not data.get("success") or not data.get("obj"):
                logger.error(f"Client not found for UUID: {uuid[:8]}...")
                return False
            
            # Get inbound ID and current settings
            inbound = data["obj"][0]
            inbound_id = inbound["id"]
            settings = inbound.get("settings", "{}")
            
            # Parse settings and update client name
            import json
            settings_json = json.loads(settings)
            clients = settings_json.get("clients", [])
            
            for client in clients:
                if client.get("id") == uuid:
                    client["remark"] = new_name
                    break
            else:
                logger.error(f"Client with UUID {uuid[:8]}... not found in inbound")
                return False
            
            # Update settings back
            new_settings = json.dumps(settings_json)
            
            # Update inbound via API
            update_data = {
                "up": inbound.get("up", 0),
                "down": inbound.get("down", 0),
                "total": inbound.get("total", 0),
                "remark": inbound.get("remark", ""),
                "enable": inbound.get("enable", True),
                "expiryTime": inbound.get("expiryTime", 0),
                "listen": inbound.get("listen", ""),
                "port": inbound.get("port", 0),
                "protocol": inbound.get("protocol", ""),
                "settings": new_settings,
                "streamSettings": inbound.get("streamSettings", "{}"),
                "sniffing": inbound.get("sniffing", "{}"),
            }
            
            r = await http.post(
                f"{XUI_HOST}/panel/api/inbounds/update/{inbound_id}",
                json=update_data
            )
            r.raise_for_status()
            result = r.json()
            
            if result.get("success"):
                logger.info(f"Updated client name to: {new_name}")
                return True
            else:
                logger.error(f"Failed to update client: {result}")
                return False
                
    except Exception as e:
        logger.error(f"Error updating client name: {e}")
        return False
