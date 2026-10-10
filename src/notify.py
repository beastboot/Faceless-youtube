"""Telegram run reports: links, publish times, failures and thumbnail options on your phone.
Inactive until TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID exist (README: Telegram)."""
import os

from .common import http, log

API = "https://api.telegram.org/bot{token}/{method}"


def _cfg():
    return os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")


def send(text):
    token, chat = _cfg()
    if not (token and chat):
        return
    try:
        http().post(API.format(token=token, method="sendMessage"), timeout=30,
                    data={"chat_id": chat, "text": text[:4000], "disable_web_page_preview": "true"})
    except Exception as e:
        log.warning("Telegram message failed: %s", e)


def send_photos(paths, caption=""):
    """Up to 10 images as one album (e.g. the 3 thumbnail options for YouTube's Test & Compare)."""
    token, chat = _cfg()
    paths = [p for p in paths if p and os.path.exists(p)][:10]
    if not (token and chat and paths):
        return
    import json
    media, files = [], {}
    for k, p in enumerate(paths):
        files[f"p{k}"] = open(p, "rb")
        media.append({"type": "photo", "media": f"attach://p{k}", **({"caption": caption[:1000]} if k == 0 and caption else {})})
    try:
        http().post(API.format(token=token, method="sendMediaGroup"), timeout=60,
                    data={"chat_id": chat, "media": json.dumps(media)}, files=files)
    except Exception as e:
        log.warning("Telegram photos failed: %s", e)
    finally:
        for f in files.values():
            f.close()
