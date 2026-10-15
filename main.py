#!/usr/bin/env python3
"""Daily run: pick trending topics, then make and upload long videos + Shorts.

    python main.py                      # use counts from config.yaml, upload to YouTube
    python main.py --longs 0 --shorts 1 --no-upload    # local test, nothing published
    python main.py --topic "Dyatlov Pass incident" --format short --no-upload
    python main.py --upload-dir output/<run-folder>     # retry the upload of a rendered video
    python main.py --reach                             # reach report + keep-public + comments only
"""
import argparse
import json
import os
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

from src import notify, pipeline, reach, state, trends, youtube
from src.common import load_config, log, setup_logging


def release_reserve(cfg, reason):
    """Publish one private reserve video so the channel never misses a day."""
    reserve = [r for r in state.load() if r.get("reserve") and r.get("video_id")]
    if not reserve or not youtube.available():
        return None
    yt = youtube.client()
    st = {v["id"]: v["status"]["privacyStatus"] for v in yt.videos().list(
        part="status", id=",".join(r["video_id"] for r in reserve)).execute().get("items", [])}
    for r in reserve:
        if st.get(r["video_id"]) == "private":
            yt.videos().update(part="status", body={"id": r["video_id"], "status": {
                "privacyStatus": "public", "selfDeclaredMadeForKids": False, "embeddable": True,
                "license": "youtube"}}).execute()
            notify.send(f"🛟 Reserve video published ({reason}): {r['title']}\nhttps://youtu.be/{r['video_id']}")
            log.info("Released reserve video %s (%s)", r["video_id"], reason)
            return r["video_id"]
    return None


def announce_live(cfg, window_minutes=65):
    """Telegram 'now live' for scheduled videos whose publish time passed in the last hour.
    Stateless: run hourly, each video is announced once (its publish time falls in one window)."""
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)
    due = []
    for r in state.load():
        at = r.get("publish_at")
        if not (at and r.get("video_id")):
            continue
        t = datetime.fromisoformat(at.replace("Z", "+00:00"))
        if now - timedelta(minutes=window_minutes) < t <= now:
            due.append(r)
    # Safety net: no new public video for 30+ hours (failed or late run) -> release a reserve video.
    rows = [r for r in state.load() if r.get("video_id") and not r.get("reserve")]
    def went_public(r):
        return r.get("publish_at") or r.get("created_utc") or ""
    latest = max((went_public(r) for r in rows), default="")
    if latest:
        last = datetime.fromisoformat(latest.replace("Z", "+00:00"))
        if now - last > timedelta(hours=30):
            release_reserve(cfg, "no new video for 30+ hours")
    if not due or not youtube.available():
        return
    items = youtube.client().videos().list(part="status,statistics",
                                           id=",".join(r["video_id"] for r in due)).execute().get("items", [])
    info = {v["id"]: v for v in items}
    for r in due:
        v = info.get(r["video_id"])
        if v and v["status"]["privacyStatus"] == "public":
            notify.send(f"🌐 Live now: {r['title']}\nhttps://youtu.be/{r['video_id']}\n"
                        f"👀 {v['statistics'].get('viewCount', '0')} views so far")
        elif v:
            notify.send(f"⚠️ Should be live but is {v['status']['privacyStatus']}: {r['title']}\n"
                        f"https://youtu.be/{r['video_id']}")


def send_report(report, failures, upload):
    """Telegram summary: every video with link + publish time, failures, and the 3 thumbnail
    options of each long video for YouTube Studio's Test & Compare."""
    import json
    lines = [f"📊 Run finished: {len([r for r in report if r[2] or not upload])} video(s) made, "
             f"{len(failures)} failure(s)"]
    for fmt, lang, vid, work in report:
        try:
            meta = json.loads((work / "metadata.json").read_text())
        except Exception:
            meta = {}
        when = next((r.get("publish_at") for r in state.load() if r.get("video_id") == vid and vid), None)
        link = f"https://youtu.be/{vid}" if vid else "(test render, not uploaded)"
        lines.append(f"• {fmt}/{lang}: {meta.get('title', work.name)}\n  {link}" + (f"\n  ⏰ public at {when} UTC" if when else ""))
    if failures:
        lines.append("⚠️ Failed:\n" + "\n".join(failures[:6]))
    notify.send("\n".join(lines))
    for fmt, lang, vid, work in report:
        if fmt == "long":
            thumbs = sorted(str(p) for p in work.glob("thumbnail-*.jpg"))
            notify.send_photos(thumbs, "Thumbnail options for YouTube Studio → Test & compare")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--longs", type=int)
    ap.add_argument("--shorts", type=int)
    ap.add_argument("--no-upload", action="store_true", help="render only, do not publish")
    ap.add_argument("--topic", help="skip trend picking and make this topic")
    ap.add_argument("--format", choices=["long", "short"], default="short")
    ap.add_argument("--reach", action="store_true", help="only run the reach report/actions")
    ap.add_argument("--watch", action="store_true", help="announce videos that went public in the last hour")
    ap.add_argument("--compilation", action="store_true", help="build the weekly Shorts compilation now")
    ap.add_argument("--upload-dir", help="upload an already-rendered output/<run> folder")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    load_dotenv()
    setup_logging(args.verbose)
    cfg = load_config()
    sc = cfg["schedule"]
    upload = not args.no_upload
    if upload and not youtube.available():
        log.error("YT_CLIENT_ID / YT_CLIENT_SECRET / YT_REFRESH_TOKEN missing. "
                  "Run scripts/get_youtube_token.py or use --no-upload.")
        return 2

    timer = os.environ.get("GITHUB_EVENT_NAME") == "schedule" or os.environ.get("AUTO_RUN") == "true"
    if timer and not (args.reach or args.watch or args.compilation):
        # GitHub sometimes fires the daily timer very late; if today's videos were already made by a
        # manual run in the meantime, don't make a second batch.
        today = datetime.now(timezone.utc).strftime("%Y%m%d")
        if any(r.get("video_id") and str(r.get("dir", "")).startswith(today) for r in state.load()):
            log.info("Today's videos were already made by an earlier run; nothing to do")
            return 0

    if args.watch:
        announce_live(cfg)
        return 0

    if args.reach or (upload and not args.upload_dir):
        try:
            reach.run(cfg)  # measure + fix yesterday's videos before making today's
        except Exception as e:
            log.warning("Reach step failed: %s", e)
        if args.reach:
            return 0

    if args.upload_dir:
        work = Path(args.upload_dir)
        fmt = "short" if "-short-" in work.name else "long"
        vid = pipeline.upload_existing(cfg, work, fmt)
        log.info("DONE %s https://youtu.be/%s", fmt, vid)
        return 0

    from datetime import datetime as _dt
    comp = cfg.get("compilation") or {}
    sunday = comp.get("enabled") and _dt.utcnow().strftime("%a").lower() == comp.get("day", "sun")
    if args.compilation or (sunday and upload and not args.topic):
        from src import compilation
        from src.common import ROOT
        for lang, lc in cfg["languages"].items():   # one compilation per live channel, in its language
            if not lc.get("enabled"):
                continue
            try:
                youtube.use_channel(lc.get("youtube_env", ""))
                vid, work = compilation.build(cfg, ROOT / "output" / f"weekly-{_dt.utcnow():%Y%m%d}-{lang}",
                                              upload=upload, lang=lang)
                notify.send(f"📼 Weekly compilation ({lang}) done: https://youtu.be/{vid}" if vid
                            else f"📼 Weekly compilation ({lang}) rendered (test)")
            except Exception as e:
                log.error("Weekly compilation (%s) failed: %s", lang, e)
                notify.send(f"❌ Weekly compilation ({lang}) failed: {str(e)[:300]}")
            finally:
                youtube.use_channel("")
        if args.compilation:
            return 0

    if args.topic:
        targets = {args.format: 1}
        queues = {args.format: [{"topic": args.topic, "wikipedia_title": args.topic, "angle": ""}]}
    else:
        targets = {"long": sc["longs_per_run"] if args.longs is None else args.longs,
                   "short": sc["shorts_per_run"] if args.shorts is None else args.shorts}
        if sunday and upload and args.shorts is None:
            targets["short"] = max(0, targets["short"] - 1)   # the compilation takes that upload slot
        longs, shorts = trends.pick_topics(cfg, targets["long"], targets["short"])
        queues = {"long": longs, "short": shorts}

    langs_cfg = cfg["languages"]
    primary = [l for l, c in langs_cfg.items() if c.get("enabled") and "longs_per_run" not in c]
    extra = {l: c for l, c in langs_cfg.items() if c.get("enabled") and "longs_per_run" in c}
    done = {"long": 0, "short": 0}
    report, failures = [], []
    notify.send(f"🎬 Making videos: {targets.get('long', 0)} long + {targets.get('short', 0)} Shorts"
                + (" (test, no upload)" if not upload else "")
                + "\nTopics:\n" + "\n".join(f"• {f}: {t.get('topic')}" for f in ("long", "short")
                                               for t in queues.get(f, [])[: targets.get(f, 0)]))
    slots = {}  # (lang, fmt) -> publish slots used
    quota_hit = False
    for fmt in ("long", "short"):
        # Work down the ranked candidates (spares included) until this format's target is met.
        for topic in queues.get(fmt, []):
            if quota_hit or done[fmt] >= targets.get(fmt, 0):
                break
            langs = topic.get("langs") or \
                primary + [l for l, c in extra.items() if done[fmt] < c.get(f"{fmt}s_per_run", 0)]
            publish_at = {}
            for lang in langs:
                n = slots.get((lang, fmt), 0)
                publish_at[lang] = (youtube.next_publish_time(langs_cfg[lang].get(f"{fmt}_publish_utc"), n)
                                    if upload else None)
            try:
                results = pipeline.produce(cfg, topic, fmt, langs, upload=upload, publish_at=publish_at)
                report.extend((fmt, lang, vid, work) for lang, vid, work in results)
                for lang, vid, work in results:   # ✅ per video, the moment it's done
                    try:
                        title = json.loads((work / "metadata.json").read_text()).get("title", work.name)
                    except Exception:
                        title = work.name
                    when = publish_at.get(lang) if vid else None
                    notify.send(f"✅ {fmt}/{lang} done: {title}\n"
                                + (f"https://youtu.be/{vid}" if vid else "(test render, not uploaded)")
                                + (f"\n⏰ goes public {when} UTC" if when else ("\n🌐 public now" if vid else "")))
                for lang, vid, work in results:
                    slots[(lang, fmt)] = slots.get((lang, fmt), 0) + 1
                    log.info("DONE %s/%s %s", fmt, lang, f"https://youtu.be/{vid}" if vid else work)
                if not topic.get("langs"):   # owner requests for specific languages are extra videos,
                    done[fmt] += 1           # they don't take the day's regular slot
            except Exception as e:
                log.error("FAILED %s %r: %s — trying next candidate", fmt, topic.get("topic"), e)
                failures.append(f"{fmt}: {topic.get('topic')} — {str(e)[:120]}")
                notify.send(f"❌ {fmt} failed: {topic.get('topic')}\n{str(e)[:300]}\n(trying the next topic)")
                log.debug(traceback.format_exc())
                if any(k in str(e) for k in ("Not enough research", "No Wikipedia article", "Quality gate")):
                    # Remember it so the topic picker never proposes it again.
                    state.append({"format": f"{fmt}-skipped", "topic": topic.get("topic"),
                                  "reason": str(e)[:200]})
                if "quotaExceeded" in str(e):
                    log.error("YouTube quota exhausted for today; stopping.")
                    quota_hit = True

    ok, wanted = sum(done.values()), sum(targets.values())
    if upload and wanted and not any(vid for _, _, vid, _ in report):
        release_reserve(cfg, "today's run produced no videos")
    send_report(report, failures, upload)
    log.info("Finished: %d/%d videos", ok, wanted)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
