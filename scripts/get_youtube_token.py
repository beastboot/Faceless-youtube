#!/usr/bin/env python3
"""One-time: authorize this app to upload to your YouTube channel and print the refresh token.

    python scripts/get_youtube_token.py path/to/client_secret.json

Prints a link to open in your browser. Sign in with the Google account that OWNS the channel (pick the channel if asked).
Put the three printed values into .env (local) and GitHub repo secrets.
"""
import json
import sys
import time

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/youtube.upload",
          "https://www.googleapis.com/auth/youtube.force-ssl"]

if len(sys.argv) != 2:
    sys.exit(__doc__)
secret_file = sys.argv[1]
flow = InstalledAppFlow.from_client_secrets_file(secret_file, SCOPES)
# Same link for every attempt (fixed state + one PKCE verifier on this flow), and keep
# waiting if a stale tab or a denied consent hits the local server first.
creds = None
for attempt in range(40):
    try:
        creds = flow.run_local_server(
            port=8765, open_browser=False, access_type="offline", prompt="consent",
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
print("\nAdd these to .env and to GitHub → Settings → Secrets and variables → Actions:\n")
print(f"YT_CLIENT_ID={client['client_id']}")
print(f"YT_CLIENT_SECRET={client['client_secret']}")
print(f"YT_REFRESH_TOKEN={creds.refresh_token}")
