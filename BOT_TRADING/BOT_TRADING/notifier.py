import os

from utils import log


def telegram_enabled():
    return bool(os.getenv("TELEGRAM_BOT_TOKEN") and os.getenv("TELEGRAM_CHAT_ID"))


def notify(message):
    if not telegram_enabled():
        return False

    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    url = f"https://api.telegram.org/bot{token}/sendMessage"

    try:
        import requests

        response = requests.post(
            url,
            json={
                "chat_id": chat_id,
                "text": message,
                "disable_web_page_preview": True,
            },
            timeout=10,
        )
        if response.ok:
            return True

        log(f"TELEGRAM FAILED: {response.status_code} {response.text}")
    except Exception as exc:
        log(f"TELEGRAM ERROR: {type(exc).__name__}: {exc}")

    return False
