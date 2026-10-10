"""1280x720 thumbnails in two proven templates, A/B tested automatically.

yellow:   full-width yellow top banner, big black text, *highlighted* words in red, image below
          (the "Unsolved India" look).
redstrip: darkened image, huge white words on the left, the *highlighted* words on a red strip
          (the "ReBorn Mindset" look).
Text marks highlights with asterisks: "दुनिया के 7 *सबसे रहस्यमयी* चीज़ें"."""
import json
import math
import random
import re

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageOps

from . import fonts
from .common import ROOT, run

W, H = 1280, 720
YELLOW, RED, BLACK, WHITE = (255, 222, 0), (226, 18, 18), (10, 10, 10), (255, 255, 255)
TEMPLATES = ("rahasya", "storybook", "yellow", "redstrip")


def background_from(visual, work):
    if visual and visual.get("kind") == "image" and visual.get("path"):
        return Image.open(visual["path"]).convert("RGB")
    if visual and visual.get("kind") == "video":
        frame = work / "thumb-frame.jpg"
        run(["ffmpeg", "-y", "-v", "error", "-ss", "1.5", "-i", visual["path"], "-frames:v", "1", frame])
        return Image.open(frame).convert("RGB")
    return Image.new("RGB", (W, H), (20, 24, 30))


def parse(text):
    """'a *b c*? d' -> [('a', False), ('b', True), ('c?', True), ('d', False)]; markers never shown."""
    words = []
    for i, part in enumerate(re.split(r"\*+", text or "")):
        for wd in part.split():
            if words and not any(ch.isalnum() for ch in wd):
                words[-1] = (words[-1][0] + wd, words[-1][1])   # "?" / "!" stick to the word before
                continue
            words.append((wd, i % 2 == 1))           # odd segments sat between asterisks
    if words and not any(h for _, h in words):  # no marks: highlight the last word or two
        k = 2 if len(words) > 3 else 1
        words = [(w, i >= len(words) - k) for i, (w, _) in enumerate(words)]
    return [(w, h) for w, h in words if w]


def choose_template(cfg):
    """Rotate templates until each has data, then mostly use the winner (see reach.py)."""
    tc = cfg.get("thumbnails", {})
    if tc.get("mode") in TEMPLATES:
        return tc["mode"]
    pool = [t for t in tc.get("templates") or TEMPLATES[:2] if t in TEMPLATES]
    win = ROOT / "data" / "thumb_winner.json"
    if win.exists():
        best = json.loads(win.read_text()).get("winner")
        if best in pool and random.random() < 0.8:
            return best
    hist = ROOT / "data" / "history.json"
    n = sum(1 for r in json.loads(hist.read_text()) if r.get("format") == "long") if hist.exists() else 0
    return pool[n % len(pool)]


def _lines(draw, words, fnt, max_w):
    lines, cur = [], []
    for w in words:
        trial = " ".join(x for x, _ in cur + [w])
        if cur and draw.textlength(trial, font=fnt) > max_w:
            lines.append(cur)
            cur = []
        cur.append(w)
    if cur:
        lines.append(cur)
    return lines


def _draw_words(d, x, y, line, fnt, color, hot_color, stroke=0, stroke_fill=BLACK):
    space = d.textlength(" ", font=fnt)
    for w, hot in line:
        d.text((x, y), w, font=fnt, fill=hot_color if hot else color, stroke_width=stroke, stroke_fill=stroke_fill)
        x += d.textlength(w, font=fnt) + space


def _fit(d, words, lang, max_w, max_lines, start, floor):
    size = start
    while size > floor:
        fnt = fonts.font(size, lang)
        lines = _lines(d, words, fnt, max_w)
        if len(lines) <= max_lines:
            return fnt, lines, size
        size -= 6
    fnt = fonts.font(floor, lang)
    return fnt, _lines(d, words, fnt, max_w)[:max_lines], floor


def yellow(bg, text, lang):
    img = ImageOps.fit(bg, (W, H), Image.LANCZOS)
    img = ImageEnhance.Contrast(ImageEnhance.Color(img).enhance(1.25)).enhance(1.12)
    d = ImageDraw.Draw(img)
    words = parse(text if lang != "en" else (text or "").upper())
    fnt, lines, size = _fit(d, words, lang, W - 60, 2, 96, 54)
    lh = int(size * (1.32 if lang != "en" else 1.08))
    bar = lh * len(lines) + 34
    d.rectangle((0, 0, W, bar), fill=YELLOW)
    y = 14 if lang != "en" else 20
    for line in lines:
        lw = d.textlength(" ".join(w for w, _ in line), font=fnt)
        _draw_words(d, (W - lw) / 2, y, line, fnt, BLACK, RED)
        y += lh
    return img


def redstrip(bg, text, lang):
    img = ImageOps.fit(bg, (W, H), Image.LANCZOS)
    img = ImageEnhance.Brightness(ImageEnhance.Contrast(img).enhance(1.3)).enhance(0.8)
    shade = Image.new("L", (W, H))
    sd = ImageDraw.Draw(shade)
    for x in range(W):  # dark vignette on the left for the text
        sd.line((x, 0, x, H), fill=int(200 * max(0, 1 - x / (W * 0.6))))
    img.paste(Image.new("RGB", (W, H), (8, 8, 8)), (0, 0), shade)
    d = ImageDraw.Draw(img)
    words = parse(text if lang != "en" else (text or "").upper())
    plain = [(w, False) for w, h in words if not h] or words[:1]
    hot = [(w, False) for w, h in words if h] or words[-1:]
    f1, l1, s1 = _fit(d, plain, lang, W * 0.55, 2, 120, 64)
    f2, l2, s2 = _fit(d, hot, lang, W * 0.55, 1, 140, 70)
    lh1 = int(s1 * (1.3 if lang != "en" else 1.05))
    block = lh1 * len(l1) + int(s2 * 1.45)
    y = (H - block) / 2
    for line in l1:
        _draw_words(d, 50, y, line, f1, WHITE, WHITE, stroke=8)
        y += lh1
    tw = d.textlength(" ".join(w for w, _ in l2[0]), font=f2)
    pad = 26
    d.rectangle((0, y + 6, 50 + tw + pad, y + s2 * 1.3 + 6), fill=RED)
    _draw_words(d, 50, y + (4 if lang == "en" else -2), l2[0], f2, WHITE, WHITE)
    return img


def storybook(bg, text, lang, label="ODD ANNALS"):
    """Painted scene, dark translucent top band with big multicolour words, yellow series label."""
    img = ImageOps.fit(bg, (W, H), Image.LANCZOS).convert("RGBA")
    img = Image.alpha_composite(img, Image.new("RGBA", (W, H), (0, 0, 0, 0)))
    d = ImageDraw.Draw(img)
    words = parse(text if lang != "en" else (text or "").title())
    fnt, lines, size = _fit(d, words, lang, W - 80, 2, 92, 52)
    lh = int(size * (1.32 if lang != "en" else 1.12))
    band = Image.new("RGBA", (W, lh * len(lines) + 40), (10, 10, 14, 170))
    img.alpha_composite(band, (0, 26))
    d = ImageDraw.Draw(img)
    palette = [(255, 92, 92), (255, 255, 255), (255, 214, 40)]       # red / white / yellow
    y = 40 if lang != "en" else 46
    k = 0
    for line in lines:
        lw = d.textlength(" ".join(w for w, _ in line), font=fnt)
        x = (W - lw) / 2
        space = d.textlength(" ", font=fnt)
        for wd, hot in line:
            col = palette[0] if hot and k % 2 == 0 else palette[2] if hot else WHITE
            if hot:
                k += 1
            d.text((x, y), wd, font=fnt, fill=col, stroke_width=4, stroke_fill=(0, 0, 0))
            x += d.textlength(wd, font=fnt) + space
        y += lh
    has_hindi = any("\u0900" <= ch <= "\u097f" for ch in label)
    lf = fonts.font(70 if has_hindi else 78, "hi" if has_hindi else "en")
    tw = d.textlength(label, font=lf)
    d.text(((W - tw) / 2, H - 112), label, font=lf, fill=(255, 214, 40), stroke_width=6, stroke_fill=(0, 0, 0))
    return img.convert("RGB")


def _arrow(d, start, end, color=RED, width=22):
    """Thick arrow with a triangular head, YouTube-thumbnail style."""
    (x0, y0), (x1, y1) = start, end
    ang = math.atan2(y1 - y0, x1 - x0)
    head = width * 2.6
    bx, by = x1 - head * math.cos(ang), y1 - head * math.sin(ang)
    for wd, col in ((width + 10, (255, 255, 255)), (width, color)):
        d.line((x0, y0, bx, by), fill=col, width=wd)
    left = (bx + head * 0.75 * math.sin(ang), by - head * 0.75 * math.cos(ang))
    right = (bx - head * 0.75 * math.sin(ang), by + head * 0.75 * math.cos(ang))
    d.polygon([(x1, y1), left, right], fill=color, outline=(255, 255, 255))


def rahasya(bg, text, lang):
    """The Hindi mystery niche's winning look: saturated epic art, huge stacked text with the key
    words biggest in yellow, a circled close-up inset and a red arrow pointing at it."""
    img = ImageOps.fit(bg, (W, H), Image.LANCZOS)
    img = ImageEnhance.Contrast(ImageEnhance.Color(img).enhance(1.4)).enhance(1.15)
    img = img.filter(ImageFilter.UnsharpMask(radius=2, percent=110))
    # darken the left side for the text
    shade = Image.new("L", (W, H))
    sd = ImageDraw.Draw(shade)
    for x in range(W):
        sd.line((x, 0, x, H), fill=int(170 * max(0, 1 - x / (W * 0.62))))
    img.paste(Image.new("RGB", (W, H), (0, 0, 0)), (0, 0), shade)
    # inset: 2x close-up of the art's focal area, in a ringed circle, bottom-right
    src = ImageOps.fit(bg, (W, H), Image.LANCZOS)
    cx, cy, r = int(W * 0.62), int(H * 0.45), 150
    crop = src.crop((cx - r, cy - r, cx + r, cy + r)).resize((2 * 170, 2 * 170), Image.LANCZOS)
    crop = ImageEnhance.Color(crop).enhance(1.3)
    mask = Image.new("L", crop.size)
    ImageDraw.Draw(mask).ellipse((0, 0, crop.width - 1, crop.height - 1), fill=255)
    ix, iy = W - crop.width - 40, H - crop.height - 40
    d = ImageDraw.Draw(img)
    d.ellipse((ix - 14, iy - 14, ix + crop.width + 14, iy + crop.height + 14), fill=RED)
    d.ellipse((ix - 6, iy - 6, ix + crop.width + 6, iy + crop.height + 6), fill=YELLOW)
    img.paste(crop, (ix, iy), mask)
    # text: plain words white, highlighted words huge yellow
    words = parse(text if lang != "en" else (text or "").upper())
    plain = [(w, False) for w, h in words if not h]
    hot = [(w, True) for w, h in words if h] or words[-1:]
    f1, l1, s1 = _fit(d, plain, lang, W * 0.56, 2, 104, 60) if plain else (None, [], 0)
    f2, l2, s2 = _fit(d, hot, lang, W * 0.58, 2, 170, 80)
    lh1, lh2 = int(s1 * (1.25 if lang != "en" else 1.05)), int(s2 * (1.2 if lang != "en" else 1.0))
    y = (H - (lh1 * len(l1) + lh2 * len(l2))) / 2 - 20
    for line in l1:
        _draw_words(d, 46, y, line, f1, WHITE, WHITE, stroke=9)
        y += lh1
    for line in l2:
        _draw_words(d, 40, y, line, f2, YELLOW, YELLOW, stroke=12)
        y += lh2
    _arrow(d, (int(W * 0.58), int(H * 0.30)), (ix - 10, iy + 40))
    return img


def make(visual, text, out, work, lang="en", template="yellow", label="ODD ANNALS"):
    bg = background_from(visual, work)
    if template == "storybook":
        img = storybook(bg, text, lang, label)
    elif template == "rahasya":
        img = rahasya(bg, text, lang)
    else:
        img = (yellow if template == "yellow" else redstrip)(bg, text, lang)
    img.filter(ImageFilter.UnsharpMask(radius=2, percent=60)).save(out, quality=92)
    return out
