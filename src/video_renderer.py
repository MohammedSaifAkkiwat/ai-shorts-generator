"""
src/video_renderer.py
Stage D — Automated Video Rendering.

Responsibilities:
- Resize/crop stock video clips and images to 1080×1920 (portrait).
- Concatenate scene clips, each synchronized to narration duration.
- Burn word-highlight captions using FFmpeg ASS filter.
- Mix narration + background music (ducked under voice).
- Output final_short.mp4 in H.264 + AAC.

Design notes:
- Uses FFmpeg directly via subprocess for maximum control.
- moviepy is used only for clip operations that require Python logic.
- Audio is normalized to -14 LUFS (YouTube standard) where pyloudnorm is available.
- Never stretches footage: uses scale + pad (letterbox) for mismatched clips.
- Avoids cropping important subjects: center-crop is used as safe default.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

W, H = 1080, 1920   # Target resolution


class VideoRenderer:
    """Assemble the final YouTube Short from scenes, narration, and captions."""

    def __init__(self, config: dict, work_dir: str | Path, output_dir: str | Path):
        self.cfg = config
        self.work_dir  = Path(work_dir)
        self.output_dir = Path(output_dir)
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self.output_dir.mkdir(parents=True, exist_ok=True)

        vid_cfg = config.get("video", {})
        self.fps         = vid_cfg.get("fps", 30)
        self.bitrate     = vid_cfg.get("bitrate", "8000k")
        self.audio_bitrate = vid_cfg.get("audio_bitrate", "192k")
        self.transition  = vid_cfg.get("transition_duration", 0.4)

        music_cfg = config.get("music", {})
        self.music_enabled   = music_cfg.get("enabled", True)
        self.music_vol_narr  = music_cfg.get("volume_during_narration", 0.08)
        self.music_vol_io    = music_cfg.get("volume_intro_outro", 0.25)
        self.music_fade_in   = music_cfg.get("fade_in", 1.5)
        self.music_fade_out  = music_cfg.get("fade_out", 2.0)

    # ── Public API ─────────────────────────────────────────────────────────────

    def render(
        self,
        scene_clips: list[dict],      # [{"path": ..., "duration": ..., "asset_type": ...}]
        narration_path: str,
        narration_duration: float,
        ass_captions_path: str,
        output_filename: str = "final_short.mp4",
        music_path: Optional[str] = None,
        title: Optional[str] = None,
    ) -> str:
        """
        Full render pipeline.
        Returns path to final_short.mp4.
        """
        logger.info("=== Starting video render pipeline ===")

        # Step 1: Prepare each scene clip (resize/crop to 1080×1920)
        prepared_clips = []
        for i, clip_info in enumerate(scene_clips):
            out = self.work_dir / f"prepared_{i:02d}.mp4"
            self._prepare_clip(
                src=clip_info["path"],
                dst=str(out),
                duration=clip_info["duration"],
                asset_type=clip_info.get("asset_type", "video"),
            )
            prepared_clips.append(str(out))

        # Step 2: Concatenate all scene clips
        raw_video = str(self.work_dir / "raw_concat.mp4")
        self._concatenate_clips(prepared_clips, raw_video, narration_duration)

        # Step 3: Mix audio (narration + optional music)
        mixed_audio = str(self.work_dir / "mixed_audio.aac")
        self._mix_audio(narration_path, narration_duration, music_path, mixed_audio)

        # Step 4: Burn captions and mux final video
        output_path = str(self.output_dir / output_filename)
        self._burn_captions_and_mux(
            video_path=raw_video,
            audio_path=mixed_audio,
            ass_path=ass_captions_path,
            output_path=output_path,
            total_duration=narration_duration,
        )

        logger.info(f"=== Render complete: {output_path} ===")
        return output_path

    # ── Step 1: Prepare clips ──────────────────────────────────────────────────

    def _prepare_clip(
        self,
        src: str,
        dst: str,
        duration: float,
        asset_type: str = "video",
    ):
        """
        Resize and crop a clip to 1080×1920.
        For images: convert to video with Ken Burns zoom.
        For videos: scale+crop, loop/trim to duration.
        """
        duration = max(duration, 2.0)

        if asset_type == "image":
            # Ken Burns zoom from static image
            vf = (
                f"scale={int(W*1.08)}:{int(H*1.08)}:force_original_aspect_ratio=increase,"
                f"crop={W}:{H},"
                f"zoompan=z='min(zoom+0.002,1.05)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
                f":d={int(duration * self.fps)}:s={W}x{H}:fps={self.fps},"
                f"setsar=1"
            )
            cmd = [
                "ffmpeg", "-y",
                "-loop", "1",
                "-i", src,
                "-vf", vf,
                "-t", str(duration),
                "-c:v", "libx264",
                "-preset", "fast",
                "-crf", "22",
                "-pix_fmt", "yuv420p",
                dst,
            ]
        else:
            # Video: scale+crop center, loop if shorter than duration, trim if longer
            vf = (
                f"scale={W}:{H}:force_original_aspect_ratio=increase,"
                f"crop={W}:{H},"
                f"setsar=1"
            )
            cmd = [
                "ffmpeg", "-y",
                "-stream_loop", "-1",   # Loop video if needed
                "-i", src,
                "-vf", vf,
                "-t", str(duration),
                "-c:v", "libx264",
                "-preset", "fast",
                "-crf", "22",
                "-pix_fmt", "yuv420p",
                "-an",                  # Strip audio from source clips
                dst,
            ]

        self._run_ffmpeg(cmd, f"prepare_clip:{Path(src).name}")

    # ── Step 2: Concatenate ────────────────────────────────────────────────────

    def _concatenate_clips(
        self, clip_paths: list[str], output: str, target_duration: float
    ):
        """Concatenate prepared clips into a single video stream."""
        if not clip_paths:
            raise RuntimeError("No clips to concatenate")

        # Write concat list
        concat_file = self.work_dir / "video_concat.txt"
        with open(concat_file, "w") as f:
            for p in clip_paths:
                f.write(f"file '{Path(p).resolve()}'\n")

        cmd = [
            "ffmpeg", "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", str(concat_file),
            "-t", str(target_duration + 1),   # slight buffer
            "-c:v", "libx264",
            "-preset", "fast",
            "-crf", "20",
            "-pix_fmt", "yuv420p",
            "-an",
            output,
        ]
        self._run_ffmpeg(cmd, "concatenate_clips")

    # ── Step 3: Audio mixing ───────────────────────────────────────────────────

    def _mix_audio(
        self,
        narration_path: str,
        narration_duration: float,
        music_path: Optional[str],
        output: str,
    ):
        """
        Mix narration with optional background music.
        Music is ducked to 8% volume under narration.
        """
        if not music_path or not Path(music_path).exists():
            # No music: just normalize narration
            cmd = [
                "ffmpeg", "-y",
                "-i", narration_path,
                "-af", "loudnorm=I=-14:TP=-1.5:LRA=11,aresample=44100",
                "-c:a", "aac",
                "-b:a", self.audio_bitrate,
                output,
            ]
            self._run_ffmpeg(cmd, "normalize_narration")
            return

        # Build amix filter: music at low volume, narration on top
        # Fade music in/out
        afade_in  = self.music_fade_in
        afade_out = self.music_fade_out
        music_vol = self.music_vol_narr
        fade_start = max(0, narration_duration - afade_out)

        music_filter = (
            f"[1:a]"
            f"volume={music_vol},"
            f"afade=t=in:st=0:d={afade_in},"
            f"afade=t=out:st={fade_start:.2f}:d={afade_out},"
            f"aloop=loop=-1:size=2e+09,"
            f"atrim=0:{narration_duration}"
            f"[music_mixed];"
            f"[0:a]loudnorm=I=-14:TP=-1.5:LRA=11[narr];"
            f"[narr][music_mixed]amix=inputs=2:duration=first:normalize=0[out]"
        )

        cmd = [
            "ffmpeg", "-y",
            "-i", narration_path,
            "-i", music_path,
            "-filter_complex", music_filter,
            "-map", "[out]",
            "-c:a", "aac",
            "-b:a", self.audio_bitrate,
            output,
        ]
        self._run_ffmpeg(cmd, "mix_audio")

    # ── Step 4: Caption burn-in + mux ──────────────────────────────────────────

    def _burn_captions_and_mux(
        self,
        video_path: str,
        audio_path: str,
        ass_path: str,
        output_path: str,
        total_duration: float,
    ):
        """
        Burn ASS captions into video, mux with audio, trim to duration.
        Windows note: FFmpeg's 'ass' filter requires the drive-letter colon
        to be escaped as '\\:' within the vf filter string.
        We copy the ASS to work_dir to keep the path short and safe.
        """
        import shutil as _shutil

        # Copy ASS to a predictable short path inside work_dir
        safe_ass = self.work_dir / "captions_burn.ass"
        _shutil.copy2(ass_path, str(safe_ass))

        # Build Windows-safe path for FFmpeg vf filter:
        # Convert to forward slashes, then escape the drive-letter colon.
        # E.g.  E:/AI-Shorts-Generator/...  ->  E\:/AI-Shorts-Generator/...
        ass_fwd = safe_ass.resolve().as_posix()  # forward slashes
        if len(ass_fwd) > 1 and ass_fwd[1] == ":":
            ass_fwd = ass_fwd[0] + "\\:" + ass_fwd[2:]  # escape drive colon

        vf = f"ass='{ass_fwd}'"

        cmd = [
            "ffmpeg", "-y",
            "-i", video_path,
            "-i", audio_path,
            "-vf", vf,
            "-t", str(total_duration),
            "-map", "0:v",
            "-map", "1:a",
            "-c:v", "libx264",
            "-preset", "medium",
            "-crf", "18",
            "-b:v", self.bitrate,
            "-pix_fmt", "yuv420p",
            "-c:a", "aac",
            "-b:a", self.audio_bitrate,
            "-movflags", "+faststart",
            "-metadata", "comment=Generated by AI-Shorts-Generator",
            output_path,
        ]
        self._run_ffmpeg(cmd, "burn_captions_and_mux")

    # ── FFmpeg runner ──────────────────────────────────────────────────────────

    def _run_ffmpeg(self, cmd: list[str], step_name: str):
        """Run an FFmpeg command, logging stderr on failure."""
        logger.info(f"FFmpeg [{step_name}]: {' '.join(cmd[:6])}...")
        try:
            result = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            if result.returncode != 0:
                logger.error(f"FFmpeg [{step_name}] failed (rc={result.returncode})")
                logger.error(result.stderr[-2000:])   # Last 2000 chars of stderr
                raise subprocess.CalledProcessError(result.returncode, cmd)
        except FileNotFoundError:
            raise RuntimeError(
                "FFmpeg not found. Please install FFmpeg and ensure it is in your PATH.\n"
                "Windows: winget install Gyan.FFmpeg  OR  download from https://ffmpeg.org/download.html"
            )

    # ── Utility ───────────────────────────────────────────────────────────────

    def get_video_info(self, path: str) -> dict:
        """Return basic video info via FFprobe."""
        try:
            result = subprocess.run(
                [
                    "ffprobe", "-v", "quiet",
                    "-print_format", "json",
                    "-show_format",
                    "-show_streams",
                    path,
                ],
                capture_output=True, text=True, check=True
            )
            return json.loads(result.stdout)
        except Exception as e:
            logger.warning(f"FFprobe failed for {path}: {e}")
            return {}
