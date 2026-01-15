"""Quick test script for Telegram notifications."""
import asyncio
from telegram_notifier import AlertNotification, send_alert_notification

async def test():
    alert = AlertNotification(
        symbol="VNM",
        condition="gte",
        target_price=75.0,
        current_price=76.5,
        notes="Test alert - please ignore"
    )
    
    success = await send_alert_notification(alert)
    print(f"Notification sent: {success}")

if __name__ == "__main__":
    asyncio.run(test())
