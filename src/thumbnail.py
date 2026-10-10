"""1280x720 thumbnail: strongest visual + 2-3 huge words with heavy outline."""
from PIL import Image, ImageDraw, ImageEnhance, ImageOps

from . import fonts
from .common import run

W, H = 1280, 720


def background_from(visual, work):
    if visual["kind"] == "image":
        return Image.open(visual["path"]).convert("RGB")
    if visual["kind"] == "video":
        frame = work / "thumb-frame.jpg"
        run(["ffmpeg", "-y", "-v", "error", "-ss", "1.5", "-i", visual["path"], "-frames:v", "1", frame])
        return Image.open(frame).convert("RGB")
    return Image.new("RGB", (W, H), (20, 24, 30))


def make(visual, text, out, work):
    bg = ImageOps.fit(background_from(visual, work), (W, H), Image.LANCZOS)
    bg = ImageEnhance.Contrast(ImageEnhance.Color(bg).enhance(1.35)).enhance(1.15)
    # Dark gradient on the left so text holds contrast at feed size.
    shade = Image.new("L", (W, H))
    sd = ImageDraw.Draw(shade)
    for x in range(W):
        sd.line((x, 0, x, H), fill=int(210 * max(0, 1 - x / (W * 0.75))))
    bg.paste(Image.new("RGB", (W, H), (0, 0, 0)), (0, 0), shade)

    d = ImageDraw.Draw(bg)
    words = (text or "").upper().split()[:3]
    size = 150
    while size > 70:
        fnt = fonts.font(size)
        lines = fonts.wrap(d, " ".join(words), fnt, W * 0.62)
        if len(lines) * size * 1.08 < H * 0.8:
            break
        size -= 10
    y = (H - len(lines) * size * 1.08) / 2
    for i, line in enumerate(lines):
        color = (255, 214, 0) if i == len(lines) - 1 else (255, 255, 255)
        d.text((60, y), line, font=fnt, fill=color, stroke_width=10, stroke_fill=(0, 0, 0))
        y += size * 1.08
    bg.save(out, quality=90)
    return out
