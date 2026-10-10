"""YouTube Data API v3: OAuth from env, upload, thumbnail, captions, playlists, stats, research."""
import json
import os
import statistics
from datetime import datetime, timedelta, timezone

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

from .common import log

SCOPES = ["https://www.googleapis.com/auth/youtube.upload",
          "https://www.googleapis.com/auth/youtube.force-ssl",
          "https://www.googleapis.com/auth/yt-analytics.readonly"]

_client = None
_creds = None


def available():
    return all(os.environ.get(k) for k in ("YT_CLIENT_ID", "YT_CLIENT_SECRET", "YT_REFRESH_TOKEN"))


def credentials():
    client()
    return _creds


def client():
    global _client, _creds
    if _client is None:
        _creds = creds = Credentials(
            token=None,
            refresh_token=os.environ["YT_REFRESH_TOKEN"],
            token_uri="https://oauth2.googleapis.com/token",
            client_id=os.environ["YT_CLIENT_ID"],
            client_secret=os.environ["YT_CLIENT_SECRET"],
        )
        _client = build("youtube", "v3", credentials=creds, cache_discovery=False)
    return _client


def next_publish_time(slots_utc, index):
    """Return RFC3339 publishAt for the index-th slot today, or None if that slot has passed."""
    if not slots_utc or index >= len(slots_utc):
        return None
    hh, mm = map(int, slots_utc[index].split(":"))
    now = datetime.now(timezone.utc)
    at = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if at - now < timedelta(minutes=20):  # passed or too close -> publish now
        return None
    return at.isoformat().replace("+00:00", "Z")


def upload(cfg, video_path, meta, publish_at=None):
    yc = cfg["youtube"]
    tags, total = [], 0
    for t in meta.get("tags", []):  # YouTube caps tags at ~500 chars total
        t = t.replace("<", "").replace(">", "").strip()
        if t and total + len(t) + 2 <= 480:
            tags.append(t)
            total += len(t) + 2
    status = {
        "privacyStatus": "private" if publish_at else yc["privacy"],
        "selfDeclaredMadeForKids": yc["made_for_kids"],
        "containsSyntheticMedia": yc["contains_synthetic_media"],
        "embeddable": True,
        "license": "youtube",
    }
    if publish_at:
        status["publishAt"] = publish_at
    body = {
        "snippet": {
            "title": meta["title"][:100],
            "description": meta["description"][:4900],
            "tags": tags,
            "categoryId": cfg["channel"]["category_id"],
            "defaultLanguage": meta.get("lang") or cfg["channel"]["language"],
            "defaultAudioLanguage": meta.get("lang") or cfg["channel"]["language"],
        },
        "status": status,
    }
    parts = "snippet,status"
    if meta.get("localizations"):  # e.g. English title/description shown to English-language viewers
        body["localizations"] = meta["localizations"]
        parts += ",localizations"
    media = MediaFileUpload(str(video_path), mimetype="video/mp4", chunksize=8 << 20, resumable=True)
    req = client().videos().insert(part=parts, body=body, media_body=media)
    resp = None
    while resp is None:
        prog, resp = req.next_chunk()
        if prog:
            log.info("  upload %d%%", int(prog.progress() * 100))
    vid = resp["id"]
    log.info("Uploaded https://youtu.be/%s (%s%s)", vid, status["privacyStatus"],
             f", publishes {publish_at}" if publish_at else "")
    return vid


def set_thumbnail(video_id, path):
    try:
        client().thumbnails().set(videoId=video_id, media_body=MediaFileUpload(str(path))).execute()
    except HttpError as e:
        # 403 here usually means the channel is not phone-verified yet.
        log.warning("Thumbnail not set (%s). Verify the channel at youtube.com/verify", e.status_code)


def upload_captions(video_id, srt_path, language="en"):
    try:
        client().captions().insert(
            part="snippet",
            body={"snippet": {"videoId": video_id, "language": language,
                              "name": {"hi": "हिन्दी", "hi-Latn": "Hinglish", "en": "English"}.get(language, language),
                              "isDraft": False}},
            media_body=MediaFileUpload(str(srt_path), mimetype="application/octet-stream"),
        ).execute()
    except HttpError as e:
        log.warning("Captions not uploaded: %s", e)


PLAYLISTS = {  # (format, lang) -> title; created automatically on first use
    ("long", "hi"): "Odd Annals — रहस्यमयी इतिहास (पूरी कहानियाँ)",
    ("short", "hi"): "Odd Annals — 1 मिनट के रहस्य",
    ("long", "en"): "Odd Annals — Full Documentaries",
    ("short", "en"): "Odd Annals — 60-Second Mysteries",
}


def playlist_for(cfg, fmt, lang, series=None):
    """Playlist id for this format/language (or series), creating it (50 quota units, once) if needed."""
    from .common import ROOT
    path = ROOT / "data" / "playlists.json"
    known = json.loads(path.read_text()) if path.exists() else {}
    key = f"series-{series['id']}-{lang}" if series else f"{fmt}-{lang}"
    if known.get(key):
        return known[key]
    if cfg["youtube"].get(f"{fmt}_playlist_id") and lang == cfg["channel"]["language"]:
        return cfg["youtube"][f"{fmt}_playlist_id"]
    try:
        title = (f"{series['name']} | {cfg['channel']['name']}" if series
                 else PLAYLISTS.get((fmt, lang), f"{cfg['channel']['name']} — {fmt} ({lang})"))
        pl = client().playlists().insert(part="snippet,status", body={
            "snippet": {"title": title, "defaultLanguage": lang},
            "status": {"privacyStatus": "public"}}).execute()
        known[key] = pl["id"]
        path.write_text(json.dumps(known, indent=1, ensure_ascii=False))
        log.info("Created playlist %r", title)
        return pl["id"]
    except HttpError as e:
        log.warning("Playlist create failed: %s", e)
        return None


def post_comment(video_id, text):
    """Channel's own first comment with a question — comments drive distribution."""
    client().commentThreads().insert(part="snippet", body={"snippet": {
        "videoId": video_id, "topLevelComment": {"snippet": {"textOriginal": text}}}}).execute()


def add_to_playlist(video_id, playlist_id):
    if not playlist_id:
        return
    try:
        client().playlistItems().insert(part="snippet", body={"snippet": {
            "playlistId": playlist_id,
            "resourceId": {"kind": "youtube#video", "videoId": video_id}}}).execute()
    except HttpError as e:
        log.warning("Playlist add failed: %s", e)


def video_stats(video_ids):
    """{video_id: views} for our own uploads (1 quota unit per 50 ids)."""
    out = {}
    ids = [v for v in video_ids if v]
    for i in range(0, len(ids), 50):
        resp = client().videos().list(part="statistics", id=",".join(ids[i:i + 50])).execute()
        for it in resp.get("items", []):
            out[it["id"]] = int(it["statistics"].get("viewCount", 0))
    return out


def competitor_outliers(cfg):
    """swipe.py method: rank competitors' recent videos by multiple of their own channel median.

    Uses ~3 quota units per channel (channels.list + playlistItems.list + videos.list).
    """
    rc = cfg["research"]
    yt = client()
    rows = []
    for handle in rc["competitor_handles"]:
        try:
            ch = yt.channels().list(part="contentDetails,snippet", forHandle=handle).execute()
            if not ch.get("items"):
                log.warning("Competitor %s not found, skipping", handle)
                continue
            uploads = ch["items"][0]["contentDetails"]["relatedPlaylists"]["uploads"]
            name = ch["items"][0]["snippet"]["title"]
            pl = yt.playlistItems().list(part="contentDetails", playlistId=uploads,
                                         maxResults=min(50, rc["videos_per_channel"])).execute()
            ids = [i["contentDetails"]["videoId"] for i in pl.get("items", [])]
            if len(ids) < 4:
                continue  # a median off fewer than four videos is noise
            vs = yt.videos().list(part="snippet,statistics", id=",".join(ids)).execute()
            vids = [(v["snippet"]["title"], int(v["statistics"].get("viewCount", 0)))
                    for v in vs.get("items", [])]
            med = statistics.median(v for _, v in vids) or 1
            for title, views in vids:
                rows.append({"channel": name, "title": title, "views": views,
                             "multiple": round(views / med, 2)})
        except HttpError as e:
            log.warning("Research on %s failed: %s", handle, e)
    out = [r for r in rows if r["multiple"] >= rc["outlier_min_multiple"]]
    out.sort(key=lambda r: -r["multiple"])
    return out[:20]
