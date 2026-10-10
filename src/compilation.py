"""Sunday compilation: the week's Hindi Shorts stitched into one long video (watch hours from
footage we already have; no extra images). Shorts are kept as GitHub artifacts by the daily run."""
import json
import os
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

from PIL import Image, ImageDraw

from . import fonts, state, tts, youtube
from .common import ROOT, ffprobe_duration, fmt_ts, log, run

W, H, FPS = 1920, 1080, 30


def fetch_week_shorts(dest, days=7):
    """Download this week's Short videos from the daily runs' 'shorts-*' artifacts (gh CLI)."""
    repo = os.environ.get("GITHUB_REPOSITORY")
    if not repo:
        raise RuntimeError("compilation runs on GitHub Actions (needs GITHUB_REPOSITORY)")
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    arts = json.loads(subprocess.run(["gh", "api", f"repos/{repo}/actions/artifacts?per_page=100"],
                                     capture_output=True, text=True, check=True).stdout)["artifacts"]
    for a in arts:
        if a["name"].startswith("shorts-") and not a["expired"] and a["created_at"] >= since:
            subprocess.run(["gh", "run", "download", str(a["workflow_run"]["id"]), "-n", a["name"],
                            "-D", str(dest / a["name"]), "-R", repo], check=False)
    return sorted(dest.rglob("final.mp4"))


def title_card(n, title, out):
    img = Image.new("RGB", (W, H), (12, 12, 18))
    d = ImageDraw.Draw(img)
    d.text((W / 2, H * 0.36), f"#{n}", font=fonts.font(260, "en"), fill=(255, 214, 10), anchor="mm",
           stroke_width=8, stroke_fill=(0, 0, 0))
    f = fonts.font(70, "hi")
    lines = fonts.wrap(d, title.replace("#shorts", "").strip(), f, W * 0.8)[:2]
    for k, line in enumerate(lines):
        d.text((W / 2, H * 0.62 + k * 95), line, font=f, fill=(255, 255, 255), anchor="mm")
    img.save(out)
    return out


def build(cfg, work, upload=True):
    work.mkdir(parents=True, exist_ok=True)
    clips = fetch_week_shorts(work / "dl")
    meta_by_dir = {}
    for c in clips:
        m = c.parent / "metadata.json"
        meta_by_dir[c] = json.loads(m.read_text()) if m.exists() else {"title": c.parent.name}
    clips = [c for c in clips if "-short-" in str(c) or "clip" in str(c)][:12]
    if len(clips) < 4:
        raise RuntimeError(f"only {len(clips)} Shorts this week; compilation needs 4+")
    voice = cfg["languages"]["hi"]["voice"]
    parts, chapters, t = [], [], 0.0
    for n, c in enumerate(reversed(clips), 1):
        title = meta_by_dir[c].get("title", "")
        # numbered card with a spoken intro
        mp3 = work / f"intro-{n}.mp3"
        tts.speak(f"नंबर {len(clips) - n + 1}।", voice, "+5%", mp3)
        card = title_card(len(clips) - n + 1, title, work / f"card-{n}.png")
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
    title = f"इस हफ्ते के {len(clips)} सबसे रहस्यमयी किस्से 😱 | Odd Annals Weekly"
    chap = "\n".join(f"{fmt_ts(a)} {b}" for a, b in chapters) if len(chapters) >= 3 else ""
    desc = (f"इस हफ्ते की सारी रहस्यमयी कहानियाँ एक साथ — {len(clips)} किस्से, एक वीडियो।\n\n"
            + (f"अध्याय:\n{chap}\n\n" if chap else "")
            + "हर दिन नई कहानी के लिए Odd Annals को सब्सक्राइब करें।\n\n#रहस्य #इतिहास #mystery")
    meta = {"title": title, "description": desc, "lang": "hi",
            "tags": ["रहस्य", "इतिहास", "mystery in hindi", "history in hindi", "Odd Annals", "weekly mysteries"]}
    (work / "metadata.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1))
    log.info("Weekly compilation ready: %d stories, %.0fs", len(clips), t)
    vid = None
    if upload:
        vid = youtube.upload(cfg, out, meta)
        state.append({"format": "long", "lang": "hi", "topic": f"weekly compilation {datetime.now():%Y-%m-%d}",
                      "title": title, "video_id": vid, "duration": round(t, 1), "dir": work.name,
                      "compilation": True})
    return vid, work
