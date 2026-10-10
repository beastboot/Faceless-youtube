"""One topic end to end: research -> script (shots) -> AI illustrations -> per-language voice,
edit, thumbnail -> upload. English and Hindi versions share the same illustrations."""
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from . import imagegen, keywords, review
from . import research as research_mod
from . import llm, script_writer, social, state, thumbnail, tts, video, youtube
from .common import ROOT, ffprobe_duration, fmt_ts, log, slugify
from .media import MediaPicker

TEXT = {
    "en": {"chapters": "Chapters:", "sources": "Sources:",
           "ai": "Illustrations: AI-generated. Narration: AI voice.",
           "subscribe": "Subscribe to {name} for a new story every day.",
           "full": "▶ Watch the full documentary: {url}", "intro": "Intro"},
    "hi": {"chapters": "अध्याय:", "sources": "स्रोत:", "english": "🇬🇧 In English:",
           "ai": "चित्र: AI द्वारा बनाए गए। आवाज़: AI वॉइस।",
           "subscribe": "ऐसी ही रोचक कहानियों के लिए {name} को सब्सक्राइब करें।",
           "full": "▶ पूरी डॉक्यूमेंट्री देखें: {url}", "intro": "शुरुआत"},
}


# ---- illustrations (shared by every language) ---------------------------------------------
def illustrate(cfg, script, art, fmt, res):
    """One AI image per shot (+ thumbnail art). Falls back to stock footage per shot if needed."""
    art.mkdir(parents=True, exist_ok=True)
    landscape = cfg[fmt]["width"] > cfg[fmt]["height"]
    shots = [sh for sc in script["scenes"] for sh in sc["shots"]]
    chars = script.get("characters") or {}

    deadline = time.time() + 60 * (cfg["images"].get("max_minutes") or {}).get(fmt, 30)

    def one(i_shot):
        i, sh = i_shot
        path = art / f"shot-{i:03d}.jpg"
        if path.exists():
            return i, {"kind": "image", "path": path, "fill": True}
        if time.time() > deadline:  # image time budget spent: stock/reuse fills the rest
            return i, None
        prompt = sh["image_prompt"] or sh["line"]
        # Same detailed description of every character in the shot, every time -> consistent look.
        names = [n for n in chars if n in (sh.get("cast") or []) or n.lower() in prompt.lower()]
        if names:
            prompt = f"{prompt}. " + "; ".join(f"{n}: {chars[n]}" for n in names)
        try:
            provider = imagegen.generate(prompt, path, cfg, seed=1000 + i, landscape=landscape, fmt=fmt)
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
    results = dict(broll)
    for k, i in enumerate(need):
        results[i] = made.get(leaders[min(k // group, len(leaders) - 1)])
    if group > 1:
        log.info("Image budget: %d shots share %d images", len(need), len(leaders))

    # A failed shot reuses the NEAREST AI picture of this same story (a fresh camera move makes it
    # feel new). Random stock search is the last resort: it can return unrelated footage.
    ai = sorted(i for i, v in results.items() if v and v.get("kind") == "image")
    last_ok = None
    for i, sh in enumerate(shots):
        vis = results.get(i)
        if vis is None and ai:
            vis = results[min(ai, key=lambda j: abs(j - i))]
        if vis is None and cfg["images"].get("fallback_to_stock"):
            query = sh.get("stock_query") or " ".join((sh["image_prompt"] or sh["line"]).split()[:5])
            vis = picker.for_scene({"stock_query": query}, i)
        if vis is None or vis.get("kind") == "color":
            vis = last_ok or {"kind": "color", "path": None}
        sh["visual"] = {"kind": vis["kind"], "path": str(vis["path"]) if vis.get("path") else None,
                        "fill": vis.get("fill", False), "move": sh.get("move"), "ambient": sh.get("ambient")}
        if vis.get("kind") != "color":
            last_ok = sh["visual"]
    n_ai = sum(1 for i in need if made.get(leaders[min(need.index(i) // group, len(leaders) - 1)]))
    log.info("Visuals: %d AI-illustrated, %d stock B-roll, %d fallbacks",
             n_ai, len(broll), len(shots) - n_ai - len(broll))
    script["credits"] = picker.credits  # attribution for any CC-licensed fallback images

    thumb_art = art / "thumb-art.jpg"
    if not thumb_art.exists():
        try:
            imagegen.generate(script.get("thumbnail_prompt") or shots[0]["image_prompt"], thumb_art, cfg,
                              seed=7, landscape=True, fmt="long")
        except Exception as e:
            log.warning("Thumbnail art failed (%s); using first shot", e)
    script["thumb_visual"] = ({"kind": "image", "path": str(thumb_art)} if thumb_art.exists()
                              else shots[0]["visual"])


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


def build_description(cfg, script, res, chapters, fmt, lang):
    t = TEXT[lang]
    parts = [(script.get("description_intro") or "").strip()]
    if fmt == "short" and (url := latest_long_url(lang)):
        parts.append(t["full"].format(url=url))  # route Shorts viewers to watch hours
    if chapters:
        parts.append(f"{t['chapters']}\n{chapters}")
    parts.append((script.get("description_body") or "").strip())
    if lang != "en" and script.get("en"):
        en = script["en"]
        parts.append(f"{t['english']}\n{en['description_intro']} {en['description_body']}".strip())
    parts.append(f"{t['sources']}\n{res['wiki_url']}")
    parts.append(t["ai"])
    if script.get("credits"):
        parts.append("Image credits:\n" + "\n".join(list(dict.fromkeys(script["credits"]))[:20]))
    if fmt == "long":
        parts.append(t["subscribe"].format(name=cfg["channel"]["name"]))
    tags = [h if h.startswith("#") else "#" + h.replace(" ", "")
            for h in (script.get("hashtags") or [])][:3]
    if fmt == "short" and "#shorts" not in [x.lower() for x in tags]:
        tags.append("#shorts")
    parts.append(" ".join(tags))
    return "\n\n".join(p for p in parts if p)


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


def series_for(cfg, topic):
    all_series = cfg.get("series") or []
    return next((x for x in all_series if x["id"] == topic.get("series")), all_series[0] if all_series else None)


def render_language(cfg, script, res, fmt, lang, work, template="yellow", series=None, episode=None):
    vc = cfg["video"]
    voice, rate = voice_for(cfg, fmt, lang)
    hinglish = cfg["languages"].get(lang, {}).get("captions") == "hinglish"
    flat = [(si, sh, k == len(sc["shots"]) - 1)
            for si, sc in enumerate(script["scenes"]) for k, sh in enumerate(sc["shots"])]

    def speak(i_item):
        i, (si, sh, last) = i_item
        mp3 = work / f"voice-{i:03d}.mp3"
        words = tts.speak(sh["line"], voice, rate, mp3)
        max_words = 3 if fmt == "short" else 7
        if hinglish and sh.get("caption"):
            caps = tts.retext_captions(words, sh["caption"], max_words)
        else:
            caps = tts.caption_chunks(words, max_words)
        return {"audio": mp3, "audio_dur": ffprobe_duration(mp3), "lang": lang, "scene": si,
                "line_text": sh["line"],
                "whoosh": i > 0 and sh.get("visual") != flat[i - 1][1].get("visual"),
                "caption_lang": "en" if hinglish else lang, "captions": caps,
                "visual": sh["visual"], "tail": vc["scene_tail_seconds"] if last else vc["shot_tail_seconds"]}

    with ThreadPoolExecutor(4) as ex:
        items = list(ex.map(speak, enumerate(flat)))
    log.info("[%s] narrated %d shots", lang, len(items))

    out = work / "final.mp4"
    starts, total = video.assemble(items, work, out, cfg, fmt)
    if fmt == "short" and total > 175:
        log.warning("Short is %.0fs (>3 min limit); YouTube will treat it as a regular video", total)
    video.write_srt(items, starts, work / "captions.srt")
    caption_track = "hi-Latn" if hinglish else lang  # YouTube's "Hindi (Latin)" subtitle language
    thumbnail.make(script["thumb_visual"], script["thumbnail_text"], work / "thumbnail.jpg", work, lang, template,
                   label=(series or {}).get("label") or cfg["thumbnails"].get("label") or cfg["channel"]["name"].upper())

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
    description = build_description(cfg, script, res, chapters, fmt, lang)
    if series:
        head = (f"📚 {series['name']} — एपिसोड {episode}" if lang == "hi" else f"📚 Series: {series['name']} — Episode {episode}")
        description = head + "\n\n" + description if fmt == "long" else description + f"\n\n📚 {series['name']}"
    timeline = [[round(st, 2), it.get("line_text", "")] for it, st in zip(items, starts)]
    meta = {"title": title, "lang": lang, "caption_track": caption_track, "timeline": timeline,
            "duration": round(total, 2),
            "localizations": localizations,
            "description": description,
            "tags": script.get("tags") or [],
            "engagement_question": script.get("engagement_question") or ""}
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

    res = research_mod.research(topic)
    if len(res["text"]) < (3000 if fmt == "long" else 600):
        raise RuntimeError(f"Not enough research material for {res['wiki_title']}")
    script_en = script_writer.write(cfg, topic, res, fmt)
    script_en = review.review(cfg, script_en, res, fmt)  # raises -> main tries the next topic
    phrases = keywords.for_topic(topic["topic"], res["wiki_title"])
    illustrate(cfg, script_en, base / "art", fmt, res)

    template = thumbnail.choose_template(cfg)  # same A/B arm for every language of this topic
    series = series_for(cfg, topic)
    episode = 1 + sum(1 for r in state.load() if r.get("series") == (series or {}).get("id")
                      and r.get("format") == fmt and r.get("video_id"))
    results = []
    for lang in langs:
        try:
            script = (script_en if lang == "en"
                      else script_writer.translate_hindi(cfg, script_en, fmt, phrases))
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
                              "pending_comment": meta["engagement_question"] if video_id else ""})
            cleanup(work)
            results.append((lang, video_id, work))
        except Exception as e:
            log.error("[%s] version failed: %s", lang, e)
            if "quotaExceeded" in str(e):
                raise
    if not results:
        raise RuntimeError("No language version could be produced")
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
curiosity), the same hook in Hinglish (Roman letters), a title under 70 characters, and 3-5
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
            clip = {**{key: script.get(key) for key in ("hashtags", "tags", "engagement_question", "credits")},
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
            when = youtube.next_publish_time(slots, k) if upload else None
            vid = publish(cfg, work, "short", meta, when) if upload else None
            if upload:
                state.append({"format": "short", "lang": lang, "topic": f"{script['title']} (clip {k + 1})",
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
    video_id = youtube.upload(cfg, work / "final.mp4", meta, publish_at)
    tl = ROOT / "data" / "timelines"
    tl.mkdir(parents=True, exist_ok=True)
    (tl / f"{video_id}.json").write_text(json.dumps({"duration": meta.get("duration"),
                                                     "timeline": meta.get("timeline")}, ensure_ascii=False))
    if fmt == "long":
        youtube.set_thumbnail(video_id, work / "thumbnail.jpg")
    if cfg[fmt].get("upload_captions"):
        youtube.upload_captions(video_id, work / "captions.srt", meta.get("caption_track", lang))
    youtube.add_to_playlist(video_id, youtube.playlist_for(cfg, fmt, lang, series if fmt == "long" else None))
    if fmt == "short":
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
        if p.is_file() and p.name not in KEEP:
            p.unlink()
