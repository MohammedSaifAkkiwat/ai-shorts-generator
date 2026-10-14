#!/usr/bin/env python3
"""
generate_short.py
AI YouTube Shorts Generator — Main Entrypoint.

Usage:
  python generate_short.py --script inputs/my_script.txt --topic "Space Telescopes"
  python generate_short.py --script inputs/my_script.txt --title "Webb's Greatest Hits"
  python generate_short.py --help

Environment Variables (set in .env):
  PEXELS_API_KEY     — Required for stock video/photos
  PIXABAY_API_KEY    — Required for stock media + music
  GEMINI_API_KEY     — Recommended for AI segmentation + fact-checking

Outputs (saved to outputs/<title>_<timestamp>/):
  final_short.mp4         — The finished video
  script.txt              — Original script
  captions.srt            — Subtitle file
  sources_and_licenses.csv — Asset manifest
  fact_check_report.md    — Fact-check findings
  video_metadata.txt      — YouTube title/description/hashtags
  production_report.md    — QC results
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path

# ─── Load .env FIRST before any other imports ────────────────────────────────
from dotenv import load_dotenv
load_dotenv()

# ─── Local imports ────────────────────────────────────────────────────────────
sys.path.insert(0, str(Path(__file__).parent))

from src.script_handler import ScriptHandler, load_config
from src.asset_finder import AssetFinder
from src.asset_generator import AssetGenerator
from src.narrator import Narrator
from src.caption_maker import CaptionMaker
from src.music_finder import MusicFinder
from src.video_renderer import VideoRenderer
from src.fact_checker import FactChecker
from src.quality_checker import QualityChecker
from src.metadata_writer import MetadataWriter

# ─── Logging setup ───────────────────────────────────────────────────────────

def setup_logging(verbose: bool = False):
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        format="%(asctime)s  %(levelname)-8s  %(message)s",
        datefmt="%H:%M:%S",
        level=level,
        handlers=[logging.StreamHandler(sys.stdout)],
    )

logger = logging.getLogger(__name__)

# ─── Pre-flight checks ────────────────────────────────────────────────────────

def check_ffmpeg():
    """Verify FFmpeg is installed and accessible."""
    import subprocess
    try:
        result = subprocess.run(
            ["ffmpeg", "-version"],
            capture_output=True, text=True
        )
        if result.returncode == 0:
            version_line = result.stdout.split("\n")[0]
            logger.info(f"FFmpeg found: {version_line}")
            return True
    except FileNotFoundError:
        pass

    print("\n" + "="*60)
    print("❌  FFmpeg is not installed or not in PATH.")
    print("")
    print("Install FFmpeg using one of these methods:")
    print("  Option 1 (winget):")
    print("    winget install Gyan.FFmpeg")
    print("")
    print("  Option 2 (manual):")
    print("    1. Download from https://www.gyan.dev/ffmpeg/builds/")
    print("       (ffmpeg-release-essentials.zip)")
    print("    2. Extract to C:\\ffmpeg\\")
    print("    3. Add C:\\ffmpeg\\bin to your PATH environment variable")
    print("    4. Restart your terminal")
    print("="*60 + "\n")
    return False


def check_api_keys():
    """Warn about missing optional API keys."""
    warnings = []
    if not os.getenv("PEXELS_API_KEY"):
        warnings.append("⚠️  PEXELS_API_KEY not set — Pexels stock media unavailable")
    if not os.getenv("PIXABAY_API_KEY"):
        warnings.append("⚠️  PIXABAY_API_KEY not set — Pixabay stock media + music unavailable")
    if not os.getenv("GEMINI_API_KEY"):
        warnings.append("ℹ️  GEMINI_API_KEY not set — using rule-based script analysis")
    for w in warnings:
        logger.warning(w)
    return len([w for w in warnings if w.startswith("⚠️")]) == 0  # True if no errors


# ─── Output folder setup ──────────────────────────────────────────────────────

def create_output_dir(base_dir: str, title: str) -> Path:
    """Create a timestamped output folder."""
    safe_title = re.sub(r"[^\w\s-]", "", title).strip()
    safe_title = re.sub(r"[\s]+", "_", safe_title)[:40]
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    folder_name = f"{safe_title}_{timestamp}" if safe_title else timestamp
    out = Path(base_dir) / folder_name
    out.mkdir(parents=True, exist_ok=True)
    return out


def create_temp_dir(output_dir: Path) -> Path:
    temp = output_dir / "temp"
    temp.mkdir(exist_ok=True)
    return temp


# ─── Main pipeline ────────────────────────────────────────────────────────────

def generate_short(
    script_path: str,
    topic: str = "",
    title: str = "",
    config_path: str = "config.yaml",
    verbose: bool = False,
    skip_fact_check: bool = False,
    keep_temp: bool = False,
):
    setup_logging(verbose)
    start_time = time.time()

    print("\n" + "━"*60)
    print("  🎬  AI YouTube Shorts Generator")
    print("━"*60 + "\n")

    # ── Pre-flight ─────────────────────────────────────────────────────────────
    if not check_ffmpeg():
        sys.exit(1)
    check_api_keys()

    # ── Load config ────────────────────────────────────────────────────────────
    cfg = load_config(config_path)

    # ── Stage A: Script Input & Validation ────────────────────────────────────
    print("📄  Stage A: Loading and validating script...")
    handler = ScriptHandler(cfg)
    raw_text = handler.load_from_file(script_path)
    analysis = handler.analyse(raw_text, topic=topic or title)

    if analysis.duration_warning:
        print(f"\n{analysis.duration_warning}")
        response = input("Continue anyway? [y/N]: ").strip().lower()
        if response != "y":
            print("Aborted. Please adjust your script and try again.")
            sys.exit(0)

    print(f"  ✅ {len(analysis.scenes)} scenes | ~{analysis.estimated_duration:.0f}s estimated")
    for i, scene in enumerate(analysis.scenes):
        print(f"     Scene {i}: {scene.text[:60]}...")
        print(f"             Keywords: {', '.join(scene.keywords)}")

    # ── Setup output folder ────────────────────────────────────────────────────
    video_title = title or topic or "YouTube_Short"
    base_output_dir = cfg.get("output", {}).get("base_dir", "outputs")
    output_dir = create_output_dir(base_output_dir, video_title)
    temp_dir   = create_temp_dir(output_dir)
    asset_dir  = temp_dir / "assets"
    audio_dir  = temp_dir / "audio"
    asset_dir.mkdir(exist_ok=True)
    audio_dir.mkdir(exist_ok=True)

    print(f"\n📁  Output folder: {output_dir}\n")

    # Save copy of script
    shutil.copy(script_path, output_dir / "script.txt")

    # ── Stage E (part 1): Fact-Checking ───────────────────────────────────────
    if not skip_fact_check:
        print("🔍  Stage E(a): Fact-checking script...")
        fact_checker = FactChecker(cfg)
        claims, fact_report = fact_checker.check_script(analysis.scenes, topic=topic or title)
        fact_checker.save_report(fact_report, output_dir)

        flagged = [c for c in claims if c.status in ("FLAGGED", "UNVERIFIED")]
        if flagged:
            print(f"\n  ⚠️  {len(flagged)} claim(s) flagged/unverified:")
            for c in flagged[:3]:
                print(f"     [{c.status}] {c.text[:80]}")
            print(f"  📋  See fact_check_report.md for details.")
            print()
    else:
        # Write minimal report
        fact_report_path = output_dir / "fact_check_report.md"
        fact_report_path.write_text("# Fact-Check Report\n\nFact-checking skipped (--skip-fact-check flag).\n")

    # ── Stage B: Visual Discovery ──────────────────────────────────────────────
    print("🖼️   Stage B: Finding licensed visuals...")
    asset_finder = AssetFinder(cfg, asset_dir)
    asset_generator = AssetGenerator(cfg, asset_dir / "generated")
    scene_clips: list[dict] = []

    for scene in analysis.scenes:
        print(f"  Scene {scene.index}: searching for '{' '.join(scene.keywords[:2])}'...")
        asset = asset_finder.find_for_scene(scene.index, scene.keywords)

        if asset and asset_finder.download_asset(asset):
            clip_info = {
                "path": asset.local_path,
                "duration": scene.estimated_duration,
                "asset_type": asset.asset_type,
                "scene_index": scene.index,
            }
            print(f"    ✅ {asset.asset_type} from {asset.creator} ({asset.license})")
        else:
            # Fallback: generate gradient card
            print(f"    ℹ️  No stock asset found — generating gradient card")
            generated_path = asset_generator.generate_scene_clip(
                scene_index=scene.index,
                text=scene.text,
                duration=scene.estimated_duration,
                keywords=scene.keywords,
            )
            clip_info = {
                "path": generated_path,
                "duration": scene.estimated_duration,
                "asset_type": "video",
                "scene_index": scene.index,
            }

        scene_clips.append(clip_info)

    # Save asset manifest
    asset_finder.save_manifest(output_dir)
    print(f"  ✅ Visual manifest saved ({len(asset_finder.manifest)} licensed assets)")

    # ── Stage B: Background Music ──────────────────────────────────────────────
    music_path = None
    music_track = None
    if cfg.get("music", {}).get("enabled", True):
        print("\n🎵  Stage B: Finding background music (Pixabay)...")
        music_finder = MusicFinder(cfg, asset_dir / "music")
        music_track = music_finder.find_track(topic=topic or title)
        if music_track and music_finder.download_track(music_track):
            music_path = music_track.local_path
            print(f"  ✅ Music: '{music_track.title}' by {music_track.artist}")
        else:
            print("  ℹ️  No music found — narration-only audio")

    # ── Stage C: Narration ─────────────────────────────────────────────────────
    print("\n🎙️   Stage C: Generating narration...")
    narrator = Narrator(cfg, audio_dir)
    narration = narrator.narrate_all(analysis.scenes)
    print(f"  ✅ Narration: {narration.total_duration:.1f}s total | {len(narration.word_timings)} words")
    print(f"     Engine: {narration.scenes[0].engine_used if narration.scenes else 'unknown'}")

    # ── Stage C: Captions ─────────────────────────────────────────────────────
    print("\n💬  Stage C: Generating captions...")
    caption_maker = CaptionMaker(cfg)
    srt_path = str(output_dir / "captions.srt")
    ass_path = str(temp_dir / "captions.ass")

    caption_maker.generate_srt(narration.word_timings, srt_path)
    caption_maker.generate_ass(narration.word_timings, ass_path, narration.total_duration)
    print(f"  ✅ SRT: {srt_path}")
    print(f"  ✅ ASS (word-highlight): {ass_path}")

    # ── Stage D: Video Rendering ───────────────────────────────────────────────
    print("\n🎬  Stage D: Rendering video...")
    renderer = VideoRenderer(cfg, temp_dir / "video_work", output_dir)
    output_video = renderer.render(
        scene_clips=scene_clips,
        narration_path=narration.merged_audio_path,
        narration_duration=narration.total_duration,
        ass_captions_path=ass_path,
        output_filename="final_short.mp4",
        music_path=music_path,
        title=video_title,
    )
    print(f"  ✅ Video rendered: {output_video}")

    # ── Stage E: Quality Check ─────────────────────────────────────────────────
    print("\n✅  Stage E: Running quality checks...")
    qc = QualityChecker(cfg)
    all_passed, qc_report = qc.check_all(output_video, srt_path, output_dir)
    status_icon = "✅" if all_passed else "⚠️"
    print(f"  {status_icon} QC {'PASSED' if all_passed else 'ISSUES FOUND'}")
    print(f"     See production_report.md for details")

    # ── Stage E: Metadata ──────────────────────────────────────────────────────
    print("\n📝  Stage E: Generating YouTube metadata...")
    meta_writer = MetadataWriter(cfg)
    music_attr = music_track.attribution() if music_track else None
    asset_attrs = [a.attribution for a in asset_finder.manifest]
    meta = meta_writer.generate(
        script_text=raw_text,
        topic=topic or title,
        title_hint=title,
        music_attribution=music_attr,
        asset_attributions=asset_attrs,
    )
    meta_writer.save(meta, output_dir)
    print(f"  ✅ Metadata saved")
    print(f"     Suggested title: {meta.get('title', '')}")

    # ── Cleanup temp files ─────────────────────────────────────────────────────
    keep_temp_cfg = cfg.get("output", {}).get("keep_temp_assets", False)
    if not keep_temp and not keep_temp_cfg:
        try:
            shutil.rmtree(temp_dir)
            logger.info("Temp files cleaned up")
        except Exception as e:
            logger.warning(f"Could not clean up temp dir: {e}")

    # ── Summary ────────────────────────────────────────────────────────────────
    elapsed = time.time() - start_time
    print("\n" + "━"*60)
    print("  🎉  DONE!")
    print("━"*60)
    print(f"\n📁  Output folder: {output_dir.resolve()}")
    print(f"🎬  Video:         {output_dir.resolve() / 'final_short.mp4'}")
    print(f"📋  Script:        {output_dir.resolve() / 'script.txt'}")
    print(f"💬  Captions:      {output_dir.resolve() / 'captions.srt'}")
    print(f"📊  Fact-check:    {output_dir.resolve() / 'fact_check_report.md'}")
    print(f"📝  Metadata:      {output_dir.resolve() / 'video_metadata.txt'}")
    print(f"✅  QC report:     {output_dir.resolve() / 'production_report.md'}")
    print(f"\n⏱️   Total time: {elapsed:.0f}s")
    print()
    print("⚠️  IMPORTANT: Review fact_check_report.md before publishing.")
    print("    Visually inspect the video before uploading to YouTube.")
    print("    Do NOT upload automatically without human review.\n")

    return str(output_dir)


# ─── CLI ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="AI YouTube Shorts Generator — Turn a script into a polished Short.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python generate_short.py --script inputs/my_script.txt --topic "Space Exploration"
  python generate_short.py --script inputs/my_script.txt --title "Webb Telescope 2024"
  python generate_short.py --script inputs/sample_script.txt --verbose
  python generate_short.py --script inputs/my_script.txt --skip-fact-check
        """,
    )
    parser.add_argument(
        "--script", required=True,
        help="Path to script .txt file (e.g. inputs/my_script.txt)"
    )
    parser.add_argument(
        "--topic", default="",
        help="Topic or subject (used for keyword search and metadata)"
    )
    parser.add_argument(
        "--title", default="",
        help="Suggested video title (optional)"
    )
    parser.add_argument(
        "--config", default="config.yaml",
        help="Path to config.yaml (default: config.yaml)"
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Enable verbose/debug logging"
    )
    parser.add_argument(
        "--skip-fact-check", action="store_true",
        help="Skip fact-checking (faster, for testing)"
    )
    parser.add_argument(
        "--keep-temp", action="store_true",
        help="Keep temporary files after render (for debugging)"
    )

    args = parser.parse_args()

    try:
        output_dir = generate_short(
            script_path=args.script,
            topic=args.topic,
            title=args.title,
            config_path=args.config,
            verbose=args.verbose,
            skip_fact_check=args.skip_fact_check,
            keep_temp=args.keep_temp,
        )
    except KeyboardInterrupt:
        print("\n\nAborted by user.")
        sys.exit(0)
    except Exception as e:
        logger.exception(f"Fatal error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
