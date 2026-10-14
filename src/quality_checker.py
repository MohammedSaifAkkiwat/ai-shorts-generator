"""
src/quality_checker.py
Stage E — Quality Control.

Responsibilities:
- Verify the output MP4 exists and is non-zero.
- Check dimensions (1080×1920), duration (40–65 s), and FPS via FFprobe.
- Verify audio track is present and check peak levels.
- Check subtitle synchronization (basic: verify SRT timestamps match duration).
- Generate production_report.md with PASS/WARN/FAIL status per check.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class QCResult:
    check: str
    status: str       # PASS | WARN | FAIL
    detail: str = ""


class QualityChecker:
    """Run automated quality checks on the rendered Short."""

    def __init__(self, config: dict):
        self.cfg = config
        vid_cfg = config.get("video", {})
        self.target_w   = vid_cfg.get("width", 1080)
        self.target_h   = vid_cfg.get("height", 1920)
        self.min_dur    = vid_cfg.get("target_duration_min", 40)
        self.max_dur    = vid_cfg.get("target_duration_max", 65)
        self.results: list[QCResult] = []

    # ── Public API ─────────────────────────────────────────────────────────────

    def check_all(
        self,
        video_path: str,
        srt_path: Optional[str] = None,
        output_dir: Optional[str | Path] = None,
    ) -> tuple[bool, str]:
        """
        Run all QC checks on the video.
        Returns (all_passed: bool, report_markdown: str).
        """
        self.results = []

        # 1. File exists and non-zero
        self._check_file_exists(video_path)

        # 2. Dimensions
        info = self._get_video_info(video_path)
        self._check_dimensions(info)

        # 3. Duration
        self._check_duration(info)

        # 4. Audio track
        self._check_audio(info)

        # 5. FPS
        self._check_fps(info)

        # 6. SRT sync (basic)
        if srt_path:
            self._check_srt_sync(srt_path, info)

        # 7. File size sanity
        self._check_file_size(video_path)

        all_passed = all(r.status in ("PASS", "WARN") for r in self.results)
        report = self._build_report(video_path)

        if output_dir:
            out = Path(output_dir) / "production_report.md"
            out.write_text(report, encoding="utf-8")
            logger.info(f"Production report saved: {out}")

        status_str = "PASSED" if all_passed else "FAILED"
        logger.info(f"QC {status_str} — {len(self.results)} checks")
        return all_passed, report

    # ── Individual checks ─────────────────────────────────────────────────────

    def _check_file_exists(self, path: str):
        p = Path(path)
        if p.exists() and p.stat().st_size > 0:
            self.results.append(QCResult("File exists", "PASS", f"Size: {p.stat().st_size / 1024:.0f} KB"))
        elif p.exists():
            self.results.append(QCResult("File exists", "FAIL", "File is empty (0 bytes)"))
        else:
            self.results.append(QCResult("File exists", "FAIL", f"File not found: {path}"))

    def _check_dimensions(self, info: dict):
        streams = info.get("streams", [])
        video_streams = [s for s in streams if s.get("codec_type") == "video"]
        if not video_streams:
            self.results.append(QCResult("Dimensions", "FAIL", "No video stream found"))
            return
        vs = video_streams[0]
        w = int(vs.get("width", 0))
        h = int(vs.get("height", 0))
        if w == self.target_w and h == self.target_h:
            self.results.append(QCResult("Dimensions", "PASS", f"{w}×{h}"))
        else:
            status = "WARN" if abs(w - self.target_w) < 10 and abs(h - self.target_h) < 10 else "FAIL"
            self.results.append(QCResult("Dimensions", status, f"Expected {self.target_w}×{self.target_h}, got {w}×{h}"))

    def _check_duration(self, info: dict):
        dur = float(info.get("format", {}).get("duration", 0))
        if self.min_dur <= dur <= self.max_dur + 5:
            self.results.append(QCResult("Duration", "PASS", f"{dur:.1f}s"))
        elif dur < self.min_dur:
            self.results.append(QCResult("Duration", "WARN", f"{dur:.1f}s — shorter than {self.min_dur}s target"))
        else:
            self.results.append(QCResult("Duration", "WARN", f"{dur:.1f}s — longer than {self.max_dur}s target"))

    def _check_audio(self, info: dict):
        streams = info.get("streams", [])
        audio_streams = [s for s in streams if s.get("codec_type") == "audio"]
        if audio_streams:
            codec = audio_streams[0].get("codec_name", "unknown")
            sr = audio_streams[0].get("sample_rate", "?")
            self.results.append(QCResult("Audio track", "PASS", f"codec={codec}, sample_rate={sr}"))
        else:
            self.results.append(QCResult("Audio track", "FAIL", "No audio stream found"))

    def _check_fps(self, info: dict):
        streams = info.get("streams", [])
        video_streams = [s for s in streams if s.get("codec_type") == "video"]
        if not video_streams:
            return
        fps_str = video_streams[0].get("r_frame_rate", "0/1")
        try:
            num, den = fps_str.split("/")
            fps = int(num) / int(den)
            if 24 <= fps <= 60:
                self.results.append(QCResult("Frame rate", "PASS", f"{fps:.1f} fps"))
            else:
                self.results.append(QCResult("Frame rate", "WARN", f"{fps:.1f} fps (expected 24–60)"))
        except Exception:
            self.results.append(QCResult("Frame rate", "WARN", f"Could not parse FPS: {fps_str}"))

    def _check_srt_sync(self, srt_path: str, info: dict):
        """Basic SRT check: verify last subtitle end time ≤ video duration."""
        try:
            srt_text = Path(srt_path).read_text(encoding="utf-8")
            # Find last timestamp
            times = []
            import re
            for match in re.finditer(
                r"(\d+:\d+:\d+[,\.]\d+)\s*-->\s*(\d+:\d+:\d+[,\.]\d+)", srt_text
            ):
                end_str = match.group(2).replace(",", ".")
                parts = end_str.split(":")
                t = float(parts[0]) * 3600 + float(parts[1]) * 60 + float(parts[2])
                times.append(t)

            if not times:
                self.results.append(QCResult("SRT sync", "WARN", "No timestamps found in SRT"))
                return

            last_sub = max(times)
            vid_dur = float(info.get("format", {}).get("duration", 0))

            if last_sub <= vid_dur + 2:
                self.results.append(QCResult("SRT sync", "PASS", f"Last subtitle at {last_sub:.1f}s, video {vid_dur:.1f}s"))
            else:
                self.results.append(QCResult("SRT sync", "WARN", f"Last subtitle ({last_sub:.1f}s) extends past video ({vid_dur:.1f}s)"))
        except Exception as e:
            self.results.append(QCResult("SRT sync", "WARN", f"Could not verify SRT: {e}"))

    def _check_file_size(self, path: str):
        try:
            size_mb = Path(path).stat().st_size / (1024 * 1024)
            if size_mb < 500:
                self.results.append(QCResult("File size", "PASS", f"{size_mb:.1f} MB"))
            else:
                self.results.append(QCResult("File size", "WARN", f"{size_mb:.1f} MB — larger than expected"))
        except Exception as e:
            self.results.append(QCResult("File size", "WARN", str(e)))

    # ── Report ────────────────────────────────────────────────────────────────

    def _build_report(self, video_path: str) -> str:
        pass_count = sum(1 for r in self.results if r.status == "PASS")
        warn_count = sum(1 for r in self.results if r.status == "WARN")
        fail_count = sum(1 for r in self.results if r.status == "FAIL")
        overall = "✅ PASSED" if fail_count == 0 else "❌ FAILED"

        lines = [
            "# Production Report",
            "",
            f"**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M')}",
            f"**Output file:** `{video_path}`",
            f"**Overall:** {overall}",
            f"**Checks:** {pass_count} passed · {warn_count} warnings · {fail_count} failed",
            "",
            "| Check | Status | Detail |",
            "|---|---|---|",
        ]
        icons = {"PASS": "✅", "WARN": "⚠️", "FAIL": "❌"}
        for r in self.results:
            lines.append(f"| {r.check} | {icons.get(r.status, r.status)} {r.status} | {r.detail} |")

        lines += [
            "",
            "---",
            "",
            "> ⚠️ **Note:** This report reflects automated checks only.",
            "> Visual quality, caption readability, and content accuracy require human review before upload.",
        ]
        return "\n".join(lines)

    # ── Utility ───────────────────────────────────────────────────────────────

    def _get_video_info(self, path: str) -> dict:
        try:
            result = subprocess.run(
                [
                    "ffprobe", "-v", "quiet",
                    "-print_format", "json",
                    "-show_format", "-show_streams",
                    path,
                ],
                capture_output=True, text=True, check=True,
            )
            return json.loads(result.stdout)
        except Exception as e:
            logger.error(f"FFprobe failed: {e}")
            return {}
