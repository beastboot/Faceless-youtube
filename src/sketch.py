"""Sketch-cartoon animation: a pen draws each illustration's outlines on paper, then the colours
wash in and the camera drifts. Every shot is drawn live, so the video is animated, not a slideshow.

Frames are computed with numpy and piped straight into ffmpeg."""
import math
import random
import subprocess

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageOps

from .common import log

PAPER = np.array([247, 243, 234], dtype=np.uint8)   # warm off-white
INK = np.array([24, 24, 28], dtype=np.uint8)


def _pen(size=90):
    """Pencil sprite (RGBA) with its tip at the bottom-left corner."""
    im = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    s = size
    body = [(s * 0.18, s * 0.70), (s * 0.70, s * 0.18), (s * 0.84, s * 0.32), (s * 0.32, s * 0.84)]
    d.polygon(body, fill=(255, 196, 0, 255), outline=(60, 40, 0, 255))
    d.polygon([(s * 0.70, s * 0.18), (s * 0.78, s * 0.10), (s * 0.92, s * 0.24), (s * 0.84, s * 0.32)],
              fill=(230, 120, 140, 255), outline=(60, 40, 0, 255))                   # eraser
    d.polygon([(0, s), (s * 0.18, s * 0.70), (s * 0.32, s * 0.84)], fill=(240, 210, 160, 255),
              outline=(60, 40, 0, 255))                                              # wood tip
    d.polygon([(0, s), (s * 0.07, s * 0.88), (s * 0.12, s * 0.93)], fill=(20, 20, 20, 255))  # lead
    arr = np.asarray(im)
    return arr[..., :3], (arr[..., 3:4] / 255.0)


def _line_art(img):
    """Ink mask = lines, not dark areas: pixels clearly darker than their neighbourhood
    (outlines, hatching) plus edges between colour regions. Works on dark images too."""
    gray = ImageOps.grayscale(img)
    g = np.asarray(gray).astype(np.int16)
    local = np.asarray(gray.filter(ImageFilter.BoxBlur(7))).astype(np.int16)
    lines = (local - g) > 22
    edges = np.asarray(gray.filter(ImageFilter.GaussianBlur(1.5)).filter(ImageFilter.FIND_EDGES)) > 22
    ink = lines | edges
    ink_img = Image.fromarray((ink * 255).astype(np.uint8)).filter(ImageFilter.MedianFilter(3))
    ink_img = ink_img.filter(ImageFilter.MaxFilter(3))      # thicken so lines read at phone size
    return np.asarray(ink_img) > 0


def _draw_order(ink, cell, rng):
    """Rank ink pixels so the pen sweeps like a hand drawing: band by band, serpentine,
    skipping empty paper. Returns (order map, cell centres in drawing order)."""
    h, w = ink.shape
    rows, cols = math.ceil(h / cell), math.ceil(w / cell)
    band = 5
    seq = []
    for b in range(0, rows, band):
        for c in (range(cols) if (b // band) % 2 == 0 else range(cols - 1, -1, -1)):
            for r in range(b, min(b + band, rows)):
                seq.append((r, c))
    rank = np.full((rows, cols), -1, dtype=np.int32)
    centres = []
    for r, c in seq:
        if ink[r * cell:(r + 1) * cell, c * cell:(c + 1) * cell].any():
            rank[r, c] = len(centres)
            centres.append((c * cell + cell // 2, r * cell + cell // 2))
    n = max(len(centres), 1)
    order = np.repeat(np.repeat(rank, cell, 0), cell, 1)[:h, :w].astype(np.float32)
    order += rng.random((h, w), dtype=np.float32) * 0.999  # stroke texture inside a cell
    order[~ink] = np.inf
    return order, centres, n


def render(src, out, w, h, fps, frames, draw_share=0.55, seed=0, crf=23, preset="veryfast"):
    """Write a `frames`-long clip of the illustration being drawn, coloured and held."""
    rng = np.random.default_rng(seed)
    img = ImageOps.fit(Image.open(src).convert("RGB"), (w, h), Image.LANCZOS)
    color = np.asarray(img).astype(np.float32)
    ink = _line_art(img)
    order, centres, n = _draw_order(ink, max(16, w // 45), rng)
    paper = np.broadcast_to(PAPER, (h, w, 3)).astype(np.float32)
    line_frame = np.where(ink[..., None], INK, PAPER).astype(np.float32)
    pen_rgb, pen_a = _pen(max(60, w // 12))
    ph, pw = pen_a.shape[:2]

    if draw_share <= 0:          # continuing shot on the same picture: already drawn and coloured
        d_frames, c_frames = 0, 0
    else:
        d_frames = min(frames - 1, max(int(frames * draw_share), int(fps * 0.8)))
        c_frames = max(1, min(int(fps * 0.45), frames - d_frames))
    zoom_dir = random.Random(seed).choice([1, -1])
    proc = subprocess.Popen(
        ["ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w}x{h}",
         "-r", str(fps), "-i", "-", "-c:v", "libx264", "-preset", preset, "-crf", str(crf),
         "-pix_fmt", "yuv420p", str(out)], stdin=subprocess.PIPE)
    try:
        for f in range(frames):
            if f < d_frames:                                   # 1) pen draws the outlines
                k = (f + 1) / d_frames * n
                frame = np.where((order < k)[..., None], INK, PAPER).astype(np.uint8)
                if f < d_frames - 1 and centres:
                    x, y = centres[min(int(k), n - 1)]
                    y0, x0 = max(0, y - ph), max(0, x)
                    y1, x1 = min(h, y0 + ph), min(w, x0 + pw)
                    if y1 > y0 and x1 > x0:
                        a = pen_a[ph - (y1 - y0):, :x1 - x0]
                        region = frame[y0:y1, x0:x1].astype(np.float32)
                        frame[y0:y1, x0:x1] = (region * (1 - a) + pen_rgb[ph - (y1 - y0):, :x1 - x0] * a
                                               ).astype(np.uint8)
            elif f < d_frames + c_frames:                      # 2) colours wash in
                t = (f - d_frames + 1) / c_frames
                lines = np.where(ink[..., None], line_frame, paper)
                frame = (lines * (1 - t) + np.minimum(color, np.where(ink[..., None], 60, 255)) * t
                         ).astype(np.uint8)
            else:                                              # 3) hold with a slow drift
                t = (f - d_frames - c_frames) / max(1, frames - d_frames - c_frames)
                z = 1 + 0.05 * (t if zoom_dir > 0 else 1 - t)
                cw, ch = int(w / z), int(h / z)
                x0, y0 = (w - cw) // 2, (h - ch) // 2
                base = np.minimum(color, np.where(ink[..., None], 60, 255)).astype(np.uint8)
                frame = np.asarray(Image.fromarray(base[y0:y0 + ch, x0:x0 + cw]).resize((w, h), Image.BILINEAR))
            proc.stdin.write(np.ascontiguousarray(frame).tobytes())
    finally:
        proc.stdin.close()
        if proc.wait() != 0:
            raise RuntimeError(f"ffmpeg failed rendering sketch clip {out}")
    log.debug("sketch clip %s (%d frames, %d strokes)", out, frames, n)
    return out
