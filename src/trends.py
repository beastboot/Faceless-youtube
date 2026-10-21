"""Find trending, niche-fit topics and let the LLM pick today's videos."""
import html
import json
import random
import re
from datetime import date, datetime, timedelta, timezone

from . import keywords, llm, occasions, state, youtube
from .common import http, log

SKIP_PREFIXES = ("Special:", "Main_Page", "Wikipedia:", "File:", "Portal:", "Help:", "Category:",
                 "Template:", "Talk:", "User:", "Deaths_in_", "List_of_")


def wiki_on_this_day(session):
    now = datetime.now(timezone.utc)
    url = f"https://en.wikipedia.org/api/rest_v1/feed/onthisday/events/{now:%m}/{now:%d}"
    events = session.get(url, timeout=30).json().get("events", [])
    out = []
    for e in events:
        pages = [p.get("title", "").replace("_", " ") for p in e.get("pages", [])[:2]]
        out.append(f"{e.get('year')}: {e.get('text')} [wiki: {', '.join(pages)}]")
    return out


def wiki_most_viewed(session):
    for back in (1, 2, 3):
        d = datetime.now(timezone.utc) - timedelta(days=back)
        url = ("https://wikimedia.org/api/rest_v1/metrics/pageviews/top/en.wikipedia/"
               f"all-access/{d:%Y/%m/%d}")
        r = session.get(url, timeout=30)
        if r.ok:
            arts = r.json()["items"][0]["articles"]
            return [f"{a['article'].replace('_', ' ')} ({a['views']:,} views)"
                    for a in arts if not a["article"].startswith(SKIP_PREFIXES)][:120]
    return []


def wiki_featured(session):
    """Today's featured article, 'Did you know...' facts and news from Wikipedia's main page."""
    d = datetime.now(timezone.utc)
    j = session.get(f"https://en.wikipedia.org/api/rest_v1/feed/featured/{d:%Y/%m/%d}",
                    timeout=30).json()
    out = []
    if j.get("tfa"):
        out.append(f"FEATURED ARTICLE: {j['tfa'].get('titles', {}).get('normalized')} — "
                   f"{j['tfa'].get('extract', '')[:200]}")
    out += [f"DID YOU KNOW: {_strip(x.get('html', ''))}" for x in j.get("dyk", [])]
    out += [f"NEWS: {_strip(x.get('story', ''))}" for x in j.get("news", [])]
    return out


def _strip(html):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", html)).strip()


def rss_titles(session, url, limit=40):
    r = session.get(url, timeout=30)
    r.raise_for_status()
    titles = re.findall(r"<title>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>", r.text, re.S)
    return [html.unescape(t.strip()) for t in titles[1:limit + 1]]  # first is the feed title


def gather_candidates(cfg=None):
    s = http()
    reddit = "https://www.reddit.com/r/{}/top/.rss?t={}"
    sources = {
        "wikipedia_featured_and_did_you_know": lambda: wiki_featured(s),
        "wikipedia_on_this_day": lambda: wiki_on_this_day(s),
        "wikipedia_most_viewed_yesterday": lambda: wiki_most_viewed(s),
        "google_trends_india": lambda: rss_titles(s, "https://trends.google.com/trending/rss?geo=IN"),
        "youtube_searches_hindi": lambda: keywords.niche_searches(cfg) if cfg else [],
        "reddit_todayilearned": lambda: rss_titles(s, reddit.format("todayilearned", "day")),
        "reddit_unresolvedmysteries": lambda: rss_titles(s, reddit.format("UnresolvedMysteries", "week"), 25),
        "reddit_historyporn": lambda: rss_titles(s, reddit.format("HistoryPorn", "week"), 25),
    }
    out = {}
    for name, fn in sources.items():
        try:
            out[name] = fn()
            log.info("Trend source %s: %d items", name, len(out[name]))
        except Exception as e:  # any single source may be down or rate-limited
            log.warning("Trend source %s failed: %s", name, e)
    return out


def channel_feedback():
    """Our own uploads with views (+ retention when Analytics is enabled), best first."""
    rows = [r for r in state.load()[-60:] if r.get("video_id")]
    if not rows:
        return []
    stats_path = state.HISTORY.parent / "stats.json"
    stats = json.loads(stats_path.read_text()) if stats_path.exists() else {}
    if not stats and youtube.available():
        try:
            stats = {k: {"views": v} for k, v in youtube.video_stats([r["video_id"] for r in rows]).items()}
        except Exception as e:
            log.warning("Could not fetch own stats: %s", e)
    perf = []
    for r in rows:
        s = stats.get(r["video_id"], {})
        perf.append({"format": r["format"], "lang": r.get("lang", "en"), "title": r["title"],
                     "views": s.get("views", 0), "avg_percent_watched": s.get("avg_pct")})
    return sorted(perf, key=lambda r: -r["views"])


def _base(topic):
    """'X — Part 2' and 'x' are the same story."""
    return re.sub(r"\s*[—-]+\s*part\s*\d+\s*$", "", (topic or "").strip().lower()).strip(" —-")


def recent_winners(n=5):
    path = state.HISTORY.parent / "winners.json"
    try:
        return json.loads(path.read_text())[-n:] if path.exists() else []
    except ValueError:
        return []


def recent_topics(days=7):
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    return {_base(r.get("topic")) for r in state.load()
            if r.get("video_id") and (r.get("created_utc") or "") >= since} - {""}


def pick_topics(cfg, n_long, n_short, spares=2):
    """Returns ranked candidates: n + spares per format, best first, so a topic whose
    Wikipedia article turns out too thin can be replaced without a second LLM call."""
    want_long = n_long + spares if n_long else 0
    want_short = n_short + max(spares, n_short // 2 + 2) if n_short else 0   # the quality gate rejects some
    cands = gather_candidates(cfg)
    for k in cands:
        random.shuffle(cands[k])
    outliers, feedback = [], channel_feedback()
    if youtube.available():
        try:
            outliers = youtube.competitor_outliers(cfg)
            log.info("Competitor outliers found: %d", len(outliers))
        except Exception as e:
            log.warning("Competitor research failed: %s", e)

    ch = cfg["channel"]
    system = ("You are the head of content strategy for a faceless YouTube channel. You pick topics "
              "that are factual, researchable on Wikipedia, and have high click-through and watch-time "
              "potential. Reply with JSON only.")
    prompt = f"""Channel niche: {ch['niche']}
Audience: {ch['audience']}

Pick {want_long} topic(s) for LONG videos (8-12 minutes; needs a rich story with many verifiable details,
a narrative arc, tension) and {want_short} topic(s) for SHORTS (45-60 seconds; one jaw-dropping,
verifiable fact or moment that can be told fast).

Rules:
- Every topic must fit the niche. Ignore celebrities, sports, politics-of-today and gossip.
- SENSITIVITY (hard rule): never pick topics that mock, sexualise, or question the sacred figures,
  scriptures or beliefs of ANY religion (blasphemy cases, "Jesus/Prophet/god had a lover" etc.), and
  no caste or Hindu-Muslim/communal controversies. Respectful history of temples, festivals, saints
  and religious architecture is fine.
- Prefer topics that are trending today (the candidates below) when they fit; otherwise choose a strong
  evergreen story the candidates point to.
- Each topic must map to one existing English Wikipedia article title that holds the facts.
- Never repeat or closely resemble a topic we already covered (list below).
- All {want_long + want_short} topics must be different from each other. List the strongest first.
- For LONG topics, the Wikipedia article must be long and detailed (a major article, not a stub).
- Learn from the competitor outliers (videos that beat their own channel's median views by the multiple
  shown): borrow the angle and framing patterns, never copy the title.
- Learn from our own channel performance: do more of what got views AND high average % watched.
- The channel is in ENGLISH for a worldwide English-speaking audience (India, US, UK). Mix about half
  Indian stories (temples, forts, kings, unsolved Indian places) and half world stories.
- WHAT WORKS ON OUR CHANNEL (our Shorts data): our best Short by far (219 views, 79% watched) was
  "Windscale 1957: The Mocked Filters That Saved Britain" - a dramatic REAL event with a year, and a
  "nobody believed them / they were mocked, then it saved everyone" twist. Next best: impossible-seeming
  objects and places ("a bridge 7000 years old", "a pillar that floats", "bricks that float").
  Prefer: real disasters and near-disasters, cover-ups and ignored warnings, lone people proven right,
  things that "shouldn't exist", survival against the odds - each with a specific year, number or place.
  Avoid: vague legends with no checkable facts, slow biographies, topics that need a lot of background.
- Use the youtube_searches candidates as evidence of what people search for right now.
- RIDE THE NEWS: at least ONE Short must hook onto something people are searching for THIS WEEK (a
  trending candidate below, today's news, an anniversary in the next days) through a real historical
  story it connects to (e.g. a new archaeological find -> the mystery it reopens; a storm in the news ->
  the deadliest storm in history). Say the connection in "why".

TRENDING CANDIDATES:
{json.dumps({k: v[:40] for k, v in cands.items()}, ensure_ascii=False, indent=0)}

COMPETITOR OUTLIERS (multiple of own-channel median):
{json.dumps(outliers, ensure_ascii=False, indent=0) if outliers else "none available"}

WEEKLY STRATEGY INSIGHTS (follow these):
{json.dumps(json.loads((state.HISTORY.parent / "insights.json").read_text()) if (state.HISTORY.parent / "insights.json").exists() else [], ensure_ascii=False)}

OUR CHANNEL PERFORMANCE (most viewed first):
{json.dumps(feedback[:15], ensure_ascii=False, indent=0) if feedback else "no data yet (new channel)"}

ALREADY COVERED (do not repeat):
{json.dumps(state.used_topics()[-150:], ensure_ascii=False)}

UPCOMING OCCASIONS IN INDIA (search interest peaks around these; a related video published 2-5 days
early catches the wave). Also consider major Hindu/Sikh/Muslim/Jain festivals and historical
anniversaries you know fall in the next 12 days from {date.today():%d %B %Y}:
{json.dumps(occasions.upcoming(), ensure_ascii=False)}
If one fits the niche, make at least one topic about the history or mystery behind it.

SHORTS SERIES (our named shows; file every SHORT under exactly one, by id, and prefer topics that fit one
clearly - a recognisable show is what turns viewers into subscribers). About TWO-THIRDS of the Shorts go
to "impossible" (Things That Shouldn't Exist): impossible-seeming objects and places are our best
performers on BOTH channels (Krishna's Butterball, a 250-ton rock that won't roll: 669 English + 952 Tamil
views, our best ever; hanging pillar 350; floating-stone pillar 163). Real, visitable, photogenic
objects with one measurable impossible fact work best:
{json.dumps([dict(id=x["id"], name=x["name_en"], about=x["about"]) for x in cfg.get("short_series", [])], ensure_ascii=False)}

RECENT WINNERS (our Shorts that beat our usual views 2x+; make at least 2 of today's Short topics follow
the SAME PATTERN as the newest one - same kind of story and twist - but a DIFFERENT real story):
{json.dumps(recent_winners(), ensure_ascii=False) if recent_winners() else "none yet"}

SERIES (assign every topic to exactly one, by id):
{json.dumps([dict(id=x["id"], about=x["about"]) for x in cfg.get("series", [])], ensure_ascii=False)}

Return:
{{"topics": [{{"format": "long" | "short", "topic": "short topic name", "series": "series id",
  "short_series": "shorts series id (shorts only)",
  "wikipedia_title": "Exact Wikipedia article title", "angle": "the specific hook/angle for this video",
  "why": "one line: why this will get views"}}]}}"""
    data = llm.ask_json(cfg, system, prompt)
    # Some fallback models return the list itself instead of {"topics": [...]}.
    topics = data if isinstance(data, list) else data.get("topics", [])
    topics = [t for t in topics if isinstance(t, dict) and t.get("topic")]
    indian = {x["id"] for x in cfg.get("series", []) if x["id"] in ("mandir", "pauranik", "bharat")}

    recent = recent_topics(days=7)

    def ranked(items, n):
        """Indian and world topics alternate (the English channel's audience is worldwide); anything
        covered in the last 7 days is dropped (follow-ups of our own hits come from the queue below)."""
        items = [t for t in items if _base(t.get("topic")) not in recent
                 and _base(t.get("wikipedia_title")) not in recent]
        ind = [t for t in items if t.get("series") in indian]
        world = [t for t in items if t.get("series") not in indian]
        out = []
        for k in range(max(len(ind), len(world))):
            out += ind[k:k + 1] + world[k:k + 1]
        return out

    longs = ranked([t for t in topics if t.get("format") == "long"], n_long)[:want_long]
    shorts = ranked([t for t in topics if t.get("format") == "short"], n_short)[:want_short]
    # Our own winners' follow-ups and viewer requests jump the queue (consumed once used).
    qpath = state.HISTORY.parent / "followups.json"
    queue = json.loads(qpath.read_text()) if qpath.exists() else []
    now = datetime.now(timezone.utc).isoformat()
    if queue:
        for fmt, lst, n in (("long", longs, n_long), ("short", shorts, n_short)):
            ready = [x for x in queue if x.get("format") == fmt and (x.get("not_before") or "") <= now]
            picked = ready[:1] if n else []  # at most 1 per format per run
            for x in picked:
                lst.insert(0, {**{k: v for k, v in x.items() if k not in ("kind", "not_before")},
                               "format": fmt, "angle": x.get("angle", ""),
                               "wikipedia_title": x.get("wikipedia_title") or x["topic"].split(" — ")[0]})
                queue.remove(x)
                log.info("Queued %s topic first: %s (%s)", fmt, x["topic"], x.get("kind"))
        qpath.write_text(json.dumps(queue, indent=1, ensure_ascii=False))
    # Weekly formats: a quiz Short on the quiz day, a two-part long story on the two-part day.
    fx = cfg.get("formats") or {}
    day = datetime.now(timezone.utc).strftime("%a").lower()
    if shorts and day == fx.get("quiz_day") and not any(t.get("special") for t in shorts):
        target = next((t for t in shorts if t.get("series") in ("mandir", "bharat", "pauranik", "duniya")), shorts[-1])
        target["special"] = "quiz"
    if longs and day == fx.get("two_part_day") and not longs[0].get("special"):
        longs[0]["special"] = "part1"
        longs[0]["topic"] = longs[0]["topic"] + " — Part 1"
    # Daily two-part Short: the best fresh story is split in two, both parts go public together, and
    # Part 1 ends on "Part 2 is already on my channel" (viewers tap through, and subscribe for more).
    if fx.get("short_two_part_daily") and len(shorts) > 1:
        t = next((t for t in shorts if not t.get("special") and "part" not in t["topic"].lower()), None)
        if t:
            t["special"] = "short_part1"
            t["topic"] = t["topic"] + " — Part 1"
    for t in longs + shorts:
        log.info("Picked %-5s: %s — %s", t["format"], t["topic"], t.get("angle", ""))
    return longs, shorts
