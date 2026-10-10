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


def for_topic(topic, wiki_title, hi_title=None, lang="hi"):
    """Search phrases around one topic, most-searched first, for titles, tags and descriptions.

    Autocomplete lists the most popular completions first, so a phrase scores higher the more seed
    queries return it and the nearer the top it appears. Seeds cover Hindi, Hinglish (Roman Hindi,
    which most Indians type) and English, since all three bring search traffic."""
    name = wiki_title.split("(")[0].strip()
    seeds = [name, topic, f"{name} in hindi", f"{name} story", f"{name} history", f"{name} mystery",
             f"{name} ka rahasya", f"{name} ki kahani", f"{name} kya hai", f"{name} ka sach",
             f"{name} की कहानी", f"{name} रहस्य", f"{name} सच"]
    if hi_title and hi_title != name:
        seeds += [hi_title, f"{hi_title} का रहस्य", f"{hi_title} की कहानी", f"{hi_title} इतिहास"]
    score, first_seen = {}, {}
    for q in dict.fromkeys(seeds):
        for rank, phrase in enumerate(suggest(q, lang)):
            k = phrase.strip().lower()
            if not k:
                continue
            score[k] = score.get(k, 0) + 1.0 / (1 + rank * 0.35)
            first_seen.setdefault(k, phrase.strip())
        time.sleep(0.2)
    ranked = [first_seen[k] for k in sorted(score, key=score.get, reverse=True)]
    log.info("Keywords for %s: %d search phrases; top: %s", wiki_title, len(ranked), ", ".join(ranked[:5]))
    return ranked[:40]


def primary(phrases, names):
    """The single phrase to build the title and description around: the most-searched phrase that
    names the subject (falls back to the subject's name)."""
    keys = [n.lower() for n in names if n]
    for p in phrases:
        if any(k.split()[0] in p.lower() for k in keys if k.split()):
            return p
    return names[0] if names else (phrases[0] if phrases else "")
