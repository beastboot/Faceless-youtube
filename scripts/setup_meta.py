#!/usr/bin/env python3
"""One-time: connect Instagram Reels + Facebook Reels cross-posting.

Put three values in .env (from developers.facebook.com, see the steps Claude gave you):
    META_APP_ID=...          App settings > Basic
    META_APP_SECRET=...      App settings > Basic (click Show)
    META_SHORT_TOKEN=...     Graph API Explorer > Generate Access Token (user token, ~1 hour)
then run:
    python scripts/setup_meta.py

It swaps the short token for a Page token that does not expire, finds your Facebook Page and the
Instagram account linked to it, writes META_ACCESS_TOKEN / FB_PAGE_ID / IG_USER_ID into .env and
(if the gh CLI is logged in) into the GitHub repo secrets. No token is printed.
"""
import os
import re
import subprocess
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
GRAPH = "https://graph.facebook.com/v21.0"


def get(path, **params):
    r = requests.get(f"{GRAPH}/{path}", params=params, timeout=30)
    j = r.json()
    if "error" in j:
        sys.exit(f"Meta said: {j['error'].get('message')}")
    return j


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
    app, secret, short = (os.environ.get(k) for k in ("META_APP_ID", "META_APP_SECRET", "META_SHORT_TOKEN"))
    if not (app and secret and short):
        sys.exit("Add META_APP_ID, META_APP_SECRET and META_SHORT_TOKEN to .env first.")
    long_user = get("oauth/access_token", grant_type="fb_exchange_token", client_id=app,
                    client_secret=secret, fb_exchange_token=short)["access_token"]
    pages = get("me/accounts", access_token=long_user, fields="id,name,access_token,instagram_business_account").get("data", [])
    if not pages:
        sys.exit("No Facebook Page found for this login. Create a Page and give the app pages_show_list.")
    page = next((p for p in pages if "odd" in p["name"].lower()), pages[0])
    ig = (page.get("instagram_business_account") or {}).get("id")
    print(f"Facebook Page: {page['name']} ({page['id']})")
    print(f"Instagram account linked: {'yes' if ig else 'NO - link it in the Page settings, then run this again'}")
    values = {"META_ACCESS_TOKEN": page["access_token"], "FB_PAGE_ID": page["id"]}
    if ig:
        values["IG_USER_ID"] = ig
    save(env, values)
    print("Saved to .env")
    try:
        for k, v in values.items():
            subprocess.run(["gh", "secret", "set", k], input=v, text=True, check=True, cwd=ROOT,
                           capture_output=True)
        print("Saved to GitHub secrets:", ", ".join(values))
    except Exception as e:
        print("Could not set GitHub secrets automatically:", e)
    info = get("debug_token", input_token=page["access_token"], access_token=f"{app}|{secret}")["data"]
    print("Page token expires:", "never" if not info.get("expires_at") else info["expires_at"])


if __name__ == "__main__":
    main()
