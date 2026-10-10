"""Assemble the final video with ffmpeg: Ken Burns stills, stock clips, captions, narration, music."""
import math
import os
import random
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageOps

from . import fonts
from .common import ROOT, ffprobe_duration, log, run, srt_ts


# ---- stills -------------------------------------------------------------------------------
def prepare_still(src, out, w, h, scale=2):
    """Fit any image into a (scale*w x scale*h) canvas: blurred cover background + sharp foreground."""
    W, H = w * scale, h * scale
    im = ImageOps.exif_transpose(Image.open(src)).convert("RGB")
    bg = ImageOps.fit(im, (W, H), Image.LANCZOS).filter(ImageFilter.GaussianBlur(40))
    bg = Image.blend(bg, Image.new("RGB", (W, H), (0, 0, 0)), 0.35)
    ratio_img, ratio_out = im.width / im.height, W / H
    if abs(ratio_img - ratio_out) / ratio_out < 0.2:
        canvas = ImageOps.fit(im, (W, H), Image.LANCZOS)  # close enough: fill the frame
    else:
        fg = ImageOps.contain(im, (W, H), Image.LANCZOS)
        canvas = bg
        canvas.paste(fg, ((W - fg.width) // 2, (H - fg.height) // 2))
    canvas.save(out, quality=92)
    return out


def ken_burns(frames):
    n = max(frames - 1, 1)
    c = "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"
    return random.choice([
        (f"1+0.18*on/{n}", *c),                                   # slow zoom in
        (f"1.18-0.18*on/{n}", *c),                                # slow zoom out
        ("1.15", f"(iw-iw/zoom)*on/{n}", c[1]),                   # pan left -> right
        ("1.15", f"(iw-iw/zoom)*(1-on/{n})", c[1]),               # pan right -> left
    ])


# ---- captions ----------------------------------------------------------------------------
def caption_png(text, out, w, h, short):
    size = int(w * (0.085 if short else 0.034))
    fnt = fonts.font(size)
    tmp = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    text = text.upper() if short else text
    lines = fonts.wrap(tmp, text, fnt, w * 0.86)
    stroke = max(3, size // 9)
    line_h = int(size * 1.18)
    bw = int(max(tmp.textlength(l, font=fnt) for l in lines)) + stroke * 2 + 40
    bh = line_h * len(lines) + stroke * 2 + 24
    img = Image.new("RGBA", (bw, bh), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    if not short:
        d.rounded_rectangle((0, 0, bw, bh), radius=18, fill=(0, 0, 0, 150))
    for i, line in enumerate(lines):
        lw = d.textlength(line, font=fnt)
        d.text(((bw - lw) / 2, 12 + i * line_h), line, font=fnt,
               fill=(255, 230, 0) if short and i == 0 and len(lines) > 1 else (255, 255, 255),
               stroke_width=stroke if short else 0, stroke_fill=(0, 0, 0))
    img.save(out)
    return out, bw, bh


# ---- per-scene rendering -----------------------------------------------------------------
def render_scene(scene, idx, work, cfg, fmt):
    fc, vc = cfg[fmt], cfg["video"]
    w, h, fps = fc["width"], fc["height"], vc["fps"]
    short = fmt == "short"
    frames = math.ceil((scene["audio_dur"] + vc["scene_tail_seconds"]) * fps)
    dur = frames / fps
    seg = work / f"seg-{idx:03d}.mp4"
    wav = work / f"seg-{idx:03d}.wav"

    run(["ffmpeg", "-y", "-v", "error", "-i", scene["audio"], "-af", "apad",
         "-t", f"{dur:.6f}", "-ar", "44100", "-ac", "2", wav])

    inputs, base = [], ""
    vis = scene["visual"]
    if vis["kind"] == "video":
        inputs = ["-stream_loop", "-1", "-i", vis["path"]]
        base = (f"[0:v]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},"
                f"setsar=1,fps={fps},trim=duration={dur:.6f},setpts=PTS-STARTPTS[v0]")
    elif vis["kind"] == "image":
        still = prepare_still(vis["path"], work / f"still-{idx:03d}.jpg", w, h)
        z, x, y = ken_burns(frames)
        inputs = ["-i", still]
        base = (f"[0:v]zoompan=z='{z}':x='{x}':y='{y}':d={frames}:s={w}x{h}:fps={fps},"
                f"setsar=1,trim=duration={dur:.6f}[v0]")
    else:
        inputs = ["-f", "lavfi", "-i", f"color=c=0x101418:s={w}x{h}:r={fps}"]
        base = f"[0:v]trim=duration={dur:.6f},setsar=1[v0]"

    graph, last = [base], "v0"
    if fc["burn_captions"]:
        for k, cap in enumerate(scene["captions"]):
            png, bw, bh = caption_png(cap["text"], work / f"cap-{idx:03d}-{k:03d}.png", w, h, short)
            inputs += ["-i", png]
            y_pos = int(h * 0.66) if short else int(h * 0.86 - bh)
            nxt = f"v{k + 1}"
            graph.append(f"[{last}][{k + 1}:v]overlay=x=(W-w)/2:y={y_pos}:"
                         f"enable='between(t,{cap['start']:.3f},{cap['end'] + 0.05:.3f})'[{nxt}]")
            last = nxt
    run(["ffmpeg", "-y", "-v", "error", *inputs, "-filter_complex", ";".join(graph),
         "-map", f"[{last}]", "-frames:v", str(frames), "-r", str(fps), "-an",
         "-c:v", "libx264", "-preset", vc["preset"], "-crf", str(vc["crf"]),
         "-pix_fmt", "yuv420p", "-threads", "2", seg])
    return seg, wav, dur


def assemble(scenes, work, out_path, cfg, fmt):
    """scenes: [{audio, audio_dur, visual, captions}] -> final mp4. Returns scene start times."""
    work = Path(work)
    fonts.font_path()  # fetch once before threads start
    workers = max(1, min(4, (os.cpu_count() or 2) // 2))
    log.info("Rendering %d scenes with %d workers", len(scenes), workers)
    with ThreadPoolExecutor(workers) as ex:
        results = list(ex.map(lambda a: render_scene(a[1], a[0], work, cfg, fmt), enumerate(scenes)))

    starts, t = [], 0.0
    for _, _, d in results:
        starts.append(t)
        t += d
    (work / "video.txt").write_text("".join(f"file '{r[0].name}'\n" for r in results))
    (work / "audio.txt").write_text("".join(f"file '{r[1].name}'\n" for r in results))
    run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", work / "video.txt",
         "-c", "copy", work / "video.mp4"])
    run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", work / "audio.txt",
         "-c", "copy", work / "narration.wav"])

    music = sorted((ROOT / "assets" / "music").glob("*.mp3"))
    cmd = ["ffmpeg", "-y", "-v", "error", "-i", work / "video.mp4", "-i", work / "narration.wav"]
    if music:
        track = random.choice(music)
        vol = cfg["video"]["music_volume"]
        cmd += ["-stream_loop", "-1", "-i", track, "-filter_complex",
                f"[2:a]volume={vol},afade=t=out:st={max(t - 3, 0):.2f}:d=3[m];"
                f"[1:a][m]amix=inputs=2:duration=first:normalize=0[a]",
                "-map", "0:v", "-map", "[a]"]
        log.info("Background music: %s", track.name)
    else:
        cmd += ["-map", "0:v", "-map", "1:a"]
    cmd += ["-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-t", f"{t:.3f}",
            "-movflags", "+faststart", out_path]
    run(cmd)
    log.info("Video ready: %s (%.1fs)", out_path, ffprobe_duration(out_path))
    return starts, t


def write_srt(scenes, starts, path):
    lines, n = [], 1
    for sc, off in zip(scenes, starts):
        for cap in sc["captions"]:
            lines.append(f"{n}\n{srt_ts(off + cap['start'])} --> {srt_ts(off + cap['end'])}\n"
                         f"{cap['text']}\n")
            n += 1
    Path(path).write_text("\n".join(lines))
    return path
