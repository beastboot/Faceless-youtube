"""Hooks -> scored; script -> scenes; packaging -> titles linted. All grounded in research notes."""
import json
import random

from . import llm
from .common import ROOT, log
from .quality import hookscore
from .quality import title as titlelint

# Rotating story structures keep every video structurally different (YouTube's
# "inauthentic content" policy targets mass-produced, templated uploads).
STRUCTURES = {
    "long": [
        "Cold open in the most dramatic moment, then rewind to how it began and build back to it.",
        "Investigation: present the mystery, walk through each piece of evidence, weigh the theories.",
        "Chronological rise-and-fall told through the eyes of one key person.",
        "Myth vs reality: the popular version first, then dismantle it fact by fact.",
        "Countdown of the strangest details of the story, building to the most surprising one.",
        "Ticking clock: narrate the critical hours/days as they happened, with timestamps.",
    ],
    "short": [
        "Open on the single most surprising fact, then the one detail that makes it even stranger.",
        "Pose a question, give two wrong answers people assume, then the real one.",
        "Tell it as a 3-beat micro story: setup, twist, punchline fact.",
        "Start with 'In <year>, ...' and end on a line that makes viewers rewatch.",
    ],
}


def _voice(cfg):
    p = ROOT / cfg["channel"]["voice_guide"]
    return p.read_text() if p.exists() else ""


def _system(cfg):
    return ("You write scripts for a faceless documentary YouTube channel narrated by an AI voice. "
            "You are meticulous about facts: you only state what the research notes support. "
            "Reply with JSON only.\n\nCHANNEL VOICE GUIDE:\n" + _voice(cfg))


def best_hook(cfg, topic, research, fmt):
    n = cfg["quality"]["hook_candidates"]
    formulas = json.loads((ROOT / "src/quality/hooks.json").read_text())
    names = [f"{f['name']}: {f['shape']}" for f in formulas["hooks"]]
    prompt = f"""Write {n} different opening hooks (the first spoken line, 9-24 words) for a
{fmt} video about: {topic['topic']}. Angle: {topic.get('angle', '')}.

Each hook must use a different formula from this list and be built only on facts in the notes:
{chr(10).join(names)}

Rules: {' '.join(formulas['rules'])}
Use a concrete number, name or date. Address the viewer as "you" where natural. Leave a question open.

RESEARCH NOTES:
{research['text'][:6000]}

Return {{"hooks": [{{"formula": "...", "hook": "..."}}]}}"""
    hooks = [h["hook"] for h in llm.ask_json(cfg, _system(cfg), prompt).get("hooks", []) if h.get("hook")]
    if not hooks:
        raise RuntimeError("LLM returned no hooks")
    scored = sorted(((hookscore.score(h)[1], h) for h in hooks), reverse=True)
    for s, h in scored:
        log.debug("hook %3d  %s", s, h)
    log.info("Best hook (%d/100, %s): %s", scored[0][0], hookscore.band(scored[0][0]), scored[0][1])
    return scored[0][1]


def write(cfg, topic, research, fmt):
    fc = cfg[fmt]
    hook = best_hook(cfg, topic, research, fmt)
    structure = random.choice(STRUCTURES[fmt])
    images = [f"{i}: {im['desc'] or im['title']}" for i, im in enumerate(research["images"][:40])]
    words = fc["target_words"]
    long_bits = """- Mark 5-8 scenes with a short "chapter" title where a new section starts (the first scene
  must have one). Chapters must be at least 30 seconds apart.
- Around the middle, add one soft line teasing what is coming later (open loop) to hold retention.
- End with one call to action to subscribe for the next story.""" if fmt == "long" else """- No chapters. No intro. Last line should loop back to the first line or end on a punch.
- No call to action except optionally "Follow for more" in under 4 words."""
    n_titles = cfg["quality"]["title_candidates"]
    prompt = f"""Write the full narration for a {fmt} YouTube video.

TOPIC: {topic['topic']}
ANGLE: {topic.get('angle', '')}
STRUCTURE TO USE: {structure}
THE FIRST SENTENCE MUST BE EXACTLY THIS HOOK: "{hook}"
LENGTH: about {words} words of narration in total (this is important; at least {int(words * 0.85)}).

Scene rules:
- At least {fc['min_scenes']} scenes. Each scene is 1-4 sentences of narration and one visual.
- Visual per scene: either an image from the AVAILABLE IMAGES list (by index) when it clearly fits,
  or a stock-footage search query (2-4 plain English words, things a stock site has: "old map",
  "foggy forest", "stormy ocean", "ancient ruins", "candle dark room"). Never names of real people
  in stock queries.
- Write numbers and years as they should be spoken. No stage directions, emojis or markdown in narration.
{long_bits}

Packaging (YouTube SEO):
- {n_titles} title candidates: under 60 characters, front-load the subject, include a number, name
  or date, curiosity without clickbait lies. Max two ALL-CAPS words.
- 3 thumbnail_text candidates: 2-3 words, must NOT repeat words from the titles.
- description_intro: 2 sentences that contain the words people would search for.
- description_body: 1 short paragraph summary (no spoilers of the ending).
- search_queries: 3 queries this video should rank for.
- tags: 12-15 tags (spellings, names, places, era, related searches).
- hashtags: 3 hashtags.

AVAILABLE IMAGES (index: description):
{chr(10).join(images) if images else "none"}

RESEARCH NOTES (the only allowed source of facts):
{research['text']}

Return JSON:
{{"scenes": [{{"narration": "...", "image": <index or null>, "stock_query": "...",
  "chapter": "optional chapter title"}}],
 "titles": ["..."], "thumbnail_texts": ["..."], "description_intro": "...",
 "description_body": "...", "search_queries": ["..."], "tags": ["..."], "hashtags": ["#..."]}}"""

    best = None
    for attempt in range(cfg["quality"]["max_script_attempts"]):
        data = llm.ask_json(cfg, _system(cfg), prompt)
        scenes = [s for s in (data.get("scenes") or []) if (s.get("narration") or "").strip()]
        n_words = sum(len(s["narration"].split()) for s in scenes)
        log.info("Script attempt %d: %d scenes, %d words (target %d)",
                 attempt + 1, len(scenes), n_words, words)
        if best is None or n_words > best[1]:
            best = (data | {"scenes": scenes}, n_words)
        if n_words >= words * 0.75 and len(scenes) >= fc["min_scenes"] * 0.7:
            break
        prompt += (f"\n\nYOUR PREVIOUS ATTEMPT WAS TOO SHORT ({n_words} words, {len(scenes)} scenes)."
                   f" Write at least {words} words across at least {fc['min_scenes']} scenes.")
    data = best[0]
    data["hook"] = hook
    data["structure"] = structure
    data.update(package(data, fmt))
    return data


def package(data, fmt):
    """title.py lint over every title x thumbnail pairing; keep the best pairing."""
    titles = [t.strip() for t in (data.get("titles") or []) if t.strip()] or ["Untitled story"]
    thumbs = [t.strip() for t in (data.get("thumbnail_texts") or []) if t.strip()] or [""]
    ranked = []
    for t in titles:
        for th in thumbs:
            r = titlelint.check(t, th or None)
            kinds = {k for k, _ in r["issues"]}
            # Vague or shouty titles are a hard loss against any clean alternative.
            ranked.append((not kinds & {"vague", "shouting"}, r["score"], t, th))
    ranked.sort(reverse=True)
    _, score, title, thumb = ranked[0]
    log.info("Best title (%d/100): %s | thumb: %s", score, title, thumb)
    if fmt == "short" and "#shorts" not in title.lower():
        title = title[:90] + " #shorts"
    return {"title": title, "thumbnail_text": thumb}
