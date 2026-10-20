"""Bridge to the Remotion motion-graphics renderer (remotion/): turns the shot list into props,
stages the assets, renders the silent picture track. Audio is mixed afterwards in video.py."""
import hashlib
import json
import os
import shutil
from pathlib import Path

from . import fonts, vfx
from .common import ROOT, ffprobe_duration, log, run

RDIR = ROOT / "remotion"

# ffmpeg-style transition names in config.yaml -> Remotion entrance styles
TRANSITIONS = {"slideleft": "slide-left", "smoothleft": "slide-left", "slideright": "slide-right",
               "smoothright": "slide-right", "slideup": "slide-up", "slidedown": "slide-up",
               "zoomin": "zoom", "wipeleft": "wipe", "circleopen": "circle", "fade": "fade",
               "fadeblack": "fade", "hblur": "fade", "dissolve": "fade"}

END_CARD = {"hi": {"title": "ऐसी और कहानियों के लिए", "button": "सब्सक्राइब करें"},
            "mr": {"title": "अशा आणखी कथांसाठी", "button": "सबस्क्राइब करा"},
            "ta": {"title": "இதுபோன்ற கதைகளுக்கு", "button": "சப்ஸ்கிரைப்"},
            "te": {"title": "ఇలాంటి కథల కోసం", "button": "సబ్‌స్క్రైబ్"},
            "bn": {"title": "এমন আরও গল্পের জন্য", "button": "সাবস্ক্রাইব"},
            "en": {"title": "For more stories like this", "button": "Subscribe"}}


def ensure_node():
    """npm install once (CI caches node_modules) and fetch the headless browser."""
    if not (RDIR / "node_modules" / "remotion").exists():
        log.info("Installing Remotion dependencies (first run)")
        run(["npm", "ci" if (RDIR / "package-lock.json").exists() else "install", "--no-audit", "--no-fund"], cwd=RDIR)
    run(["npx", "remotion", "browser", "ensure"], cwd=RDIR)


def camera_clips(scenes, durs, stage, run_id, cfg, fmt):
    """2.5D camera move for every picture shot that has a depth map, written as JPEG frames straight
    into the render's staging folder: {shot index: {"dir", "count"}}."""
    vc, fc = cfg["video"], cfg[fmt]
    if not vc.get("parallax", True):
        return {}
    from . import parallax
    fps, xf = vc["fps"], vc.get("transition_seconds", 0.35)
    jobs = []
    for i, (sc, d) in enumerate(zip(scenes, durs)):
        vis = sc.get("visual") or {}
        if vis.get("kind") != "image" or not vis.get("depth"):
            continue
        move = vis.get("move") or ["zoom_in", "pan_left", "zoom_out", "pan_right", "rise"][i % 5]
        frames = int(round((d + xf) * fps)) + 2   # covers the shot plus the next shot's transition
        jobs.append((i, {"image": vis["path"], "depth": vis["depth"], "move": move, "frames": frames,
                         "width": fc["width"], "height": fc["height"], "fps": fps, "out": stage / f"cam{i:03d}"}))
    if not jobs:
        return {}
    import time
    t0 = time.time()
    done = parallax.render_clips(jobs, fn=parallax.render_frames)
    log.info("3D camera: %d/%d shots in %.0fs", len(done), len(jobs), time.time() - t0)
    counts = {i: kw["frames"] for i, kw in jobs}
    return {i: {"dir": f"run/{run_id}/{Path(p).name}", "count": counts[i]} for i, p in done.items()}


CUTAWAYS = ["detail_left", "detail_right", "detail_top"]   # clearly different close-ups of the painting


def split_long_shots(scenes, starts, durs, max_len=3.2):
    """Shorts need a new view every 2-3 seconds. A picture held longer is cut into parts, each with a
    different framing of the same painting (close-up left/right/top, push-in, pull-out), with the
    captions moved to the part where they are spoken."""
    out_sc, out_st, out_d = [], [], []
    for i, (sc, st, d) in enumerate(zip(scenes, starts, durs)):
        vis = sc.get("visual") or {}
        k = int(d // max_len) + 1 if vis.get("kind") == "image" and d > max_len + 0.6 else 1
        part = d / k
        for j in range(k):
            sub = dict(sc)
            lo, hi = j * part, (j + 1) * part
            if j:
                sub.update(visual={**vis, "move": CUTAWAYS[(i + j) % len(CUTAWAYS)]}, graphic=None,
                           whoosh=True, chapter_card=None, cut="zoom")   # punch-in, never a slide on the same picture
            if k > 1:
                sub["captions"] = [{**c, "start": max(0.0, c["start"] - lo), "end": min(part, c["end"] - lo)}
                                   for c in sc.get("captions") or [] if lo - 0.05 <= c["start"] < hi]
            out_sc.append(sub)
            out_st.append(st + lo)
            out_d.append(part)
    return out_sc, out_st, out_d


def render(scenes, starts, durs, work, out, cfg, fmt):
    vc, fc = cfg["video"], cfg[fmt]
    if fmt == "short":
        n = len(scenes)
        scenes, starts, durs = split_long_shots(scenes, starts, durs)
        if len(scenes) > n:
            log.info("Short: %d long shots split -> %d views (a new view every ~3 s)", n, len(scenes))
    ensure_node()
    run_id = hashlib.sha1(str(work).encode()).hexdigest()[:12]
    pub = RDIR / "public"
    stage = pub / "run" / run_id
    stage.mkdir(parents=True, exist_ok=True)
    (pub / "fonts").mkdir(parents=True, exist_ok=True)
    langs = {"en", "hi"} | {sc.get("caption_lang") or sc.get("lang") or "en" for sc in scenes}
    regional = None
    for lang in langs:
        src = Path(fonts.font_path(lang))
        if not (pub / "fonts" / src.name).exists():
            shutil.copy(src, pub / "fonts" / src.name)
        if lang not in ("en", "hi", "mr"):
            regional = src.name

    copied = {}

    def stage_file(path):
        if not path:
            return None
        path = str(path)
        if path not in copied:
            name = f"{len(copied):03d}{Path(path).suffix.lower()}"
            shutil.copy(path, stage / name)
            copied[path] = f"run/{run_id}/{name}"
        return copied[path]

    cams = camera_clips(scenes, durs, stage, run_id, cfg, fmt)
    styles = [TRANSITIONS.get(t, "fade") for t in vc["transitions"].get(fmt, ["fade"])]
    shots = []
    for i, (sc, st, d) in enumerate(zip(scenes, starts, durs)):
        vis = sc.get("visual") or {}
        shots.append({
            "start": round(st, 4), "dur": round(d, 4),
            "image": stage_file(vis.get("path")) if vis.get("kind") == "image" else None,
            "video": stage_file(vis.get("path")) if vis.get("kind") == "video" else None,
            "frames": cams.get(i),   # 3D camera move replaces the flat one on this picture
            "chapter": sc.get("chapter_card") if fmt == "long" else None,
            "move": vis.get("move") or ["zoom_in", "pan_left", "zoom_out", "pan_right", "rise"][i % 5],
            "ambient": vis.get("ambient") or "none",
            # a new picture arrives with a styled move; the same picture continuing just dissolves
            "transition": sc.get("cut") or (styles[i % len(styles)] if sc.get("whoosh", True) else "fade"),
            "captions": [{"text": c["text"], "s": round(c["start"], 3), "e": round(c["end"], 3)}
                         for c in sc.get("captions") or []] if fc.get("burn_captions") else [],
            "graphic": sc.get("graphic") or None,
            "tag": vis.get("tag"),   # "REAL PHOTO" stamp on archive pictures
            # real filmed smoke / fog / sparks over the picture, and a light leak on some new pictures
            "overlay": stage_file(vfx.clip(vis.get("ambient"))) if vc.get("vfx", True) else None,
            "leak": stage_file(vfx.clip("leak")) if vc.get("vfx", True) and i > 0 and i % 3 == 0
                    and sc.get("whoosh", True) else None,
            "impact": bool(sc.get("impact")),
        })
    if fmt == "short" and shots and (scenes[0].get("hook_text") or scenes[0].get("line_text")):
        # The swipe-or-stay decision happens in the first second: a punchy title on screen (it replaces any
        # script graphic on shot 1, and carries the series badge). The thumbnail
        # punchline, so it adds to the spoken hook instead of repeating the captions word for word.
        words = (scenes[0].get("hook_text") or scenes[0]["line_text"]).split()
        shots[0]["graphic"] = {"type": "fact", "text": " ".join(words[: min(7, len(words))]), "instant": True,
                               "badge": scenes[0].get("badge")}
    if fmt == "long":  # keep the closing seconds clear for the animated subscribe card
        end_at = sum(durs) - 7
        for sh, sc in zip(shots, scenes):
            if sh["start"] + sh["dur"] > end_at:
                sh["graphic"] = None
                sc["graphic"] = None   # keep sound design in sync with what is on screen
    grain = pub / "fonts" / "grain.png"  # fine film grain texture, generated once
    if not grain.exists():
        import numpy as np
        from PIL import Image
        g = np.random.default_rng(1).normal(128, 40, (1296, 2304)).clip(0, 255).astype(np.uint8)
        Image.fromarray(g).save(grain)
    lang = scenes[0].get("lang", "en") if scenes else "en"
    logo = ROOT / "branding" / lang / "logo.png"   # each channel's own logo (English / Tamil tagline)
    if not logo.exists():
        logo = ROOT / "branding" / "logo.png"
    props = {
        "width": fc["width"], "height": fc["height"], "fps": vc["fps"], "total": round(sum(durs), 4),
        "lang": lang, "short": fmt == "short", "shots": shots,
        "transitionSeconds": vc.get("transition_seconds", 0.35), "captionsOn": bool(fc.get("burn_captions")),
        "fonts": {"display": "Anton", "hindi": "Mukta", "regional": "Regional",
                  "regionalFile": f"fonts/{regional}" if regional else None},
        "logo": stage_file(logo) if logo.exists() else None,
        "grain": "fonts/grain.png",
        "endCard": END_CARD.get(lang, END_CARD["en"]) if fmt == "long" else None,
        "followText": (scenes[0].get("follow_text") if scenes else None) if fmt == "short" else None,
    }
    props_path = work / "motion-props.json"
    props_path.write_text(json.dumps(props, ensure_ascii=False))
    conc = str(max(1, (os.cpu_count() or 2) - 1))
    log.info("Remotion: rendering %d shots, %.0fs, %d graphics", len(shots), props["total"],
             sum(1 for s in shots if s["graphic"]))
    try:
        run(["npx", "remotion", "render", "src/index.ts", "Main", str(out), f"--props={props_path}",
             "--muted", "--codec=h264", f"--crf={vc['crf']}", f"--concurrency={conc}", "--log=error",
             *[str(f) for f in vc.get("remotion_flags") or []]], cwd=RDIR)
    finally:
        shutil.rmtree(stage, ignore_errors=True)
    log.info("Remotion: picture track ready (%.1fs)", ffprobe_duration(out))
    return out
