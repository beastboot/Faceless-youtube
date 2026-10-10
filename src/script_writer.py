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


def _clean_scenes(data):
    """Keep shots that have a spoken line; derive each scene's narration from its shots."""
    scenes = []
    for sc in data.get("scenes") or []:
        shots = [{"line": (sh.get("line") or "").strip(),
                  "image_prompt": (sh.get("image_prompt") or "").strip(),
                  "cast": [c for c in (sh.get("cast") or []) if isinstance(c, str)],
                  "move": (sh.get("move") or "").strip(), "ambient": (sh.get("ambient") or "").strip(),
                  "stock_query": (sh.get("stock_query") or "").strip()}
                 for sh in (sc.get("shots") or []) if (sh.get("line") or "").strip()]
        if shots:
            scenes.append({"chapter": (sc.get("chapter") or "").strip(), "shots": shots,
                           "narration": " ".join(sh["line"] for sh in shots)})
    return scenes


def write(cfg, topic, research, fmt):
    fc = cfg[fmt]
    hook = best_hook(cfg, topic, research, fmt)
    structure = random.choice(STRUCTURES[fmt])
    words = fc["target_words"]
    long_bits = """- Mark 5-8 scenes with a short "chapter" title where a new section starts (the first scene
  must have one). Chapters must be at least 30 seconds apart.
- Around the middle, add one soft line teasing what is coming later (open loop) to hold retention.
- End with one call to action to subscribe for the next story.""" if fmt == "long" else """- No chapters. No intro. Last line should loop back to the first line or end on a punch.
- No call to action except optionally "Follow for more" in under 4 words."""
    n_titles = cfg["quality"]["title_candidates"]
    shot_rule = ("A shot is ONE short sentence or clause, 3-5 seconds when read aloud: Shorts need a new "
                 "picture every few seconds (aim for 12-16 shots in total)." if fmt == "short" else
                 "A shot is ONE spoken sentence (or two very short ones), about 5-9 seconds when read aloud.")
    share = cfg["images"].get("stock_broll_share", 0) if fmt == "long" else 0
    broll_rule = (f"For about {int(share * 100)}% of shots that show GENERIC scenery or atmosphere (ocean, "
                  "desert, forest, night sky, crowd, old town street, candle, rain, ruins) also add "
                  '"stock_query": 2-4 plain English words to find a real stock video clip. Never for '
                  "shots showing specific people or events." if share else
                  "Do not add stock_query fields.")
    prompt = f"""Write the full narration for a {fmt} YouTube video, split into shots for an
animated documentary. Every shot gets its own AI-generated illustration.

TOPIC: {topic['topic']}
ANGLE: {topic.get('angle', '')}
STRUCTURE TO USE: {structure}
THE FIRST LINE MUST BE EXACTLY THIS HOOK: "{hook}"
LENGTH: about {words} words of narration in total (this is important; at least {int(words * 0.85)}).

Scene and shot rules:
- At least {fc['min_scenes']} scenes. Each scene has 1-5 shots.
- {shot_rule}
- Every shot has an image_prompt that shows EXACTLY what that line is talking about: who (with the
  correct region, ethnicity and age of the real people involved), doing what, where, era-accurate
  clothing, buildings, vehicles and objects, camera framing (wide shot, close-up,
  over-the-shoulder, aerial), mood and lighting. Vary the framing from shot to shot.
- Every shot also has "cast" (names of the recurring characters visible in it, [] if none),
  "move" (camera: zoom_in | zoom_out | pan_left | pan_right | rise | push_focus — vary them, push_focus
  for emotional beats) and "ambient" (a moving light/weather layer matching the mood: fireflies |
  sparkles | dust | leaves | rain | snow | embers | none — magical/night: fireflies or sparkles,
  sunlight indoors: dust, autumn/forest: leaves, storms/sorrow: rain, battles/fire: embers).
- Define each recurring character once in "characters" (region/ethnicity, age, build, hair, clothing,
  distinctive features) and repeat that exact description inside every image_prompt where they appear, so the
  character looks the same in every shot.
- {broll_rule}
- Each image_prompt is a complete illustrated SCENE (characters acting in a setting), never a
  portrait on a blank background.
- Image prompts must never ask for text, captions, signs with words, logos, brand names, gore or real
  photos, and never depict living or modern public figures (politicians, celebrities). Historical
  figures are fine, described by their era-accurate appearance.
- Retell the facts in your own words. Never copy sentences from the research notes.
- Compose every image so the subject is centred: it will be cropped to fit the frame.
  Depict violence and death only indirectly (aftermath, shadows, empty rooms).
- Write numbers and years as they should be spoken. No stage directions, emojis or markdown in lines.
{long_bits}

Packaging (YouTube SEO):
- {n_titles} title candidates: under 60 characters, front-load the subject, include a number, name
  or date, curiosity without clickbait lies. Max two ALL-CAPS words.
- 3 thumbnail_text candidates: 2-4 words, must NOT repeat words from the titles; wrap the 1-2 key words
  in *asterisks* (shown in red).
- thumbnail_prompt: one image prompt for a striking thumbnail scene (one clear subject, high
  contrast, emotion), with empty space on the left for text.
- description_intro: 2 sentences that contain the words people would search for.
- description_body: 1 short paragraph summary (no spoilers of the ending).
- engagement_question: one short question to ask viewers in a pinned-style comment.
- search_queries: 3 queries this video should rank for.
- tags: 12-15 tags (spellings, names, places, era, related searches).
- hashtags: 3 hashtags.

RESEARCH NOTES (the only allowed source of facts):
{research['text']}

Return JSON:
{{"characters": {{"Name": "visual description"}},
 "scenes": [{{"chapter": "optional chapter title",
   "shots": [{{"line": "spoken sentence", "image_prompt": "what the illustration shows",
              "cast": ["Name"], "move": "zoom_in", "ambient": "dust", "stock_query": "optional"}}]}}],
 "titles": ["..."], "thumbnail_texts": ["..."], "thumbnail_prompt": "...",
 "description_intro": "...", "description_body": "...", "engagement_question": "...",
 "search_queries": ["..."], "tags": ["..."], "hashtags": ["#..."]}}"""

    best = None
    for attempt in range(cfg["quality"]["max_script_attempts"]):
        data = llm.ask_json(cfg, _system(cfg), prompt)
        scenes = _clean_scenes(data)
        n_words = sum(len(s["narration"].split()) for s in scenes)
        n_shots = sum(len(s["shots"]) for s in scenes)
        log.info("Script attempt %d: %d scenes, %d shots, %d words (target %d)",
                 attempt + 1, len(scenes), n_shots, n_words, words)
        if best is None or n_words > best[1]:
            best = (data | {"scenes": scenes}, n_words)
        if n_words >= words * 0.75 and len(scenes) >= fc["min_scenes"] * 0.7:
            break
        prompt += (f"\n\nYOUR PREVIOUS ATTEMPT WAS TOO SHORT ({n_words} words, {len(scenes)} scenes)."
                   f" Write at least {words} words across at least {fc['min_scenes']} scenes.")
    data = best[0]
    data["hook"] = hook
    data["structure"] = structure
    data["lang"] = "en"
    data.update(package(data, fmt))
    return data


def translate_hindi(cfg, script, fmt, search_phrases=()):
    """Same shots, same images: translate every line plus packaging into natural spoken Hindi."""
    lines = [[sh["line"] for sh in sc["shots"]] for sc in script["scenes"]]
    chapters = [sc["chapter"] for sc in script["scenes"]]
    prompt = f"""Translate this {fmt} YouTube documentary script into natural, spoken Hindi
(Devanagari script) for an Indian audience. Keep it conversational like a popular Hindi
documentary narrator, not bookish/shuddh Hindi: common English loanwords are fine where Hindi
speakers actually use them. Keep names, places and numbers accurate. Write numbers and years
as they should be spoken in Hindi.

STRICT: keep exactly the same structure: {len(lines)} scenes, and the same number of lines in
each scene, in the same order. One Hindi line per English line.

ENGLISH LINES (scene by scene):
{json.dumps(lines, ensure_ascii=False)}

CHAPTER TITLES (translate non-empty ones, keep empty ones empty):
{json.dumps(chapters, ensure_ascii=False)}

ALSO write "hinglish": the SAME Hindi lines written in Roman/Latin letters the way Indians type
Hindi on WhatsApp (e.g. "1959 mein nau hikers achanak gayab ho gaye"). Same structure, one per line.
Keep English loanwords in normal English spelling. No diacritics.

SEARCH PHRASES Indian viewers actually type on YouTube about this topic (from autocomplete). Use the
most relevant ones word-for-word in the titles, the first description sentence and the tags:
{json.dumps(list(search_phrases)[:30], ensure_ascii=False)}

Also write Hindi YouTube packaging for Indian viewers searching in Hindi and Hinglish:
- 5 titles (under 70 characters, curiosity + a number, name or date; may mix Hindi with key English words)
- 3 thumbnail_texts: 3-6 punchy Hindi words; wrap the 1-3 most emotional/curious words in *asterisks*
  (they are shown in red), e.g. "दुनिया के 7 *सबसे रहस्यमयी* चीज़ें". Must not just repeat the title.
- description_intro (2 sentences), description_body (1 paragraph), engagement_question
- tags: 15 tags mixing Hindi, Hinglish (Hindi in Latin letters) and English keywords
- hashtags: 3 hashtags

Return JSON:
{{"scenes": [["hindi line", ...], ...], "hinglish": [["roman hindi line", ...], ...],
 "chapters": ["..."], "titles": ["..."],
 "thumbnail_texts": ["..."], "description_intro": "...", "description_body": "...",
 "engagement_question": "...", "tags": ["..."], "hashtags": ["#..."]}}"""
    for attempt in range(3):
        data = llm.ask_json(cfg, "You are an expert Hindi documentary translator. Reply with JSON only.",
                            prompt, temperature=0.4)
        hi = data.get("scenes") or []
        hl = data.get("hinglish") or []
        same = lambda xs: len(xs) == len(lines) and all(len(a) == len(b) for a, b in zip(xs, lines))
        if same(hi) and same(hl):
            break
        log.warning("Hindi translation shape mismatch (attempt %d), retrying", attempt + 1)
    else:
        raise RuntimeError("Hindi translation did not keep the shot structure")
    out = {k: v for k, v in script.items() if k not in ("scenes",)}
    chs = data.get("chapters") or chapters
    out["scenes"] = [
        {"chapter": (chs[i] if i < len(chs) else "") or "",
         "shots": [{"line": str(h).strip(), "caption": str(r).strip(),
                    "image_prompt": sh["image_prompt"], "visual": sh.get("visual")}
                   for h, r, sh in zip(hi[i], hl[i], sc["shots"])],
         "narration": " ".join(str(x).strip() for x in hi[i])}
        for i, sc in enumerate(script["scenes"])]
    for k in ("description_intro", "description_body", "engagement_question", "tags", "hashtags"):
        out[k] = data.get(k) or script.get(k)
    titles = [t.strip() for t in (data.get("titles") or []) if t and t.strip()]
    thumbs = [t.strip() for t in (data.get("thumbnail_texts") or []) if t and t.strip()]
    out["title"] = (titles[0] if titles else script["title"])[:95]
    if fmt == "short" and "#shorts" not in out["title"].lower():
        out["title"] = out["title"][:88] + " #shorts"
    out["thumbnail_text"] = thumbs[0] if thumbs else script["thumbnail_text"]
    out["lang"] = "hi"
    # Keep the English packaging: shown to English-language viewers via YouTube localizations,
    # and as an English summary inside the Hindi description.
    out["en_chapters"] = chapters
    out["en"] = {"title": script["title"], "description_intro": script.get("description_intro") or "",
                 "description_body": script.get("description_body") or ""}
    return out


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
