"""Bold display font (Anton, OFL) downloaded once; system fallbacks."""
from pathlib import Path

from PIL import ImageFont

from .common import ROOT, download, log

ANTON_URL = "https://github.com/google/fonts/raw/main/ofl/anton/Anton-Regular.ttf"
FALLBACKS = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/System/Library/Fonts/Supplemental/Impact.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
]


def font_path():
    p = ROOT / "assets" / "fonts" / "Anton-Regular.ttf"
    if not p.exists():
        p.parent.mkdir(parents=True, exist_ok=True)
        try:
            download(ANTON_URL, p)
        except Exception as e:
            log.warning("Font download failed (%s); using system font", e)
    if p.exists():
        return str(p)
    for f in FALLBACKS:
        if Path(f).exists():
            return f
    raise RuntimeError("No bold TTF font found; add one at assets/fonts/Anton-Regular.ttf")


def font(size):
    return ImageFont.truetype(font_path(), size)


def wrap(draw, text, fnt, max_width):
    lines, cur = [], ""
    for word in text.split():
        trial = f"{cur} {word}".strip()
        if cur and draw.textlength(trial, font=fnt) > max_width:
            lines.append(cur)
            cur = word
        else:
            cur = trial
    if cur:
        lines.append(cur)
    return lines
