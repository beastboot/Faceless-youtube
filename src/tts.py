"""Free neural voiceover via edge-tts, with word timings for captions."""
import asyncio

import edge_tts

from .common import log

TICKS = 10_000_000  # edge-tts offsets are in 100ns units


async def _speak(text, voice, rate, mp3_path):
    words = []
    com = edge_tts.Communicate(text, voice, rate=rate, boundary="WordBoundary")
    with open(mp3_path, "wb") as f:
        async for chunk in com.stream():
            if chunk["type"] == "audio":
                f.write(chunk["data"])
            elif chunk["type"] == "WordBoundary":
                start = chunk["offset"] / TICKS
                words.append({"word": chunk["text"], "start": start,
                              "end": start + chunk["duration"] / TICKS})
    return words


def speak(text, voice, rate, mp3_path, retries=3):
    """Write mp3 and return [{'word', 'start', 'end'}] in seconds."""
    for attempt in range(retries):
        try:
            return asyncio.run(_speak(text, voice, rate, str(mp3_path)))
        except Exception as e:
            log.warning("TTS attempt %d failed: %s", attempt + 1, e)
    raise RuntimeError("TTS failed")


def caption_chunks(words, max_words, max_chars=32):
    """Group word timings into short caption lines."""
    chunks, cur = [], []
    for w in words:
        text = " ".join(x["word"] for x in cur + [w])
        if cur and (len(cur) >= max_words or len(text) > max_chars):
            chunks.append(cur)
            cur = []
        cur.append(w)
        if w["word"][-1:] in ".!?" and len(cur) >= 2:
            chunks.append(cur)
            cur = []
    if cur:
        chunks.append(cur)
    return [{"text": " ".join(x["word"] for x in c), "start": c[0]["start"], "end": c[-1]["end"]}
            for c in chunks]


def retext_captions(words, display_text, max_words, max_chars=32):
    """Captions that SHOW display_text (e.g. Hinglish) but follow the timing of the spoken words
    (e.g. Hindi). Display words are mapped onto spoken words proportionally."""
    disp = display_text.split()
    if not words or not disp:
        return caption_chunks(words, max_words, max_chars)
    n, m = len(words), len(disp)
    chunks, cur = [], []
    for i, w in enumerate(disp):
        if cur and (len(cur) >= max_words or len(" ".join(disp[j] for j in cur + [i])) > max_chars):
            chunks.append(cur)
            cur = []
        cur.append(i)
    if cur:
        chunks.append(cur)
    out = []
    for c in chunks:
        a = min(n - 1, c[0] * n // m)
        b = min(n - 1, max(a, -(-(c[-1] + 1) * n // m) - 1))  # ceil, inclusive end
        out.append({"text": " ".join(disp[j] for j in c), "start": words[a]["start"], "end": words[b]["end"]})
    return out


# ---- Gemini TTS: expressive, directable narration -------------------------------------------
GEMINI_TTS = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


class GeminiTTSUnavailable(RuntimeError):
    pass


def _speech_bounds(path):
    """(start, end) of actual speech, from ffmpeg silencedetect."""
    import re
    import subprocess
    p = subprocess.run(["ffmpeg", "-i", str(path), "-af", "silencedetect=noise=-38dB:d=0.12", "-f", "null", "-"],
                       capture_output=True, text=True)
    dur = 0.0
    m = re.search(r"Duration: (\d+):(\d+):([\d.]+)", p.stderr)
    if m:
        dur = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
    starts = [float(x) for x in re.findall(r"silence_start: ([\d.]+)", p.stderr)]
    ends = [float(x) for x in re.findall(r"silence_end: ([\d.]+)", p.stderr)]
    s0 = ends[0] if starts and starts[0] < 0.05 and ends else 0.0
    e0 = starts[-1] if starts and (not ends or starts[-1] > ends[-1]) else dur
    return s0, max(e0, s0 + 0.2)


def _estimate_words(text, start, end):
    """Spread words over the spoken span by length (good enough for karaoke within one line)."""
    words = text.split()
    weights = [len(w) + 1 for w in words]
    total = sum(weights) or 1
    out, t = [], start
    for w, k in zip(words, weights):
        d = (end - start) * k / total
        out.append({"word": w, "start": t, "end": t + d * 0.92})
        t += d
    return out


def speak_gemini(text, out_mp3, cfg_tts):
    """Narrate one line with Gemini TTS. Raises GeminiTTSUnavailable when every model is exhausted."""
    import base64
    import os
    import subprocess
    from .common import http
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        raise GeminiTTSUnavailable("no GEMINI_API_KEY")
    body = {"contents": [{"parts": [{"text": f"{cfg_tts['style']}\n\n{text}"}]}],
            "generationConfig": {"responseModalities": ["AUDIO"], "speechConfig": {"voiceConfig": {
                "prebuiltVoiceConfig": {"voiceName": cfg_tts.get("voice", "Charon")}}}}}
    errors = []
    for model in cfg_tts["models"]:
        if model in _tts_exhausted:
            continue
        for attempt in range(2):
            try:
                r = http().post(GEMINI_TTS.format(model=model), params={"key": key}, json=body, timeout=(15, 90))
            except Exception as e:
                errors.append(f"{model}: {e}")
                break
            if r.status_code == 429:
                if "quota" in r.text.lower():
                    _tts_exhausted.add(model)
                    break
                import time
                time.sleep(8 * (attempt + 1))
                continue
            if not r.ok:
                errors.append(f"{model}: HTTP {r.status_code}")
                break
            try:
                part = r.json()["candidates"][0]["content"]["parts"][0]["inlineData"]
            except (KeyError, IndexError):
                errors.append(f"{model}: no audio")
                break
            raw = base64.b64decode(part["data"])
            mime = part.get("mimeType", "")
            src = out_mp3.with_suffix(".raw")
            src.write_bytes(raw)
            fmt_args = [] if "wav" in mime else ["-f", "s16le", "-ar", "24000", "-ac", "1"]
            subprocess.run(["ffmpeg", "-y", "-v", "error", *fmt_args, "-i", str(src), "-b:a", "128k", str(out_mp3)],
                           check=True)
            src.unlink(missing_ok=True)
            s, e = _speech_bounds(out_mp3)
            return _estimate_words(text, s, e)
    raise GeminiTTSUnavailable("; ".join(errors[-3:]) or "all Gemini TTS models exhausted")


_tts_exhausted = set()
