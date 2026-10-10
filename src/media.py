"""Visual per scene: Wikimedia image > Pexels/Pixabay video > Pexels photo > Openverse image > fallback."""
import os
import random

from .common import download, http, log

PEXELS_VIDEOS = "https://api.pexels.com/videos/search"
PEXELS_PHOTOS = "https://api.pexels.com/v1/search"
PIXABAY_VIDEOS = "https://pixabay.com/api/videos/"
OPENVERSE_IMAGES = "https://api.openverse.org/v1/images/"


class MediaPicker:
    def __init__(self, workdir, width, height, research_images):
        self.dir = workdir
        self.w, self.h = width, height
        self.portrait = height > width
        self.s = http()
        self.wiki = research_images
        self.used_wiki, self.used_stock = set(), set()
        self.credits = []

    # ---- stock providers -------------------------------------------------------------
    def _pexels_video(self, query):
        key = os.environ.get("PEXELS_API_KEY")
        if not key:
            return None
        r = self.s.get(PEXELS_VIDEOS, headers={"Authorization": key}, timeout=30, params={
            "query": query, "per_page": 15,
            "orientation": "portrait" if self.portrait else "landscape"})
        if not r.ok:
            log.warning("Pexels %s: %s", r.status_code, r.text[:120])
            return None
        for v in r.json().get("videos", []):
            if f"pexels-{v['id']}" in self.used_stock or v.get("duration", 0) < 4:
                continue
            files = [f for f in v["video_files"] if f.get("file_type") == "video/mp4" and f.get("height")]
            # smallest file that still covers our output resolution
            short_side = min(self.w, self.h)
            ok = [f for f in files if min(f["width"], f["height"]) >= short_side * 0.95]
            f = min(ok, key=lambda f: f["width"] * f["height"]) if ok else (
                max(files, key=lambda f: f["width"] * f["height"]) if files else None)
            if f:
                self.used_stock.add(f"pexels-{v['id']}")
                return f["link"], f"pexels-{v['id']}.mp4"
        return None

    def _pixabay_video(self, query):
        key = os.environ.get("PIXABAY_API_KEY")
        if not key:
            return None
        r = self.s.get(PIXABAY_VIDEOS, timeout=30, params={"key": key, "q": query[:100],
                                                           "per_page": 15, "safesearch": "true"})
        if not r.ok:
            return None
        for v in r.json().get("hits", []):
            if f"pixabay-{v['id']}" in self.used_stock:
                continue
            vids = v.get("videos", {})
            f = vids.get("large") if vids.get("large", {}).get("url") else vids.get("medium")
            if f and f.get("url"):
                self.used_stock.add(f"pixabay-{v['id']}")
                return f["url"], f"pixabay-{v['id']}.mp4"
        return None

    def _pexels_photo(self, query):
        key = os.environ.get("PEXELS_API_KEY")
        if not key:
            return None
        r = self.s.get(PEXELS_PHOTOS, headers={"Authorization": key}, timeout=30, params={
            "query": query, "per_page": 10,
            "orientation": "portrait" if self.portrait else "landscape"})
        if not r.ok:
            return None
        for p in r.json().get("photos", []):
            if f"pexelsimg-{p['id']}" not in self.used_stock:
                self.used_stock.add(f"pexelsimg-{p['id']}")
                return p["src"]["large2x"], f"pexelsimg-{p['id']}.jpg"
        return None

    def _openverse_image(self, query):
        """No key needed. Only licenses that allow commercial use and modification without share-alike
        obligations on the whole video: CC0, public domain mark, CC BY (credited in the description)."""
        r = self.s.get(OPENVERSE_IMAGES, timeout=30, params={
            "q": query, "license": "by,cc0,pdm", "size": "large", "page_size": 20,
            "aspect_ratio": "tall" if self.portrait else "wide", "mature": "false"})
        if not r.ok:
            return None
        for x in r.json().get("results", []):
            if x["id"] in self.used_stock or (x.get("width") or 0) < 1000:
                continue
            self.used_stock.add(x["id"])
            if x["license"] == "by":
                self.credits.append(f"{x.get('title') or 'Image'} — {x.get('creator') or 'Unknown'} "
                                    f"(CC {x['license'].upper()} {x.get('license_version', '')}) "
                                    f"{x.get('foreign_landing_url', '')}")
            ext = ".png" if x["url"].lower().endswith(".png") else ".jpg"
            return x["url"], f"openverse-{x['id'][:12]}{ext}"
        return None

    # ---- public ------------------------------------------------------------------------
    def _wiki_image(self, idx):
        im = self.wiki[idx]
        ext = ".png" if im["url"].lower().endswith(".png") else ".jpg"
        path = download(im["url"], self.dir / f"wiki-{idx}{ext}", self.s)
        self.used_wiki.add(idx)
        self.credits.append(f"{im['title'].replace('File:', '')} — {im['artist']} ({im['license']}) "
                            f"{im['page']}")
        return {"kind": "image", "path": path}

    def for_scene(self, scene, i):
        idx = scene.get("image")
        if isinstance(idx, int) and 0 <= idx < len(self.wiki) and idx not in self.used_wiki:
            try:
                return self._wiki_image(idx)
            except Exception as e:
                log.warning("Wiki image %s failed: %s", idx, e)
        query = (scene.get("stock_query") or "history").strip()
        for fn, kind in ((self._pexels_video, "video"), (self._pixabay_video, "video"),
                         (self._pexels_photo, "image"), (self._openverse_image, "image")):
            for q in (query, " ".join(query.split()[:2])):
                try:
                    hit = fn(q)
                except Exception as e:
                    log.warning("%s(%r) failed: %s", fn.__name__, q, e)
                    hit = None
                if hit:
                    url, name = hit
                    try:
                        return {"kind": kind, "path": download(url, self.dir / name, self.s)}
                    except Exception as e:
                        log.warning("Download failed (%s), trying next source", e)
        # Last resort: reuse any unused (or random) research image.
        spare = [k for k in range(len(self.wiki)) if k not in self.used_wiki] or list(range(len(self.wiki)))
        if spare:
            return self._wiki_image(random.choice(spare))
        return {"kind": "color", "path": None}
