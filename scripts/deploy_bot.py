#!/usr/bin/env python3
"""Deploy the Telegram bot (bot/worker.js) to Cloudflare Workers (free) and point Telegram at it.

    python scripts/deploy_bot.py

Needs in .env: CF_ACCOUNT_ID, CF_WORKERS_TOKEN (a Cloudflare token from the "Edit Cloudflare Workers"
template; CF_API_TOKEN is used if it has that permission), TELEGRAM_BOT_TOKEN,
TELEGRAM_CHAT_ID, GEMINI_API_KEY, YT_CLIENT_ID/SECRET/REFRESH_TOKEN (+ the _TA ones for Tamil).
Secrets are stored encrypted in the Worker, never in the repository. Re-run after changing worker.js.
"""
import json
import os
import secrets
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
NAME = "oddannals-telegram"
API = "https://api.cloudflare.com/client/v4"
SECRETS = ["TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "GEMINI_API_KEY",
           "YT_CLIENT_ID", "YT_CLIENT_SECRET", "YT_REFRESH_TOKEN",
           "YT_CLIENT_ID_TA", "YT_CLIENT_SECRET_TA", "YT_REFRESH_TOKEN_TA"]
# .env name -> name inside the Worker. GITHUB_CHANGE_TOKEN: fine-grained GitHub token for this repo
# only, "Actions: read and write" -> lets /change start .github/workflows/telegram-change.yml
OPTIONAL = {"GITHUB_CHANGE_TOKEN": "GH_DISPATCH_TOKEN"}
COMMANDS = [("stats", "Subscribers, views, videos"), ("latest", "Newest videos with views"),
            ("video", "One video in depth (link or number)"), ("analytics", "Last 7 days (or /analytics 28)"),
            ("monetize", "Partner Program progress"), ("report", "Today's reach report"),
            ("runs", "Recent automation runs"), ("dashboard", "Open the dashboard"),
            ("change", "Change something (you approve first)"), ("undo", "Undo the last change"),
            ("help", "What I can do")]


def cf(method, path, ok_fail=False, **kw):
    r = requests.request(method, f"{API}/accounts/{os.environ['CF_ACCOUNT_ID']}{path}",
                         headers={"Authorization": f"Bearer {os.environ.get('CF_WORKERS_TOKEN') or os.environ['CF_API_TOKEN']}"}, timeout=60, **kw)
    j = r.json()
    if not j.get("success") and ok_fail:
        return None
    if not j.get("success"):
        sys.exit(f"Cloudflare {method} {path} failed: {j.get('errors')}\n"
                 "If this is an access error: create a token from the 'Edit Cloudflare Workers' template and "
                 "add it to .env as CF_WORKERS_TOKEN.")
    return j["result"]


def main():
    load_dotenv(ROOT / ".env")
    missing = [k for k in SECRETS[:6] + ["CF_ACCOUNT_ID", "CF_API_TOKEN"] if not os.environ.get(k)]
    if missing:
        sys.exit(f"Missing in .env: {', '.join(missing)}")
    hook_secret = secrets.token_urlsafe(32)
    bindings = [{"type": "secret_text", "name": k, "text": os.environ[k]} for k in SECRETS if os.environ.get(k)]
    bindings += [{"type": "secret_text", "name": name, "text": os.environ[k]}
                 for k, name in OPTIONAL.items() if os.environ.get(k)]
    bindings.append({"type": "secret_text", "name": "WEBHOOK_SECRET", "text": hook_secret})
    meta = {"main_module": "worker.js", "compatibility_date": "2025-09-01", "bindings": bindings}
    code = (ROOT / "bot" / "worker.js").read_bytes()
    cf("PUT", f"/workers/scripts/{NAME}", files={
        "metadata": (None, json.dumps(meta), "application/json"),
        "worker.js": ("worker.js", code, "application/javascript+module")})
    print("Worker uploaded")
    # Daily video run at 03:17 UTC (08:47 IST), on time (GitHub's own schedule runs late)
    if os.environ.get("GITHUB_CHANGE_TOKEN"):
        cf("PUT", f"/workers/scripts/{NAME}/schedules", json=[{"cron": "17 3 * * *"}])
        print("Daily timer set: 03:17 UTC")
    sub = (cf("GET", "/workers/subdomain", ok_fail=True) or {}).get("subdomain")
    if not sub:   # first Worker on this account: claim its free *.workers.dev address
        for name in ("oddannals", f"oddannals-{secrets.token_hex(3)}"):
            made = cf("PUT", "/workers/subdomain", ok_fail=True, json={"subdomain": name})
            if made:
                sub = made["subdomain"]
                break
        else:
            sys.exit("Could not create a workers.dev address; open Workers & Pages once in the Cloudflare dashboard.")
    cf("POST", f"/workers/scripts/{NAME}/subdomain", json={"enabled": True, "previews_enabled": False})
    url = f"https://{NAME}.{sub}.workers.dev"
    tg = f"https://api.telegram.org/bot{os.environ['TELEGRAM_BOT_TOKEN']}"
    r = requests.post(f"{tg}/setWebhook", json={"url": url, "secret_token": hook_secret,
                                                "allowed_updates": ["message", "edited_message", "callback_query"],
                                                "drop_pending_updates": True}, timeout=30).json()
    print("Telegram webhook:", r.get("description"))
    requests.post(f"{tg}/setMyCommands", json={"commands": [{"command": c, "description": d} for c, d in COMMANDS]},
                  timeout=30)
    print(f"Bot is live at {url}. Send /help to your bot.")


if __name__ == "__main__":
    main()
