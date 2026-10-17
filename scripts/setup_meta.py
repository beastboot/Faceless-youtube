#!/usr/bin/env python3
"""One-time: connect Instagram Reels cross-posting (Instagram Login - no Facebook Page needed).

1. developers.facebook.com > Create app > use case "Manage messaging & content on Instagram"
2. Instagram > "API setup with Instagram business login" > Add account (log in to the Instagram) >
   Generate token (a 60-day token; the daily run keeps extending it)
3. Put it in .env as   IG_ACCESS_TOKEN=...   and run:
       python scripts/setup_meta.py

It checks the token, finds the Instagram account id, writes IG_USER_ID into .env and both values into
the GitHub repo secrets (gh CLI). No token is printed.
"""
import os
import re
import subprocess
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent


def save(env, values):
    text = env.read_text() if env.exists() else ""
    for k, v in values.items():
        if re.search(rf"(?m)^{k}=", text):
            text = re.sub(rf"(?m)^{k}=.*$", f"{k}={v}", text)
        else:
            text += f"\n{k}={v}"
    env.write_text(text.strip() + "\n")


def main():
    env = ROOT / ".env"
    load_dotenv(env)
    token = (os.environ.get("IG_ACCESS_TOKEN") or "").strip()
    if not token:
        sys.exit("Add IG_ACCESS_TOKEN=... to .env first (see the steps at the top of this file).")
    me = requests.get("https://graph.instagram.com/v21.0/me",
                      params={"fields": "user_id,username,account_type", "access_token": token}, timeout=30).json()
    if "error" in me:
        sys.exit(f"Instagram said: {me['error'].get('message')}")
    print(f"Instagram account: @{me.get('username')} ({me.get('account_type')})")
    if me.get("account_type") not in ("BUSINESS", "MEDIA_CREATOR", "CREATOR"):
        print("Note: posting needs a Professional (Creator or Business) account.")
    values = {"IG_ACCESS_TOKEN": token, "IG_USER_ID": str(me["user_id"])}
    save(env, values)
    print("Saved to .env")
    for k, v in values.items():
        subprocess.run(["gh", "secret", "set", k], input=v, text=True, check=True, cwd=ROOT, capture_output=True)
    print("Saved to GitHub secrets:", ", ".join(values))


if __name__ == "__main__":
    main()
