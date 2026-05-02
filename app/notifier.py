import logging, httpx
from app.config import config

log = logging.getLogger("notify")

async def send_telegram(message: str):
    if not config.notify.enable_telegram:
        return
    token = config.notify.telegram_bot_token
    chat_id = config.notify.telegram_chat_id
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(url, json={
                "chat_id": chat_id, "text": message, "parse_mode": "HTML"
            })
            if resp.status_code != 200:
                log.error("Telegram failed: %s", resp.text)
    except Exception as e:
        log.error("Telegram error: %s", e)
