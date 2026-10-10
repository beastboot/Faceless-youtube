#!/usr/bin/env python3
"""One-time channel branding: description, keywords, banner (via API) and a logo for you to upload.

    python scripts/setup_branding.py            # generate branding/ files to review (changes nothing)
    python scripts/setup_branding.py --apply    # upload banner + set description/keywords on YouTube

YouTube's API cannot set the profile picture: upload branding/logo.png yourself in
YouTube Studio > Customization > Branding.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image, ImageDraw, ImageEnhance, ImageOps  # noqa: E402

from src import fonts, imagegen, llm, youtube  # noqa: E402
from src.common import ROOT, load_config, log, setup_logging  # noqa: E402

OUT = ROOT / "branding"


def text_assets(cfg):
    p = OUT / "branding.json"
    if p.exists():
        return json.loads(p.read_text())
    ch = cfg["channel"]
    data = llm.ask_json(cfg, "You are a YouTube channel branding expert for Indian audiences. JSON only.", f"""
Channel: {ch['name']}. Niche: {ch['niche']}. Main language: Hindi (animated documentaries with
Hinglish captions), audience: curious Indians 16-40. New videos daily: one 10-minute story + Shorts.
Write:
- description_hi: YouTube "About" text in Hindi, 380-450 characters. First sentence must contain the
  search words people use (रहस्य, इतिहास, history in hindi). What viewers get, upload schedule, subscribe line.
- description_en: the same in natural English, 380-450 characters, first sentence with "history",
  "mysteries", "animated documentaries", "Hindi".
No fake claims, max 2 emojis each.
- keywords: 15 channel keywords (Hindi, Hinglish and English), multi-word ones in double quotes.
- tagline: banner tagline in Hindi, max 6 words.
Return {{"description_hi": "...", "description_en": "...", "keywords": ["..."], "tagline": "..."}}""",
                        temperature=0.6)
    data["description"] = f"{data['description_hi'].strip()}\n\n— English —\n{data['description_en'].strip()}"
    OUT.mkdir(exist_ok=True)
    p.write_text(json.dumps(data, indent=1, ensure_ascii=False))
    return data


def banner(cfg, tagline):
    p = OUT / "banner.jpg"
    art = OUT / "banner-art.jpg"
    if not art.exists():
        imagegen.generate("panoramic scene of ancient mysterious ruins, a lost temple in mist, an old map "
                          "and a glowing artifact, night sky with stars, sense of discovery", art, cfg,
                          seed=42, landscape=True, fmt="long")
    W, H = 2560, 1440
    img = ImageOps.fit(Image.open(art).convert("RGB"), (W, H), Image.LANCZOS)
    img = ImageEnhance.Brightness(img).enhance(0.6)
    d = ImageDraw.Draw(img)
    name = cfg["channel"]["name"].upper()
    f1, f2 = fonts.font(190, "en"), fonts.font(84, "hi")
    # Everything inside YouTube's 1546x423 centre safe area (visible on every device).
    w1 = d.textlength(name, font=f1)
    d.text(((W - w1) / 2, H / 2 - 190), name, font=f1, fill=(255, 222, 0), stroke_width=10, stroke_fill=(0, 0, 0))
    w2 = d.textlength(tagline, font=f2)
    d.rectangle(((W - w2) / 2 - 30, H / 2 + 40, (W + w2) / 2 + 30, H / 2 + 160), fill=(226, 18, 18))
    d.text(((W - w2) / 2, H / 2 + 38), tagline, font=f2, fill=(255, 255, 255))
    img.save(p, quality=92)
    return p


def logo(cfg):
    p = OUT / "logo.png"
    art = OUT / "logo-art.jpg"
    if not art.exists():
        imagegen.generate("emblem of an ancient all-seeing eye inside a weathered bronze compass, glowing "
                          "golden light, dark background, centered, iconic, simple bold shapes", art, cfg,
                          seed=7, landscape=True, fmt="long")
    im = ImageOps.fit(Image.open(art).convert("RGB"), (800, 800), Image.LANCZOS)
    mask = Image.new("L", (800, 800))
    ImageDraw.Draw(mask).ellipse((0, 0, 800, 800), fill=255)
    out = Image.new("RGBA", (800, 800))
    out.paste(im, (0, 0), mask)
    out.save(p)
    return p


def apply(text, banner_path):
    yt = youtube.client()
    up = yt.channelBanners().insert(media_body=str(banner_path)).execute()
    ch = yt.channels().list(part="brandingSettings", mine=True).execute()["items"][0]
    bs = ch.get("brandingSettings", {})
    bs.setdefault("channel", {}).update({
        "description": text["description"][:1000],
        "keywords": " ".join(text["keywords"])[:500],
        "defaultLanguage": "hi",
        "country": "IN",
    })
    bs.setdefault("image", {})["bannerExternalUrl"] = up["url"]
    yt.channels().update(part="brandingSettings", body={"id": ch["id"], "brandingSettings": bs}).execute()
    # English translation of the channel: shown to viewers whose YouTube language is English.
    yt.channels().update(part="localizations", body={"id": ch["id"], "localizations": {
        "en": {"title": load_config()["channel"]["name"], "description": text["description_en"][:1000]}}}).execute()
    log.info("Channel description, keywords, country and banner updated.")


def main():
    setup_logging()
    cfg = load_config()
    OUT.mkdir(exist_ok=True)
    text = text_assets(cfg)
    b = banner(cfg, text["tagline"])
    lg = logo(cfg)
    print(f"\nDescription:\n{text['description']}\n\nKeywords: {', '.join(text['keywords'])}\n"
          f"Tagline: {text['tagline']}\nBanner: {b}\nLogo (upload manually in Studio): {lg}\n")
    if "--apply" in sys.argv:
        apply(text, b)


if __name__ == "__main__":
    main()
