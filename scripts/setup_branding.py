#!/usr/bin/env python3
"""One-time channel branding: description, keywords, banner (via API) and a logo for you to upload.

    python scripts/setup_branding.py                 # Hindi main channel: generate files to review
    python scripts/setup_branding.py --apply         # upload banner + set description/keywords on YouTube
    python scripts/setup_branding.py ta --apply      # a regional channel (Tamil), its own YT_*_TA credentials

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
LANG_NAMES = {"hi": "Hindi", "ta": "Tamil", "te": "Telugu", "bn": "Bengali", "mr": "Marathi"}


def text_assets(cfg, lang="hi"):
    p = OUT / "branding.json"
    if p.exists():
        return json.loads(p.read_text())
    if lang != "hi":
        return _regional_text(cfg, lang, p)
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


def _regional_text(cfg, lang, p):
    name = LANG_NAMES[lang]
    lc = cfg["languages"][lang]
    data = llm.ask_json(cfg, f"You are a YouTube branding expert for {name}-speaking audiences. JSON only.", f"""
Channel: {lc['channel_name']}. Niche: {cfg['channel']['niche']}. Language: {name} (animated documentaries
with captions in {name}); same stories as the Hindi channel Odd Annals. New videos daily.
Write: description_local ({name} script, 380-450 characters, first sentence with the search words {name}
speakers use for history/mysteries, what viewers get, schedule, subscribe line), description_en (same in
English, 380-450 characters), 15 keywords ({name} + English, multi-word ones in double quotes), tagline
({name}, max 6 words). No fake claims, max 2 emojis each.
Return {{"description_local": "...", "description_en": "...", "keywords": ["..."], "tagline": "..."}}""",
                        temperature=0.6)
    data["description"] = f"{data['description_local'].strip()}\n\n— English —\n{data['description_en'].strip()}"
    OUT.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=1, ensure_ascii=False))
    return data


def banner(cfg, tagline, lang="hi", name=None):
    p = OUT / "banner.jpg"
    art = OUT / "banner-art.jpg"
    shared = ROOT / "branding" / "banner-art.jpg"   # one brand: reuse the main channel's art
    if not art.exists() and shared.exists():
        import shutil
        shutil.copy(shared, art)
    if not art.exists():
        imagegen.generate("panoramic scene of ancient mysterious ruins, a lost temple in mist, an old map "
                          "and a glowing artifact, night sky with stars, sense of discovery", art, cfg,
                          seed=42, landscape=True, fmt="long")
    W, H = 2560, 1440
    img = ImageOps.fit(Image.open(art).convert("RGB"), (W, H), Image.LANCZOS)
    img = ImageEnhance.Brightness(img).enhance(0.6)
    d = ImageDraw.Draw(img)
    name = (name or cfg["channel"]["name"]).upper()
    f1, f2 = fonts.font(190 if len(name) < 14 else 150, "en"), fonts.font(84, lang)
    # Everything inside YouTube's 1546x423 centre safe area (visible on every device).
    w1 = d.textlength(name, font=f1)
    d.text(((W - w1) / 2, H / 2 - 190), name, font=f1, fill=(255, 222, 0), stroke_width=10, stroke_fill=(0, 0, 0))
    w2 = d.textlength(tagline, font=f2)
    d.rectangle(((W - w2) / 2 - 30, H / 2 + 40, (W + w2) / 2 + 30, H / 2 + 160), fill=(226, 18, 18))
    d.text(((W - w2) / 2, H / 2 + 38), tagline, font=f2, fill=(255, 255, 255))
    img.save(p, quality=92)
    return p


def typographic_logo(cfg, lang, tag):
    """Gold 'OA' badge with a tagline in the channel's language (matches the main logo)."""
    from PIL import ImageFilter  # noqa: F401
    p = OUT / "logo.png"
    S = 800
    im = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    for r in range(S // 2, 0, -4):
        t = r / (S / 2)
        d.ellipse((S / 2 - r, S / 2 - r, S / 2 + r, S / 2 + r),
                  fill=(int(12 + 30 * (1 - t)), int(16 + 28 * (1 - t)), int(34 + 40 * (1 - t)), 255))
    d.ellipse((22, 22, S - 22, S - 22), outline=(255, 206, 0, 255), width=18)
    d.ellipse((58, 58, S - 58, S - 58), outline=(255, 206, 0, 90), width=4)
    f = fonts.font(390, "en")
    bbox = d.textbbox((0, 0), "OA", font=f)
    w = d.textlength("OA", font=f)
    y = (S - (bbox[3] - bbox[1])) / 2 - bbox[1] - 30
    d.text(((S - w) / 2, y), "OA", font=f, fill=(255, 222, 0, 255), stroke_width=10, stroke_fill=(0, 0, 0, 255))
    d.rectangle((S / 2 - 150, y + bbox[3] + 20, S / 2 + 150, y + bbox[3] + 48), fill=(226, 18, 18, 255))
    fs = fonts.font(50, lang)
    tw = d.textlength(tag, font=fs)
    d.text(((S - tw) / 2, y + bbox[3] + 62), tag, font=fs, fill=(255, 255, 255, 255))
    im.save(p)
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


def apply(text, banner_path, lang="hi"):
    yt = youtube.client()
    up = yt.channelBanners().insert(media_body=str(banner_path)).execute()
    ch = yt.channels().list(part="brandingSettings", mine=True).execute()["items"][0]
    bs = ch.get("brandingSettings", {})
    bs.setdefault("channel", {}).update({
        "description": text["description"][:1000],
        "keywords": " ".join(text["keywords"])[:500],
        "defaultLanguage": lang,
        "country": "IN",
    })
    bs.setdefault("image", {})["bannerExternalUrl"] = up["url"]
    yt.channels().update(part="brandingSettings", body={"id": ch["id"], "brandingSettings": bs}).execute()
    # English translation of the channel: shown to viewers whose YouTube language is English.
    yt.channels().update(part="localizations", body={"id": ch["id"], "localizations": {
        "en": {"title": ch.get("brandingSettings", {}).get("channel", {}).get("title") or load_config()["channel"]["name"],
               "description": text["description_en"][:1000]}}}).execute()
    log.info("Channel description, keywords, country and banner updated.")


def main():
    global OUT
    setup_logging()
    cfg = load_config()
    lang = next((a for a in sys.argv[1:] if not a.startswith("--")), "hi")
    if lang != "hi":
        OUT = ROOT / "branding" / lang
        youtube.use_channel(cfg["languages"][lang]["youtube_env"])
    OUT.mkdir(parents=True, exist_ok=True)
    text = text_assets(cfg, lang)
    if lang == "hi":
        b = banner(cfg, text["tagline"])
        lg = logo(cfg)
    else:
        b = banner(cfg, text["tagline"], lang, cfg["languages"][lang]["channel_name"])
        lg = typographic_logo(cfg, lang, {"ta": "மர்மம் • வரலாறு", "te": "రహస్యం • చరిత్ర",
                                          "bn": "রহস্য • ইতিহাস", "mr": "रहस्य • इतिहास"}[lang])
    print(f"\nDescription:\n{text['description']}\n\nKeywords: {', '.join(text['keywords'])}\n"
          f"Tagline: {text['tagline']}\nBanner: {b}\nLogo (upload manually in Studio): {lg}\n")
    if "--apply" in sys.argv:
        apply(text, b, lang)


if __name__ == "__main__":
    main()
