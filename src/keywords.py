"""What Indian viewers actually type: YouTube search autocomplete (free, no key) in Hindi/Hinglish."""
import json
import time

from .common import http, log

URL = "https://suggestqueries.google.com/complete/search"


def suggest(query, lang="hi"):
    try:
        r = http().get(URL, params={"client": "firefox", "ds": "yt", "hl": lang, "gl": "IN", "q": query},
                       timeout=20)
        return json.loads(r.content.decode("utf-8", "replace"))[1]
    except Exception as e:
        log.debug("autocomplete %r failed: %s", query, e)
        return []


def niche_searches(cfg, limit=80):
    """Popular niche searches right now — a trend source for the topic picker."""
    out = []
    for seed in cfg["keywords"]["niche_seeds"]:
        out += suggest(seed)
        time.sleep(0.2)
    seen, uniq = set(), []
    for q in out:
        if q.lower() not in seen:
            seen.add(q.lower())
            uniq.append(q)
    return uniq[:limit]


def for_topic(topic, wiki_title):
    """Search phrases around one topic, for titles, tags and descriptions."""
    base = [wiki_title, topic]
    queries = base + [f"{wiki_title} in hindi", f"{wiki_title} story", f"{wiki_title} की कहानी",
                      f"{wiki_title} रहस्य", f"{wiki_title} सच"]
    found = []
    for q in queries:
        found += suggest(q)
        time.sleep(0.2)
    seen, uniq = set(), []
    for q in found:
        if q.lower() not in seen:
            seen.add(q.lower())
            uniq.append(q)
    log.info("Keywords for %s: %d search phrases", wiki_title, len(uniq))
    return uniq[:40]
