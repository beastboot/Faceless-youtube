"""2.5D camera clips: a painting plus its depth map becomes a short video where near things (people)
slide against the far background and grow faster when the camera pushes in (the After Effects
"displacement map" look). Rendered with OpenCV on the CPU, so it is fast on CI runners too."""
import math
import subprocess
from pathlib import Path

import numpy as np

from .common import log


def _ease(t):
    # same curve as the Remotion renderer's ease (approx. cubic-bezier(0.33, 0, 0.2, 1))
    return 1 - (1 - t) ** 2.6 if t < 1 else 1.0


def camera(move, t, seconds):
    """zoom, pan(x,y), shift(x,y), dolly for time t in [0,1] (mirrors remotion/src/Parallax.tsx)."""
    e = _ease(t)
    drift = (0.004 * math.sin(seconds * 0.7), 0.003 * math.cos(seconds * 0.55))
    zoom, pan, shift, dolly = 1.08 + 0.06 * e, (0.0, 0.0), drift, 0.05 + 0.07 * e
    if move == "zoom_out":
        zoom, dolly = 1.16 - 0.08 * e, 0.12 - 0.08 * e
    elif move == "pan_left":
        zoom, shift, dolly = 1.12, (0.028 - 0.056 * e, drift[1]), 0.03
    elif move == "pan_right":
        zoom, shift, dolly = 1.12, (-0.028 + 0.056 * e, drift[1]), 0.03
    elif move == "rise":
        zoom, shift, dolly = 1.12, (drift[0], 0.022 - 0.044 * e), 0.03
    elif move == "push_focus":
        zoom, dolly = 1.06 + 0.14 * e, 0.04 + 0.12 * e
    elif move == "detail_left":
        zoom, pan, shift, dolly = 1.7 + 0.08 * e, (-0.17 + 0.03 * e, 0.0), (0.012 - 0.024 * e, drift[1]), 0.04
    elif move == "detail_right":
        zoom, pan, shift, dolly = 1.7 + 0.08 * e, (0.17 - 0.03 * e, 0.0), (-0.012 + 0.024 * e, drift[1]), 0.04
    elif move == "detail_top":
        zoom, pan, shift, dolly = 1.7 + 0.08 * e, (0.0, -0.14 + 0.03 * e), (drift[0], 0.012 - 0.024 * e), 0.04
    return zoom, pan, shift, dolly


def _frames(image, depth, move, frames, width, height, fps, focus=0.3):
    """Yield BGR frames of the 2.5D camera move."""
    import cv2
    img = cv2.imread(str(image), cv2.IMREAD_COLOR)
    dep = cv2.imread(str(depth), cv2.IMREAD_GRAYSCALE)
    ih, iw = img.shape[:2]
    # work at a resolution a little above the output so zooms stay sharp, never above the source
    scale = min(1.0, max(width, height) * 1.25 / max(iw, ih))
    if scale < 1:
        img = cv2.resize(img, (int(iw * scale), int(ih * scale)), interpolation=cv2.INTER_AREA)
    ih, iw = img.shape[:2]
    dep = cv2.resize(dep, (iw, ih), interpolation=cv2.INTER_LINEAR).astype(np.float32) / 255
    ia, fa = iw / ih, width / height
    cover = (fa / ia, 1.0) if ia > fa else (1.0, ia / fa)       # object-fit: cover
    vx, vy = np.meshgrid((np.arange(width, dtype=np.float32) + 0.5) / width - 0.5,
                         (np.arange(height, dtype=np.float32) + 0.5) / height - 0.5)
    for f in range(frames):
        zoom, pan, shift, dolly = camera(move, min(1.0, f / max(1, frames)), f / fps)
        px = 0.5 + vx * (cover[0] / zoom) + pan[0]
        py = 0.5 + vy * (cover[1] / zoom) + pan[1]
        d = cv2.remap(dep, px * iw - 0.5, py * ih - 0.5, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
        k = d - focus
        sx = px - (shift[0] + (px - 0.5) * dolly) * k
        sy = py - (shift[1] + (py - 0.5) * dolly) * k
        yield cv2.remap(img, sx * iw - 0.5, sy * ih - 0.5, cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)


def render_clip(image, depth, move, frames, width, height, fps, out, crf=16):
    """Write an H.264 clip of `frames` frames; returns its path."""
    out = Path(out)
    if out.exists():
        return out
    proc = subprocess.Popen(
        ["ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{width}x{height}",
         "-r", str(fps), "-i", "-", "-c:v", "libx264", "-preset", "veryfast", "-crf", str(crf),
         "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(out)], stdin=subprocess.PIPE)
    try:
        for frame in _frames(image, depth, move, frames, width, height, fps):
            proc.stdin.write(frame.tobytes())
    finally:
        proc.stdin.close()
        if proc.wait() != 0:
            out.unlink(missing_ok=True)
            raise RuntimeError(f"ffmpeg failed for {out.name}")
    return out


def render_frames(image, depth, move, frames, width, height, fps, out, quality=88):
    """Write the move as numbered JPEGs (00000.jpg...) in the folder `out`; returns the folder.
    The renderer shows them as plain pictures, which is much faster than decoding a video."""
    import cv2
    out = Path(out)
    if (out / f"{frames - 1:05d}.jpg").exists():
        return out
    out.mkdir(parents=True, exist_ok=True)
    for i, frame in enumerate(_frames(image, depth, move, frames, width, height, fps)):
        cv2.imwrite(str(out / f"{i:05d}.jpg"), frame, [cv2.IMWRITE_JPEG_QUALITY, quality])
    return out


def render_clips(jobs, workers=None, fn=None):
    """jobs: [(key, kwargs)] -> {key: path}; a failed clip leaves that shot on the flat camera."""
    import os
    from concurrent.futures import ThreadPoolExecutor
    done = {}

    def one(job):
        key, kw = job
        try:
            return key, (fn or render_clip)(**kw)
        except Exception as e:
            log.warning("3D camera clip failed (%s); flat camera for that shot", e)
            return key, None

    with ThreadPoolExecutor(workers or max(1, (os.cpu_count() or 2))) as ex:
        for key, path in ex.map(one, jobs):
            if path:
                done[key] = path
    return done
