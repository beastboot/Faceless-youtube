"""Reach: measure every video, keep them public, start conversations, repackage underperformers,
and write a report. Runs at the start of each daily job (python main.py --reach for just this)."""
import json
import os
import statistics
from datetime import datetime, timedelta, timezone

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from . import llm, state, youtube
from .common import ROOT, log

STATS = ROOT / "data" / "stats.json"
FOLLOWUPS = ROOT / "data" / "followups.json"   # topics queued from our own winners + viewer requests
REPLIED = ROOT / "data" / "replied.json"
INSIGHTS = ROOT / "data" / "insights.json"
REPORT = ROOT / "reports" / "reach-report.md"
STRATEGY = ROOT / "reports" / "strategy.md"


def _parse(ts):
    return datetime.fromisoformat(ts.replace("Z", "+00:00")) if ts else None


def _analytics(video_ids, since):
    """Watch time / retention / subs per video. Needs the yt-analytics.readonly scope; returns None
    (with a hint) if the token predates that scope or the YouTube Analytics API is not enabled.
    An empty dict means access works but YouTube hasn't processed data yet (it lags 1-3 days)."""
    try:
        api = build("youtubeAnalytics", "v2", credentials=youtube.credentials(),
                    cache_discovery=False)
        out = {}
        for i in range(0, len(video_ids), 200):
            ids = video_ids[i:i + 200]
            r = api.reports().query(
                ids="channel==MINE", startDate=since, endDate=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                metrics="views,estimatedMinutesWatched,averageViewDuration,averageViewPercentage,subscribersGained,shares",
                dimensions="video", filters="video==" + ",".join(ids), sort="-views",
                maxResults=200).execute()
            cols = [c["name"] for c in r.get("columnHeaders", [])]
            for row in r.get("rows", []):
                d = dict(zip(cols, row))
                out[d["video"]] = d
        return out
    except HttpError as e:
        log.warning("YouTube Analytics unavailable (%s). Enable 'YouTube Analytics API' in Google Cloud "
                    "and re-run scripts/get_youtube_token.py to add watch-time/retention to reports.",
                    e.status_code)
    except Exception as e:
        log.warning("YouTube Analytics unavailable: %s", e)
    return None


def _traffic_sources(since):
    try:
        api = build("youtubeAnalytics", "v2", credentials=youtube.credentials(),
                    cache_discovery=False)
        r = api.reports().query(ids="channel==MINE", startDate=since,
                                endDate=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                                metrics="views", dimensions="insightTrafficSourceType", sort="-views").execute()
        return [(row[0], int(row[1])) for row in r.get("rows", [])]
    except Exception:
        return []


def _repackage(cfg, row, stats_row):
    """New title for a video far below its peers. Title-only: works in CI where art is gone."""
    lang = row.get("lang", "en")
    fmt = row["format"]
    from .script_writer import REGIONAL, title_ok
    lang_name, writing = REGIONAL.get(lang, ("Hindi", "Devanagari") if lang == "hi" else ("English", "English"))
    prompt = f"""This {lang_name} YouTube {fmt} is underperforming:
title "{row['title']}", {stats_row['views']} views after {stats_row['age_h']:.0f} hours, while similar videos
on the channel get a median of {stats_row['peer_median']:.0f}. The topic is: {row.get('topic')}.
Write 5 alternative titles {'in Hindi (Devanagari, key English words allowed)' if lang == 'hi' else
'in English' if lang == 'en' else f'in {lang_name} ({writing}; only proper nouns in English letters; no other Indian script)'}
that are more specific and curiosity-driven: a concrete number, name or date; under 60 characters;
no clickbait lies; max two ALL-CAPS words.{' End with #shorts.' if fmt == 'short' else ''}
Return {{"titles": ["..."]}}"""
    titles = [t for t in llm.ask_json(cfg, "You are a YouTube packaging expert. JSON only.", prompt)
              .get("titles", []) if t and t.strip() and t.strip() != row["title"] and title_ok(t, lang)]
    if not titles:
        return None
    if lang == "en":
        from .quality import title as titlelint
        titles.sort(key=lambda t: -titlelint.check(t)["score"])
    new = titles[0].strip()[:100]
    yt = youtube.client()
    v = yt.videos().list(part="snippet", id=row["video_id"]).execute()["items"][0]
    snip = v["snippet"]
    snip["title"] = new
    yt.videos().update(part="snippet", body={"id": row["video_id"], "snippet": {
        k: snip[k] for k in ("title", "description", "tags", "categoryId", "defaultLanguage",
                             "defaultAudioLanguage") if k in snip}}).execute()
    return new


def _load_queue():
    return json.loads(FOLLOWUPS.read_text()) if FOLLOWUPS.exists() else []


def _save_queue(q):
    FOLLOWUPS.write_text(json.dumps(q[-40:], indent=1, ensure_ascii=False))


def double_down(table, actions):
    """A video far above its peers earns a follow-up: same subject, new angle or 'Part 2'."""
    q = _load_queue()
    done = {x.get("source_video") for x in q}
    queued = False
    for r, x in table:
        if not x or x["privacy"] != "public" or r.get("followed_up") or r["video_id"] in done \
                or not (24 <= x["age_h"] <= 120):
            continue
        peers = [y["views"] for q2, y in table if y and q2["format"] == r["format"] and q2["video_id"] != r["video_id"]]
        if len(peers) < 3:
            continue
        med = statistics.median(peers) or 1
        if x["views"] >= 2 * med and x["views"] >= 200:
            q.append({"kind": "winner", "format": "short", "series": r.get("series"),
                      "topic": f"{r.get('topic') or r['title']} — Part 2",
                      "angle": f"Follow-up to our hit video '{r['title']}' ({x['views']} views, {x['views'] / med:.1f}x "
                               f"our typical): a deeper or unseen side of the same story, referencing Part 1",
                      "source_video": r["video_id"]})
            actions.append(f"🔥 Winner ({x['views'] / med:.1f}x typical): queued a Part 2 of '{r['title'][:50]}'")
            # Remember it on the video itself: the queue entry is gone once used, and without this the
            # same hit was queued again every day (Windscale got three "Part 2"s).
            r["followed_up"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
            queued = True
    _save_queue(q)
    return queued


def collect_requests(cfg, texts, actions):
    """Topic requests in viewer comments -> the front of the topic queue."""
    if not texts:
        return
    data = llm.ask_json(cfg, "You extract viewer requests from YouTube comments. JSON only.", f"""
Comments on a Hindi history & mysteries channel:
{json.dumps(texts[:60], ensure_ascii=False)}
1) List the specific video topics viewers ASK for (e.g. "make a video on Bhangarh fort"), merged and counted.
2) Pick at most 2 genuine curious QUESTIONS about history/mysteries (e.g. "why was the Kailasa temple carved
   top-down?") with the asker's first name only (from "author") and an English topic for research.
Ignore praise, spam, abuse and vague text.
Return {{"requests": [{{"topic": "English topic name", "count": n}}],
 "questions": [{{"asker": "first name", "question": "question in the viewer's language", "topic": "English topic"}}]}}""",
                        temperature=0.2)
    q = _load_queue()
    have = {x["topic"].lower() for x in q}
    for qu in (data.get("questions") or [])[:2]:
        if qu.get("question") and qu.get("topic") and qu["topic"].lower() not in have:
            q.append({"kind": "question", "special": "question", "format": "short", "series": None,
                      "topic": qu["topic"], "asker": (qu.get("asker") or "a viewer").split()[0][:20],
                      "question": qu["question"][:200], "angle": "Answer a viewer's question"})
            actions.append(f"❓ Ask Itihaas Dada queued: {qu['question'][:80]}")
    for req in (data.get("requests") or [])[:5]:
        t = (req.get("topic") or "").strip()
        if t and t.lower() not in have:
            q.append({"kind": "request", "format": "long" if (req.get("count") or 1) >= 3 else "short",
                      "topic": t, "angle": f"Requested by {req.get('count') or 1} viewer(s) in comments", "series": None})
            actions.append(f"💬 Viewer request queued: {t} ({req.get('count') or 1}x)")
    _save_queue(q)


def reply_to_comments(cfg, actions):
    """Short, warm English replies to new viewer comments (skips spam/abuse). ~50 quota units each."""
    limit = cfg.get("reach", {}).get("max_replies_per_day", 10)
    if not limit:
        return
    yt = youtube.client()
    me = yt.channels().list(part="id", mine=True).execute()["items"][0]["id"]
    done = set(json.loads(REPLIED.read_text())) if REPLIED.exists() else set()
    threads = yt.commentThreads().list(part="snippet,replies", allThreadsRelatedToChannelId=me,
                                       maxResults=50, order="time", textFormat="plainText").execute()
    todo = []
    try:
        collect_requests(cfg, [{"author": t["snippet"]["topLevelComment"]["snippet"].get("authorDisplayName", ""),
                                "text": t["snippet"]["topLevelComment"]["snippet"]["textDisplay"][:300]}
                               for t in threads.get("items", [])
                               if (t["snippet"]["topLevelComment"]["snippet"].get("authorChannelId") or {}).get("value") != me],
                         actions)
    except Exception as e:
        log.warning("Comment requests failed: %s", e)
    for t in threads.get("items", []):
        top = t["snippet"]["topLevelComment"]["snippet"]
        author = (top.get("authorChannelId") or {}).get("value")
        replied_by_me = any((c["snippet"].get("authorChannelId") or {}).get("value") == me
                            for c in (t.get("replies") or {}).get("comments", []))
        if t["id"] in done or author == me or replied_by_me:
            continue
        todo.append({"id": t["id"], "video": t["snippet"].get("videoId"), "comment": top["textDisplay"][:500]})
        if len(todo) >= limit:
            break
    if not todo:
        return
    prompt = f"""You run the YouTube channel {cfg['channel']['name']} (animated history and mysteries,
mostly in Hindi). Write a reply IN ENGLISH to each viewer comment below.
Rules: 1-2 short sentences, warm and human, no hashtags, no links, at most one emoji. Thank them,
answer briefly if they ask something (say you're not sure rather than invent facts), and where natural
invite them to suggest the next mystery. If a comment is spam, abusive, sexual, political flame-bait,
or self-promotion, return "skip" for it.
COMMENTS: {json.dumps(todo, ensure_ascii=False)}
Return {{"replies": [{{"id": "...", "reply": "..." or "skip"}}]}}"""
    replies = llm.ask_json(cfg, "You are a friendly community manager. JSON only.", prompt, temperature=0.7)
    for r in replies.get("replies") or []:
        text = (r.get("reply") or "").strip()
        if r.get("id") not in {t["id"] for t in todo}:
            continue
        done.add(r["id"])
        if not text or text.lower() == "skip":
            continue
        try:
            yt.comments().insert(part="snippet", body={"snippet": {"parentId": r["id"],
                                                                   "textOriginal": text}}).execute()
            actions.append(f"Replied to a comment: {text[:60]}")
        except HttpError as e:
            log.warning("Reply failed: %s", e.status_code)
    REPLIED.write_text(json.dumps(sorted(done)))


def retention(video_id, since):
    """Retention curve -> hook hold at 30s and the biggest drop-offs mapped to the script line spoken."""
    tl_path = ROOT / "data" / "timelines" / f"{video_id}.json"
    if not tl_path.exists():
        return None
    tl = json.loads(tl_path.read_text())
    dur, timeline = tl.get("duration") or 0, tl.get("timeline") or []
    api = build("youtubeAnalytics", "v2", credentials=youtube.credentials(), cache_discovery=False)
    r = api.reports().query(ids="channel==MINE", startDate=since,
                            endDate=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                            metrics="audienceWatchRatio", dimensions="elapsedVideoTimeRatio",
                            filters=f"video=={video_id}").execute()
    pts = [(float(a), float(b)) for a, b in r.get("rows", [])]
    if len(pts) < 10 or not dur:
        return None

    def line_at(sec):
        cur = ""
        for st, text in timeline:
            if st <= sec:
                cur = text
        return cur

    hook = next((w for x, w in pts if x * dur >= 30), pts[-1][1])
    drops = sorted(((pts[i - 1][1] - pts[i][1], pts[i][0]) for i in range(2, len(pts))), reverse=True)[:3]
    return {"held_at_30s": round(hook, 2),
            "biggest_drops": [{"at": f"{int(x * dur) // 60}:{int(x * dur) % 60:02d}", "drop": round(d, 3),
                               "line": line_at(x * dur)[:140]} for d, x in drops if d > 0.02]}


def weekly_strategy(cfg, table, actions):
    """Once a week: what worked, what didn't, and concrete changes. Insights feed the topic picker."""
    # The date inside the file, not its mtime: a fresh GitHub checkout makes every file look new.
    import re
    m = re.search(r"Weekly strategy — (\d{4}-\d{2}-\d{2})", STRATEGY.read_text()) if STRATEGY.exists() else None
    if m and (datetime.now(timezone.utc) - datetime.fromisoformat(m.group(1)).replace(tzinfo=timezone.utc)).days < 7:
        return
    rows = [{"title": r["title"], "lang": r.get("lang", "en"), "format": r["format"],
             "topic": r.get("topic"), "thumbnail": r.get("thumb_template"),
             "age_hours": round(x["age_h"]), "views": x["views"],
             "avg_percent_watched": x["avg_pct"], "watch_minutes": x["watch_min"], "subs": x["subs"],
             "likes": x["likes"], "comments": x["comments"],
             "retention": x.get("retention")} for r, x in table if x]
    if len(rows) < 5:
        return
    prompt = f"""You are the growth strategist for a faceless YouTube channel (animated history and mysteries)
that now makes SHORTS ONLY (English main channel + a Tamil channel), aiming for monetization
(1,000 subscribers + 10 million Shorts views in 90 days). Older long videos and Hindi videos are listed
for context only. Here is every video with its performance:
{json.dumps(rows, ensure_ascii=False)}

Write a short weekly review: which topics, title styles and hooks worked (cite numbers), which
didn't, and 3-5 concrete changes for next week. Also write "telegram": the same review for the owner
on their phone, in simple Hinglish, under 900 characters, no markdown: top 3 Shorts with views and %
watched, what they had in common, what flopped and why, and what the channel will do differently. Where "retention" exists, use it: held_at_30s is the
share still watching at 0:30 (hook strength); biggest_drops show the exact line where viewers left —
say what about those lines made people leave and how scripts should change. Then give 3-6 one-line "insights" for the AI topic
picker (e.g. "Indian fort mysteries get 3x the views of European history").
Return {{"review_markdown": "...", "telegram": "...", "insights": ["..."]}}"""
    data = llm.ask_json(cfg, "You are a data-driven YouTube strategist. JSON only.", prompt, temperature=0.4)
    STRATEGY.write_text(f"# Weekly strategy — {datetime.now(timezone.utc):%Y-%m-%d}\n\n"
                        + (data.get("review_markdown") or "") + "\n")
    INSIGHTS.write_text(json.dumps(data.get("insights") or [], indent=1, ensure_ascii=False))
    actions.append("Wrote weekly strategy review (reports/strategy.md)")
    if data.get("telegram"):
        from . import notify
        notify.send("📈 Is hafte kya chala:\n\n" + data["telegram"].strip()[:1500]
                    + "\n\n(Topic picker ab yahi seekh kar agle hafte ke topics chunega.)")


def run(cfg):
    if not youtube.available():
        log.info("Reach: YouTube not configured, skipping")
        return
    rc = cfg.get("reach", {})
    rows = state.load()
    ours = [r for r in rows if r.get("video_id")]
    if not ours:
        log.info("Reach: no uploaded videos yet")
        return
    yt = youtube.client()
    now = datetime.now(timezone.utc)
    info = {}
    ids = [r["video_id"] for r in ours]
    for i in range(0, len(ids), 50):
        for v in yt.videos().list(part="status,statistics,snippet,contentDetails",
                                  id=",".join(ids[i:i + 50])).execute().get("items", []):
            info[v["id"]] = v
    since = min((_parse(r.get("created_utc")) or now) for r in ours).strftime("%Y-%m-%d")
    ana = _analytics(list(info), since)
    have_analytics = ana is not None
    ana = ana or {}

    actions, changed = [], False
    table = []
    for r in ours:
        v = info.get(r["video_id"])
        if not v:
            table.append((r, None))
            continue
        st, s = v["status"], v["statistics"]
        published = _parse(v["snippet"].get("publishedAt"))
        publish_at = _parse(st.get("publishAt"))
        # 1) Keep videos reachable: anything private with no future schedule gets made public.
        # (Not videos the owner chose to hide, e.g. the old Hindi uploads on the English channel.)
        if (rc.get("auto_publish_private", True) and st["privacyStatus"] == "private" and not r.get("hidden")
                and not (publish_at and publish_at > now) and st.get("uploadStatus") == "processed"):
            try:
                yt.videos().update(part="status", body={"id": r["video_id"], "status": {
                    "privacyStatus": "public", "selfDeclaredMadeForKids": False,
                    "embeddable": True, "license": "youtube"}}).execute()
                st["privacyStatus"] = "public"
                actions.append(f"Made public: {r['title']}")
            except HttpError as e:
                actions.append(f"Could not make public ({e.status_code}): {r['title']}")
        # 2) First comment once the video is public (comments are refused on private videos).
        if r.get("pending_comment") and st["privacyStatus"] == "public" and rc.get("post_comments", True):
            try:
                youtube.post_comment(r["video_id"], r["pending_comment"])
                actions.append(f"Posted question on: {r['title']}")
                r["pending_comment"] = ""
                changed = True
            except HttpError as e:
                log.warning("Comment failed on %s: %s", r["video_id"], e.status_code)
        # Copyright watch: a claim shows up as a regional block or a rejection reason.
        blocked = (v.get("contentDetails") or {}).get("regionRestriction", {}).get("blocked") or []
        reason = st.get("rejectionReason") or st.get("failureReason")
        if blocked or reason in ("copyright", "claim", "duplicate", "trademark", "termsOfUse"):
            actions.append(f"⚠️ COPYRIGHT/POLICY ALERT on {r['title']}: "
                           f"{reason or ''} {('blocked in ' + str(len(blocked)) + ' countries') if blocked else ''}"
                           " — check YouTube Studio > Content > Restrictions")
        a = ana.get(r["video_id"], {})
        row = {"views": int(s.get("viewCount", 0)), "likes": int(s.get("likeCount", 0)),
               "comments": int(s.get("commentCount", 0)), "privacy": st["privacyStatus"],
               "age_h": ((now - published).total_seconds() / 3600) if published else 0,
               "watch_min": a.get("estimatedMinutesWatched"), "avg_pct": a.get("averageViewPercentage"),
               "subs": a.get("subscribersGained"), "shares": a.get("shares")}
        table.append((r, row))

    # 3) Repackage clear underperformers (once each), judged against same format+language peers.
    min_age = rc.get("repackage_after_hours", 72)
    for r, row in table:
        if not row or r.get("repackaged") or row["privacy"] != "public" or row["age_h"] < min_age:
            continue
        peers = [x["views"] for q, x in table if x and q["format"] == r["format"]
                 and q.get("lang", "en") == r.get("lang", "en") and x["age_h"] >= min_age]
        if len(peers) < 4:
            continue
        row["peer_median"] = statistics.median(peers)
        if row["views"] < rc.get("repackage_below_median", 0.5) * row["peer_median"]:
            try:
                new = _repackage(cfg, r, row)
                if new:
                    actions.append(f"Retitled: {r['title']} → {new}")
                    r["repackaged"] = {"old": r["title"], "at": now.isoformat(timespec="seconds")}
                    r["title"] = new
                    changed = True
            except Exception as e:
                log.warning("Repackage failed for %s: %s", r["video_id"], e)

    if have_analytics:
        for r, x in table:
            if x and x["age_h"] >= 72 and x["views"] >= 30:
                try:
                    x["retention"] = retention(r["video_id"], since)
                except Exception as e:
                    log.debug("Retention for %s failed: %s", r["video_id"], e)
    from . import extras
    today = datetime.now(timezone.utc)
    dirty = []
    steps = [lambda: dirty.append(double_down(table, actions)), lambda: reply_to_comments(cfg, actions),
             lambda: weekly_strategy(cfg, table, actions)]
    if today.strftime("%a") == "Mon":
        steps.append(lambda: extras.community_post(cfg))
    if today.day == 1:
        steps.append(lambda: extras.monthly_ebook(cfg))
    for step in steps:
        try:
            step()
        except Exception as e:
            log.warning("Reach step failed: %s", e)

    # 4) Thumbnail A/B: once each template has 3+ long videos older than 3 days, record the winner.
    arms = {}
    for r, x in table:
        if x and r["format"] == "long" and r.get("thumb_template") and x["age_h"] >= 72:
            arms.setdefault(r["thumb_template"], []).append(x["views"])
    if len(arms) >= 2 and all(len(v) >= 3 for v in arms.values()):
        med = {k: statistics.median(v) for k, v in arms.items()}
        winner = max(med, key=med.get)
        (ROOT / "data" / "thumb_winner.json").write_text(json.dumps({"winner": winner, "median_views": med}))
        actions.append(f"Thumbnail A/B: '{winner}' leads ({', '.join(f'{k} {v:.0f}' for k, v in med.items())} median views)")

    if changed or any(dirty):
        state.HISTORY.write_text(json.dumps(rows, indent=1, ensure_ascii=False))
    STATS.write_text(json.dumps({r["video_id"]: row for r, row in table if row}, indent=1))
    write_report(table, actions, _traffic_sources(since) if have_analytics else [], have_analytics)
    telegram_summary(table, actions, have_analytics)
    record_daily(table)
    for a in actions:
        log.info("Reach: %s", a)


def record_daily(table):
    """One row per day for the growth dashboard (docs/dashboard)."""
    path = ROOT / "data" / "daily.json"
    rows = json.loads(path.read_text()) if path.exists() else []
    try:
        ch = youtube.client().channels().list(part="statistics", mine=True).execute()["items"][0]["statistics"]
    except Exception:
        ch = {}
    vids = [x for _, x in table if x]
    today = {"date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
             "subscribers": int(ch.get("subscriberCount", 0) or 0),
             "views": sum(x["views"] for x in vids),
             "watch_hours": round(sum((x["watch_min"] or 0) for x in vids) / 60, 2),
             "public_videos": sum(1 for x in vids if x["privacy"] == "public")}
    rows = [r for r in rows if r["date"] != today["date"]] + [today]
    path.write_text(json.dumps(rows[-400:], indent=1))


def telegram_summary(table, actions, have_analytics):
    """Short analytics report for the phone."""
    from . import notify
    rows = sorted([(r, x) for r, x in table if x], key=lambda t: -t[1]["views"])
    views = sum(x["views"] for _, x in rows)
    mins = sum((x["watch_min"] or 0) for _, x in rows)
    subs = sum((x["subs"] or 0) for _, x in rows)
    try:
        ch = youtube.client().channels().list(part="statistics", mine=True).execute()["items"][0]["statistics"]
    except Exception:
        ch = {}
    lines = ["📈 Odd Annals analytics",
             f"Subscribers: {ch.get('subscriberCount', '?')} / 1,000",
             f"Watch hours: {mins / 60:,.1f} / 4,000" + ("" if have_analytics else " (analytics pending)"),
             f"Views (all videos): {views:,}" + (f" · subs gained: {subs}" if subs else ""),
             f"Public videos: {sum(1 for _, x in rows if x['privacy'] == 'public')}"]
    if rows:
        lines.append("\nTop videos:")
        for r, x in rows[:5]:
            pct = f", {x['avg_pct']:.0f}% watched" if x.get("avg_pct") is not None else ""
            lines.append(f"• {x['views']:,} views{pct} — {r['title'][:55]}\n  https://youtu.be/{r['video_id']}")
    if actions:
        lines.append("\nActions today:\n" + "\n".join(f"• {a[:120]}" for a in actions[:8]))
    notify.send("\n".join(lines))


def write_report(table, actions, sources, have_analytics):
    REPORT.parent.mkdir(exist_ok=True)
    rows = sorted([(r, x) for r, x in table if x], key=lambda t: -t[1]["views"])
    total_views = sum(x["views"] for _, x in rows)
    total_min = sum((x["watch_min"] or 0) for _, x in rows)
    lines = [f"# 📊 Odd Annals reach report — {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC", "",
             f"**{len(rows)} videos · {total_views:,} views"
             + (f" · {total_min / 60:,.1f} watch hours** (monetization needs 4,000)" if have_analytics else "**"),
             ""]
    if not have_analytics:
        lines += ["> Watch time, retention and subscriber data need the YouTube Analytics API — see README.", ""]
    elif not any(x["watch_min"] is not None for _, x in rows):
        lines += ["> Analytics connected. YouTube reports watch time 1-3 days late, so new videos show – for now.", ""]
    lines += ["| Video | Lang | Type | Views | Likes | Comments | Avg % watched | Watch min | Subs | Status |",
              "|---|---|---|---:|---:|---:|---:|---:|---:|---|"]
    for r, x in rows:
        url = f"https://youtu.be/{r['video_id']}"
        pct = f"{x['avg_pct']:.0f}%" if x["avg_pct"] is not None else "–"
        wm = f"{x['watch_min']:.0f}" if x["watch_min"] is not None else "–"
        subs = x["subs"] if x["subs"] is not None else "–"
        title = r["title"].replace("|", "/")[:60]
        lines.append(f"| [{title}]({url}) | {r.get('lang', 'en')} | {r['format']} | {x['views']:,} | "
                     f"{x['likes']} | {x['comments']} | {pct} | {wm} | {subs} | {x['privacy']} |")
    if sources:
        lines += ["", "**Where views come from:** " + ", ".join(f"{k} {v:,}" for k, v in sources[:8])]
    lines += ["", "## Actions taken today", *(f"- {a}" for a in actions or ["Nothing needed."])]
    text = "\n".join(lines) + "\n"
    REPORT.write_text(text)
    if os.environ.get("GITHUB_STEP_SUMMARY"):  # shows on the Actions run page
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as f:
            f.write(text)
    log.info("Reach report: %s", REPORT)
