"""Persistent run history (committed back to the repo by GitHub Actions) for dedup and feedback."""
import json
from datetime import datetime, timezone

from .common import ROOT

HISTORY = ROOT / "data" / "history.json"


def load():
    if HISTORY.exists():
        return json.loads(HISTORY.read_text())
    return []


def append(entry):
    rows = load()
    entry = {"created_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"), **entry}
    rows.append(entry)
    HISTORY.parent.mkdir(parents=True, exist_ok=True)
    HISTORY.write_text(json.dumps(rows, indent=1, ensure_ascii=False))


def used_topics(limit=300):
    return [r.get("topic", "") for r in load()[-limit:]]
