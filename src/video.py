"""Assemble the final video with ffmpeg: Ken Burns stills, stock clips, captions, narration, music."""
import math
import os
import random
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageOps

from . import audio, fonts, motion, sketch, storybook
from .common import ROOT, ffprobe_duration, log, run, srt_ts


# ---- stills -------------------------------------------------------------------------------
def prepare_still(src, out, w, h, scale=2, fill=False):
    """Fit any image into a (scale*w x scale*h) canvas: blurred cover background + sharp foreground,
    or crop to fill the frame (fill=True, for AI illustrations composed for any crop)."""
    W, H = w * scale, h * scale
    im = ImageOps.exif_transpose(Image.open(src)).convert("RGB")
    bg = ImageOps.fit(im, (W, H), Image.LANCZOS).filter(ImageFilter.GaussianBlur(40))
    bg = Image.blend(bg, Image.new("RGB", (W, H), (0, 0, 0)), 0.35)
    ratio_img, ratio_out = im.width / im.height, W / H
    if fill or abs(ratio_img - ratio_out) / ratio_out < 0.2:
        canvas = ImageOps.fit(im, (W, H), Image.LANCZOS)  # close enough: fill the frame
    else:
        fg = ImageOps.contain(im, (W, H), Image.LANCZOS)
        canvas = bg
        canvas.paste(fg, ((W - fg.width) // 2, (H - fg.height) // 2))
    canvas.save(out, quality=92)
    return out


def ken_burns(frames, short=False):
    """A camera move for a still. Shorts get punchier moves (punch-in, shake) like anime edits."""
    n = max(frames - 1, 1)
    c = "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"
    k = 10  # frames for a punch-in
    calm = [
        (f"1+0.18*on/{n}", *c),                                   # slow zoom in
        (f"1.18-0.18*on/{n}", *c),                                # slow zoom out
        ("1.15", f"(iw-iw/zoom)*on/{n}", c[1]),                   # pan left -> right
        ("1.15", f"(iw-iw/zoom)*(1-on/{n})", c[1]),               # pan right -> left
        ("1.15", c[0], f"(ih-ih/zoom)*(1-on/{n})"),               # tilt up
    ]
    punchy = [
        (f"if(lt(on,{k}),1+0.22*on/{k},1.22+0.05*(on-{k})/{n})", *c),            # punch-in then drift
        ("1.1", f"{c[0]}+8*sin(on*0.9)", f"{c[1]}+6*cos(on*1.3)"),               # handheld shake
        (f"1.25-0.2*on/{n}", f"{c[0]}+5*sin(on*0.7)", c[1]),                     # pull back + sway
    ]
    return random.choice(calm + punchy * 2 if short else calm * 2 + punchy[:1])


# ---- captions ----------------------------------------------------------------------------
def caption_png(text, out, w, h, short, lang="en"):
    size = int(w * ((0.085 if lang == "en" else 0.075) if short else 0.034))
    fnt = fonts.font(size, lang)
    tmp = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    text = text.upper() if short and lang == "en" else text
    lines = fonts.wrap(tmp, text, fnt, w * 0.86)
    stroke = max(3, size // 9)
    line_h = int(size * (1.18 if lang == "en" else 1.45))  # Devanagari needs room for matras
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
def shot_wav(scene, idx, work, cfg):
    """Shot narration padded to a whole number of frames (+ whoosh on a new picture)."""
    vc = cfg["video"]
    fps = vc["fps"]
    dur = math.ceil((scene["audio_dur"] + scene.get("tail", vc["scene_tail_seconds"])) * fps) / fps
    wav = work / f"seg-{idx:03d}.wav"
    if scene.get("whoosh") and vc.get("sfx_volume", 0) > 0:
        # Transition whoosh under the first moments of every new shot.
        run(["ffmpeg", "-y", "-v", "error", "-i", scene["audio"], "-i", audio.whoosh(), "-filter_complex",
             f"[0]aresample=44100,aformat=channel_layouts=stereo,apad[n];"
             f"[1]volume={vc['sfx_volume']}[w];[n][w]amix=inputs=2:duration=first:normalize=0[a]",
             "-map", "[a]", "-t", f"{dur:.6f}", "-ar", "44100", "-ac", "2", wav])
    else:
        run(["ffmpeg", "-y", "-v", "error", "-i", scene["audio"], "-af", "apad",
             "-t", f"{dur:.6f}", "-ar", "44100", "-ac", "2", wav])
    return wav, dur


def render_scene(scene, idx, work, cfg, fmt):
    fc, vc = cfg[fmt], cfg["video"]
    w, h, fps = fc["width"], fc["height"], vc["fps"]
    short = fmt == "short"
    frames = math.ceil((scene["audio_dur"] + scene.get("tail", vc["scene_tail_seconds"])) * fps)
    dur = frames / fps
    # Video runs a little past the narration so the next shot can transition over it.
    extra = 0 if scene.get("last") else round(vc.get("transition_seconds", 0) * fps)
    vframes = frames + extra
    vdur = vframes / fps
    seg = work / f"seg-{idx:03d}.mp4"
    wav, dur = shot_wav(scene, idx, work, cfg)

    inputs, base = [], ""
    vis = scene["visual"]
    if vis["kind"] == "video":
        inputs = ["-stream_loop", "-1", "-i", vis["path"]]
        base = (f"[0:v]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},"
                f"setsar=1,fps={fps},trim=duration={vdur:.6f},setpts=PTS-STARTPTS[v0]")
    elif vis["kind"] == "image" and vc.get("animation") == "storybook":
        # Painted scene comes alive: camera move + drifting light particles + soft glow.
        clip = work / f"story-{idx:03d}.mp4"
        move = vis.get("move") or random.Random(idx).choice(storybook.MOVES)
        if not scene.get("whoosh", True) and idx > 0:   # same painting continues: change the move
            move = {"zoom_in": "pan_right", "zoom_out": "pan_left"}.get(move, "zoom_out")
        storybook.render(vis["path"], clip, w, h, fps, vframes, move=move,
                         ambient=vis.get("ambient") or "none", seed=idx, crf=vc["crf"], preset=vc["preset"])
        inputs = ["-i", clip]
        base = f"[0:v]setsar=1,trim=duration={vdur:.6f},setpts=PTS-STARTPTS[v0]"
    elif vis["kind"] == "image" and vc.get("animation") == "sketch":
        # Pen draws the cartoon, colours wash in, camera drifts (src/sketch.py).
        clip = work / f"sketch-{idx:03d}.mp4"
        sketch.render(vis["path"], clip, w, h, fps, vframes,
                      draw_share=vc.get("sketch_draw_share", 0.55) if scene.get("whoosh", True) or idx == 0 else 0,
                      seed=idx, crf=vc["crf"], preset=vc["preset"])
        inputs = ["-i", clip]
        base = f"[0:v]setsar=1,trim=duration={vdur:.6f},setpts=PTS-STARTPTS[v0]"
    elif vis["kind"] == "image":
        still = prepare_still(vis["path"], work / f"still-{idx:03d}.jpg", w, h, fill=vis.get("fill", False))
        z, x, y = ken_burns(frames, short)
        inputs = ["-i", still]
        base = (f"[0:v]zoompan=z='{z}':x='{x}':y='{y}':d={vframes}:s={w}x{h}:fps={fps},"
                f"setsar=1,trim=duration={vdur:.6f}[v0]")
    else:
        inputs = ["-f", "lavfi", "-i", f"color=c=0x101418:s={w}x{h}:r={fps}"]
        base = f"[0:v]trim=duration={vdur:.6f},setsar=1[v0]"

    graph, last = [base], "v0"
    if fc["burn_captions"]:
        for k, cap in enumerate(scene["captions"]):
            png, bw, bh = caption_png(cap["text"], work / f"cap-{idx:03d}-{k:03d}.png", w, h, short,
                                      scene.get("caption_lang", scene.get("lang", "en")))
            inputs += ["-i", png]
            y_pos = int(h * 0.66) if short else int(h * 0.86 - bh)
            nxt = f"v{k + 1}"
            graph.append(f"[{last}][{k + 1}:v]overlay=x=(W-w)/2:y={y_pos}:"
                         f"enable='between(t,{cap['start']:.3f},{cap['end'] + 0.05:.3f})'[{nxt}]")
            last = nxt
    run(["ffmpeg", "-y", "-v", "error", *inputs, "-filter_complex", ";".join(graph),
         "-map", f"[{last}]", "-frames:v", str(vframes), "-r", str(fps), "-an",
         "-c:v", "libx264", "-preset", vc["preset"], "-crf", str(vc["crf"]),
         "-pix_fmt", "yuv420p", "-threads", "2", seg])
    return seg, wav, dur


def assemble(scenes, work, out_path, cfg, fmt):
    """scenes: [{audio, audio_dur, visual, captions}] -> final mp4. Returns scene start times."""
    work = Path(work)
    for lang in {s.get("caption_lang", s.get("lang", "en")) for s in scenes}:
        fonts.font_path(lang)  # fetch once before threads start
    workers = max(1, min(4, (os.cpu_count() or 2) // 2))
    for i, sc in enumerate(scenes):
        sc["last"] = i == len(scenes) - 1
    if cfg["video"].get("renderer") == "remotion":
        # Motion-graphics renderer: audio per shot here, all visuals in one Remotion render.
        with ThreadPoolExecutor(workers) as ex:
            wavs = list(ex.map(lambda a: shot_wav(a[1], a[0], work, cfg), enumerate(scenes)))
        results = [(None, w_, d) for w_, d in wavs]
    else:
        log.info("Rendering %d scenes with %d workers", len(scenes), workers)
        with ThreadPoolExecutor(workers) as ex:
            results = list(ex.map(lambda a: render_scene(a[1], a[0], work, cfg, fmt), enumerate(scenes)))

    starts, t = [], 0.0
    for _, _, d in results:
        starts.append(t)
        t += d
    (work / "audio.txt").write_text("".join(f"file '{r[1].name}'\n" for r in results))
    xd = cfg["video"].get("transition_seconds", 0)
    remotion_ok = False
    if cfg["video"].get("renderer") == "remotion":
        try:
            motion.render(scenes, starts, [r[2] for r in results], work, work / "video.mp4", cfg, fmt)
            remotion_ok = True
        except Exception as e:
            # Never lose a video to the renderer: fall back to the built-in ffmpeg renderer.
            log.error("Remotion render failed (%s); falling back to the ffmpeg renderer", e)
            with ThreadPoolExecutor(workers) as ex:
                results = list(ex.map(lambda a: render_scene(a[1], a[0], work, cfg, fmt), enumerate(scenes)))
    if remotion_ok:
        pass
    elif xd and len(results) > 1:
        join_with_transitions(results, scenes, work / "video.mp4", cfg, fmt, xd)
    else:
        (work / "video.txt").write_text("".join(f"file '{r[0].name}'\n" for r in results))
        run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", work / "video.txt",
             "-c", "copy", work / "video.mp4"])
    run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", work / "audio.txt",
         "-c", "copy", work / "narration.wav"])

    mood = next((s.get("mood") for s in scenes if s.get("mood")), None)
    track, is_file = audio.music_for(t, work, mood)
    vc = cfg["video"]
    vol = vc["music_volume"] if is_file else vc.get("bed_volume", 0.35)
    # Sound design synced to picture: pop on graphics, impact on big reveals, riser into chapters.
    events = []
    for sc, st in zip(scenes, starts):
        g = (sc.get("graphic") or {}).get("type")
        if sc.get("impact"):   # the picture shakes and flashes on this line (Remotion)
            events.append((st + 0.05, audio.impact(), vc.get("impact_volume", 0.45) * 1.3))
        elif g in ("counter", "fact"):
            events.append((st + 0.25, audio.impact(), vc.get("impact_volume", 0.45)))
        elif g:
            events.append((st + 0.25, audio.pop(), vc.get("pop_volume", 0.35)))
        if sc.get("chapter_start") and st > 2:
            events.append((st - 1.55, audio.riser(), vc.get("riser_volume", 0.4)))
    sfx = audio.sfx_track(events, t, work / "sfx.wav") if vc.get("sound_design", True) else None
    # Voice: gentle compression; music ducks under the narration (sidechain) like a documentary
    # mix; SFX on top; final mix normalised to YouTube's -14 LUFS.
    # Studio voice chain: rumble cut, less mud, presence for phone speakers, soft de-ess, glue compression.
    voice_fx = ("highpass=f=75,equalizer=f=250:t=q:w=1.2:g=-2,equalizer=f=3200:t=q:w=1.4:g=2.5,"
                "equalizer=f=10000:t=q:w=1:g=1.5,deesser=i=0.3,"
                "acompressor=threshold=-20dB:ratio=3:attack=5:release=120:makeup=2")
    audio_graph = (f"[1:a]{voice_fx},asplit=2[v][vkey];"
                   f"[2:a]volume={vol * (2.2 if is_file else 1)},afade=t=out:st={max(t - 3, 0):.2f}:d=3[m0];"
                   f"[m0][vkey]sidechaincompress=threshold=0.03:ratio=8:attack=30:release=450[m];"
                   + (f"[3:a]anull[fx];[v][m][fx]amix=inputs=3:duration=first:normalize=0," if sfx else
                      "[v][m]amix=inputs=2:duration=first:normalize=0,")
                   + "loudnorm=I=-14:TP=-1.5:LRA=11[a]")
    up_h = (vc.get("upload_height") or {}).get(fmt)
    w, h = cfg[fmt]["width"], cfg[fmt]["height"]
    if up_h and up_h > h:
        # Uploading above 1080p makes YouTube use its higher-quality VP9 encode for every viewer.
        up_w = int(round(w * up_h / h / 2)) * 2
        video_args = ["-filter_complex", f"[0:v]scale={up_w}:{up_h}:flags=lanczos,setsar=1[vv];" + audio_graph,
                      "-map", "[vv]", "-map", "[a]", "-c:v", "libx264", "-preset", vc["preset"],
                      "-crf", str(vc["crf"]), "-pix_fmt", "yuv420p"]
    else:
        video_args = ["-filter_complex", audio_graph, "-map", "0:v", "-map", "[a]", "-c:v", "copy"]
    cmd = ["ffmpeg", "-y", "-v", "error", "-i", work / "video.mp4", "-i", work / "narration.wav",
           *(["-stream_loop", "-1"] if is_file else []), "-i", track,
           *(["-i", sfx] if sfx else []), *video_args]
    cmd += ["-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-t", f"{t:.3f}",
            "-movflags", "+faststart", out_path]
    run(cmd)
    log.info("Video ready: %s (%.1fs)", out_path, ffprobe_duration(out_path))
    return starts, t


def join_with_transitions(results, scenes, out, cfg, fmt, xd, batch=40):
    """xfade every cut. Segment k (except the last) is xd longer than its audio, and its successor
    starts transitioning in at exactly the audio boundary, so picture and narration stay in sync.
    Joined in batches to keep ffmpeg's open-file count sane on long videos."""
    vc = cfg["video"]
    styles = vc["transitions"][fmt]
    durs = [r[2] for r in results]

    def chain(seg_paths, seg_durs, seg_scenes, dst, tail_extra):
        inputs, graph, prev, offset = [], [], "0:v", 0.0
        for p in seg_paths:
            inputs += ["-i", p]
        for k in range(1, len(seg_paths)):
            offset += seg_durs[k - 1]
            # Same picture continuing (shared image) -> soft dissolve; new picture -> styled move.
            kind = random.choice(styles) if seg_scenes[k].get("whoosh") else "fade"
            graph.append(f"[{prev}][{k}:v]xfade=transition={kind}:duration={xd}:offset={offset:.4f}[x{k}]")
            prev = f"x{k}"
        total = sum(seg_durs) + tail_extra
        run(["ffmpeg", "-y", "-v", "error", *inputs, "-filter_complex", ";".join(graph) or "[0:v]null[x0]",
             "-map", f"[{prev if graph else 'x0'}]", "-t", f"{total:.4f}", "-r", str(vc["fps"]),
             "-c:v", "libx264", "-preset", vc["preset"], "-crf", str(vc["crf"]), "-pix_fmt", "yuv420p", dst])

    paths = [r[0] for r in results]
    if len(paths) <= batch:
        chain(paths, durs, scenes, out, 0)
        return
    # Batch: each batch ends with its last segment's extra xd so batches can be xfaded together.
    parts, part_durs, part_scenes = [], [], []
    for b in range(0, len(paths), batch):
        sl = slice(b, b + batch)
        dst = out.with_name(f"part-{b // batch:02d}.mp4")
        last_batch = b + batch >= len(paths)
        chain(paths[sl], durs[sl], scenes[sl], dst, 0 if last_batch else xd)
        parts.append(dst)
        part_durs.append(sum(durs[sl]))
        part_scenes.append(scenes[b])
    chain(parts, part_durs, part_scenes, out, 0)


def write_srt(scenes, starts, path):
    lines, n = [], 1
    for sc, off in zip(scenes, starts):
        for cap in sc["captions"]:
            lines.append(f"{n}\n{srt_ts(off + cap['start'])} --> {srt_ts(off + cap['end'])}\n"
                         f"{cap['text']}\n")
            n += 1
    Path(path).write_text("\n".join(lines))
    return path
