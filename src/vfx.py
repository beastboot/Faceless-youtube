"""Real filmed VFX layers (smoke, fog, sparks, light leaks on black) from Pixabay (free licence, no
attribution needed), blended over the pictures in "screen" mode by the renderer. Clips are fetched
by id at run time and cached in models/vfx (not committed: the licence forbids redistributing them
as stand-alone files)."""
import os

from .common import ROOT, http, log

CLIPS = {"smoke": 66070, "fog": 197704, "embers": 7960, "leak": 4865}
DIR = ROOT / "models" / "vfx"
_cache = {}


def clip(kind):
    """Local path of the overlay clip for `kind`, or None (no key / not a VFX kind / download failed)."""
    vid = CLIPS.get(kind)
    key = os.environ.get("PIXABAY_API_KEY")
    if not vid or not key:
        return None
    if kind in _cache:
        return _cache[kind]
    path = DIR / f"{kind}-{vid}.mp4"
    if not path.exists():
        try:
            hit = http().get("https://pixabay.com/api/videos/", params={"key": key, "id": vid},
                             timeout=30).json()["hits"][0]
            url = (hit["videos"].get("medium") or hit["videos"]["small"])["url"]
            DIR.mkdir(parents=True, exist_ok=True)
            path.write_bytes(http().get(url, timeout=120).content)
        except Exception as e:
            log.warning("VFX clip %s unavailable: %s", kind, e)
            path = None
    _cache[kind] = str(path) if path else None
    return _cache[kind]
