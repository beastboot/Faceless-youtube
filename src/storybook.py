"""Storybook animation: a painted scene comes alive with a slow camera move, drifting light
particles (fireflies, sparkles, dust, leaves, rain, snow, embers) and a soft breathing glow.
The look of animated spiritual/story channels, rendered free with numpy + ffmpeg."""
import math
import subprocess

import numpy as np
from PIL import Image, ImageFilter, ImageOps

from .common import log

AMBIENTS = ("fireflies", "sparkles", "dust", "leaves", "rain", "snow", "embers", "none")


class Particles:
    def __init__(self, kind, w, h, rng):
        self.kind, self.w, self.h, self.rng = kind, w, h, rng
        n = {"fireflies": 38, "sparkles": 46, "dust": 70, "leaves": 16, "rain": 220,
             "snow": 120, "embers": 50}.get(kind, 0)
        self.x = rng.random(n) * w
        self.y = rng.random(n) * h
        self.phase = rng.random(n) * 6.283
        self.size = rng.random(n)
        s = w / 1920
        self.vx, self.vy = {
            "fireflies": (rng.normal(0, 18, n) * s, rng.normal(-6, 14, n) * s),
            "sparkles": (rng.normal(0, 6, n) * s, rng.normal(-10, 6, n) * s),
            "dust": (rng.normal(8, 6, n) * s, rng.normal(-3, 4, n) * s),
            "leaves": (rng.normal(40, 15, n) * s, rng.uniform(60, 110, n) * s),
            "rain": (np.full(n, -60.0) * s, rng.uniform(1500, 2000, n) * s),
            "snow": (rng.normal(0, 20, n) * s, rng.uniform(40, 90, n) * s),
            "embers": (rng.normal(0, 20, n) * s, rng.uniform(-120, -60, n) * s),
        }.get(kind, (np.zeros(n), np.zeros(n)))
        self.n = n
        yy, xx = np.mgrid[-24:25, -24:25]
        self.blob = np.exp(-(xx ** 2 + yy ** 2) / (2 * 7.0 ** 2)).astype(np.float32)   # soft glow sprite

    def step(self, dt, t):
        self.x = (self.x + self.vx * dt + (np.sin(t * 0.9 + self.phase) * 12 * dt if self.kind == "fireflies" else 0)) % self.w
        self.y = (self.y + self.vy * dt) % self.h

    def draw(self, frame, t):
        """frame: float32 HxWx3 in 0..255, modified in place."""
        if self.n == 0:
            return frame
        h, w = frame.shape[:2]
        if self.kind == "rain":
            streak = np.zeros((h, w), np.float32)
            for x, y in zip(self.x.astype(int), self.y.astype(int)):
                y0, y1 = max(0, y), min(h, y + int(30 * w / 1920))
                if 0 <= x < w and y1 > y0:
                    streak[y0:y1, x] = 70
            frame += streak[..., None] * np.array([0.8, 0.85, 1.0], np.float32)
            return frame
        colors = {"fireflies": (255, 236, 140), "sparkles": (255, 255, 230), "dust": (255, 240, 210),
                  "leaves": (190, 150, 60), "snow": (255, 255, 255), "embers": (255, 140, 40)}
        col = np.array(colors.get(self.kind, (255, 255, 255)), np.float32) / 255.0
        for i in range(self.n):
            if self.kind in ("fireflies", "sparkles"):
                a = 0.5 + 0.5 * math.sin(t * (2.5 if self.kind == "sparkles" else 1.4) + self.phase[i] * 3)
            else:
                a = 0.55
            scale = 0.5 + self.size[i] * (1.3 if self.kind != "dust" else 0.6)
            r = min(24, max(3, int(16 * scale)))
            sprite = self.blob[24 - r:25 + r, 24 - r:25 + r] * a * (230 if self.kind != "dust" else 120)
            x, y = int(self.x[i]), int(self.y[i])
            y0, y1, x0, x1 = y - r, y + r + 1, x - r, x + r + 1
            sy0, sx0 = max(0, -y0), max(0, -x0)
            y0, x0, y1, x1 = max(0, y0), max(0, x0), min(h, y1), min(w, x1)
            if y1 <= y0 or x1 <= x0:
                continue
            spr = sprite[sy0:sy0 + (y1 - y0), sx0:sx0 + (x1 - x0)]
            frame[y0:y1, x0:x1] += spr[..., None] * col
        return frame


MOVES = ("zoom_in", "zoom_out", "pan_left", "pan_right", "rise", "push_focus")


def _crop_box(move, t, W, H, w, h):
    """Camera window (x, y, cw, ch) on the oversized painting at time t in 0..1."""
    e = t * t * (3 - 2 * t)
    if move == "zoom_out":
        z = 1.16 - 0.14 * e
    elif move in ("pan_left", "pan_right", "rise"):
        z = 1.12
    elif move == "push_focus":
        z = 1.0 + 0.22 * e
    else:
        z = 1.0 + 0.14 * e
    cw, ch = W / z, H / z
    cx, cy = W / 2, H / 2
    if move == "pan_left":
        cx = W / 2 + (W - cw) / 2 * (1 - 2 * e)
    elif move == "pan_right":
        cx = W / 2 - (W - cw) / 2 * (1 - 2 * e)
    elif move == "rise":
        cy = H / 2 + (H - ch) / 2 * (1 - 2 * e)
    return cx - cw / 2, cy - ch / 2, cw, ch


def render(src, out, w, h, fps, frames, move="zoom_in", ambient="none", seed=0, crf=23, preset="veryfast",
           glow=True):
    rng = np.random.default_rng(seed)
    over = 1.25
    W, H = int(w * over), int(h * over)
    paint = ImageOps.fit(Image.open(src).convert("RGB"), (W, H), Image.LANCZOS)
    if glow:   # soft bloom layer, breathed in and out
        bloom = np.asarray(paint.filter(ImageFilter.GaussianBlur(18))).astype(np.float32)
    parts = Particles(ambient if ambient in AMBIENTS else "none", w, h, rng)
    move = move if move in MOVES else "zoom_in"
    proc = subprocess.Popen(
        ["ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w}x{h}",
         "-r", str(fps), "-i", "-", "-c:v", "libx264", "-preset", preset, "-crf", str(crf),
         "-pix_fmt", "yuv420p", str(out)], stdin=subprocess.PIPE)
    bloom_img = Image.fromarray(bloom.astype(np.uint8)) if glow else None
    try:
        for f in range(frames):
            t = f / max(1, frames - 1)
            sec = f / fps
            x, y, cw, ch = _crop_box(move, t, W, H, w, h)
            box = (x, y, x + cw, y + ch)
            frame = np.asarray(paint.resize((w, h), Image.BILINEAR, box=box)).astype(np.float32)
            if glow:
                b = np.asarray(bloom_img.resize((w, h), Image.BILINEAR, box=box)).astype(np.float32)
                k = 0.10 + 0.05 * math.sin(sec * 1.1 + seed)
                frame = frame * (1 - k) + np.maximum(frame, b) * k * 1.6       # screen-ish glow
            parts.step(1 / fps, sec)
            parts.draw(frame, sec)
            proc.stdin.write(np.clip(frame, 0, 255).astype(np.uint8).tobytes())
    finally:
        proc.stdin.close()
        if proc.wait() != 0:
            raise RuntimeError(f"ffmpeg failed rendering storybook clip {out}")
    log.debug("storybook clip %s (%s, %s, %d frames)", out, move, ambient, frames)
    return out
