"""Free-tier LLM client: Google Gemini first, Groq as fallback. Always returns parsed JSON."""
import json
import os
import re
import time

import requests

from .common import http, log

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"


class LLMError(RuntimeError):
    pass


_exhausted = set()  # models whose daily quota ran out during this run


def _parse_json(text):
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Fall back to the outermost {...} or [...] block.
        m = re.search(r"(\{.*\}|\[.*\])", text, re.S)
        if m:
            return json.loads(m.group(1))
        raise


def _gemini(model, system, prompt, temperature):
    key = os.environ["GEMINI_API_KEY"]
    body = {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": temperature,
                             "responseMimeType": "application/json",
                             "maxOutputTokens": 16384},
    }
    r = http().post(GEMINI_URL.format(model=model), params={"key": key}, json=body, timeout=(15, 150))
    if r.status_code >= 400:
        raise LLMError(f"gemini {model} HTTP {r.status_code}: {r.text[:300]}")
    data = r.json()
    try:
        return "".join(p.get("text", "") for p in data["candidates"][0]["content"]["parts"])
    except (KeyError, IndexError):
        raise LLMError(f"gemini {model} returned no content: {str(data)[:300]}")


def _groq(model, system, prompt, temperature):
    key = os.environ["GROQ_API_KEY"]
    body = {
        "model": model,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": prompt}],
        "temperature": temperature,
        "response_format": {"type": "json_object"},
        "max_tokens": 8000,
    }
    r = http().post(GROQ_URL, headers={"Authorization": f"Bearer {key}"}, json=body, timeout=180)
    if r.status_code >= 400:
        raise LLMError(f"groq {model} HTTP {r.status_code}: {r.text[:300]}")
    return r.json()["choices"][0]["message"]["content"]


def ask_vision_json(cfg, prompt, images, models=None):
    """Gemini with pictures: `images` are JPEG bytes, sent in order after the prompt. Returns parsed
    JSON, or None when no model could answer (callers treat that as "no opinion")."""
    import base64
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        return None
    parts = [{"text": prompt}] + [{"inline_data": {"mime_type": "image/jpeg",
                                                   "data": base64.b64encode(b).decode()}} for b in images]
    body = {"contents": [{"role": "user", "parts": parts}],
            "generationConfig": {"temperature": 0.2, "responseMimeType": "application/json"}}
    for model in models or ["gemini-flash-lite-latest", "gemini-flash-latest"]:
        if model in _exhausted:
            continue
        try:
            r = http().post(GEMINI_URL.format(model=model), params={"key": key}, json=body, timeout=(15, 150))
            if r.status_code == 429 and "quota" in r.text.lower():
                _exhausted.add(model)
                continue
            r.raise_for_status()
            text = "".join(p.get("text", "") for p in r.json()["candidates"][0]["content"]["parts"])
            return _parse_json(text)
        except Exception as e:
            log.warning("Vision check with %s failed: %s", model, str(e)[:150])
    return None


def ask_json(cfg, system, prompt, temperature=None):
    """Try every configured provider/model until one returns valid JSON."""
    lc = cfg["llm"]
    temperature = lc.get("temperature", 0.9) if temperature is None else temperature
    providers = {"gemini": (_gemini, "GEMINI_API_KEY", lc.get("gemini_models", [])),
                 "groq": (_groq, "GROQ_API_KEY", lc.get("groq_models", []))}
    attempts = []
    for name in lc["providers"]:
        fn, env, models = providers[name]
        if not os.environ.get(env):
            attempts.append(f"{name}: {env} not set")
            continue
        for model in models:
            if model in _exhausted:
                continue
            for retry in range(3):
                try:
                    data = _parse_json(fn(model, system, prompt, temperature))
                    if isinstance(data, list) and len(data) == 1 and isinstance(data[0], dict):
                        data = data[0]   # an object wrapped in a list
                    if not isinstance(data, dict):
                        # every prompt asks for a JSON object; a bare list is the wrong shape
                        raise ValueError("expected a JSON object, got a list")
                    return data
                except requests.RequestException as e:
                    attempts.append(f"{model}: network error {e}")
                    log.warning("%s network error (%s), trying next model", model, type(e).__name__)
                    break
                except LLMError as e:
                    msg = str(e)
                    attempts.append(msg)
                    if "HTTP 429" in msg and ("quota" in msg.lower() or "billing" in msg.lower()):
                        # Daily/plan quota is gone: retrying won't help today. Skip for this run.
                        log.warning("%s: daily quota exhausted, skipping it for this run", model)
                        _exhausted.add(model)
                        break
                    if any(f"HTTP {c}" in msg for c in (429, 500, 503)):
                        wait = 20 * (retry + 1)
                        log.warning("%s rate-limited/unavailable, retrying in %ss", model, wait)
                        time.sleep(wait)
                        continue
                    log.warning("LLM error: %s", msg)
                    break  # model gone / bad request -> try next model
                except (json.JSONDecodeError, ValueError) as e:
                    attempts.append(f"{model}: invalid JSON ({e})")
                    log.warning("%s returned invalid JSON, retrying", model)
    raise LLMError("All LLM providers failed:\n  " + "\n  ".join(attempts[-6:]))
