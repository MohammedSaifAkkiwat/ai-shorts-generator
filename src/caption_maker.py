"""
src/caption_maker.py
Stage C — Caption Generation.

Responsibilities:
- Generate an SRT caption file from word timings.
- Produce a word-highlight ASS subtitle file (TikTok style).
  Each word turns yellow as it is spoken; others remain white.
- Handle scenes with estimated timings (when gTTS is used as fallback).

Output:
- captions.srt  — standard subtitle file for the output package
- captions.ass  — Advanced SubStation Alpha for word-highlight burn-in
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ─── SRT helpers ──────────────────────────────────────────────────────────────

def _fmt_srt_time(seconds: float) -> str:
    """Format seconds as SRT timestamp: HH:MM:SS,mmm"""
    ms = int((seconds % 1) * 1000)
    s = int(seconds) % 60
    m = int(seconds) // 60 % 60
    h = int(seconds) // 3600
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


# ─── ASS helpers ──────────────────────────────────────────────────────────────

def _fmt_ass_time(seconds: float) -> str:
    """Format seconds as ASS timestamp: H:MM:SS.cc (centiseconds)"""
    cs = int((seconds % 1) * 100)
    s = int(seconds) % 60
    m = int(seconds) // 60 % 60
    h = int(seconds) // 3600
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


# ─── Main class ───────────────────────────────────────────────────────────────

class CaptionMaker:
    """Generate SRT and word-highlight ASS captions from word timings."""

    # Grouping: how many words per SRT subtitle entry
    WORDS_PER_GROUP = 4
    # Maximum line width in characters
    MAX_LINE_CHARS = 25

    def __init__(self, config: dict):
        self.cfg = config
        cap_cfg = config.get("captions", {})
        self.font_size      = cap_cfg.get("font_size", 72)
        self.highlight_color = cap_cfg.get("highlight_color", "yellow")
        self.base_color      = cap_cfg.get("base_color", "white")
        self.outline_color   = cap_cfg.get("outline_color", "black")
        self.outline_width   = cap_cfg.get("outline_width", 3)
        self.style           = cap_cfg.get("style", "word_highlight")

    # ── Public API ────────────────────────────────────────────────────────────

    def generate_srt(
        self,
        word_timings: list,      # list[WordTiming]
        output_path: str | Path,
    ) -> str:
        """Generate an SRT file from word timings. Returns path."""
        output_path = Path(output_path)
        entries = self._group_words(word_timings, self.WORDS_PER_GROUP)

        lines = []
        for i, (words, start, end) in enumerate(entries, start=1):
            text = " ".join(w.word for w in words)
            text = self._clean_caption_text(text)
            lines.append(str(i))
            lines.append(f"{_fmt_srt_time(start)} --> {_fmt_srt_time(end)}")
            # Word-wrap long lines
            lines.append(self._wrap_text(text, self.MAX_LINE_CHARS))
            lines.append("")

        output_path.write_text("\n".join(lines), encoding="utf-8")
        logger.info(f"SRT saved: {output_path} ({len(entries)} entries)")
        return str(output_path)

    def generate_ass(
        self,
        word_timings: list,      # list[WordTiming]
        output_path: str | Path,
        total_duration: float,
    ) -> str:
        """
        Generate an ASS subtitle file with word-by-word highlight.
        Each 'cue' shows a group of words; the active word is highlighted yellow.
        Returns path.
        """
        output_path = Path(output_path)
        header = self._ass_header()

        events = []
        groups = self._group_words(word_timings, self.WORDS_PER_GROUP)

        for group_words, group_start, group_end in groups:
            # For each word in the group, create a dialogue event where
            # that word is yellow and others are white.
            for active_idx, active_word in enumerate(group_words):
                w_start = active_word.start_time
                w_end   = active_word.end_time

                # Build colored line
                colored_parts = []
                for j, wt in enumerate(group_words):
                    word_text = self._clean_caption_text(wt.word)
                    if j == active_idx:
                        colored_parts.append(
                            f"{{\\c&H00FFFF&}}{word_text}{{\\c&HFFFFFF&}}"
                        )   # yellow highlight (ASS uses BGR: 00FFFF = yellow)
                    else:
                        colored_parts.append(f"{{\\c&HFFFFFF&}}{word_text}")
                dialogue_text = " ".join(colored_parts)

                events.append(
                    f"Dialogue: 0,{_fmt_ass_time(w_start)},{_fmt_ass_time(w_end)},"
                    f"Default,,0,0,0,,{dialogue_text}"
                )

        content = header + "\n".join(events)
        output_path.write_text(content, encoding="utf-8-sig")  # BOM for compatibility
        logger.info(f"ASS saved: {output_path} ({len(events)} word events)")
        return str(output_path)

    # ── Private helpers ───────────────────────────────────────────────────────

    def _group_words(
        self, word_timings: list, group_size: int
    ) -> list[tuple[list, float, float]]:
        """Group word timings into caption chunks."""
        groups = []
        for i in range(0, len(word_timings), group_size):
            chunk = word_timings[i:i + group_size]
            if not chunk:
                continue
            start = chunk[0].start_time
            end   = chunk[-1].end_time
            # Ensure minimum display duration
            if end - start < 0.3:
                end = start + 0.3
            groups.append((chunk, start, end))
        return groups

    def _clean_caption_text(self, text: str) -> str:
        """Remove punctuation that looks bad in captions."""
        # Keep basic punctuation but strip control chars
        text = re.sub(r"[\x00-\x1f]", "", text)
        return text.strip()

    def _wrap_text(self, text: str, max_chars: int) -> str:
        """Wrap text at word boundaries."""
        words = text.split()
        lines = []
        current = []
        for word in words:
            test = " ".join(current + [word])
            if len(test) > max_chars and current:
                lines.append(" ".join(current))
                current = [word]
            else:
                current.append(word)
        if current:
            lines.append(" ".join(current))
        return "\n".join(lines)

    def _ass_header(self) -> str:
        """Return ASS file header with style definition."""
        # Font: try Montserrat Bold, fall back to Arial Bold
        font_name = "Montserrat"

        # Color format in ASS: &HAABBGGRR (alpha, blue, green, red)
        # White = &H00FFFFFF, Yellow = &H0000FFFF, Black = &H00000000
        highlight_ass = "&H0000FFFF"   # Yellow
        base_ass      = "&H00FFFFFF"   # White
        outline_ass   = "&H00000000"   # Black
        back_ass      = "&H80000000"   # Semi-transparent black box

        return f"""[Script Info]
Title: AI Shorts Captions
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{font_name},{self.font_size},{base_ass},{highlight_ass},{outline_ass},{back_ass},-1,0,0,0,100,100,0,0,1,{self.outline_width},1,2,80,80,300,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
