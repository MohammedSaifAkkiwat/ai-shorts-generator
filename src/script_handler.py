"""
src/script_handler.py
Stage A — Script Input, Validation, and Scene Segmentation.

Responsibilities:
- Read script from a .txt file or stdin.
- Estimate speaking duration (words / wpm).
- Validate length (40–65 seconds target).
- Segment script into scenes (sentence / paragraph based).
- Extract per-scene keywords for visual search.
- Optionally use Gemini to improve segmentation and keyword quality.
"""

from __future__ import annotations

import os
import re
import sys
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml

logger = logging.getLogger(__name__)


# ─── Data structures ─────────────────────────────────────────────────────────

@dataclass
class Scene:
    index: int
    text: str
    keywords: list[str] = field(default_factory=list)
    estimated_duration: float = 0.0   # seconds

@dataclass
class ScriptAnalysis:
    raw_text: str
    scenes: list[Scene]
    total_words: int
    estimated_duration: float           # seconds
    wpm: int
    duration_warning: Optional[str]     # None if within range


# ─── Main class ──────────────────────────────────────────────────────────────

class ScriptHandler:
    """Parse, validate and segment a script into scenes."""

    DEFAULT_WPM = 150

    def __init__(self, config: dict):
        self.cfg = config
        self.wpm = config.get("narration", {}).get("words_per_minute", self.DEFAULT_WPM)
        self.min_dur = config.get("video", {}).get("target_duration_min", 40)
        self.max_dur = config.get("video", {}).get("target_duration_max", 65)
        self._gemini = None   # lazy init

    # ── Public API ────────────────────────────────────────────────────────────

    def load_from_file(self, path: str | Path) -> str:
        """Read script text from a file. Raises FileNotFoundError if missing."""
        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(f"Script file not found: {path}")
        text = p.read_text(encoding="utf-8").strip()
        if not text:
            raise ValueError(f"Script file is empty: {path}")
        logger.info(f"Loaded script from {path} ({len(text)} chars)")
        return text

    def load_from_stdin(self) -> str:
        """Read script from stdin."""
        print("Paste your script below. Press Ctrl+Z (Windows) then Enter when done:")
        lines = sys.stdin.readlines()
        text = "".join(lines).strip()
        if not text:
            raise ValueError("No script received from stdin.")
        return text

    def analyse(self, raw_text: str, topic: Optional[str] = None) -> ScriptAnalysis:
        """
        Validate and segment the script.
        Returns ScriptAnalysis with scenes, word count, duration estimate, warning.
        """
        cleaned = self._clean_text(raw_text)
        words = cleaned.split()
        total_words = len(words)
        estimated_duration = round(total_words / self.wpm * 60, 1)

        # Duration warning
        warning = None
        if estimated_duration < self.min_dur:
            warning = (
                f"⚠️  Script is too short ({estimated_duration:.0f}s estimated). "
                f"Target: {self.min_dur}–{self.max_dur}s. Add ~"
                f"{int((self.min_dur - estimated_duration) / 60 * self.wpm)} words."
            )
        elif estimated_duration > self.max_dur:
            warning = (
                f"⚠️  Script is too long ({estimated_duration:.0f}s estimated). "
                f"Target: {self.min_dur}–{self.max_dur}s. Remove ~"
                f"{int((estimated_duration - self.max_dur) / 60 * self.wpm)} words."
            )

        # Try Gemini segmentation first; fall back to rule-based
        gemini_key = os.getenv("GEMINI_API_KEY", "")
        use_gemini = (
            self.cfg.get("fact_checking", {}).get("engine") == "gemini"
            and gemini_key
        )

        if use_gemini:
            scenes = self._segment_with_gemini(cleaned, topic)
        else:
            scenes = self._segment_rule_based(cleaned)

        # Assign per-scene duration proportional to word count
        scene_words = [len(s.text.split()) for s in scenes]
        for i, scene in enumerate(scenes):
            scene.estimated_duration = round(
                scene_words[i] / max(total_words, 1) * estimated_duration, 2
            )

        return ScriptAnalysis(
            raw_text=raw_text,
            scenes=scenes,
            total_words=total_words,
            estimated_duration=estimated_duration,
            wpm=self.wpm,
            duration_warning=warning,
        )

    # ── Private helpers ───────────────────────────────────────────────────────

    def _clean_text(self, text: str) -> str:
        """Normalize whitespace and remove unwanted characters."""
        text = re.sub(r"\r\n|\r", "\n", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        text = re.sub(r"[ \t]+", " ", text)
        return text.strip()

    def _segment_rule_based(self, text: str) -> list[Scene]:
        """
        Split on blank lines (paragraphs) first; then sentences within paragraphs.
        Each resulting scene gets basic TF-IDF-style keyword extraction.
        """
        paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
        if not paragraphs:
            paragraphs = [text]

        # If only one paragraph, split into sentences
        if len(paragraphs) == 1:
            sentences = self._split_sentences(text)
            # Group into ~3-sentence scenes
            group_size = 2
            groups = [
                " ".join(sentences[i:i + group_size])
                for i in range(0, len(sentences), group_size)
            ]
            paragraphs = [g for g in groups if g.strip()]

        scenes = []
        for idx, para in enumerate(paragraphs):
            keywords = self._extract_keywords(para)
            scenes.append(Scene(index=idx, text=para, keywords=keywords))
        return scenes

    def _split_sentences(self, text: str) -> list[str]:
        """Naive sentence splitter."""
        # Split on . ? ! followed by space or newline
        parts = re.split(r"(?<=[.?!])\s+", text)
        return [p.strip() for p in parts if p.strip()]

    def _extract_keywords(self, text: str, top_n: int = 5) -> list[str]:
        """
        Simple keyword extraction: remove stop words, return most common nouns.
        Returns at least 2 broad fallback terms if extraction yields nothing.
        """
        stop_words = {
            "a", "an", "the", "and", "or", "but", "in", "on", "at", "to",
            "for", "of", "with", "by", "from", "is", "are", "was", "were",
            "it", "its", "this", "that", "these", "those", "we", "our",
            "their", "they", "has", "have", "had", "be", "been", "not",
            "as", "so", "if", "than", "then", "which", "who", "what",
            "just", "only", "also", "some", "any", "all", "more", "most",
            "first", "last", "new", "old", "one", "two", "three",
        }
        words = re.findall(r"\b[a-zA-Z]{3,}\b", text.lower())
        freq: dict[str, int] = {}
        for w in words:
            if w not in stop_words:
                freq[w] = freq.get(w, 0) + 1
        keywords = sorted(freq, key=lambda k: -freq[k])[:top_n]
        return keywords if keywords else ["nature", "technology"]

    def _segment_with_gemini(self, text: str, topic: Optional[str]) -> list[Scene]:
        """Use Gemini to split script into scenes and extract visual search keywords."""
        try:
            import google.generativeai as genai
            api_key = os.getenv("GEMINI_API_KEY", "")
            genai.configure(api_key=api_key)
            model_name = self.cfg.get("gemini", {}).get("model", "gemini-1.5-flash")
            model = genai.GenerativeModel(model_name)

            topic_hint = f" The video topic is: {topic}." if topic else ""
            prompt = f"""You are a YouTube Shorts script editor.{topic_hint}

Split the following script into 4–8 short scenes suitable for a 40–60 second YouTube Short.
For each scene provide:
1. The exact scene text (do not change words, only split)
2. 3–5 specific, visual search keywords for finding stock video footage

Return ONLY valid JSON in this exact format (no markdown fences):
{{
  "scenes": [
    {{"text": "scene text here", "keywords": ["keyword1", "keyword2", "keyword3"]}},
    ...
  ]
}}

Script:
{text}"""

            response = model.generate_content(prompt)
            raw = response.text.strip()
            # Strip any accidental markdown fences
            raw = re.sub(r"^```json\s*", "", raw)
            raw = re.sub(r"```$", "", raw)

            import json
            data = json.loads(raw)
            scenes = []
            for idx, s in enumerate(data.get("scenes", [])):
                scenes.append(
                    Scene(
                        index=idx,
                        text=s.get("text", ""),
                        keywords=s.get("keywords", []),
                    )
                )
            logger.info(f"Gemini segmentation returned {len(scenes)} scenes")
            return scenes if scenes else self._segment_rule_based(text)

        except Exception as e:
            logger.warning(f"Gemini segmentation failed ({e}); using rule-based fallback")
            return self._segment_rule_based(text)


# ─── Helper: load config ──────────────────────────────────────────────────────

def load_config(config_path: str | Path = "config.yaml") -> dict:
    """Load YAML configuration file."""
    p = Path(config_path)
    if not p.exists():
        logger.warning(f"config.yaml not found at {p}; using defaults")
        return {}
    with open(p, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}
