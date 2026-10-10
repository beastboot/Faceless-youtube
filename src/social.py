"""Cross-post Shorts as Instagram Reels and Facebook Reels via Meta's Graph API (resumable upload,
so no public video hosting is needed). Inactive until META_ACCESS_TOKEN + IG_USER_ID / FB_PAGE_ID exist.

Setup (once): Instagram professional account linked to a Facebook Page, a Meta app with
instagram_content_publish + pages_manage_posts, and a long-lived Page access token. See README."""
import os
import time

from .common import http, log

GRAPH = "https://graph.facebook.com/v21.0"


def _token():
    return os.environ.get("META_ACCESS_TOKEN")


def instagram_reel(video_path, caption):
    ig, token = os.environ.get("IG_USER_ID"), _token()
    if not (ig and token):
        return None
    s = http()
    r = s.post(f"{GRAPH}/{ig}/media", params={"media_type": "REELS", "upload_type": "resumable",
                                              "caption": caption[:2200], "share_to_feed": "true",
                                              "access_token": token}, timeout=60)
    r.raise_for_status()
    container, upload_uri = r.json()["id"], r.json()["uri"]
    data = open(video_path, "rb").read()
    up = s.post(upload_uri, headers={"Authorization": f"OAuth {token}", "offset": "0",
                                     "file_size": str(len(data))}, data=data, timeout=600)
    up.raise_for_status()
    for _ in range(60):  # Instagram processes the video before it can be published
        st = s.get(f"{GRAPH}/{container}", params={"fields": "status_code", "access_token": token},
                   timeout=30).json().get("status_code")
        if st == "FINISHED":
            break
        if st == "ERROR":
            raise RuntimeError("Instagram rejected the video")
        time.sleep(10)
    pub = s.post(f"{GRAPH}/{ig}/media_publish", params={"creation_id": container, "access_token": token},
                 timeout=60)
    pub.raise_for_status()
    return pub.json().get("id")


def facebook_reel(video_path, description):
    page, token = os.environ.get("FB_PAGE_ID"), _token()
    if not (page and token):
        return None
    s = http()
    start = s.post(f"{GRAPH}/{page}/video_reels", params={"upload_phase": "start", "access_token": token},
                   timeout=60)
    start.raise_for_status()
    vid, upload_url = start.json()["video_id"], start.json()["upload_url"]
    data = open(video_path, "rb").read()
    up = s.post(upload_url, headers={"Authorization": f"OAuth {token}", "offset": "0",
                                     "file_size": str(len(data))}, data=data, timeout=600)
    up.raise_for_status()
    fin = s.post(f"{GRAPH}/{page}/video_reels", params={
        "upload_phase": "finish", "video_id": vid, "video_state": "PUBLISHED",
        "description": description[:2200], "access_token": token}, timeout=60)
    fin.raise_for_status()
    return vid


def cross_post(cfg, video_path, meta, youtube_id):
    """Post a finished Short to Instagram/Facebook. Never fails the YouTube pipeline."""
    sc = cfg.get("social") or {}
    if not _token():
        return
    tags = " ".join(t for t in meta.get("description", "").split() if t.startswith("#"))[:200]
    caption = f"{meta['title'].replace('#shorts', '').strip()}\n\n▶ YouTube: https://youtu.be/{youtube_id}\n\n{tags}"
    for name, enabled, fn in (("Instagram", sc.get("instagram"), instagram_reel),
                              ("Facebook", sc.get("facebook"), facebook_reel)):
        if not enabled:
            continue
        try:
            pid = fn(video_path, caption)
            if pid:
                log.info("Cross-posted to %s (%s)", name, pid)
        except Exception as e:
            log.warning("%s cross-post failed: %s", name, e)
