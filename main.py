#!/usr/bin/env python3
"""Daily run: pick trending topics, then make and upload long videos + Shorts.

    python main.py                      # use counts from config.yaml, upload to YouTube
    python main.py --longs 0 --shorts 1 --no-upload    # local test, nothing published
    python main.py --topic "Dyatlov Pass incident" --format short --no-upload
    python main.py --upload-dir output/<run-folder>     # retry the upload of a rendered video
"""
import argparse
import sys
import traceback
from pathlib import Path

from dotenv import load_dotenv

from src import pipeline, trends, youtube
from src.common import load_config, log, setup_logging


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--longs", type=int)
    ap.add_argument("--shorts", type=int)
    ap.add_argument("--no-upload", action="store_true", help="render only, do not publish")
    ap.add_argument("--topic", help="skip trend picking and make this topic")
    ap.add_argument("--format", choices=["long", "short"], default="short")
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

    done = {"long": 0, "short": 0}
    quota_hit = False
    for fmt in ("long", "short"):
        # Work down the ranked candidates (spares included) until this format's target is met.
        for topic in queues.get(fmt, []):
            if quota_hit or done[fmt] >= targets.get(fmt, 0):
                break
            publish_at = youtube.next_publish_time(sc[f"{fmt}_publish_utc"], done[fmt]) if upload else None
            try:
                vid, work = pipeline.make_video(cfg, topic, fmt, upload=upload, publish_at=publish_at)
                log.info("DONE %s %s", fmt, f"https://youtu.be/{vid}" if vid else work)
                done[fmt] += 1
            except Exception as e:
                log.error("FAILED %s %r: %s — trying next candidate", fmt, topic.get("topic"), e)
                log.debug(traceback.format_exc())
                if "quotaExceeded" in str(e):
                    log.error("YouTube quota exhausted for today; stopping.")
                    quota_hit = True

    ok, wanted = sum(done.values()), sum(targets.values())
    log.info("Finished: %d/%d videos", ok, wanted)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
