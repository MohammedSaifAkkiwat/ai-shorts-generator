"""
src/narrator.py
Stage C — Text-to-Speech Narration.

Responsibilities:
- Generate natural narration audio using edge-tts (primary).
- Fall back to gTTS if edge-tts is unavailable.
- Return per-word timing data for caption synchronization.
- Produce a single merged narration WAV/MP3 file.
- Respect commercial-use licensing:
  * edge-tts: Microsoft Azure TTS via Edge — permitted for general use.
  * gTTS: Google TTS — permitted for non-advertising general use.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


# ─── Data structures ──────────────────────────────────────────────────────────

@dataclass
class WordTiming:
    """Timing for a single word in the narration."""
    word: str
    start_time: float    # seconds from beginning of clip
    end_time: float      # seconds from beginning of clip
    scene_index: int


@dataclass
class NarrationResult:
    """Result of narrating a single scene."""
    scene_index: int
    text: str
    audio_path: str      # Path to audio file for this scene
    duration: float      # seconds
    word_timings: list[WordTiming] = field(default_factory=list)
    engine_used: str = ""


@dataclass
class FullNarration:
    """Combined narration result for the whole script."""
    scenes: list[NarrationResult]
    merged_audio_path: str       # Combined audio file
    total_duration: float
    word_timings: list[WordTiming]   # All words with global timestamps


# ─── Main class ───────────────────────────────────────────────────────────────

class Narrator:
    """Generate TTS narration for script scenes."""

    def __init__(self, config: dict, output_dir: str | Path):
        self.cfg = config
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

        narration_cfg = config.get("narration", {})
        self.primary_engine   = narration_cfg.get("primary_engine", "edge_tts")
        self.voice            = narration_cfg.get("voice", "en-US-AriaNeural")
        self.fallback_engine  = narration_cfg.get("fallback_engine", "gtts")
        self.silence_between  = narration_cfg.get("silence_between_scenes", 0.3)

    # ── Public API ─────────────────────────────────────────────────────────────

    def narrate_all(self, scenes: list) -> FullNarration:
        """
        Narrate all scenes, merge audio, compute global word timings.
        scenes: list of Scene dataclass objects from script_handler.
        """
        results: list[NarrationResult] = []
        for scene in scenes:
            result = self._narrate_scene(scene.index, scene.text)
            results.append(result)

        merged_path, total_dur, global_timings = self._merge_audio(results)

        return FullNarration(
            scenes=results,
            merged_audio_path=merged_path,
            total_duration=total_dur,
            word_timings=global_timings,
        )

    # ── Per-scene narration ───────────────────────────────────────────────────

    def _narrate_scene(self, scene_index: int, text: str) -> NarrationResult:
        """Narrate a single scene. Tries primary engine, then fallback."""
        logger.info(f"Narrating scene {scene_index}: {text[:50]}...")

        if self.primary_engine == "edge_tts":
            result = self._edge_tts(scene_index, text)
            if result:
                return result

        # Fallback
        result = self._gtts_fallback(scene_index, text)
        if result:
            return result

        raise RuntimeError(
            f"All TTS engines failed for scene {scene_index}. "
            "Check internet connection and installed packages."
        )

    # ── edge-tts ──────────────────────────────────────────────────────────────

    def _edge_tts(self, scene_index: int, text: str) -> Optional[NarrationResult]:
        """Generate narration using edge-tts with word-level timing."""
        try:
            import edge_tts
        except ImportError:
            logger.warning("edge-tts not installed. Install with: pip install edge-tts")
            return None

        audio_path = self.output_dir / f"scene_{scene_index:02d}_narration.mp3"
        timing_path = self.output_dir / f"scene_{scene_index:02d}_timing.json"

        async def _generate():
            # edge-tts >= 7.x requires explicit boundary='WordBoundary' for word-level timing
            communicate = edge_tts.Communicate(text, self.voice, boundary="WordBoundary")
            word_timings = []
            sentence_timings = []
            with open(audio_path, "wb") as f:
                async for chunk in communicate.stream():
                    if chunk["type"] == "audio":
                        f.write(chunk["data"])
                    elif chunk["type"] == "WordBoundary":
                        word_timings.append({
                            "word": chunk["text"],
                            "start": chunk["offset"] / 10_000_000,  # 100-ns units → seconds
                            "duration": chunk["duration"] / 10_000_000,
                        })
                    elif chunk["type"] == "SentenceBoundary":
                        # Fallback: sentence-level timing for proportional word estimation
                        sentence_timings.append({
                            "text": chunk["text"],
                            "start": chunk["offset"] / 10_000_000,
                            "duration": chunk["duration"] / 10_000_000,
                        })
            return word_timings, sentence_timings

        try:
            # Run async in a new event loop (safe for Windows)
            try:
                loop = asyncio.get_event_loop()
                if loop.is_closed():
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                raw_word_data, raw_sent_data = loop.run_until_complete(_generate())
            except RuntimeError:
                raw_word_data, raw_sent_data = asyncio.run(_generate())

            duration = self._get_audio_duration(str(audio_path))

            if raw_word_data:
                # Ideal path: word-level timings available
                word_timings = [
                    WordTiming(
                        word=w["word"],
                        start_time=w["start"],
                        end_time=w["start"] + w["duration"],
                        scene_index=scene_index,
                    )
                    for w in raw_word_data
                ]
                logger.info(f"edge-tts: scene {scene_index} — {duration:.2f}s, {len(word_timings)} word timings")
            else:
                # Fallback: build proportional word timings from sentence boundaries
                # (some edge-tts voices only emit SentenceBoundary even with boundary='WordBoundary')
                logger.info(
                    f"edge-tts: scene {scene_index} — no WordBoundary events; "
                    "computing proportional word timings from audio duration"
                )
                words = text.split()
                word_timings = []
                if words and duration > 0:
                    # Distribute words evenly across sentence boundaries if available
                    if raw_sent_data:
                        for sent in raw_sent_data:
                            s_start = sent["start"]
                            s_dur   = sent["duration"]
                            s_words = sent["text"].split()
                            if not s_words:
                                continue
                            w_dur = s_dur / len(s_words)
                            for j, word in enumerate(s_words):
                                word_timings.append(WordTiming(
                                    word=word,
                                    start_time=s_start + j * w_dur,
                                    end_time=s_start + (j + 1) * w_dur,
                                    scene_index=scene_index,
                                ))
                    else:
                        # Uniform distribution across full duration
                        w_dur = duration / len(words)
                        for i, word in enumerate(words):
                            word_timings.append(WordTiming(
                                word=word,
                                start_time=i * w_dur,
                                end_time=(i + 1) * w_dur,
                                scene_index=scene_index,
                            ))

            # Save timing data for debugging
            save_data = raw_word_data if raw_word_data else raw_sent_data
            with open(timing_path, "w") as f:
                json.dump(save_data, f, indent=2)

            return NarrationResult(
                scene_index=scene_index,
                text=text,
                audio_path=str(audio_path),
                duration=duration,
                word_timings=word_timings,
                engine_used="edge_tts",
            )

        except Exception as e:
            logger.warning(f"edge-tts failed for scene {scene_index}: {e}")
            return None

    # ── gTTS fallback ─────────────────────────────────────────────────────────

    def _gtts_fallback(self, scene_index: int, text: str) -> Optional[NarrationResult]:
        """Generate narration using gTTS (no word-level timing available)."""
        try:
            from gtts import gTTS
        except ImportError:
            logger.warning("gTTS not installed. Install with: pip install gTTS")
            return None

        audio_path = self.output_dir / f"scene_{scene_index:02d}_narration_gtts.mp3"
        try:
            tts = gTTS(text=text, lang="en", tld="com", slow=False)
            tts.save(str(audio_path))
            duration = self._get_audio_duration(str(audio_path))

            # Estimate word timings proportionally (gTTS has no timing API)
            words = text.split()
            word_timings = []
            if words:
                word_dur = duration / len(words)
                for i, word in enumerate(words):
                    word_timings.append(WordTiming(
                        word=word,
                        start_time=i * word_dur,
                        end_time=(i + 1) * word_dur,
                        scene_index=scene_index,
                    ))

            logger.info(f"gTTS: scene {scene_index} — {duration:.2f}s (estimated word timings)")
            return NarrationResult(
                scene_index=scene_index,
                text=text,
                audio_path=str(audio_path),
                duration=duration,
                word_timings=word_timings,
                engine_used="gtts",
            )
        except Exception as e:
            logger.warning(f"gTTS failed for scene {scene_index}: {e}")
            return None

    # ── Audio merging ─────────────────────────────────────────────────────────

    def _merge_audio(
        self, results: list[NarrationResult]
    ) -> tuple[str, float, list[WordTiming]]:
        """
        Merge all scene audio files into one, inserting silence between scenes.
        Returns (merged_path, total_duration, global_word_timings).
        """
        merged_path = str(self.output_dir / "full_narration.mp3")
        silence_path = self._create_silence(self.silence_between)

        # Build FFmpeg concat input
        concat_inputs: list[str] = []
        for i, res in enumerate(results):
            concat_inputs.append(res.audio_path)
            if i < len(results) - 1:
                concat_inputs.append(silence_path)

        # Write concat file
        concat_file = self.output_dir / "concat_list.txt"
        with open(concat_file, "w") as f:
            for p in concat_inputs:
                f.write(f"file '{Path(p).resolve()}'\n")

        cmd = [
            "ffmpeg", "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", str(concat_file),
            "-c:a", "libmp3lame",
            "-q:a", "2",
            merged_path,
        ]
        try:
            subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except (subprocess.CalledProcessError, FileNotFoundError) as e:
            logger.error(f"Audio merge failed: {e}")
            # If FFmpeg not available, just use first scene audio as fallback
            if results:
                merged_path = results[0].audio_path

        total_duration = self._get_audio_duration(merged_path)

        # Compute global word timings with cumulative offset
        global_timings: list[WordTiming] = []
        offset = 0.0
        for i, res in enumerate(results):
            for wt in res.word_timings:
                global_timings.append(WordTiming(
                    word=wt.word,
                    start_time=wt.start_time + offset,
                    end_time=wt.end_time + offset,
                    scene_index=wt.scene_index,
                ))
            offset += res.duration + self.silence_between

        logger.info(f"Merged narration: {total_duration:.2f}s total")
        return merged_path, total_duration, global_timings

    # ── Utility ───────────────────────────────────────────────────────────────

    def _get_audio_duration(self, path: str) -> float:
        """Get duration of an audio file in seconds using FFprobe."""
        try:
            result = subprocess.run(
                [
                    "ffprobe", "-v", "quiet",
                    "-print_format", "json",
                    "-show_format",
                    path,
                ],
                capture_output=True, text=True, check=True
            )
            data = json.loads(result.stdout)
            return float(data.get("format", {}).get("duration", 0))
        except Exception:
            # Fallback: estimate from file size (rough: 128kbps MP3)
            try:
                size = os.path.getsize(path)
                return size / (128 * 1024 / 8)
            except Exception:
                return 5.0

    def _create_silence(self, duration: float) -> str:
        """Generate a short silent audio file for gaps between scenes."""
        silence_path = str(self.output_dir / f"silence_{duration:.1f}s.mp3")
        if Path(silence_path).exists():
            return silence_path
        try:
            subprocess.run(
                [
                    "ffmpeg", "-y",
                    "-f", "lavfi",
                    "-i", f"anullsrc=r=44100:cl=mono",
                    "-t", str(duration),
                    "-q:a", "9",
                    "-acodec", "libmp3lame",
                    silence_path,
                ],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception as e:
            logger.warning(f"Could not create silence file: {e}")
            silence_path = ""
        return silence_path
