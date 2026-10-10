#!/usr/bin/env python3
"""Daily run: pick trending topics, then make and upload long videos + Shorts.

    python main.py                      # use counts from config.yaml, upload to YouTube
    python main.py --longs 0 --shorts 1 --no-upload    # local test, nothing published
    python main.py --topic "Dyatlov Pass incident" --format short --no-upload
    python main.py --upload-dir output/<run-folder>     # retry the upload of a rendered video
    python main.py --reach                             # reach report + keep-public + comments only
"""
import argparse
import sys
import traceback
from pathlib import Path

from dotenv import load_dotenv

from src import notify, pipeline, reach, state, trends, youtube
from src.common import load_config, log, setup_logging


def send_report(report, failures, upload):
    """Telegram summary: every video with link + publish time, failures, and the 3 thumbnail
    options of each long video for YouTube Studio's Test & Compare."""
    import json
    lines = [f"📺 Odd Annals run: {len([r for r in report if r[2] or not upload])} video(s)"]
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

    if args.topic:
        targets = {args.format: 1}
        queues = {args.format: [{"topic": args.topic, "wikipedia_title": args.topic, "angle": ""}]}
    else:
        targets = {"long": sc["longs_per_run"] if args.longs is None else args.longs,
                   "short": sc["shorts_per_run"] if args.shorts is None else args.shorts}
        longs, shorts = trends.pick_topics(cfg, targets["long"], targets["short"])
        queues = {"long": longs, "short": shorts}

    langs_cfg = cfg["languages"]
    primary = [l for l, c in langs_cfg.items() if c.get("enabled") and "longs_per_run" not in c]
    extra = {l: c for l, c in langs_cfg.items() if c.get("enabled") and "longs_per_run" in c}
    done = {"long": 0, "short": 0}
    report, failures = [], []
    slots = {}  # (lang, fmt) -> publish slots used
    quota_hit = False
    for fmt in ("long", "short"):
        # Work down the ranked candidates (spares included) until this format's target is met.
        for topic in queues.get(fmt, []):
            if quota_hit or done[fmt] >= targets.get(fmt, 0):
                break
            langs = primary + [l for l, c in extra.items() if done[fmt] < c.get(f"{fmt}s_per_run", 0)]
            publish_at = {}
            for lang in langs:
                n = slots.get((lang, fmt), 0)
                publish_at[lang] = (youtube.next_publish_time(langs_cfg[lang].get(f"{fmt}_publish_utc"), n)
                                    if upload else None)
            try:
                results = pipeline.produce(cfg, topic, fmt, langs, upload=upload, publish_at=publish_at)
                report.extend((fmt, lang, vid, work) for lang, vid, work in results)
                for lang, vid, work in results:
                    slots[(lang, fmt)] = slots.get((lang, fmt), 0) + 1
                    log.info("DONE %s/%s %s", fmt, lang, f"https://youtu.be/{vid}" if vid else work)
                done[fmt] += 1
            except Exception as e:
                log.error("FAILED %s %r: %s — trying next candidate", fmt, topic.get("topic"), e)
                failures.append(f"{fmt}: {topic.get('topic')} — {str(e)[:120]}")
                log.debug(traceback.format_exc())
                if any(k in str(e) for k in ("Not enough research", "No Wikipedia article", "Quality gate")):
                    # Remember it so the topic picker never proposes it again.
                    state.append({"format": f"{fmt}-skipped", "topic": topic.get("topic"),
                                  "reason": str(e)[:200]})
                if "quotaExceeded" in str(e):
                    log.error("YouTube quota exhausted for today; stopping.")
                    quota_hit = True

    ok, wanted = sum(done.values()), sum(targets.values())
    send_report(report, failures, upload)
    log.info("Finished: %d/%d videos", ok, wanted)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
