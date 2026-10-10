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
                dimensions="video", filters="video==" + ",".join(ids), maxResults=200).execute()
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
    prompt = f"""This {'Hindi' if lang == 'hi' else 'English'} YouTube {fmt} is underperforming:
title "{row['title']}", {stats_row['views']} views after {stats_row['age_h']:.0f} hours, while similar videos
on the channel get a median of {stats_row['peer_median']:.0f}. The topic is: {row.get('topic')}.
Write 5 alternative titles {'in Hindi (Devanagari, key English words allowed)' if lang == 'hi' else 'in English'}
that are more specific and curiosity-driven: a concrete number, name or date; under 60 characters;
no clickbait lies; max two ALL-CAPS words.{' End with #shorts.' if fmt == 'short' else ''}
Return {{"titles": ["..."]}}"""
    titles = [t for t in llm.ask_json(cfg, "You are a YouTube packaging expert. JSON only.", prompt)
              .get("titles", []) if t and t.strip() and t.strip() != row["title"]]
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


def weekly_strategy(cfg, table, actions):
    """Once a week: what worked, what didn't, and concrete changes. Insights feed the topic picker."""
    if STRATEGY.exists() and (datetime.now().timestamp() - STRATEGY.stat().st_mtime) < 6.5 * 86400:
        return
    rows = [{"title": r["title"], "lang": r.get("lang", "en"), "format": r["format"],
             "topic": r.get("topic"), "thumbnail": r.get("thumb_template"),
             "age_hours": round(x["age_h"]), "views": x["views"],
             "avg_percent_watched": x["avg_pct"], "watch_minutes": x["watch_min"], "subs": x["subs"],
             "likes": x["likes"], "comments": x["comments"]} for r, x in table if x]
    if len(rows) < 5:
        return
    prompt = f"""You are the growth strategist for a faceless Hindi YouTube channel (animated history
and mysteries) aiming for monetization (1,000 subscribers + 4,000 watch hours). Here is every video
with its performance:
{json.dumps(rows, ensure_ascii=False)}

Write a short weekly review: which topics, title styles and formats worked (cite numbers), which
didn't, and 3-5 concrete changes for next week. Then give 3-6 one-line "insights" for the AI topic
picker (e.g. "Indian fort mysteries get 3x the views of European history").
Return {{"review_markdown": "...", "insights": ["..."]}}"""
    data = llm.ask_json(cfg, "You are a data-driven YouTube strategist. JSON only.", prompt, temperature=0.4)
    STRATEGY.write_text(f"# Weekly strategy — {datetime.now(timezone.utc):%Y-%m-%d}\n\n"
                        + (data.get("review_markdown") or "") + "\n")
    INSIGHTS.write_text(json.dumps(data.get("insights") or [], indent=1, ensure_ascii=False))
    actions.append("Wrote weekly strategy review (reports/strategy.md)")


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
        if (rc.get("auto_publish_private", True) and st["privacyStatus"] == "private"
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

    for step in (lambda: reply_to_comments(cfg, actions), lambda: weekly_strategy(cfg, table, actions)):
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

    if changed:
        state.HISTORY.write_text(json.dumps(rows, indent=1, ensure_ascii=False))
    STATS.write_text(json.dumps({r["video_id"]: row for r, row in table if row}, indent=1))
    write_report(table, actions, _traffic_sources(since) if have_analytics else [], have_analytics)
    for a in actions:
        log.info("Reach: %s", a)


def write_report(table, actions, sources, have_analytics):
    REPORT.parent.mkdir(exist_ok=True)
    rows = sorted([(r, x) for r, x in table if x], key=lambda t: -t[1]["views"])
    total_views = sum(x["views"] for _, x in rows)
    total_min = sum((x["watch_min"] or 0) for _, x in rows)
    lines = [f"# Odd Annals reach report — {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC", "",
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
