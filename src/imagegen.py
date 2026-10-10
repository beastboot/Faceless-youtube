"""AI illustrations for each shot. Cloudflare Workers AI (FLUX) first, Pollinations as free backup."""
import base64
import io
import os
import time
import urllib.parse

from PIL import Image

from .common import http, log

CF_URL = "https://api.cloudflare.com/client/v4/accounts/{acct}/ai/run/{model}"
POLLINATIONS_URL = "https://image.pollinations.ai/prompt/"


class ImageGenError(RuntimeError):
    pass


_cf_exhausted = False  # set once Cloudflare reports the free daily allocation is used up


def _cf_body(model, prompt, cfg, seed, landscape):
    if "flux-1-schnell" in model:  # square only; accepts just prompt + steps
        return {"prompt": prompt[:2000], "steps": cfg["images"].get("cloudflare_steps", 4)}
    w, h = (1344, 768) if landscape else (768, 1344)
    return {"prompt": prompt[:2000], "width": w, "height": h, "seed": seed}


def _cloudflare(prompt, cfg, seed, landscape):
    global _cf_exhausted
    acct, token = os.environ.get("CF_ACCOUNT_ID"), os.environ.get("CF_API_TOKEN")
    if not (acct and token):
        raise ImageGenError("Cloudflare credentials not set")
    if _cf_exhausted:
        raise ImageGenError("Cloudflare daily free allocation used up (resets 00:00 UTC)")
    errors = []
    for model in cfg["images"]["cloudflare_models"]:
        r = http().post(CF_URL.format(acct=acct, model=model),
                        headers={"Authorization": f"Bearer {token}"},
                        json=_cf_body(model, prompt, cfg, seed, landscape), timeout=180)
        if r.headers.get("content-type", "").startswith("image"):
            return Image.open(io.BytesIO(r.content)).convert("RGB")
        try:
            b64 = (r.json().get("result") or {}).get("image")
        except ValueError:
            b64 = None
        if r.ok and b64:
            return Image.open(io.BytesIO(base64.b64decode(b64))).convert("RGB")
        errors.append(f"{model} HTTP {r.status_code}: {r.text[:160]}")
        if "daily free allocation" in r.text:
            _cf_exhausted = True
            log.warning("Cloudflare free daily image budget used up; using backups for the rest of this run")
            break
    raise ImageGenError("; ".join(errors))


def _pollinations(prompt, cfg, seed, landscape):
    w, h = (1280, 720) if landscape else (720, 1280)
    params = {"width": w, "height": h, "nologo": "true", "seed": seed, "model": "flux"}
    token = os.environ.get("POLLINATIONS_TOKEN")
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    # Anonymous use is heavily rate-limited (HTTP 402); keep waiting patiently.
    for attempt in range(cfg["images"].get("pollinations_retries", 5)):
        r = http().get(POLLINATIONS_URL + urllib.parse.quote(prompt[:1500]), params=params,
                       headers=headers, timeout=150)
        if r.ok and r.headers.get("content-type", "").startswith("image"):
            im = Image.open(io.BytesIO(r.content)).convert("RGB")
            # Crop the watermark strip in the bottom-right corner.
            return im.crop((0, 0, im.width, int(im.height * 0.93)))
        time.sleep(min(8 + attempt * 4, 20))
    raise ImageGenError("Pollinations kept refusing (rate limit)")


def generate(prompt, out_path, cfg, seed, landscape=True, fmt=None):
    """Write one illustration to out_path; tries each configured provider in order."""
    styles = cfg["images"]["styles"]
    style = styles.get(fmt) or styles.get("long" if landscape else "short")
    full = f"{prompt}. {style}"
    errors = []
    for provider in cfg["images"]["providers"]:
        try:
            if provider == "cloudflare":
                im = _cloudflare(full, cfg, seed, landscape)
            elif provider == "pollinations":
                im = _pollinations(full, cfg, seed, landscape)
            else:
                continue
            im.save(out_path, quality=93)
            return provider
        except Exception as e:
            errors.append(f"{provider}: {e}")
            log.debug("Image provider %s failed: %s", provider, e)
    raise ImageGenError("; ".join(errors))
