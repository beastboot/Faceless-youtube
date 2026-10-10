"""Every long video also becomes (1) a Hindi article on the GitHub Pages site and (2) a podcast episode
(audio stored as a GitHub release asset, listed in site/podcast.xml for Spotify / JioSaavn / Apple)."""
import html
import json
import os
import subprocess
from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path

from PIL import Image

from .common import ROOT, ffprobe_duration, log, run, slugify

SITE = ROOT / "site"
INDEX = ROOT / "data" / "site.json"


def _repo():
    return os.environ.get("GITHUB_REPOSITORY", "beastboot/Faceless-youtube")


def _base_url():
    owner, name = _repo().split("/")
    return f"https://{owner}.github.io/{name}/site"


def _upload_audio(mp3):
    """Store the episode audio on the 'podcast' GitHub release (free, no size cap on total)."""
    repo = _repo()
    if subprocess.run(["gh", "release", "view", "podcast", "-R", repo], capture_output=True).returncode != 0:
        subprocess.run(["gh", "release", "create", "podcast", "-R", repo, "--title", "Podcast episodes",
                        "--notes", "Audio files for the Odd Annals podcast feed (site/podcast.xml)."], check=True)
    subprocess.run(["gh", "release", "upload", "podcast", str(mp3), "--clobber", "-R", repo], check=True)
    return f"https://github.com/{repo}/releases/download/podcast/{mp3.name}"


def _cover(cfg):
    """Square 1400x1400 podcast cover (Apple/Spotify requirement) from the channel logo."""
    out = SITE / "cover.jpg"
    if not out.exists():
        logo = ROOT / "branding" / "logo.png"
        bg = Image.new("RGB", (1400, 1400), (14, 15, 20))
        if logo.exists():
            im = Image.open(logo).convert("RGBA").resize((1200, 1200), Image.LANCZOS)
            bg.paste(im, (100, 100), im)
        bg.save(out, quality=92)
    return f"{_base_url()}/cover.jpg"


def add_episode(cfg, script, meta, work, video_id):
    """Article page + podcast episode for one published long video (Hindi)."""
    SITE.mkdir(exist_ok=True)
    (SITE / "stories").mkdir(exist_ok=True)
    slug = f"{datetime.now(timezone.utc):%Y%m%d}-{slugify(script.get('en', {}).get('title') or meta['title'])}"
    # 1) audio
    mp3 = work / f"{slug}.mp3"
    run(["ffmpeg", "-y", "-v", "error", "-i", work / "final.mp4", "-vn", "-ac", "1", "-b:a", "96k", mp3])
    try:
        audio_url = _upload_audio(mp3)
    except Exception as e:
        log.warning("Podcast audio upload failed (%s); article only", e)
        audio_url = None
    ep = {"slug": slug, "title": meta["title"], "video_id": video_id,
          "date": datetime.now(timezone.utc).isoformat(), "audio": audio_url,
          "bytes": mp3.stat().st_size, "seconds": int(ffprobe_duration(mp3)),
          "summary": (script.get("description_intro") or "")[:600]}
    # 2) article
    body = []
    for sc in script["scenes"]:
        if (sc.get("chapter") or "").strip():
            body.append(f"<h2>{html.escape(sc['chapter'])}</h2>")
        body.append(f"<p>{html.escape(sc.get('narration') or ' '.join(s['line'] for s in sc['shots']))}</p>")
    page = f"""<!doctype html><html lang="hi"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>{html.escape(meta['title'])}</title>
<meta name="description" content="{html.escape(ep['summary'][:155])}">
<style>body{{margin:0;background:#0e0f14;color:#eef0f6;font:18px/1.75 'Mukta','Noto Sans Devanagari',system-ui,sans-serif}}
main{{max-width:760px;margin:0 auto;padding:28px 16px 60px}}h1{{line-height:1.3}}h2{{color:#ffd60a;margin-top:34px}}
a{{color:#ffd60a}}.v{{position:relative;padding-top:56.25%;margin:18px 0}}.v iframe{{position:absolute;inset:0;width:100%;height:100%;border:0;border-radius:12px}}</style>
</head><body><main><p><a href="../index.html">← Odd Annals कहानियाँ</a></p><h1>{html.escape(meta['title'])}</h1>
<div class="v"><iframe src="https://www.youtube.com/embed/{video_id}" allowfullscreen loading="lazy"></iframe></div>
{''.join(body)}
<p><a href="https://youtu.be/{video_id}">▶ YouTube पर देखें</a>{f' · <a href="{audio_url}">🎧 पॉडकास्ट सुनें</a>' if audio_url else ''}</p>
</main></body></html>"""
    (SITE / "stories" / f"{slug}.html").write_text(page)
    eps = json.loads(INDEX.read_text()) if INDEX.exists() else []
    eps = [e for e in eps if e["slug"] != slug] + [ep]
    INDEX.write_text(json.dumps(eps, indent=1, ensure_ascii=False))
    _write_index(cfg, eps)
    _write_feed(cfg, eps)
    log.info("Site: article + podcast episode for %s", slug)


def _write_index(cfg, eps):
    items = "".join(f'<li><a href="stories/{e["slug"]}.html">{html.escape(e["title"])}</a>'
                    f'<br><small>{e["date"][:10]}</small></li>' for e in reversed(eps))
    (SITE / "index.html").write_text(f"""<!doctype html><html lang="hi"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Odd Annals — रहस्य और इतिहास की कहानियाँ</title>
<link rel="alternate" type="application/rss+xml" title="Odd Annals Podcast" href="podcast.xml">
<style>body{{margin:0;background:#0e0f14;color:#eef0f6;font:17px/1.6 'Mukta',system-ui,sans-serif}}
main{{max-width:760px;margin:0 auto;padding:28px 16px}}a{{color:#ffd60a;text-decoration:none}}li{{margin:14px 0}}</style>
</head><body><main><h1>📜 Odd Annals — रहस्य और इतिहास की कहानियाँ</h1>
<p><a href="https://www.youtube.com/@oddannals">▶ YouTube चैनल</a> · <a href="podcast.xml">🎧 पॉडकास्ट फ़ीड</a></p>
<ul>{items}</ul></main></body></html>""")


def _write_feed(cfg, eps):
    pc = cfg.get("podcast") or {}
    base = _base_url()
    items = []
    for e in reversed([e for e in eps if e.get("audio")]):
        d = format_datetime(datetime.fromisoformat(e["date"]))
        items.append(f"""<item><title>{html.escape(e['title'])}</title>
<description>{html.escape(e['summary'])} — https://youtu.be/{e['video_id']}</description>
<link>{base}/stories/{e['slug']}.html</link><guid isPermaLink="false">{e['slug']}</guid><pubDate>{d}</pubDate>
<enclosure url="{e['audio']}" length="{e['bytes']}" type="audio/mpeg"/>
<itunes:duration>{e['seconds']}</itunes:duration><itunes:explicit>false</itunes:explicit></item>""")
    feed = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd" xmlns:atom="http://www.w3.org/2005/Atom">
<channel><title>{html.escape(pc.get('title', 'Odd Annals — रहस्य और इतिहास'))}</title>
<link>{base}/index.html</link><language>hi</language>
<atom:link href="{base}/podcast.xml" rel="self" type="application/rss+xml"/>
<description>{html.escape(pc.get('description', 'भारत और दुनिया के अनसुने रहस्य और इतिहास की सच्ची कहानियाँ, हिंदी में।'))}</description>
<itunes:author>Odd Annals</itunes:author><itunes:image href="{_cover(cfg)}"/>
<itunes:category text="History"/><itunes:explicit>false</itunes:explicit>
<itunes:owner><itunes:name>Odd Annals</itunes:name><itunes:email>{html.escape(pc.get('owner_email', ''))}</itunes:email></itunes:owner>
{''.join(items)}
</channel></rss>"""
    (SITE / "podcast.xml").write_text(feed)
