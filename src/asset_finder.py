"""
src/asset_finder.py
Stage B — Licensed Visual Discovery.

Responsibilities:
- Search Pexels Videos/Photos API per scene keyword.
- Fallback to Pixabay Videos/Photos API.
- Fallback to Wikimedia Commons (images only).
- Verify license for every asset before downloading.
- Maintain sources_and_licenses.csv manifest.
- Never assume a freely viewable asset is copyright-free.
- Skip assets whose license cannot be verified.
- Never download YouTube or restricted-platform content.
"""

from __future__ import annotations

import csv
import logging
import os
import time
from dataclasses import dataclass, field, asdict
from datetime import date
from pathlib import Path
from typing import Optional
from urllib.parse import urlencode

import requests

logger = logging.getLogger(__name__)

# ─── Constants ────────────────────────────────────────────────────────────────

PEXELS_VIDEO_URL  = "https://api.pexels.com/videos/search"
PEXELS_PHOTO_URL  = "https://api.pexels.com/v1/search"
PIXABAY_URL       = "https://pixabay.com/api/"
PIXABAY_VIDEO_URL = "https://pixabay.com/api/videos/"
WIKIMEDIA_URL     = "https://commons.wikimedia.org/w/api.php"

HEADERS_TIMEOUT = 15   # seconds


# ─── Data structures ──────────────────────────────────────────────────────────

@dataclass
class MediaAsset:
    scene_index: int
    asset_type: str          # "video" | "image"
    source_url: str          # Direct download URL
    page_url: str            # Human-readable asset page
    creator: str
    license: str
    license_url: str
    download_date: str       # ISO 8601
    attribution: str         # Full required attribution string
    local_path: str = ""     # Set after download
    duration: float = 0.0   # seconds (video only)
    width: int = 0
    height: int = 0


# ─── Main class ───────────────────────────────────────────────────────────────

class AssetFinder:
    """Find, verify, and download licensed visual assets per scene."""

    def __init__(self, config: dict, download_dir: str | Path):
        self.cfg = config
        self.download_dir = Path(download_dir)
        self.download_dir.mkdir(parents=True, exist_ok=True)

        self.pexels_key   = os.getenv("PEXELS_API_KEY", "")
        self.pixabay_key  = os.getenv("PIXABAY_API_KEY", "")
        self.prefer_video = config.get("assets", {}).get("prefer_video", True)
        self.min_clip_dur = config.get("assets", {}).get("min_clip_duration", 3)
        self.max_clip_dur = config.get("assets", {}).get("max_clip_duration", 10)

        self.manifest: list[MediaAsset] = []
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": "AI-Shorts-Generator/1.0"})

    # ── Public API ─────────────────────────────────────────────────────────────

    def find_for_scene(self, scene_index: int, keywords: list[str]) -> Optional[MediaAsset]:
        """
        Find a licensed asset for a scene. Tries Pexels → Pixabay → Wikimedia.
        Returns the first usable asset, or None if none found.
        """
        query = " ".join(keywords[:3])
        logger.info(f"Scene {scene_index}: searching for '{query}'")

        # 1. Pexels video
        if self.pexels_key and self.prefer_video:
            asset = self._search_pexels_video(scene_index, query)
            if asset:
                return asset

        # 2. Pexels photo
        if self.pexels_key:
            asset = self._search_pexels_photo(scene_index, query)
            if asset:
                return asset

        # 3. Pixabay video
        if self.pixabay_key and self.prefer_video:
            asset = self._search_pixabay_video(scene_index, query)
            if asset:
                return asset

        # 4. Pixabay photo
        if self.pixabay_key:
            asset = self._search_pixabay_photo(scene_index, query)
            if asset:
                return asset

        # 5. Wikimedia Commons (images only)
        asset = self._search_wikimedia(scene_index, query)
        if asset:
            return asset

        logger.warning(f"Scene {scene_index}: no licensed asset found for '{query}'")
        return None

    def download_asset(self, asset: MediaAsset) -> bool:
        """Download an asset to local_path. Returns True on success."""
        ext = "mp4" if asset.asset_type == "video" else "jpg"
        filename = f"scene_{asset.scene_index:02d}_{int(time.time())}.{ext}"
        local_path = self.download_dir / filename

        try:
            logger.info(f"Downloading {asset.source_url}")
            r = self._session.get(asset.source_url, timeout=60, stream=True)
            r.raise_for_status()
            with open(local_path, "wb") as f:
                for chunk in r.iter_content(chunk_size=8192):
                    f.write(chunk)
            asset.local_path = str(local_path)
            self.manifest.append(asset)
            logger.info(f"Saved to {local_path}")
            return True
        except Exception as e:
            logger.error(f"Download failed for scene {asset.scene_index}: {e}")
            return False

    def save_manifest(self, output_dir: str | Path) -> Path:
        """Write sources_and_licenses.csv to output_dir."""
        out = Path(output_dir) / "sources_and_licenses.csv"
        fieldnames = [
            "scene_index", "asset_type", "source_url", "page_url",
            "creator", "license", "license_url", "download_date",
            "attribution", "local_path",
        ]
        with open(out, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for asset in self.manifest:
                row = asdict(asset)
                writer.writerow({k: row[k] for k in fieldnames})
        logger.info(f"Manifest saved: {out}")
        return out

    # ── Pexels ────────────────────────────────────────────────────────────────

    def _search_pexels_video(self, scene_index: int, query: str) -> Optional[MediaAsset]:
        params = {
            "query": query,
            "per_page": 5,
            "size": "medium",
            "orientation": "portrait",
        }
        headers = {"Authorization": self.pexels_key}
        try:
            r = self._session.get(
                PEXELS_VIDEO_URL, params=params, headers=headers,
                timeout=HEADERS_TIMEOUT
            )
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            logger.warning(f"Pexels video search failed: {e}")
            return None

        for video in data.get("videos", []):
            # Pick best file: prefer HD portrait
            files = sorted(
                video.get("video_files", []),
                key=lambda vf: vf.get("height", 0),
                reverse=True,
            )
            chosen = None
            for vf in files:
                dur = video.get("duration", 0)
                if vf.get("link") and self.min_clip_dur <= dur <= self.max_clip_dur + 5:
                    chosen = vf
                    break
            if not chosen and files:
                chosen = files[0]

            if not chosen:
                continue

            photographer = video.get("user", {}).get("name", "Unknown")
            page_url = video.get("url", "")

            return MediaAsset(
                scene_index=scene_index,
                asset_type="video",
                source_url=chosen["link"],
                page_url=page_url,
                creator=photographer,
                license="Pexels License",
                license_url="https://www.pexels.com/license/",
                download_date=date.today().isoformat(),
                attribution=f'Video by {photographer} on Pexels ({page_url})',
                duration=float(video.get("duration", 0)),
                width=chosen.get("width", 0),
                height=chosen.get("height", 0),
            )
        return None

    def _search_pexels_photo(self, scene_index: int, query: str) -> Optional[MediaAsset]:
        params = {"query": query, "per_page": 3, "orientation": "portrait"}
        headers = {"Authorization": self.pexels_key}
        try:
            r = self._session.get(
                PEXELS_PHOTO_URL, params=params, headers=headers,
                timeout=HEADERS_TIMEOUT
            )
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            logger.warning(f"Pexels photo search failed: {e}")
            return None

        for photo in data.get("photos", []):
            src = photo.get("src", {})
            url = src.get("portrait") or src.get("large") or src.get("original")
            if not url:
                continue
            photographer = photo.get("photographer", "Unknown")
            page_url = photo.get("url", "")
            return MediaAsset(
                scene_index=scene_index,
                asset_type="image",
                source_url=url,
                page_url=page_url,
                creator=photographer,
                license="Pexels License",
                license_url="https://www.pexels.com/license/",
                download_date=date.today().isoformat(),
                attribution=f'Photo by {photographer} on Pexels ({page_url})',
                width=photo.get("width", 0),
                height=photo.get("height", 0),
            )
        return None

    # ── Pixabay ───────────────────────────────────────────────────────────────

    def _search_pixabay_video(self, scene_index: int, query: str) -> Optional[MediaAsset]:
        params = {
            "key": self.pixabay_key,
            "q": query,
            "video_type": "film",
            "per_page": 5,
        }
        try:
            r = self._session.get(PIXABAY_VIDEO_URL, params=params, timeout=HEADERS_TIMEOUT)
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            logger.warning(f"Pixabay video search failed: {e}")
            return None

        for hit in data.get("hits", []):
            videos = hit.get("videos", {})
            # Prefer medium > small > large
            for size_key in ("medium", "small", "large", "tiny"):
                vf = videos.get(size_key, {})
                url = vf.get("url")
                if url:
                    user = hit.get("user", "Unknown")
                    page_url = f"https://pixabay.com/videos/{hit.get('id', '')}"
                    return MediaAsset(
                        scene_index=scene_index,
                        asset_type="video",
                        source_url=url,
                        page_url=page_url,
                        creator=user,
                        license="Pixabay Content License",
                        license_url="https://pixabay.com/service/license-summary/",
                        download_date=date.today().isoformat(),
                        attribution=f'Video by {user} on Pixabay ({page_url})',
                        duration=float(hit.get("duration", 0)),
                        width=vf.get("width", 0),
                        height=vf.get("height", 0),
                    )
        return None

    def _search_pixabay_photo(self, scene_index: int, query: str) -> Optional[MediaAsset]:
        params = {
            "key": self.pixabay_key,
            "q": query,
            "image_type": "photo",
            "orientation": "vertical",
            "per_page": 3,
        }
        try:
            r = self._session.get(PIXABAY_URL, params=params, timeout=HEADERS_TIMEOUT)
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            logger.warning(f"Pixabay photo search failed: {e}")
            return None

        for hit in data.get("hits", []):
            url = hit.get("largeImageURL") or hit.get("webformatURL")
            if not url:
                continue
            user = hit.get("user", "Unknown")
            page_url = f"https://pixabay.com/photos/{hit.get('id', '')}"
            return MediaAsset(
                scene_index=scene_index,
                asset_type="image",
                source_url=url,
                page_url=page_url,
                creator=user,
                license="Pixabay Content License",
                license_url="https://pixabay.com/service/license-summary/",
                download_date=date.today().isoformat(),
                attribution=f'Photo by {user} on Pixabay ({page_url})',
                width=hit.get("imageWidth", 0),
                height=hit.get("imageHeight", 0),
            )
        return None

    # ── Wikimedia Commons ─────────────────────────────────────────────────────

    def _search_wikimedia(self, scene_index: int, query: str) -> Optional[MediaAsset]:
        """Search Wikimedia Commons for CC-licensed images."""
        params = {
            "action": "query",
            "format": "json",
            "list": "search",
            "srsearch": f"{query} filetype:jpg",
            "srnamespace": "6",   # File namespace
            "srlimit": "3",
            "srprop": "snippet|titlesnippet",
        }
        try:
            r = self._session.get(WIKIMEDIA_URL, params=params, timeout=HEADERS_TIMEOUT)
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            logger.warning(f"Wikimedia search failed: {e}")
            return None

        results = data.get("query", {}).get("search", [])
        for result in results:
            title = result.get("title", "")
            if not title.startswith("File:"):
                continue

            # Get image info including license
            info = self._get_wikimedia_image_info(title)
            if not info:
                continue

            license_short = info.get("license_short", "")
            # Accept only CC or public domain licenses
            ok_licenses = ["CC", "Public Domain", "CC0", "pd", "PD"]
            if not any(lic in license_short for lic in ok_licenses):
                logger.debug(f"Wikimedia: skipping {title} — license: {license_short}")
                continue

            url = info.get("url")
            if not url:
                continue

            return MediaAsset(
                scene_index=scene_index,
                asset_type="image",
                source_url=url,
                page_url=f"https://commons.wikimedia.org/wiki/{title.replace(' ', '_')}",
                creator=info.get("author", "Unknown"),
                license=license_short,
                license_url=info.get("license_url", "https://commons.wikimedia.org/"),
                download_date=date.today().isoformat(),
                attribution=(
                    f'{info.get("author", "Unknown")} / Wikimedia Commons '
                    f'/ {license_short}'
                ),
                width=info.get("width", 0),
                height=info.get("height", 0),
            )
        return None

    def _get_wikimedia_image_info(self, title: str) -> Optional[dict]:
        """Fetch image URL and license for a Wikimedia Commons file."""
        params = {
            "action": "query",
            "format": "json",
            "titles": title,
            "prop": "imageinfo",
            "iiprop": "url|extmetadata|dimensions",
            "iiurlwidth": "1080",
        }
        try:
            r = self._session.get(WIKIMEDIA_URL, params=params, timeout=HEADERS_TIMEOUT)
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            logger.warning(f"Wikimedia image info failed for {title}: {e}")
            return None

        pages = data.get("query", {}).get("pages", {})
        for page_id, page in pages.items():
            if page_id == "-1":
                continue
            imageinfo = page.get("imageinfo", [{}])[0]
            meta = imageinfo.get("extmetadata", {})
            return {
                "url": imageinfo.get("thumburl") or imageinfo.get("url"),
                "width": imageinfo.get("thumbwidth", 0),
                "height": imageinfo.get("thumbheight", 0),
                "author": meta.get("Artist", {}).get("value", "Unknown"),
                "license_short": meta.get("LicenseShortName", {}).get("value", ""),
                "license_url": meta.get("LicenseUrl", {}).get("value", ""),
            }
        return None
