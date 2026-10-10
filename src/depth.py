"""Depth maps for the 2.5D camera: Depth-Anything-V2-Small (Apache-2.0) on the CPU via ONNX Runtime.
src/parallax.py uses them to move near things against the background (3D camera moves)."""
import threading
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

from .common import log

MODEL = ("onnx-community/depth-anything-v2-small", "onnx/model.onnx")
SIZE = 518                       # the model's native input size (multiple of 14)
_MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
_STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)
_session = None
_lock = threading.Lock()


def _load():
    global _session
    with _lock:
        if _session is None:
            import onnxruntime as ort
            from huggingface_hub import hf_hub_download
            _session = ort.InferenceSession(hf_hub_download(*MODEL), providers=["CPUExecutionProvider"])
    return _session


def depth_map(image_path, out_path=None):
    """Write `<image>.depth.png` (white = near) next to the image and return its path."""
    image_path = Path(image_path)
    out = Path(out_path) if out_path else image_path.with_suffix(".depth.png")
    if out.exists():
        return out
    im = Image.open(image_path).convert("RGB")
    x = (np.asarray(im.resize((SIZE, SIZE), Image.BICUBIC), dtype=np.float32) / 255 - _MEAN) / _STD
    sess = _load()
    with _lock:   # one inference at a time; ONNX Runtime already uses every core
        d = sess.run(None, {sess.get_inputs()[0].name: x.transpose(2, 0, 1)[None]})[0]
    d = d.reshape(d.shape[-2:])
    lo, hi = np.percentile(d, 1), np.percentile(d, 99)
    d = ((d - lo) / max(hi - lo, 1e-6)).clip(0, 1)
    m = Image.fromarray((d * 255).astype(np.uint8)).resize(im.size, Image.BICUBIC)
    # Grow near objects a little past their silhouette and soften the steps: hides the stretched
    # seams that would otherwise appear around people when the camera moves.
    r = max(3, im.width // 160) | 1
    m = m.filter(ImageFilter.MaxFilter(r)).filter(ImageFilter.GaussianBlur(im.width / 300))
    m.save(out)
    return out


def depth_maps(paths):
    """Depth for many images; a failure just leaves that shot on the flat camera."""
    made = {}
    for p in paths:
        try:
            made[str(p)] = depth_map(p)
        except Exception as e:
            log.warning("Depth map for %s failed (%s); flat camera for that shot", Path(p).name, e)
    return made
