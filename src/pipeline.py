"""One video end to end: research -> script -> voice -> visuals -> edit -> thumbnail -> upload."""
import json
from datetime import datetime, timezone

from . import research as research_mod
from . import script_writer, state, thumbnail, tts, video, youtube
from .common import ROOT, ffprobe_duration, fmt_ts, log, slugify
from .media import MediaPicker


def chapters_block(script, starts, total):
    """YouTube chapter rules: first at 0:00, at least 3, each >= 10s. Otherwise omit."""
    marks = [(starts[i], s["chapter"].strip()) for i, s in enumerate(script["scenes"])
             if (s.get("chapter") or "").strip()]
    if not marks or marks[0][0] > 0:
        marks.insert(0, (0.0, "Intro"))
    clean = []
    for t, name in marks:
        if not clean or t - clean[-1][0] >= 10:
            clean.append((t, name))
    if len(clean) < 3 or total - clean[-1][0] < 10:
        return ""
    return "\n".join(f"{fmt_ts(t)} {name}" for t, name in clean)


def build_description(cfg, script, res, chapters, credits, fmt):
    parts = [(script.get("description_intro") or "").strip()]
    if chapters:
        parts.append("Chapters:\n" + chapters)
    parts.append((script.get("description_body") or "").strip())
    parts.append(f"Sources:\n{res['wiki_url']}")
    if credits:
        uniq = list(dict.fromkeys(credits))[:25]
        parts.append("Image credits (Wikimedia Commons):\n" + "\n".join(uniq))
    parts.append("Stock footage: Pexels / Pixabay. Narration: AI voice.")
    if fmt == "long":
        parts.append(f"Subscribe to {cfg['channel']['name']} for a new story every day.")
    tags = [h if h.startswith("#") else "#" + h.replace(" ", "")
            for h in (script.get("hashtags") or [])][:3]
    if fmt == "short" and "#shorts" not in [t.lower() for t in tags]:
        tags.append("#shorts")
    parts.append(" ".join(tags))
    return "\n\n".join(p for p in parts if p)


def make_video(cfg, topic, fmt, upload=True, publish_at=None):
    fc = cfg[fmt]
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M")
    work = ROOT / "output" / f"{stamp}-{fmt}-{slugify(topic['topic'])}"
    work.mkdir(parents=True, exist_ok=True)
    log.info("=== %s: %s -> %s", fmt.upper(), topic["topic"], work.name)

    res = research_mod.research(topic)
    if len(res["text"]) < (3000 if fmt == "long" else 600):
        raise RuntimeError(f"Not enough research material for {res['wiki_title']}")
    script = script_writer.write(cfg, topic, res, fmt)
    (work / "script.json").write_text(json.dumps(script, indent=1, ensure_ascii=False))

    picker = MediaPicker(work, fc["width"], fc["height"], res["images"])
    scenes = []
    for i, sc in enumerate(script["scenes"]):
        mp3 = work / f"voice-{i:03d}.mp3"
        words = tts.speak(sc["narration"], fc["voice"], fc["rate"], mp3)
        scenes.append({
            "audio": mp3,
            "audio_dur": ffprobe_duration(mp3),
            "captions": tts.caption_chunks(words, 3 if fmt == "short" else 7),
            "visual": picker.for_scene(sc, i),
        })
    log.info("Voice + visuals ready for %d scenes", len(scenes))

    out = work / "final.mp4"
    starts, total = video.assemble(scenes, work, out, cfg, fmt)
    if fmt == "short" and total > 175:
        log.warning("Short is %.0fs (>3 min limit); YouTube will treat it as a regular video", total)
    srt = video.write_srt(scenes, starts, work / "captions.srt")

    first_image = next((s["visual"] for s in scenes if s["visual"]["kind"] == "image"), scenes[0]["visual"])
    thumb = thumbnail.make(first_image, script["thumbnail_text"], work / "thumbnail.jpg", work)

    chapters = chapters_block(script, starts, total) if fmt == "long" else ""
    meta = {"title": script["title"],
            "description": build_description(cfg, script, res, chapters, picker.credits, fmt),
            "tags": (script.get("tags") or [])}
    (work / "metadata.json").write_text(json.dumps(meta, indent=1, ensure_ascii=False))
    log.info("Title: %s", meta["title"])

    video_id = publish(cfg, work, fmt, meta, publish_at) if upload else None

    state.append({"format": fmt, "topic": topic["topic"], "wiki_title": res["wiki_title"],
                  "title": meta["title"], "video_id": video_id, "duration": round(total, 1),
                  "publish_at": publish_at, "dir": work.name})
    cleanup(work)
    return video_id, work


def publish(cfg, work, fmt, meta, publish_at=None):
    video_id = youtube.upload(cfg, work / "final.mp4", meta, publish_at)
    if fmt == "long":
        youtube.set_thumbnail(video_id, work / "thumbnail.jpg")
    if cfg[fmt].get("upload_captions"):
        youtube.upload_captions(video_id, work / "captions.srt", cfg["channel"]["language"])
    youtube.add_to_playlist(video_id, cfg["youtube"].get(f"{fmt}_playlist_id"))
    return video_id


def upload_existing(cfg, work, fmt):
    """Retry the upload of a folder that rendered but failed to publish."""
    meta = json.loads((work / "metadata.json").read_text())
    video_id = publish(cfg, work, fmt, meta)
    state.append({"format": fmt, "topic": work.name.split(f"-{fmt}-", 1)[-1].replace("-", " "),
                  "title": meta["title"], "video_id": video_id, "dir": work.name})
    cleanup(work)
    return video_id


KEEP = {"final.mp4", "thumbnail.jpg", "script.json", "metadata.json", "captions.srt"}


def cleanup(work):
    """Drop intermediates (segments, voice, downloads): ~80% of the folder's size."""
    for p in work.iterdir():
        if p.is_file() and p.name not in KEEP:
            p.unlink()
