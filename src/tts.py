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
