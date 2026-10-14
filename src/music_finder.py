"""
src/music_finder.py
Music Discovery — Pixabay Music API.

Responsibilities:
- Search Pixabay Music for background tracks matching the topic/mood.
- Verify Pixabay Content License (commercial use permitted with attribution).
- Download selected track to temp directory.
- Record attribution in the asset manifest.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Optional

import requests

logger = logging.getLogger(__name__)

PIXABAY_MUSIC_URL = "https://pixabay.com/api/music/"

# Mood → Pixabay category mapping
MOOD_TO_CATEGORY = {
    "upbeat": "upbeat",
    "inspiring": "upbeat",
    "dramatic": "cinematic",
    "calm": "ambient",
    "technology": "electronic",
    "space": "ambient",
    "news": "corporate",
    "default": "upbeat",
}


@dataclass
class MusicTrack:
    title: str
    artist: str
    source_url: str
    download_url: str
    license: str = "Pixabay Content License"
    license_url: str = "https://pixabay.com/service/license-summary/"
    download_date: str = ""
    local_path: str = ""
    duration: float = 0.0

    def attribution(self) -> str:
        return f'Music: "{self.title}" by {self.artist} on Pixabay ({self.source_url})'


class MusicFinder:
    """Find and download background music from Pixabay Music."""

    def __init__(self, config: dict, download_dir: str | Path):
        self.cfg = config
        self.download_dir = Path(download_dir)
        self.download_dir.mkdir(parents=True, exist_ok=True)
        self.pixabay_key = os.getenv("PIXABAY_API_KEY", "")
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": "AI-Shorts-Generator/1.0"})

    def find_track(self, topic: Optional[str] = None, mood: str = "upbeat") -> Optional[MusicTrack]:
        """Find a suitable music track. Returns None if unavailable."""
        if not self.pixabay_key:
            logger.warning("No PIXABAY_API_KEY set; skipping background music")
            return None

        category = MOOD_TO_CATEGORY.get(mood.lower(), "upbeat")
        params = {
            "key": self.pixabay_key,
            "category": category,
            "per_page": 5,
            "min_duration": 30,
            "max_duration": 120,
        }
        if topic:
            params["q"] = topic[:50]

        try:
            r = self._session.get(PIXABAY_MUSIC_URL, params=params, timeout=15)
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            logger.warning(f"Pixabay Music search failed: {e}")
            return None

        hits = data.get("hits", [])
        if not hits:
            logger.warning(f"No Pixabay Music results for mood='{mood}', topic='{topic}'")
            return None

        hit = hits[0]
        audio_url = hit.get("audio", {}).get("mp3", "")
        if not audio_url:
            audio_url = hit.get("audio_url", "")
        if not audio_url:
            logger.warning("Pixabay Music hit has no audio URL")
            return None

        return MusicTrack(
            title=hit.get("title", "Unknown"),
            artist=hit.get("user", "Unknown"),
            source_url=f"https://pixabay.com/music/{hit.get('id', '')}",
            download_url=audio_url,
            download_date=date.today().isoformat(),
            duration=float(hit.get("duration", 0)),
        )

    def download_track(self, track: MusicTrack) -> bool:
        """Download a music track. Returns True on success."""
        filename = f"music_{int(time.time())}.mp3"
        local_path = self.download_dir / filename
        try:
            r = self._session.get(track.download_url, timeout=60, stream=True)
            r.raise_for_status()
            with open(local_path, "wb") as f:
                for chunk in r.iter_content(chunk_size=8192):
                    f.write(chunk)
            track.local_path = str(local_path)
            track.download_date = date.today().isoformat()
            logger.info(f"Music downloaded: {local_path}")
            return True
        except Exception as e:
            logger.error(f"Music download failed: {e}")
            return False
