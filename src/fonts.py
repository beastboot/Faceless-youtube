"""Bold display font (Anton, OFL) downloaded once; system fallbacks."""
from pathlib import Path

from PIL import ImageFont, features

from .common import ROOT, download, log

FONTS = {  # language -> (file name, download URL); both OFL-licensed Google Fonts
    "en": ("Anton-Regular.ttf", "https://github.com/google/fonts/raw/main/ofl/anton/Anton-Regular.ttf"),
    "hi": ("Mukta-ExtraBold.ttf", "https://github.com/google/fonts/raw/main/ofl/mukta/Mukta-ExtraBold.ttf"),
    "mr": ("Mukta-ExtraBold.ttf", "https://github.com/google/fonts/raw/main/ofl/mukta/Mukta-ExtraBold.ttf"),
    "ta": ("MuktaMalar-ExtraBold.ttf", "https://github.com/google/fonts/raw/main/ofl/muktamalar/MuktaMalar-ExtraBold.ttf"),
    "bn": ("HindSiliguri-Bold.ttf", "https://github.com/google/fonts/raw/main/ofl/hindsiliguri/HindSiliguri-Bold.ttf"),
    "te": ("Ramabhadra-Regular.ttf", "https://github.com/google/fonts/raw/main/ofl/ramabhadra/Ramabhadra-Regular.ttf"),
}
FALLBACKS = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/System/Library/Fonts/Supplemental/Impact.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
]


def font_path(lang="en"):
    name, url = FONTS.get(lang, FONTS["en"])
    p = ROOT / "assets" / "fonts" / name
    if not p.exists():
        p.parent.mkdir(parents=True, exist_ok=True)
        try:
            download(url, p)
        except Exception as e:
            log.warning("Font download failed (%s); using system font", e)
    if p.exists():
        return str(p)
    if lang != "en":
        raise RuntimeError(f"Font for {lang!r} unavailable; captions would render as boxes")
    for f in FALLBACKS:
        if Path(f).exists():
            return f
    raise RuntimeError("No bold TTF font found; add one at assets/fonts/Anton-Regular.ttf")


def font(size, lang="en"):
    # RAQM layout shapes Devanagari (vowel signs, conjuncts) correctly when libraqm is installed.
    layout = ImageFont.Layout.RAQM if features.check("raqm") else ImageFont.Layout.BASIC
    if lang != "en" and layout != ImageFont.Layout.RAQM:
        log.warning("libraqm missing: Indian-script text will be mis-shaped (brew/apt install libraqm)")
    return ImageFont.truetype(font_path(lang), size, layout_engine=layout)


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
