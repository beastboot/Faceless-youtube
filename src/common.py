"""Shared helpers: config, logging, HTTP session, ffmpeg wrappers."""
import json
import logging
import os
import re
import subprocess
import threading
import time
from pathlib import Path

import requests
import yaml
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

ROOT = Path(__file__).resolve().parent.parent
log = logging.getLogger("faceless")

# Wikimedia throttles requests without a bot name + contact (meta.wikimedia.org/wiki/User-Agent_policy).
USER_AGENT = (f"FacelessHistoryVideoBot/1.0 "
              f"({os.environ.get('BOT_CONTACT') or 'https://github.com/faceless-history-bot'}) "
              f"python-requests/{requests.__version__}")


def setup_logging(verbose=False):
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )


def load_config(path=None):
    with open(path or ROOT / "config.yaml") as f:
        return yaml.safe_load(f)


def http():
    s = requests.Session()
    s.headers["User-Agent"] = USER_AGENT
    return s


def slugify(text, max_len=50):
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s[:max_len].rstrip("-") or "video"


def download(url, dest, session=None, timeout=60):
    dest = Path(dest)
    if dest.exists() and dest.stat().st_size > 0:
        return dest
    s = session or http()
    for attempt in range(4):
        with s.get(url, stream=True, timeout=timeout) as r:
            if r.status_code in (429, 503) and attempt < 3:
                wait = int(r.headers.get("Retry-After", "") or 5 * (attempt + 1))
                log.debug("%s for %s, retrying in %ss", r.status_code, url, wait)
                time.sleep(min(wait, 30))
                continue
            r.raise_for_status()
            tmp = dest.with_name(f"{dest.name}.{os.getpid()}-{threading.get_ident()}.part")
            with open(tmp, "wb") as f:
                for chunk in r.iter_content(1 << 16):
                    f.write(chunk)
            tmp.rename(dest)
            return dest


def run(cmd, **kw):
    """Run a command, raising with stderr tail on failure."""
    log.debug("$ %s", " ".join(str(c) for c in cmd))
    p = subprocess.run([str(c) for c in cmd], capture_output=True, text=True, **kw)
    if p.returncode != 0:
        raise RuntimeError(f"{cmd[0]} failed ({p.returncode}): {p.stderr[-1500:]}")
    return p.stdout


def ffprobe_duration(path):
    out = run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
               "-of", "json", path])
    return float(json.loads(out)["format"]["duration"])


def fmt_ts(seconds):
    """0:00 / 1:02:03 style timestamp for YouTube chapters."""
    t = int(seconds)
    h, m, s = t // 3600, (t % 3600) // 60, t % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def srt_ts(seconds):
    ms = int(round(seconds * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"
