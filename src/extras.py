"""Weekly community-post kit and monthly Kindle e-book, both delivered to Telegram.
(YouTube has no API for Community posts; KDP has no upload API: these need one manual paste/upload.)"""
import html
import json
import uuid
import zipfile
from datetime import datetime, timedelta, timezone

from . import llm, notify, state
from .common import ROOT, log

SCRIPTS = ROOT / "data" / "scripts"


def save_script(video_id, script, meta):
    """Keep each long video's Hindi script (small text) for the monthly e-book."""
    SCRIPTS.mkdir(parents=True, exist_ok=True)
    keep = {"title": meta["title"], "date": datetime.now(timezone.utc).isoformat(), "video_id": video_id,
            "scenes": [{"chapter": sc.get("chapter", ""), "narration": sc.get("narration") or
                        " ".join(s["line"] for s in sc["shots"])} for sc in script["scenes"]]}
    (SCRIPTS / f"{video_id}.json").write_text(json.dumps(keep, ensure_ascii=False, indent=1))


def community_post(cfg):
    """Monday: a ready-to-paste poll + a text post for the YouTube Community tab."""
    q = json.loads((ROOT / "data" / "followups.json").read_text()) if (ROOT / "data" / "followups.json").exists() else []
    recent = [r["title"] for r in state.load()[-12:] if r.get("video_id")]
    data = llm.ask_json(cfg, "You write YouTube Community posts for a Hindi mystery/history channel. JSON only.", f"""
Recent videos: {json.dumps(recent, ensure_ascii=False)}
Queued/next topics: {json.dumps([x.get('topic') for x in q][:6], ensure_ascii=False)}
Write in Hindi (Devanagari):
1) a poll: question asking which mystery viewers want next + exactly 4 short options (real Indian/world
   mysteries; include queued topics if any)
2) a short engaging text post (2-3 lines + one question) teasing this week's stories.
Return {{"poll_question": "...", "options": ["...","...","...","..."], "post": "..."}}""", temperature=0.7)
    opts = "\n".join(f"{chr(65 + i)}) {o}" for i, o in enumerate((data.get("options") or [])[:4]))
    notify.send("🗳️ This week's Community posts (YouTube Studio → Create → Post):\n\n"
                f"POLL:\n{data.get('poll_question', '')}\n{opts}\n\nTEXT POST:\n{data.get('post', '')}")


def _epub(path, title, chapters, cover_jpg=None):
    """Minimal valid EPUB 3 (Kindle/KDP accepts EPUB)."""
    uid = str(uuid.uuid4())
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        z.writestr("META-INF/container.xml", '<?xml version="1.0"?><container version="1.0" '
                   'xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile '
                   'full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles></container>')
        items, spine, nav = [], [], []
        for i, (ct, body) in enumerate(chapters):
            name = f"c{i:02d}.xhtml"
            z.writestr(f"OEBPS/{name}", f'<?xml version="1.0" encoding="utf-8"?><html xmlns="http://www.w3.org/1999/xhtml" '
                       f'lang="hi"><head><title>{html.escape(ct)}</title></head><body><h1>{html.escape(ct)}</h1>{body}</body></html>')
            items.append(f'<item id="c{i}" href="{name}" media-type="application/xhtml+xml"/>')
            spine.append(f'<itemref idref="c{i}"/>')
            nav.append(f'<li><a href="{name}">{html.escape(ct)}</a></li>')
        z.writestr("OEBPS/nav.xhtml", f'<?xml version="1.0" encoding="utf-8"?><html xmlns="http://www.w3.org/1999/xhtml" '
                   f'xmlns:epub="http://www.idpf.org/2007/ops"><head><title>सूची</title></head><body><nav epub:type="toc">'
                   f'<h1>सूची</h1><ol>{"".join(nav)}</ol></nav></body></html>')
        cover_item = ""
        if cover_jpg:
            z.write(cover_jpg, "OEBPS/cover.jpg")
            cover_item = '<item id="cover" href="cover.jpg" media-type="image/jpeg" properties="cover-image"/>'
        z.writestr("OEBPS/content.opf", f'<?xml version="1.0" encoding="utf-8"?><package xmlns="http://www.idpf.org/2007/opf" '
                   f'version="3.0" unique-identifier="uid"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
                   f'<dc:identifier id="uid">urn:uuid:{uid}</dc:identifier><dc:title>{html.escape(title)}</dc:title>'
                   f'<dc:language>hi</dc:language><dc:creator>Odd Annals</dc:creator>'
                   f'<meta property="dcterms:modified">{datetime.now(timezone.utc):%Y-%m-%dT%H:%M:%SZ}</meta></metadata>'
                   f'<manifest><item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>'
                   f'{cover_item}{"".join(items)}</manifest><spine>{"".join(spine)}</spine></package>')


def monthly_ebook(cfg):
    """1st of the month: last month's long stories as a Hindi Kindle e-book, sent to Telegram."""
    if not SCRIPTS.exists():
        return
    since = (datetime.now(timezone.utc) - timedelta(days=31)).isoformat()
    stories = sorted((json.loads(p.read_text()) for p in SCRIPTS.glob("*.json")), key=lambda x: x["date"])
    stories = [s for s in stories if s["date"] >= since]
    if len(stories) < 4:
        log.info("E-book: only %d stories this month; skipped", len(stories))
        return
    month = datetime.now(timezone.utc) - timedelta(days=2)
    title = f"अनसुने रहस्य — {month:%B %Y} (Odd Annals)"
    chapters = []
    for st in stories:
        body = "".join((f"<h2>{html.escape(sc['chapter'])}</h2>" if sc["chapter"] else "") +
                       f"<p>{html.escape(sc['narration'])}</p>" for sc in st["scenes"])
        chapters.append((st["title"].split("|")[0].replace("#shorts", "").strip(), body))
    out = ROOT / "output" / f"odd-annals-{month:%Y-%m}.epub"
    out.parent.mkdir(exist_ok=True)
    cover = None
    try:
        from PIL import Image, ImageDraw
        from . import fonts
        img = Image.new("RGB", (1600, 2560), (14, 15, 20))
        d = ImageDraw.Draw(img)
        d.text((800, 900), "अनसुने रहस्य", font=fonts.font(190, "hi"), fill=(255, 214, 10), anchor="mm")
        d.text((800, 1150), f"{month:%B %Y}", font=fonts.font(110, "en"), fill=(255, 255, 255), anchor="mm")
        d.text((800, 2300), "ODD ANNALS", font=fonts.font(120, "en"), fill=(229, 18, 27), anchor="mm")
        cover = ROOT / "output" / "ebook-cover.jpg"
        img.save(cover, quality=92)
    except Exception as e:
        log.warning("E-book cover failed: %s", e)
    _epub(out, title, chapters, cover)
    notify.send_document(str(out), f"📚 {title}: {len(chapters)} stories. Upload at kdp.amazon.com "
                                   "(Kindle eBook → Hindi). Set your price; cover is inside the file.")
    if cover:
        notify.send_photos([str(cover)], "E-book cover (KDP also asks for it separately)")
