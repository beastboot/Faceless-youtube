"""Sunday compilation: each channel's Shorts of the week stitched into one long video in that channel's
language (watch hours from footage we already have; no extra images). Shorts are kept as GitHub artifacts
by the daily run."""
import json
import os
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

from PIL import Image, ImageDraw

from . import fonts, state, tts, youtube
from .common import ROOT, ffprobe_duration, fmt_ts, log, run

W, H, FPS = 1920, 1080, 30

TEXT = {
    "en": {"number": "Number {n}.",
           "title": "{n} Strangest True Stories of the Week | Odd Annals Weekly",
           "desc": "All of this week's mysteries in one video: {n} true stories, back to back.",
           "chapters": "Chapters:", "sub": "Subscribe to Odd Annals for a new story every day.",
           "hashtags": "#mystery #history #truestory",
           "tags": ["mystery", "history", "true stories", "unsolved mysteries", "Odd Annals", "weekly mysteries"]},
    "ta": {"number": "எண் {n}.",
           "title": "இந்த வாரத்தின் {n} மர்மக் கதைகள் | Odd Annals Weekly",
           "desc": "இந்த வாரத்தின் எல்லா மர்மக் கதைகளும் ஒரே வீடியோவில்: {n} உண்மைக் கதைகள்.",
           "chapters": "அத்தியாயங்கள்:", "sub": "தினமும் ஒரு புதிய கதைக்கு Odd Annals Tamil-ஐ சப்ஸ்க்ரைப் செய்யுங்கள்.",
           "hashtags": "#மர்மம் #வரலாறு #mystery",
           "tags": ["மர்மம்", "வரலாறு", "mystery in tamil", "history in tamil", "Odd Annals Tamil"]},
    "hi": {"number": "नंबर {n}।",
           "title": "इस हफ्ते के {n} सबसे रहस्यमयी किस्से 😱 | Odd Annals Weekly",
           "desc": "इस हफ्ते की सारी रहस्यमयी कहानियाँ एक साथ — {n} किस्से, एक वीडियो।",
           "chapters": "अध्याय:", "sub": "हर दिन नई कहानी के लिए Odd Annals को सब्सक्राइब करें।",
           "hashtags": "#रहस्य #इतिहास #mystery",
           "tags": ["रहस्य", "इतिहास", "mystery in hindi", "history in hindi", "Odd Annals", "weekly mysteries"]},
}


def _say(text, voice, out):
    """Spoken card intro: Edge, else Kokoro for English, else a short silence (a voice outage
    shouldn't cost the week's compilation)."""
    try:
        tts.speak(text, voice, "+5%", out, retries=2)
        return
    except Exception:
        pass
    if tts.kokoro_supports(voice):
        try:
            tts.speak_kokoro(text, voice, "+5%", out)
            return
        except Exception as e:
            log.warning("Kokoro intro failed: %s", e)
    run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono", "-t", "1.2", out])


def fetch_week_shorts(dest, days=7):
    """Download this week's Short videos from the daily runs' 'shorts-*' artifacts (gh CLI)."""
    repo = os.environ.get("GITHUB_REPOSITORY")
    if not repo:
        raise RuntimeError("compilation runs on GitHub Actions (needs GITHUB_REPOSITORY)")
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    arts = json.loads(subprocess.run(["gh", "api", f"repos/{repo}/actions/artifacts?per_page=100"],
                                     capture_output=True, text=True, check=True).stdout)["artifacts"]
    for a in arts:
        if a["name"].startswith("shorts-") and not a["expired"] and a["created_at"] >= since \
                and not (dest / a["name"]).exists():   # already fetched for another channel's compilation
            subprocess.run(["gh", "run", "download", str(a["workflow_run"]["id"]), "-n", a["name"],
                            "-D", str(dest / a["name"]), "-R", repo], check=False)
    return sorted(dest.rglob("final.mp4"))


def title_card(n, title, out, lang="en"):
    img = Image.new("RGB", (W, H), (12, 12, 18))
    d = ImageDraw.Draw(img)
    d.text((W / 2, H * 0.36), f"#{n}", font=fonts.font(260, "en"), fill=(255, 214, 10), anchor="mm",
           stroke_width=8, stroke_fill=(0, 0, 0))
    f = fonts.font(70, lang)
    lines = fonts.wrap(d, title.replace("#shorts", "").strip(), f, W * 0.8)[:2]
    for k, line in enumerate(lines):
        d.text((W / 2, H * 0.62 + k * 95), line, font=f, fill=(255, 255, 255), anchor="mm")
    img.save(out)
    return out


def voice_for(cfg, lang):
    lc = cfg["languages"].get(lang) or {}
    return lc.get("voice") or cfg["short"]["voice"]


def build(cfg, work, upload=True, lang="en"):
    t_ = TEXT.get(lang, TEXT["en"])
    work.mkdir(parents=True, exist_ok=True)
    clips = fetch_week_shorts(work.parent / f"{work.name.rsplit('-', 1)[0]}-dl")
    meta_by_dir = {}
    for c in clips:
        m = c.parent / "metadata.json"
        meta_by_dir[c] = json.loads(m.read_text()) if m.exists() else {"title": c.parent.name}
    clips = [c for c in clips if ("-short-" in str(c) or "clip" in str(c))
             and (meta_by_dir[c].get("lang") or "hi") == lang][:12]
    if len(clips) < 4:
        raise RuntimeError(f"only {len(clips)} {lang} Shorts this week; compilation needs 4+")
    voice = voice_for(cfg, lang)
    parts, chapters, t = [], [], 0.0
    for n, c in enumerate(reversed(clips), 1):
        title = meta_by_dir[c].get("title", "")
        # numbered card with a spoken intro
        mp3 = work / f"intro-{n}.mp3"
        _say(t_["number"].format(n=len(clips) - n + 1), voice, mp3)
        card = title_card(len(clips) - n + 1, title, work / f"card-{n}.png", lang)
        cdur = max(2.2, ffprobe_duration(mp3) + 0.6)
        seg_card = work / f"seg-{n}a.mp4"
        run(["ffmpeg", "-y", "-v", "error", "-loop", "1", "-i", card, "-i", mp3, "-t", f"{cdur:.2f}",
             "-vf", f"scale={W}:{H},zoompan=z='1+0.0008*on':d={int(cdur * FPS)}:s={W}x{H}:fps={FPS}",
             "-af", "apad", "-c:v", "libx264", "-preset", "faster", "-crf", "20", "-pix_fmt", "yuv420p",
             "-c:a", "aac", "-ar", "48000", "-ac", "2", seg_card])
        chapters.append((t, title.replace("#shorts", "").strip()[:60]))
        t += cdur
        # the Short itself, centred on a blurred copy of itself
        seg = work / f"seg-{n}b.mp4"
        run(["ffmpeg", "-y", "-v", "error", "-i", c, "-filter_complex",
             f"[0:v]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},boxblur=30:3,eq=brightness=-0.12[bg];"
             f"[0:v]scale=-2:{H}[fg];[bg][fg]overlay=(W-w)/2:0,fps={FPS},setsar=1[v]",
             "-map", "[v]", "-map", "0:a", "-c:v", "libx264", "-preset", "faster", "-crf", "20",
             "-pix_fmt", "yuv420p", "-c:a", "aac", "-ar", "48000", "-ac", "2", seg])
        t += ffprobe_duration(seg)
        parts += [seg_card, seg]
    (work / "list.txt").write_text("".join(f"file '{p.name}'\n" for p in parts))
    out = work / "final.mp4"
    run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", work / "list.txt",
         "-c:v", "copy", "-af", "loudnorm=I=-14:TP=-1.5:LRA=11", "-c:a", "aac", "-b:a", "192k",
         "-movflags", "+faststart", out])
    title = t_["title"].format(n=len(clips))
    chap = "\n".join(f"{fmt_ts(a)} {b}" for a, b in chapters) if len(chapters) >= 3 else ""
    desc = (t_["desc"].format(n=len(clips)) + "\n\n"
            + (f"{t_['chapters']}\n{chap}\n\n" if chap else "")
            + t_["sub"] + "\n\n" + t_["hashtags"])
    meta = {"title": title, "description": desc, "lang": lang, "tags": t_["tags"]}
    (work / "metadata.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1))
    log.info("Weekly compilation ready: %d stories, %.0fs", len(clips), t)
    vid = None
    if upload:
        vid = youtube.upload(cfg, out, meta)
        state.append({"format": "long", "lang": lang, "topic": f"weekly compilation {datetime.now():%Y-%m-%d}",
                      "title": title, "video_id": vid, "duration": round(t, 1), "dir": work.name,
                      "compilation": True})
    return vid, work
