#!/usr/bin/env python3
"""One-time: authorize this app to upload to your YouTube channel and print the refresh token.

    python scripts/get_youtube_token.py path/to/client_secret.json            # main (Hindi) channel
    python scripts/get_youtube_token.py path/to/client_secret.json TA         # regional channel -> YT_*_TA

Prints a link to open in your browser. Sign in with the Google account that OWNS the channel (pick the channel if asked).
It saves the login to .env and the GitHub secrets, records the sign-in date, and redeploys the
Telegram bot (gh CLI needed).
"""
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/youtube.upload",
          "https://www.googleapis.com/auth/youtube.force-ssl",
          "https://www.googleapis.com/auth/yt-analytics.readonly"]

if len(sys.argv) not in (2, 3):
    sys.exit(__doc__)
secret_file = sys.argv[1]
sfx = f"_{sys.argv[2].upper()}" if len(sys.argv) == 3 else ""
flow = InstalledAppFlow.from_client_secrets_file(secret_file, SCOPES)
# Same link for every attempt (fixed state + one PKCE verifier on this flow), and keep
# waiting if a stale tab or a denied consent hits the local server first.
creds = None
for attempt in range(40):
    try:
        creds = flow.run_local_server(
            port=int(os.environ.get("SIGNIN_PORT", 8765)), open_browser=False, access_type="offline", prompt="consent",
            state="odd-annals-signin",
            authorization_prompt_message="Open this link in your browser:\n\n{url}\n")
        break
    except OSError:  # port still held by a previous session
        time.sleep(3)
    except Exception as e:  # MismatchingStateError, AccessDeniedError, ...
        print(f"\nSign-in attempt failed ({type(e).__name__}). Still waiting — "
              f"use the same link above, and tick every permission box.\n", flush=True)
if creds is None:
    sys.exit("Gave up after 40 failed attempts.")
client = json.load(open(secret_file))
client = client.get("installed") or client.get("web")
values = {f"YT_CLIENT_ID{sfx}": client["client_id"], f"YT_CLIENT_SECRET{sfx}": client["client_secret"],
          f"YT_REFRESH_TOKEN{sfx}": creds.refresh_token}
# Save to .env, the GitHub secrets, and the sign-in date (the daily run warns a day before it expires),
# then redeploy the Telegram bot (it keeps its own copy). Nothing secret is printed.
root = Path(__file__).resolve().parent.parent
env = root / ".env"
text = env.read_text() if env.exists() else ""
for k, v in values.items():
    text = re.sub(rf"(?m)^{k}=.*$", f"{k}={v}", text) if re.search(rf"(?m)^{k}=", text) else text + f"\n{k}={v}"
env.write_text(text.strip() + "\n")
print("Saved to .env")
try:
    for k, v in values.items():
        subprocess.run(["gh", "secret", "set", k], input=v, text=True, check=True, cwd=root, capture_output=True)
    signed = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    subprocess.run(["gh", "variable", "set", f"YT_TOKEN_DATE{sfx}", "--body", signed],
                   check=True, cwd=root, capture_output=True)
    print("Saved to GitHub secrets (+ sign-in date)")
    subprocess.run([sys.executable, str(root / "scripts" / "deploy_bot.py")], check=True, cwd=root,
                   capture_output=True)
    print("Telegram bot updated")
except (OSError, subprocess.CalledProcessError) as e:
    print(f"Could not finish GitHub/bot update ({e}); add {', '.join(values)} to the GitHub secrets "
          f"and run scripts/deploy_bot.py")
