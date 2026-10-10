"""Ground every script in real facts: Wikipedia article text + freely licensed images."""
import re

from .common import http, log

WIKI_API = "https://en.wikipedia.org/w/api.php"
COMMONS_API = "https://commons.wikimedia.org/w/api.php"
BAD_IMAGE = re.compile(r"(icon|logo|symbol|flag_of|edit-clear|question_book|wiki|stub|padlock|"
                       r"ambox|disambig|crystal_clear|nuvola|portal|commons-)", re.I)


def _strip_html(s):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", s or "")).strip()


def resolve_title(session, title):
    r = session.get(WIKI_API, params={"action": "query", "titles": title, "redirects": 1,
                                      "format": "json"}, timeout=30).json()
    pages = r["query"]["pages"]
    page = next(iter(pages.values()))
    if "missing" not in page:
        return page["title"]
    s = session.get(WIKI_API, params={"action": "query", "list": "search", "srsearch": title,
                                      "srlimit": 1, "format": "json"}, timeout=30).json()
    hits = s["query"]["search"]
    if not hits:
        raise ValueError(f"No Wikipedia article for {title!r}")
    return hits[0]["title"]


def article_text(session, title, max_chars=14000):
    r = session.get(WIKI_API, params={"action": "query", "titles": title, "prop": "extracts",
                                      "explaintext": 1, "redirects": 1, "format": "json"},
                    timeout=30).json()
    text = next(iter(r["query"]["pages"].values())).get("extract", "")
    # Drop reference/see-also tail sections.
    text = re.split(r"\n==\s*(See also|References|Notes|Further reading|External links)\s*==",
                    text)[0]
    return text[:max_chars]


def _images_from(session, api, params, min_width):
    base = {"action": "query", "prop": "imageinfo", "iiprop": "url|size|mime|extmetadata",
            "iiurlwidth": 2000, "format": "json"}
    r = session.get(api, params={**base, **params}, timeout=40).json()
    out = []
    for p in (r.get("query", {}).get("pages", {}) or {}).values():
        info = (p.get("imageinfo") or [{}])[0]
        meta = info.get("extmetadata", {})
        name = p.get("title", "")
        lic = _strip_html(meta.get("LicenseShortName", {}).get("value", ""))
        if (info.get("mime") not in ("image/jpeg", "image/png") or info.get("width", 0) < min_width
                or BAD_IMAGE.search(name) or "NonFree" in meta or not lic):
            continue  # skip icons, tiny images and non-free (fair-use) files
        out.append({
            "url": info.get("thumburl") or info["url"],
            "title": name,
            "page": info.get("descriptionurl", ""),
            "artist": _strip_html(meta.get("Artist", {}).get("value", ""))[:80] or "Unknown",
            "license": lic,
            "desc": _strip_html(meta.get("ImageDescription", {}).get("value", ""))[:200],
        })
    return out


def images(session, title, extra_query, min_width=700):
    imgs = []
    try:
        imgs += _images_from(session, WIKI_API, {"generator": "images", "titles": title,
                                                 "gimlimit": 50}, min_width)
    except Exception as e:
        log.warning("Wikipedia images failed: %s", e)
    try:
        imgs += _images_from(session, COMMONS_API, {"generator": "search", "gsrnamespace": 6,
                                                    "gsrsearch": extra_query, "gsrlimit": 30},
                             min_width)
    except Exception as e:
        log.warning("Commons search failed: %s", e)
    seen, uniq = set(), []
    for i in imgs:
        if i["title"] not in seen:
            seen.add(i["title"])
            uniq.append(i)
    return uniq


def research(topic):
    s = http()
    title = resolve_title(s, topic.get("wikipedia_title") or topic["topic"])
    text = article_text(s, title)
    imgs = images(s, title, topic["topic"])
    log.info("Research: %s — %d chars, %d free images", title, len(text), len(imgs))
    return {"wiki_title": title,
            "wiki_url": "https://en.wikipedia.org/wiki/" + title.replace(" ", "_"),
            "text": text, "images": imgs}
