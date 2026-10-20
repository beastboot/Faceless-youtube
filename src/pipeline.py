"""One topic end to end: research -> script (shots) -> AI illustrations -> per-language voice,
edit, thumbnail -> upload. English and Hindi versions share the same illustrations."""
import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from . import imagegen, keywords, review
from . import research as research_mod
from . import llm, script_writer, social, state, thumbnail, tts, video, youtube
from .common import ROOT, ffprobe_duration, fmt_ts, log, run, slugify
from .media import MediaPicker

TEXT = {
    "en": {"learn": "In this video:", "more": "📺 More stories:", "ask": "💬 {q}", "next": "Watch next:",
           "chapters": "Chapters:", "sources": "Sources:",
           "ai": "Illustrations: AI-generated. Narration: AI voice.",
           "subscribe": "Subscribe to {name} for a new story every day.",
           "full": "▶ Watch the full documentary: {url}", "intro": "Intro"},
    "ta": {"learn": "இந்த வீடியோவில்:", "more": "📺 மேலும் கதைகள்:", "ask": "💬 {q}", "next": "அடுத்து பாருங்கள்:",
           "chapters": "அத்தியாயங்கள்:", "sources": "ஆதாரங்கள்:", "english": "🇬🇧 In English:",
           "ai": "படங்கள்: AI உருவாக்கியவை. குரல்: AI.", "subscribe": "இதுபோன்ற கதைகளுக்கு {name} சேனலை சப்ஸ்கிரைப் செய்யுங்கள்.",
           "full": "▶ முழு ஆவணப்படம்: {url}", "intro": "தொடக்கம்"},
    "te": {"learn": "ఈ వీడియోలో:", "more": "📺 మరిన్ని కథలు:", "ask": "💬 {q}",
           "chapters": "అధ్యాయాలు:", "sources": "ఆధారాలు:", "english": "🇬🇧 In English:",
           "ai": "చిత్రాలు: AI రూపొందించినవి. గొంతు: AI.", "subscribe": "ఇలాంటి కథల కోసం {name} ను సబ్‌స్క్రైబ్ చేయండి.",
           "full": "▶ పూర్తి డాక్యుమెంటరీ: {url}", "intro": "ప్రారంభం"},
    "bn": {"learn": "এই ভিডিওতে:", "more": "📺 আরও গল্প:", "ask": "💬 {q}",
           "chapters": "অধ্যায়:", "sources": "সূত্র:", "english": "🇬🇧 In English:",
           "ai": "ছবি: AI দিয়ে তৈরি। কণ্ঠ: AI।", "subscribe": "এমন গল্পের জন্য {name} সাবস্ক্রাইব করুন।",
           "full": "▶ পুরো ডকুমেন্টারি: {url}", "intro": "শুরু"},
    "mr": {"learn": "या व्हिडिओमध्ये:", "more": "📺 आणखी कथा:", "ask": "💬 {q}",
           "chapters": "प्रकरणे:", "sources": "स्रोत:", "english": "🇬🇧 In English:",
           "ai": "चित्रे: AI ने तयार केली. आवाज: AI.", "subscribe": "अशा कथांसाठी {name} ला सबस्क्राइब करा.",
           "full": "▶ संपूर्ण माहितीपट: {url}", "intro": "सुरुवात"},
    "hi": {"learn": "इस वीडियो में:", "more": "📺 और कहानियाँ देखें:", "ask": "💬 {q}",
           "chapters": "अध्याय:", "sources": "स्रोत:", "english": "🇬🇧 In English:",
           "ai": "चित्र: AI द्वारा बनाए गए। आवाज़: AI वॉइस।",
           "subscribe": "ऐसी ही रोचक कहानियों के लिए {name} को सब्सक्राइब करें।",
           "full": "▶ पूरी डॉक्यूमेंट्री देखें: {url}", "intro": "शुरुआत"},
}


# ---- illustrations (shared by every language) ---------------------------------------------
def master_style(cfg):
    styles = cfg["images"]["styles"]
    return styles.get("master") or styles["long"]


def strip_style_words(cfg, text):
    """Drop words that would pull the image model away from the configured look (e.g. 'Pixar')."""
    words = cfg["images"].get("strip_words") or []
    if not words or not text:
        return text
    bad = re.compile(r",?\s*\b(" + "|".join(re.escape(w) for w in words) + r")\b[^,.;]*", re.I)
    return bad.sub("", text)


def ensure_host_reference(cfg):
    """Master picture of the recurring host, made once and kept in the repo (assets/host/), so
    every video draws the same face. Only a FLUX-quality image is accepted as the master."""
    host = cfg.get("host") or {}
    if not host:
        return None
    path = ROOT / (host.get("reference") or "assets/host/host.jpg")
    if path.exists():
        return str(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    prompt = (f"portrait of {host['look'].strip()}, waist-up, facing the viewer with a kind "
              f"knowing smile, simple warm background")
    try:
        if imagegen._cf_exhausted:
            return None
        im = imagegen._cloudflare(f"{prompt}. {master_style(cfg)}", cfg, 4242, True)
        im.save(path, quality=95)
        log.info("Created the host's master reference picture: %s", path)
        return str(path)
    except Exception as e:
        log.warning("Host reference not created yet (%s); will retry next run", e)
        return None



def character_references(cfg, script, shots, art, landscape):
    """Master picture for each main character of this story (most screen time, up to
    images.character_refs): later shots draw them from it, so they look the same throughout."""
    n = cfg["images"].get("character_refs", 0)
    chars = script.get("characters") or {}
    host = (cfg.get("host") or {}).get("name_en")
    if not n or not chars or imagegen._cf_exhausted:
        return {}
    counts = {c: sum(1 for sh in shots if c in (sh.get("cast") or [])) for c in chars if c != host}
    main = [c for c in sorted(counts, key=counts.get, reverse=True) if counts[c] >= 3][:n]
    refs = {}
    for k, name in enumerate(main):
        path = art / f"character-{slugify(name)[:30] or k}.jpg"
        if not path.exists():
            prompt = (f"reference picture of a single person, waist-up, front view, neutral expression, "
                      f"plain light background: {chars[name]}")
            try:
                im = imagegen._cloudflare(f"{prompt}. {master_style(cfg)}", cfg, 300 + k, landscape)
                im.save(path, quality=95)
            except Exception as e:
                log.warning("Reference picture for %s failed (%s); described in words instead", name, e)
                continue
        refs[name] = str(path)
    if refs:
        log.info("Character references: %s", ", ".join(refs))
    return refs


def illustrate(cfg, script, art, fmt, res):
    """One AI image per shot (+ thumbnail art). Falls back to stock footage per shot if needed."""
    art.mkdir(parents=True, exist_ok=True)
    landscape = cfg[fmt]["width"] > cfg[fmt]["height"]
    host_ref = ensure_host_reference(cfg)
    shots = [sh for sc in script["scenes"] for sh in sc["shots"]]
    chars = {n: strip_style_words(cfg, d) for n, d in (script.get("characters") or {}).items()}
    script["characters"] = chars
    refs = character_references(cfg, script, shots, art, landscape)
    ref_left = [(cfg["images"].get("reference_shots") or {}).get(fmt, 0)]   # reference shots cost ~2x
    ref_lock = threading.Lock()

    deadline = time.time() + 60 * (cfg["images"].get("max_minutes") or {}).get(fmt, 30)

    def one(i_shot):
        i, sh = i_shot
        path = art / f"shot-{i:03d}.jpg"
        if path.exists():
            return i, {"kind": "image", "path": path, "fill": True}
        if time.time() > deadline:  # image time budget spent: stock/reuse fills the rest
            return i, None
        prompt = strip_style_words(cfg, sh["image_prompt"] or sh["line"])
        # Same detailed description of every character in the shot, every time -> consistent look.
        names = [n for n in chars if n in (sh.get("cast") or []) or n.lower() in prompt.lower()]
        if names:
            prompt = f"{prompt}. " + "; ".join(f"{n}: {chars[n]}" for n in names)
        place = (script.get("locations") or {}).get(sh.get("location") or "")
        if place:   # same detailed description of a recurring place every time -> consistent setting
            prompt = f"{prompt}. Setting, {sh['location']}: {place}"
        host = cfg.get("host") or {}
        cast = sh.get("cast") or []
        ref = host_ref if host_ref and host.get("name_en") in cast else next(
            (refs[n] for n in cast if n in refs), None)
        if ref:   # same face in every shot, within the reference-shot budget
            with ref_lock:
                if ref_left[0] > 0:
                    ref_left[0] -= 1
                else:
                    ref = None
        try:
            provider = imagegen.generate(prompt, path, cfg, seed=1000 + i, landscape=landscape, fmt=fmt,
                                         reference=ref)
            log.debug("shot %d via %s", i, provider)
            return i, {"kind": "image", "path": path, "fill": True}
        except Exception as e:
            log.warning("Illustration for shot %d failed: %s", i, e)
            return i, None

    # Fallback images: only public domain / CC0 / CC BY (no share-alike or non-commercial terms).
    safe = [im for im in res["images"]
            if re.search(r"public domain|\bpd\b|cc0|^cc by \d|^cc-by-\d|^cc by$", im["license"].lower())
            and "sa" not in im["license"].lower().replace("cc by-", "")]
    picker = MediaPicker(art, cfg[fmt]["width"], cfg[fmt]["height"], safe)

    # Real stock B-roll for generic scenery shots in long videos (Unsolved-India style mix);
    # saves image budget too. Only video clips count here, never stills.
    broll = {}
    if fmt == "long":
        for i, sh in enumerate(shots):
            if sh.get("stock_query"):
                try:
                    hit = picker._pixabay_video(sh["stock_query"]) or picker._pexels_video(sh["stock_query"])
                    if hit:
                        from .common import download
                        broll[i] = {"kind": "video", "path": download(hit[0], art / hit[1]), "fill": True}
                except Exception as e:
                    log.debug("B-roll for shot %d failed: %s", i, e)
        log.info("Stock B-roll: %d shots", len(broll))

    # Stay inside the free daily image budget: neighbouring shots share an image (each still gets
    # its own camera move) when a script has more shots than max_images.
    need = [i for i in range(len(shots)) if i not in broll]
    cap = (cfg["images"].get("max_images") or {}).get(fmt) or len(need) or 1
    group = max(1, -(-len(need) // cap))
    leaders = need[::group]
    with ThreadPoolExecutor(3) as ex:
        made = dict(ex.map(one, [(i, shots[i]) for i in leaders]))
    failed = [i for i in leaders if not made.get(i)]
    if failed:  # second chance, one at a time (rate limits ease off), before anything is reused
        log.info("Retrying %d failed illustrations", len(failed))
        for i in failed:
            made[i] = one((i, shots[i]))[1]
    results = dict(broll)
    for k, i in enumerate(need):
        results[i] = made.get(leaders[min(k // group, len(leaders) - 1)])
    if group > 1:
        log.info("Image budget: %d shots share %d images", len(need), len(leaders))

    # A failed shot reuses the NEAREST AI picture of this same story (a fresh camera move makes it
    # feel new). Random stock search is the last resort: it can return unrelated footage.
    ai = sorted(i for i, v in results.items() if v and v.get("kind") == "image")
    last_ok = None
    details = ["detail_left", "detail_right", "detail_top"]
    for i, sh in enumerate(shots):
        vis = results.get(i)
        if vis is None and ai:
            near = min(ai, key=lambda j: abs(j - i))
            vis = dict(results[near])
            # Reused picture: a different framing (close-up on another part of the scene) and the
            # line's key words on screen, so the visual still follows the script.
            sh["move"] = details[i % len(details)]
            if not sh.get("graphic"):
                words = (sh.get("caption") or sh["line"]).split()
                key = " ".join(words[: min(6, len(words))])
                sh["graphic"] = {"type": "fact", "text": key}
        if vis is None and cfg["images"].get("fallback_to_stock"):
            query = sh.get("stock_query") or " ".join((sh["image_prompt"] or sh["line"]).split()[:5])
            vis = picker.for_scene({"stock_query": query}, i)
        if vis is None or vis.get("kind") == "color":
            vis = last_ok or {"kind": "color", "path": None}
        sh["visual"] = {"kind": vis["kind"], "path": str(vis["path"]) if vis.get("path") else None,
                        "fill": vis.get("fill", False), "move": sh.get("move"), "ambient": sh.get("ambient")}
        if vis.get("kind") != "color":
            last_ok = sh["visual"]
    if cfg["video"].get("parallax", True):   # depth maps -> 2.5D camera moves in the renderer
        try:
            from . import depth
            maps = depth.depth_maps(sorted({sh["visual"]["path"] for sh in shots
                                            if sh["visual"]["kind"] == "image" and sh["visual"]["path"]}))
            for sh in shots:
                d = maps.get(sh["visual"]["path"] or "")
                if d and sh["visual"]["kind"] == "image":
                    sh["visual"]["depth"] = str(d)
            log.info("Depth maps: %d pictures get the 3D camera", len(maps))
        except Exception as e:
            log.warning("Depth maps unavailable (%s); flat camera moves", e)
    n_ai = sum(1 for i in need if made.get(leaders[min(need.index(i) // group, len(leaders) - 1)]))
    log.info("Visuals: %d AI-illustrated, %d stock B-roll, %d fallbacks",
             n_ai, len(broll), len(shots) - n_ai - len(broll))
    script["credits"] = picker.credits  # attribution for any CC-licensed fallback images

    thumb_art = art / "thumb-art.jpg"
    if not thumb_art.exists():
        try:
            imagegen.generate(script.get("thumbnail_prompt") or shots[0]["image_prompt"], thumb_art, cfg,
                              seed=7, landscape=True, fmt="long", style=cfg["images"].get("thumbnail_style"))
        except Exception as e:
            log.warning("Thumbnail art failed (%s); using first shot", e)
    script["thumb_visual"] = ({"kind": "image", "path": str(thumb_art)} if thumb_art.exists()
                              else shots[0]["visual"])


def geocode_maps(script):
    """Map graphics need coordinates: OpenStreetMap Nominatim (free, 1 request/second). Maps that
    can't be located are dropped rather than pinned in the wrong place."""
    import time as _t
    from .common import http
    cache = {}
    for sc in script["scenes"]:
        for sh in sc["shots"]:
            g = sh.get("graphic")
            if not g or g.get("type") != "map":
                continue
            if g.get("lat") is not None and g.get("lon") is not None:
                continue
            place = g.get("place") or ""
            if place not in cache:
                try:
                    r = http().get("https://nominatim.openstreetmap.org/search",
                                   params={"q": place, "format": "json", "limit": 1}, timeout=20).json()
                    cache[place] = (float(r[0]["lat"]), float(r[0]["lon"])) if r else None
                except Exception as e:
                    log.debug("geocode %r failed: %s", place, e)
                    cache[place] = None
                _t.sleep(1.1)
            if cache[place]:
                g["lat"], g["lon"] = cache[place]
                g.setdefault("label", place.split(",")[0])
            else:
                sh["graphic"] = None


# ---- description / chapters ---------------------------------------------------------------
def chapters_block(script, starts, total, lang="en"):
    """YouTube chapter rules: first at 0:00, at least 3, each >= 10s. Otherwise omit."""
    marks = [(starts[i], s["chapter"].strip()) for i, s in enumerate(script["scenes"])
             if (s.get("chapter") or "").strip()]
    if not marks or marks[0][0] > 0:
        marks.insert(0, (0.0, TEXT[lang]["intro"]))
    clean = []
    for t, name in marks:
        if not clean or t - clean[-1][0] >= 10:
            clean.append((t, name))
    if len(clean) < 3 or total - clean[-1][0] < 10:
        return ""
    return "\n".join(f"{fmt_ts(t)} {name}" for t, name in clean)


def latest_long_url(lang):
    for r in reversed(state.load()):
        if r.get("format") == "long" and r.get("video_id") and r.get("lang", "en") == lang:
            return f"https://youtu.be/{r['video_id']}"
    return None


def best_shorts(lang, n=2):
    """Our most-viewed Shorts in this language (owner-hidden ones left out): "watch next" links."""
    stats_path = ROOT / "data" / "stats.json"
    stats = json.loads(stats_path.read_text()) if stats_path.exists() else {}
    rows = {r["video_id"]: r for r in state.load() if r.get("format") == "short" and r.get("video_id")
            and (r.get("lang") or "en") == lang and r.get("title") and not r.get("hidden")}
    ranked = sorted(rows.values(), key=lambda r: -(stats.get(r["video_id"]) or {}).get("views", 0))
    return [(r["title"].replace("#shorts", "").strip()[:70], f"https://youtube.com/shorts/{r['video_id']}")
            for r in ranked[:n]]


def related_videos(lang, fmt, series=None, n=3):
    """Recent long videos of this language (same series first): links that keep viewers watching.
    Shorts link to our best other Shorts instead (no long videos are made any more)."""
    if fmt == "short":
        return [f"▶ {title}: {url}" for title, url in best_shorts(lang, n)]
    rows = [r for r in state.load() if r.get("format") == "long" and r.get("video_id") and r.get("lang") == lang
            and r.get("title")]
    rows.sort(key=lambda r: (bool(series) and r.get("series") == (series or {}).get("id")), reverse=True)
    seen, out = set(), []
    for r in rows if series else reversed(rows):
        if r["video_id"] not in seen:
            seen.add(r["video_id"])
            out.append(f"▶ {r['title'].split(' | EP')[0][:70]}: https://youtu.be/{r['video_id']}")
        if len(out) >= n:
            break
    return out


def first_comment(question, fmt, lang):
    """The channel's own first comment: the question, and on Shorts a tap-through to our best Short."""
    if fmt != "short" or not (top := best_shorts(lang, 1)):
        return question
    return f"{question}\n\n▶ {TEXT[lang].get('next', TEXT[lang]['more'])} {top[0][1]}"


def subscribe_link(cfg, lang):
    handle = (cfg["languages"].get(lang) or {}).get("handle") or cfg["channel"].get("handle")
    return f"https://www.youtube.com/{handle}?sub_confirmation=1" if handle else ""


def build_description(cfg, script, res, chapters, fmt, lang, links=(), series=None):
    """Layout: the first ~150 characters are what search results and the "...more" fold show, so the
    keyword-led hook comes first and everything else follows in order of what helps watch time."""
    t = TEXT[lang]
    parts = [(script.get("description_intro") or "").strip()]
    if fmt == "short" and (top := best_shorts(lang, 1)):   # first link above the fold: our best Short
        parts.append(f"▶ {t.get('next', t['more'])} {top[0][0]}: {top[0][1]}")
    parts += [x for x in links if x]
    if script.get("key_points") and script.get("special") != "quiz":
        parts.append(t["learn"] + "\n" + "\n".join(script["key_points"]))
    if chapters:
        parts.append(f"{t['chapters']}\n{chapters}")
    parts.append((script.get("description_body") or "").strip())
    if script.get("engagement_question"):
        parts.append(t["ask"].format(q=script["engagement_question"].strip()))
    more = related_videos(lang, fmt, series)
    if more:
        parts.append(t["more"] + "\n" + "\n".join(more))
    if lang != "en" and script.get("en"):
        en = script["en"]
        parts.append(f"{t['english']}\n{en['description_intro']} {en['description_body']}".strip())
    if script.get("special") != "quiz":   # a source link would reveal the quiz answer
        parts.append(f"{t['sources']}\n{res['wiki_url']}" + (f"\n{res['hi_wiki_url']}" if res.get("hi_wiki_url") else ""))
    tag = (cfg.get("affiliate") or {}).get("amazon_tag")
    if tag:  # books on the topic; Amazon requires the disclosure line
        from urllib.parse import quote_plus
        q = quote_plus(f"{res.get('wiki_title', '')} book")
        parts.append((f"📚 इस विषय पर किताबें: https://www.amazon.in/s?k={q}&tag={tag}\n"
                      "(Amazon Associate के रूप में हम योग्य खरीद से कमाते हैं।)") if lang == "hi" else
                     (f"📚 Books on this topic: https://www.amazon.in/s?k={q}&tag={tag}\n"
                      "(As an Amazon Associate we earn from qualifying purchases.)"))
    parts.append(t["ai"])
    if script.get("credits"):
        parts.append("Image credits:\n" + "\n".join(list(dict.fromkeys(script["credits"]))[:20]))
    sub = t["subscribe"].format(name=(cfg["languages"].get(lang) or {}).get("channel_name") or cfg["channel"]["name"])
    if (link := subscribe_link(cfg, lang)):
        sub += f"\n🔔 {link}"
    parts.append(sub)
    tags = [h if h.startswith("#") else "#" + h.replace(" ", "")
            for h in (script.get("hashtags") or [])][:3]
    if fmt == "short" and "#shorts" not in [x.lower() for x in tags]:
        tags.append("#shorts")
    parts.append(" ".join(tags))
    return "\n\n".join(p for p in parts if p)


def build_tags(script, lang):
    """Main keyword first, then the most-searched real phrases, then the writer's tags (deduplicated).
    YouTube keeps ~500 characters; youtube.py trims the tail, so the order is the priority."""
    if script.get("special") == "quiz":
        return ["quiz", "guess the place", "पहचानो", "रहस्य", "quiz in hindi", "history quiz"]
    phrases = script.get("search_phrases") or []
    if lang != "hi":   # other languages (English too): only the English-letter searches carry over
        phrases = [p for p in phrases if p.isascii()]
    seen, out = set(), []
    for t in [script.get("primary_keyword") or ""] + phrases[:10] + list(script.get("tags") or []):
        t = str(t).strip().lstrip("#")
        if t and len(t) <= 60 and t.lower() not in seen and "<" not in t and ">" not in t:
            seen.add(t.lower())
            out.append(t)
    return out


# ---- one language version -----------------------------------------------------------------
def script_en_scenes(script, lang):
    """Chapter names in English for the English localization (falls back to the localized ones)."""
    names = script.get("en_chapters") or [sc.get("chapter") for sc in script["scenes"]]
    return {"scenes": [{"chapter": n or ""} for n in names]}


def voice_for(cfg, fmt, lang):
    lc = cfg["languages"].get(lang, {})
    if lc.get("voice"):
        return lc["voice"], lc.get(f"{fmt}_rate", "+0%")
    return cfg[fmt]["voice"], cfg[fmt]["rate"]


def is_short_series(cfg, series):
    return bool(series) and series.get("id") in {x["id"] for x in cfg.get("short_series") or []}


def series_for(cfg, topic):
    all_series = cfg.get("series") or []
    return next((x for x in all_series if x["id"] == topic.get("series")), all_series[0] if all_series else None)


def render_language(cfg, script, res, fmt, lang, work, template="yellow", series=None, episode=None):
    vc = cfg["video"]
    voice, rate = voice_for(cfg, fmt, lang)
    hinglish = cfg["languages"].get(lang, {}).get("captions") == "hinglish"
    flat = [(si, sh, k == len(sc["shots"]) - 1)
            for si, sc in enumerate(script["scenes"]) for k, sh in enumerate(sc["shots"])]

    narrate = [tts.speak]   # Edge; swapped for Kokoro (local English voice) when Edge is unreachable

    def speak(i_item):
        i, (si, sh, last) = i_item
        mp3 = work / f"voice-{i:03d}.mp3"
        words = narrate[0](sh["line"], voice, rate, mp3)
        max_words = 3 if fmt == "short" else 7
        if hinglish and sh.get("caption"):
            caps = tts.retext_captions(words, sh["caption"], max_words)
        else:
            caps = tts.caption_chunks(words, max_words)
        return {"audio": mp3, "audio_dur": ffprobe_duration(mp3), "lang": lang, "scene": si,
                "line_text": sh["line"], "graphic": sh.get("graphic"),
                "whoosh": i > 0 and sh.get("visual") != flat[i - 1][1].get("visual"),
                "caption_lang": "en" if hinglish else lang, "captions": caps,
                "visual": sh["visual"], "tail": vc["scene_tail_seconds"] if last else vc["shot_tail_seconds"]}

    lc = cfg["languages"].get(lang, {})
    engine = (lc.get("tts") or {}).get("engine", "edge")
    if engine == "gemini" and lc["tts"].get("test_only") and not script.get("voice_test"):
        engine = "edge"   # expressive voice on trial: only the run's test video uses it
    items = None
    voice_used = "edge"
    if engine == "gemini":
        # Expressive Gemini narration; if it can't voice EVERY line, the whole video falls back to
        # Edge so a video never switches voices half-way.
        def speak_g(i_item):
            i, (si, sh, last) = i_item
            mp3 = work / f"voice-{i:03d}.mp3"
            tcfg = dict(lc["tts"])
            if fmt == "short":   # Shorts: faster, punchier delivery
                tcfg["style"] = tcfg["style"] + " This is a YouTube Short: speak FAST and energetic, no pauses."
            words = tts.speak_gemini(sh["line"], mp3, tcfg)
            max_words = 3 if fmt == "short" else 7
            caps = (tts.retext_captions(words, sh["caption"], max_words) if hinglish and sh.get("caption")
                    else tts.caption_chunks(words, max_words))
            return {"audio": mp3, "audio_dur": ffprobe_duration(mp3), "lang": lang, "scene": si,
                    "line_text": sh["line"], "graphic": sh.get("graphic"),
                    "whoosh": i > 0 and sh.get("visual") != flat[i - 1][1].get("visual"),
                    "caption_lang": "en" if hinglish else lang, "captions": caps,
                    "visual": sh["visual"], "tail": vc["scene_tail_seconds"] if last else vc["shot_tail_seconds"]}
        try:
            with ThreadPoolExecutor(3) as ex:
                items = list(ex.map(speak_g, enumerate(flat)))
            log.info("[%s] narrated %d shots with Gemini (%s)", lang, len(items), lc["tts"].get("voice"))
            voice_used = "gemini"
        except Exception as e:
            log.warning("[%s] Gemini TTS unavailable (%s); narrating this video with Edge", lang, e)
            items = None
    if items is None:
        if tts.edge_down and tts.kokoro_supports(voice):
            narrate[0] = tts.speak_kokoro
        try:
            with ThreadPoolExecutor(4) as ex:
                items = list(ex.map(speak, enumerate(flat)))
        except RuntimeError as e:
            if not tts.kokoro_supports(voice) and narrate[0] is not tts.speak_kokoro:
                # No local backup voice for this language (Tamil): Edge refusing is usually a short burst
                # of rate limiting, so wait, then voice the lines one at a time.
                log.warning("[%s] Edge voice refused (%s); retrying slowly in 60 s", lang, e)
                time.sleep(60)
                with ThreadPoolExecutor(1) as ex:
                    items = list(ex.map(speak, enumerate(flat)))
            elif narrate[0] is tts.speak_kokoro:
                raise
            else:
                items, edge_err = None, e
        if items is None:
            # Edge refused us: voice the whole video with Kokoro so it never switches voices half-way
            log.warning("[%s] Edge voice unavailable (%s); narrating this video with Kokoro", lang, edge_err)
            narrate[0] = tts.speak_kokoro
            with ThreadPoolExecutor(2) as ex:
                items = list(ex.map(speak, enumerate(flat)))
        log.info("[%s] narrated %d shots%s", lang, len(items),
                 " with Kokoro" if narrate[0] is tts.speak_kokoro else "")
        voice_used = "kokoro" if narrate[0] is tts.speak_kokoro else "edge"
    if fmt == "short" and items:   # big title on frame one: the thumbnail's punchline, not the caption again
        items[0]["hook_text"] = re.sub(r"[*_]", "", script.get("thumbnail_text") or "").strip()
        if episode and is_short_series(cfg, series):
            name = series.get(f"name_{lang}") or series["name_en"]
            items[0]["badge"] = f"{name.upper() if lang == 'en' else name} #{episode}"
        # silent follow tag in the last second (the spoken loop stays intact)
        if script.get("special") == "short_part1":
            items[0]["follow_text"] = {"en": "Part 2 is on my channel ▶",
                                       "ta": "பாகம் 2 சேனலில் உள்ளது ▶"}.get(lang, "Part 2 ▶")
        elif lang == "en":
            items[0]["follow_text"] = (f"Follow for #{episode + 1} of {series['name_en']}"
                                       if episode and is_short_series(cfg, series) else "Follow for a true mystery every day")
        elif lang == "ta":
            items[0]["follow_text"] = "தினமும் ஒரு மர்மக் கதைக்கு Follow செய்யுங்கள்"
    seen_scene = set()
    n_chapter = 0
    for it in items:  # sound design + music need the mood and where chapters begin
        it["mood"] = script.get("mood")
        if it["scene"] not in seen_scene:
            seen_scene.add(it["scene"])
            title = (script["scenes"][it["scene"]].get("chapter") or "").strip()
            if title and fmt == "long":
                n_chapter += 1
                if it["scene"] > 0:   # the hook opens the video; cards mark the later parts
                    it["chapter_card"] = {"n": n_chapter, "title": title}
            it["chapter_start"] = bool((script["scenes"][it["scene"]].get("chapter") or "").strip()) or (
                fmt == "short" and it["scene"] == len(script["scenes"]) - 1)
    limit = cfg["languages"].get(lang, {}).get("short_max_seconds") if fmt == "short" else None
    spoken = sum(it["audio_dur"] + it["tail"] for it in items)
    if limit and spoken > limit:
        factor = min(1.3, spoken / (limit - 1.5))
        log.info("[%s] Short is %.0fs; speeding narration up x%.2f to fit %ss", lang, spoken, factor, limit)
        for it in items:
            fast = it["audio"].with_name(it["audio"].stem + "-fast.mp3")
            run(["ffmpeg", "-y", "-v", "error", "-i", it["audio"], "-filter:a", f"atempo={factor:.4f}", fast])
            it["audio"], it["audio_dur"] = fast, ffprobe_duration(fast)
            it["captions"] = [{**c, "start": c["start"] / factor, "end": c["end"] / factor} for c in it["captions"]]

    out = work / "final.mp4"
    starts, total = video.assemble(items, work, out, cfg, fmt)
    if fmt == "short" and total > 175:
        log.warning("Short is %.0fs (>3 min limit); YouTube will treat it as a regular video", total)
    video.write_srt(items, starts, work / "captions.srt")
    caption_track = "hi-Latn" if hinglish else lang  # YouTube's "Hindi (Latin)" subtitle language
    label = ((series or {}).get("label") if lang == "hi" else None) or \
        (cfg["languages"].get(lang) or {}).get("channel_name") or cfg["thumbnails"].get("label") or \
        cfg["channel"]["name"].upper()
    thumbnail.make(script["thumb_visual"], script["thumbnail_text"], work / "thumbnail.jpg", work, lang, template,
                   label=label)
    if fmt == "long":  # every template, for YouTube Studio's "Test & Compare" (A/B/C on real viewers)
        for tpl in cfg["thumbnails"].get("templates") or []:
            thumbnail.make(script["thumb_visual"], script["thumbnail_text"], work / f"thumbnail-{tpl}.jpg",
                           work, lang, tpl, label=label)

    scene_starts = {}
    for it, st in zip(items, starts):
        scene_starts.setdefault(it["scene"], st)
    chapters = (chapters_block(script, [scene_starts[i] for i in range(len(script["scenes"]))], total, lang)
                if fmt == "long" else "")
    localizations = {}
    if lang != "en" and script.get("en"):
        en_script = {**script, **script["en"], "lang": "en"}
        en_chapters = chapters_block(script_en_scenes(script, lang), [scene_starts[i] for i in range(len(script["scenes"]))],
                                     total, "en") if fmt == "long" else ""
        localizations["en"] = {"title": script["en"]["title"][:100],
                               "description": build_description(cfg, en_script, res, en_chapters, fmt, "en")[:4900]}
    title = script["title"]
    if fmt == "long" and series and episode:
        suffix = f" | EP {episode}"
        title = title[:100 - len(suffix)] + suffix
    links = []   # placed right after the opening hook, never above it
    for key, hi_txt, en_txt in (("part1_video", "▶ भाग 1 यहाँ देखें", "▶ Watch Part 1"),
                                ("quiz_video", "▶ कल का क्विज़", "▶ Yesterday's quiz")):
        if script.get(key):
            links.append(f"{hi_txt if lang == 'hi' else en_txt}: https://youtu.be/{script[key]}")
    if series:
        pl = json.loads((ROOT / "data" / "playlists.json").read_text()).get(f"series-{series['id']}-{lang}" + (cfg["languages"].get(lang) or {}).get("youtube_env", "")) \
            if (ROOT / "data" / "playlists.json").exists() else None
        sname = series["name"] if lang == "hi" else series.get("name_en") or series["name"]
        head = (f"📚 {sname} — एपिसोड {episode}" if lang == "hi" else f"📚 Series: {sname} — Episode {episode}")
        if pl:   # keeps viewers watching (session continuation is a satisfaction signal)
            head += f"\n▶ पूरी सीरीज़: https://www.youtube.com/playlist?list={pl}" if lang == "hi" else \
                f"\n▶ Full series: https://www.youtube.com/playlist?list={pl}"
        if fmt == "short" and series.get(f"name_{lang}"):
            sname = series[f"name_{lang}"]
        links.append(head if fmt == "long" else f"📚 {sname} #{episode}" + (
            f"\n▶ https://www.youtube.com/playlist?list={pl}" if pl else ""))
    description = build_description(cfg, script, res, chapters, fmt, lang, links, series)
    timeline = [[round(st, 2), it.get("line_text", "")] for it, st in zip(items, starts)]
    tags = build_tags(script, lang)
    meta = {"title": title, "lang": lang, "caption_track": caption_track, "timeline": timeline,
            "duration": round(total, 2),
            "localizations": localizations,
            "description": description,
            "tags": tags,
            "engagement_question": script.get("engagement_question") or "", "voice": voice_used}
    (work / "metadata.json").write_text(json.dumps(meta, indent=1, ensure_ascii=False))
    (work / "script.json").write_text(json.dumps(script, indent=1, ensure_ascii=False, default=str))
    log.info("[%s] %s", lang, meta["title"])
    return meta, total


def produce(cfg, topic, fmt, langs, upload=True, publish_at=None):
    """Make one topic in every requested language. publish_at: {lang: RFC3339 or None}.
    Returns [(lang, video_id, work_dir)]; raises only if nothing could be made."""
    publish_at = publish_at or {}
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M")
    base = ROOT / "output" / f"{stamp}-{fmt}-{slugify(topic['topic'])}"
    base.mkdir(parents=True, exist_ok=True)
    log.info("=== %s %s: %s -> %s", fmt.upper(), "+".join(langs), topic["topic"], base.name)

    if fmt == "short":   # Shorts are episodes of a named show (identity is what earns subscribers)
        topic["short_series_info"] = next((x for x in cfg.get("short_series") or []
                                           if x["id"] == topic.get("short_series")), None)
    res = research_mod.research(topic)
    if len(res["text"]) < (3000 if fmt == "long" else 600):
        raise RuntimeError(f"Not enough research material for {res['wiki_title']}")
    script_en = script_writer.write(cfg, topic, res, fmt)
    for key in ("part1_video", "quiz_video"):   # carried into descriptions of follow-ups
        if topic.get(key):
            script_en[key] = topic[key]
    script_en["special"] = topic.get("special")
    if topic.get("special") == "quiz":   # never leak the answer: no map/name graphics
        for sc in script_en["scenes"]:
            for sh in sc["shots"]:
                if (sh.get("graphic") or {}).get("type") in ("map", "name", "date"):
                    sh["graphic"] = None
    script_en = review.review(cfg, script_en, res, fmt)  # raises -> main tries the next topic
    phrases = [] if topic.get("special") == "quiz" else keywords.for_topic(
        topic["topic"], res["wiki_title"], res.get("hi_title"))
    # the one phrase every title, description opening and first tag is built around
    script_en["primary_keyword"] = keywords.primary(phrases, [res["wiki_title"].split("(")[0].strip(),
                                                              res.get("hi_title")]) if phrases else ""
    script_en["search_phrases"] = phrases
    script_en["research_names"] = [n for n in (res["wiki_title"].split("(")[0].strip(), res.get("hi_title")) if n]
    illustrate(cfg, script_en, base / "art", fmt, res)
    geocode_maps(script_en)

    template = thumbnail.choose_template(cfg)  # same A/B arm for every language of this topic
    series = series_for(cfg, topic)
    if fmt == "short" and topic.get("short_series_info"):
        ss = topic["short_series_info"]
        series = {**ss, "name": ss["name_en"]}
    episode = 1 + sum(1 for r in state.load() if r.get("series") == (series or {}).get("id")
                      and r.get("format") == fmt and r.get("video_id"))
    results = []
    script_en["voice_test"] = topic.get("voice_test")
    for lang in langs:
        try:
            if lang == "en":
                script = script_en
            elif lang == "hi":
                script = script_writer.translate_hindi(cfg, script_en, fmt, phrases)
            else:
                script = script_writer.translate_regional(cfg, script_en, fmt, lang, phrases=phrases)
            work = base / lang
            work.mkdir(exist_ok=True)
            meta, total = render_language(cfg, script, res, fmt, lang, work, template, series, episode)
            when = publish_at.get(lang)
            video_id = publish(cfg, work, fmt, meta, when, series) if upload else None
            if upload:  # test renders (--no-upload) don't use up the topic
                state.append({"format": fmt, "lang": lang, "topic": topic["topic"],
                              "wiki_title": res["wiki_title"], "title": meta["title"],
                              "video_id": video_id, "duration": round(total, 1), "publish_at": when,
                              "dir": f"{base.name}/{lang}", "thumb_template": template,
                              "series": (series or {}).get("id"), "episode": episode,
                              "voice": meta.get("voice"),   # which narrator (voice A/B test)
                              "hook": script_en.get("hook") or "",   # learned from in rehook()
                              "pending_comment": first_comment(meta["engagement_question"], fmt, lang)
                                                 if video_id else ""})
            if video_id and fmt == "long":
                try:
                    from . import extras
                    extras.save_script(video_id, script, meta)
                except Exception as e:
                    log.warning("Saving script failed: %s", e)
            if video_id and fmt == "long" and (cfg.get("podcast") or {}).get("enabled"):
                try:   # same story as a website article + podcast episode
                    from . import site
                    site.add_episode(cfg, script, meta, work, video_id)
                except Exception as e:
                    log.warning("Site/podcast step failed: %s", e)
            cleanup(work)
            results.append((lang, video_id, work))
        except Exception as e:
            log.error("[%s] version failed: %s", lang, e)
            if "quotaExceeded" in str(e):
                raise
    if not results:
        raise RuntimeError("No language version could be produced")
    if upload:
        queue_followups(topic, fmt, script_en, results)
    # Extra Shorts cut from this long video, in the main language (they funnel viewers to it).
    if fmt == "long" and (cfg.get("clips") or {}).get("per_long"):
        main = next(((l, v, w) for l, v, w in results if l == langs[0]), None)
        if main:
            try:
                clip_script = json.loads((main[2] / "script.json").read_text())
                results += make_clips(cfg, clip_script, res, base, main[0], main[1], upload, template, series)
            except Exception as e:
                log.error("Clips from long video failed: %s", e)
    return results


def link_parts(part2_topic, results):
    """Part 1's first comment points to Part 2 and Part 2's to Part 1 (posted once each is public)."""
    rows = state.load()
    p1 = part2_topic.get("part1_videos") or {}
    for lang, vid, _ in results:
        if not vid:
            continue
        for r in rows:
            if r.get("video_id") == p1.get(lang):
                r["pending_comment"] = f"▶ Part 2: https://youtu.be/{vid}\n\n" + (r.get("pending_comment") or "")
            elif r.get("video_id") == vid and p1.get(lang):
                r["pending_comment"] = f"◀ Part 1: https://youtu.be/{p1[lang]}\n\n" + (r.get("pending_comment") or "")
    state.HISTORY.write_text(json.dumps(rows, indent=1, ensure_ascii=False))


def queue_part2_later(part2_topic):
    """Part 2 failed in this run: make it first thing next run (Part 1 already promised it)."""
    path = ROOT / "data" / "followups.json"
    q = json.loads(path.read_text()) if path.exists() else []
    q.insert(0, {**{k: v for k, v in part2_topic.items() if not k.startswith("_")}, "kind": "part2"})
    path.write_text(json.dumps(q, indent=1, ensure_ascii=False))


def queue_followups(topic, fmt, script, results):
    """Part 1 -> queue Part 2 (2 days later); quiz -> queue tomorrow's answer reveal."""
    from datetime import datetime, timedelta, timezone
    path = ROOT / "data" / "followups.json"
    q = json.loads(path.read_text()) if path.exists() else []
    vid = next((v for _, v, _ in results if v), None)
    if topic.get("special") == "short_part1":   # made right away in this run (main.py), same publish time
        topic["_part2"] = {"special": "short_part2", "format": "short", "series": topic.get("series"),
                           "short_series": topic.get("short_series"),
                           "topic": topic["topic"].replace("— Part 1", "").strip() + " — Part 2",
                           "wikipedia_title": topic.get("wikipedia_title"), "part1_title": script.get("title"),
                           "part1_text": " ".join(sh["line"] for sc in script["scenes"] for sh in sc["shots"])[:900],
                           "part1_videos": {l: v for l, v, _ in results if v},
                           "angle": "Continue from Part 1's cliffhanger to the ending"}
    if topic.get("special") == "part1":
        q.append({"kind": "part2", "special": "part2", "format": fmt, "series": topic.get("series"),
                  "topic": topic["topic"].replace("Part 1", "").strip() + " — Part 2",
                  "wikipedia_title": topic.get("wikipedia_title"), "part1_title": script.get("title"),
                  "part1_video": vid, "angle": "Continue from Part 1's cliffhanger",
                  "not_before": (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()})
    if topic.get("special") == "quiz":
        q.append({"kind": "quiz_answer", "special": "quiz_answer", "format": "short", "series": topic.get("series"),
                  "topic": topic["topic"] + " — quiz answer", "wikipedia_title": topic.get("wikipedia_title"),
                  "quiz_video": vid, "angle": "Reveal yesterday's quiz answer",
                  "not_before": (datetime.now(timezone.utc) + timedelta(hours=20)).isoformat()})
    path.write_text(json.dumps(q, indent=1, ensure_ascii=False))


def make_clips(cfg, script, res, base, lang, long_video_id, upload, template, series):
    """Pick strong standalone moments from a long video, give each a new hook + CTA, render as Shorts."""
    cc = cfg["clips"]
    shots = [sh for sc in script["scenes"] for sh in sc["shots"]]
    listing = [{"i": i, "line": sh["line"]} for i, sh in enumerate(shots)]
    data = llm.ask_json(cfg, "You are a YouTube Shorts editor. JSON only.", f"""
From this {'Hindi' if lang == 'hi' else 'English'} documentary script, pick {cc['per_long']} different moments that
work as standalone Shorts: each a run of consecutive lines (by index) that is gripping on its own,
about 80-120 words in total (40-55 seconds), ending on a strong line. Avoid the intro and outro.
For each, write in the same language: a new opening hook line (max 14 words, creates instant
curiosity), the same hook in Hinglish (Roman letters), a title of 40-60 characters that contains the
main search keyword "{script.get('primary_keyword') or ''}" (or the subject's name) near the start, and 3-5
thumbnail words with the key words wrapped in *asterisks*.
LINES: {json.dumps(listing, ensure_ascii=False)}
Return {{"clips": [{{"start": i, "end": j, "hook": "...", "hook_hinglish": "...", "title": "...",
"thumbnail_text": "..."}}]}}""", temperature=0.6)
    cta = {"hi": ("पूरी कहानी हमारे चैनल पर देखिए, लिंक नीचे है।", "Poori kahani hamare channel par dekhiye, link neeche hai."),
           "en": ("Watch the full story on our channel, link below.", "Watch the full story on our channel, link below.")}[lang]
    out = []
    slots = cc.get("publish_utc") or []
    for k, c in enumerate((data.get("clips") or [])[:cc["per_long"]]):
        try:
            a, b = int(c["start"]), int(c["end"])
            if not (0 <= a <= b < len(shots)) or b - a > 14:
                continue
            body = [dict(sh) for sh in shots[a:b + 1]]
            first_vis = body[0].get("visual")
            clip = {**{key: script.get(key) for key in ("hashtags", "tags", "engagement_question", "credits",
                                                         "primary_keyword", "search_phrases", "research_names")},
                    "title": (c.get("title") or script["title"])[:90] + " #shorts",
                    "thumbnail_text": c.get("thumbnail_text") or script.get("thumbnail_text"),
                    "thumb_visual": first_vis,
                    "description_intro": c.get("hook") or "", "description_body": script.get("description_intro") or "",
                    "scenes": [{"chapter": "", "shots":
                                [{"line": c["hook"], "caption": c.get("hook_hinglish") or c["hook"], "visual": first_vis}]
                                + body
                                + [{"line": cta[0], "caption": cta[1], "visual": shots[-1].get("visual") or body[-1].get("visual")}]}]}
            clip["scenes"][0]["narration"] = " ".join(sh["line"] for sh in clip["scenes"][0]["shots"])
            work = base / f"{lang}-clip{k + 1}"
            work.mkdir(exist_ok=True)
            meta, total = render_language(cfg, clip, res, "short", lang, work, template, series, None)
            if long_video_id:
                meta["description"] = (f"▶ पूरी कहानी: https://youtu.be/{long_video_id}\n\n" if lang == "hi"
                                       else f"▶ Full story: https://youtu.be/{long_video_id}\n\n") + meta["description"]
                (work / "metadata.json").write_text(json.dumps(meta, indent=1, ensure_ascii=False))
            # Keep a small reserve: while it's below target, the last clip goes in privately and is only
            # published if a day's run fails (see main.release_reserve).
            reserve_now = (k == cc["per_long"] - 1 and upload and
                           sum(1 for r in state.load() if r.get("reserve") and not r.get("released"))
                           < (cfg.get("reserve") or {}).get("target", 0))
            when = None if reserve_now else (youtube.next_publish_time(slots, k) if upload else None)
            if reserve_now:
                cfg = {**cfg, "youtube": {**cfg["youtube"], "privacy": "private"}}
            vid = publish(cfg, work, "short", meta, when) if upload else None
            if upload:
                state.append({"format": "short", "lang": lang, "topic": f"{script['title']} (clip {k + 1})",
                              "reserve": bool(reserve_now),
                              "title": meta["title"], "video_id": vid, "duration": round(total, 1),
                              "publish_at": when, "clip_of": long_video_id, "dir": f"{base.name}/{work.name}",
                              "series": (series or {}).get("id"),
                              "pending_comment": meta.get("engagement_question") or ""})
            cleanup(work)
            log.info("Clip %d: %s (%.0fs)", k + 1, meta["title"], total)
            out.append((lang, vid, work))
        except Exception as e:
            log.error("Clip %d failed: %s", k + 1, e)
    return out


# ---- publishing ---------------------------------------------------------------------------
def publish(cfg, work, fmt, meta, publish_at=None, series=None):
    lang = meta.get("lang", "en")
    youtube.use_channel((cfg["languages"].get(lang) or {}).get("youtube_env", ""))
    try:
        return _publish(cfg, work, fmt, meta, publish_at, series, lang)
    finally:
        youtube.use_channel("")


def _publish(cfg, work, fmt, meta, publish_at, series, lang):
    video_id = youtube.upload(cfg, work / "final.mp4", meta, publish_at)
    tl = ROOT / "data" / "timelines"
    tl.mkdir(parents=True, exist_ok=True)
    (tl / f"{video_id}.json").write_text(json.dumps({"duration": meta.get("duration"),
                                                     "timeline": meta.get("timeline")}, ensure_ascii=False))
    if fmt == "long":
        youtube.set_thumbnail(video_id, work / "thumbnail.jpg")
    if cfg[fmt].get("upload_captions"):
        youtube.upload_captions(video_id, work / "captions.srt", meta.get("caption_track", lang))
    show = series if fmt == "long" or is_short_series(cfg, series) else None   # Shorts: their named show
    youtube.add_to_playlist(video_id, youtube.playlist_for(cfg, fmt, lang, show))
    if fmt == "short" and lang in ((cfg.get("social") or {}).get("langs") or ["en"]):   # the Instagram is English
        social.cross_post(cfg, work / "final.mp4", meta, video_id)
    return video_id


def upload_existing(cfg, work, fmt):
    """Retry the upload of a folder that rendered but failed to publish."""
    meta = json.loads((work / "metadata.json").read_text())
    video_id = publish(cfg, work, fmt, meta)
    state.append({"format": fmt, "lang": meta.get("lang", "en"),
                  "topic": work.name.split(f"-{fmt}-", 1)[-1].replace("-", " "),
                  "title": meta["title"], "video_id": video_id, "dir": work.name,
                  "pending_comment": meta.get("engagement_question", "")})
    cleanup(work)
    return video_id


KEEP = {"final.mp4", "thumbnail.jpg", "script.json", "metadata.json", "captions.srt"}


def cleanup(work):
    """Drop intermediates (segments, voice, downloads): ~80% of the folder's size."""
    for p in work.iterdir():
        if p.is_file() and p.name not in KEEP and not p.name.startswith("thumbnail"):
            p.unlink()
