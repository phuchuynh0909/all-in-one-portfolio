"""Telegram notification helper for price alerts."""
import asyncio
import aiohttp
from dataclasses import dataclass
from typing import Optional
from config import config


@dataclass
class AlertNotification:
    """Alert notification data."""
    symbol: str
    condition: str
    target_price: float
    current_price: float
    notes: Optional[str] = None


def get_condition_symbol(condition: str) -> str:
    """Convert condition code to readable symbol."""
    symbols = {
        "gt": ">",
        "gte": "≥",
        "lt": "<",
        "lte": "≤",
        "eq": "=",
    }
    return symbols.get(condition, condition)


def get_condition_description(condition: str) -> str:
    """Convert condition code to description."""
    descriptions = {
        "gt": "exceeded",
        "gte": "reached or exceeded",
        "lt": "dropped below",
        "lte": "reached or dropped below",
        "eq": "reached exactly",
    }
    return descriptions.get(condition, condition)


def format_price(price: float) -> str:
    """Format price with thousand separators."""
    return f"{price:,.2f}"


def format_alert_message(alert: AlertNotification) -> str:
    """Format alert notification as Telegram message."""
    condition_desc = get_condition_description(alert.condition)
    condition_symbol = get_condition_symbol(alert.condition)
    
    # Calculate percentage difference
    if alert.target_price > 0:
        pct_diff = ((alert.current_price - alert.target_price) / alert.target_price) * 100
        pct_str = f"+{pct_diff:.2f}%" if pct_diff >= 0 else f"{pct_diff:.2f}%"
    else:
        pct_str = "N/A"
    
    # Determine emoji based on condition
    if alert.condition in ("gt", "gte"):
        emoji = "📈" if alert.current_price >= alert.target_price else "⏳"
    elif alert.condition in ("lt", "lte"):
        emoji = "📉" if alert.current_price <= alert.target_price else "⏳"
    else:
        emoji = "🎯"
    
    message = f"""
{emoji} *PRICE ALERT TRIGGERED*

*Symbol:* `{alert.symbol}`
*Condition:* Price {condition_symbol} {format_price(alert.target_price)}

*Current Price:* {format_price(alert.current_price)}
*Target Price:* {format_price(alert.target_price)}
*Difference:* {pct_str}

_{get_condition_description(alert.condition).capitalize()} target price!_
"""
    
    if alert.notes:
        message += f"\n📝 *Notes:* {alert.notes}"
    
    return message.strip()


async def send_telegram_message(message: str) -> bool:
    """Send message via Telegram bot API."""
    if not config.telegram.enabled:
        print(f"[Telegram] Disabled. Message: {message[:100]}...")
        return False
    
    if not config.telegram.bot_token or not config.telegram.chat_id:
        print("[Telegram] Missing bot_token or chat_id")
        return False
    
    url = f"https://api.telegram.org/bot{config.telegram.bot_token}/sendMessage"
    payload = {
        "chat_id": config.telegram.chat_id,
        "text": message,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True,
    }
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, timeout=10) as response:
                if response.status == 200:
                    print(f"[Telegram] Message sent successfully")
                    return True
                else:
                    error_text = await response.text()
                    print(f"[Telegram] Failed to send: {response.status} - {error_text}")
                    return False
    except asyncio.TimeoutError:
        print("[Telegram] Request timed out")
        return False
    except Exception as e:
        print(f"[Telegram] Error sending message: {e}")
        return False


async def send_alert_notification(alert: AlertNotification) -> bool:
    """Send price alert notification via Telegram."""
    message = format_alert_message(alert)
    return await send_telegram_message(message)


def send_alert_sync(alert: AlertNotification) -> bool:
    """Synchronous wrapper for sending alert notification."""
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    
    return loop.run_until_complete(send_alert_notification(alert))

