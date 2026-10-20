"""Sharper pictures: Real-ESRGAN General x4v3 (BSD-3, Qualcomm AI Hub's ONNX export) upscales each AI
picture before the camera moves. The free image model only draws 1024x1024, and a vertical Short
crops a ~576 px wide strip of it, so without this every shot is stretched almost 2x."""
import hashlib
import io
import threading
import zipfile

import numpy as np
from PIL import Image

from .common import ROOT, http, log

URL = ("https://qaihub-public-assets.s3.us-west-2.amazonaws.com/qai-hub-models/models/"
       "real_esrgan_general_x4v3/releases/v0.64.0/real_esrgan_general_x4v3-onnx-float.zip")
SHA256 = "fc094329b24a00c936ebc1cc4c51bc59633e7dc36f4a553344342171cbd3c7cf"
DIR = ROOT / "models" / "real_esrgan_general_x4v3"
TILE, PAD = 128, 8          # the export takes fixed 128x128 tiles; overlap hides tile seams
_session = None
_lock = threading.Lock()


def _model():
    global _session
    with _lock:
        if _session is None:
            onnx = DIR / "real_esrgan_general_x4v3.onnx"
            if not onnx.exists():
                data = http().get(URL, timeout=120).content
                if hashlib.sha256(data).hexdigest() != SHA256:
                    raise RuntimeError("upscaler download failed its checksum")
                DIR.mkdir(parents=True, exist_ok=True)
                with zipfile.ZipFile(io.BytesIO(data)) as z:
                    for name in z.namelist():
                        if name.endswith((".onnx", ".data")):
                            (DIR / name.split("/")[-1]).write_bytes(z.read(name))
            import onnxruntime as ort
            _session = ort.InferenceSession(str(onnx), providers=["CPUExecutionProvider"])
    return _session


def upscale(path, target_long=2048):
    """Upscale the picture at `path` in place to `target_long` px on its long side (4x model, then
    downscaled with Lanczos). Skips pictures that are already that big."""
    im = Image.open(path).convert("RGB")
    if max(im.size) >= target_long:
        return False
    s = _model()
    a = np.asarray(im, dtype=np.float32) / 255.0
    h, w, _ = a.shape
    step = TILE - 2 * PAD
    ph, pw = -(-h // step) * step, -(-w // step) * step
    src = np.pad(a, ((PAD, ph - h + PAD), (PAD, pw - w + PAD), (0, 0)), mode="reflect")
    out = np.zeros((ph * 4, pw * 4, 3), dtype=np.float32)
    for y in range(0, ph, step):
        for x in range(0, pw, step):
            tile = src[y:y + TILE, x:x + TILE].transpose(2, 0, 1)[None]
            up = s.run(None, {"image": np.ascontiguousarray(tile)})[0][0].transpose(1, 2, 0)
            out[y * 4:(y + step) * 4, x * 4:(x + step) * 4] = up[PAD * 4:(PAD + step) * 4, PAD * 4:(PAD + step) * 4]
    big = Image.fromarray((out[:h * 4, :w * 4].clip(0, 1) * 255).round().astype(np.uint8))
    scale = target_long / max(big.size)
    big = big.resize((round(big.width * scale), round(big.height * scale)), Image.LANCZOS)
    big.save(path, quality=94)
    return True


def upscale_all(paths, target_long=2048):
    n = 0
    for p in paths:
        try:
            n += bool(upscale(p, target_long))
        except Exception as e:   # a soft picture is better than no video
            log.warning("Upscale skipped for %s: %s", p, e)
            break
    if n:
        log.info("Upscaled %d pictures to %d px", n, target_long)
    return n
