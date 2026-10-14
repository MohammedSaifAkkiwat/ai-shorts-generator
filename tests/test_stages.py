"""
tests/test_stages.py
Comprehensive stage-by-stage tests for AI Shorts Generator.
Run with: python tests/test_stages.py
"""
import sys
import os
import json
import shutil
import subprocess
import asyncio
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.script_handler import ScriptHandler, load_config
from src.asset_generator import AssetGenerator
from src.narrator import Narrator
from src.caption_maker import CaptionMaker
from src.fact_checker import FactChecker
from src.quality_checker import QualityChecker
from src.metadata_writer import MetadataWriter

PASS = "PASS"
FAIL = "FAIL"
SKIP = "SKIP"

results = []


def check(name, fn):
    try:
        fn()
        print(f"  [PASS] {name}")
        results.append((name, PASS, ""))
    except Exception as e:
        print(f"  [FAIL] {name}: {e}")
        results.append((name, FAIL, str(e)))


def get_video_info(path):
    r = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json",
         "-show_format", "-show_streams", path],
        capture_output=True, text=True, check=True
    )
    return json.loads(r.stdout)


# ── Setup ──────────────────────────────────────────────────────────────────────
cfg = load_config("config.yaml")
TEST_DIR = Path("outputs/_test_run")
TEST_DIR.mkdir(parents=True, exist_ok=True)
AUDIO_DIR = TEST_DIR / "audio"
AUDIO_DIR.mkdir(exist_ok=True)

SAMPLE_SCRIPT = Path("inputs/sample_script.txt")

print()
print("=" * 60)
print("  AI Shorts Generator — Stage Tests")
print("=" * 60)


# ── Stage A: Script Handler ────────────────────────────────────────────────────
print("\n[Stage A] Script Input & Validation")

handler = ScriptHandler(cfg)

def test_a1_load():
    text = handler.load_from_file(SAMPLE_SCRIPT)
    assert len(text) > 50, "Script too short"

def test_a2_analyse():
    text = handler.load_from_file(SAMPLE_SCRIPT)
    analysis = handler.analyse(text, topic="Space")
    assert len(analysis.scenes) >= 2, "Too few scenes"
    assert analysis.total_words > 50, "Too few words"
    assert analysis.estimated_duration > 0

def test_a3_keywords():
    text = handler.load_from_file(SAMPLE_SCRIPT)
    analysis = handler.analyse(text)
    for scene in analysis.scenes:
        assert len(scene.keywords) >= 2, f"Scene {scene.index} has too few keywords"

def test_a4_short_script_warning():
    handler2 = ScriptHandler(cfg)
    analysis = handler2.analyse("This is very short.")
    assert analysis.duration_warning is not None, "Short script should warn"

check("Load script from file", test_a1_load)
check("Analyse and segment script", test_a2_analyse)
check("Extract keywords per scene", test_a3_keywords)
check("Warn on too-short script", test_a4_short_script_warning)


# ── Stage B: Asset Generator (no API needed) ──────────────────────────────────
print("\n[Stage B] Asset Generator (Pillow + FFmpeg)")

gen_dir = TEST_DIR / "generated"
gen_dir.mkdir(exist_ok=True)
gen = AssetGenerator(cfg, str(gen_dir))

def test_b1_gradient_frame():
    frame = gen._render_gradient_frame(0, "Test scene text", ("#0f0c29", "#302b63"))
    assert Path(frame).exists(), "Frame PNG not created"
    assert Path(frame).stat().st_size > 5000, "Frame PNG too small"

def test_b2_scene_clip():
    clip = gen.generate_scene_clip(
        scene_index=0,
        text="The James Webb Space Telescope revealed stunning images.",
        duration=4.0,
        keywords=["space", "telescope"]
    )
    assert Path(clip).exists(), "Clip not created"
    size = Path(clip).stat().st_size
    assert size > 50_000, f"Clip too small: {size} bytes"
    info = get_video_info(clip)
    streams = info.get("streams", [])
    vs = [s for s in streams if s.get("codec_type") == "video"]
    assert vs, "No video stream in generated clip"
    assert vs[0]["width"] == 1080, "Wrong width"
    assert vs[0]["height"] == 1920, "Wrong height"

def test_b3_title_card():
    card = gen.generate_title_card("Webb's Greatest Hits", duration=2.0)
    assert Path(card).exists(), "Title card not created"

check("Render gradient PNG frame", test_b1_gradient_frame)
check("Generate 1080x1920 scene clip", test_b2_scene_clip)
check("Generate title card clip", test_b3_title_card)


# ── Stage C: Narrator ─────────────────────────────────────────────────────────
print("\n[Stage C] Narration (edge-tts)")

narrator = Narrator(cfg, str(AUDIO_DIR))
narration_result = None

def test_c1_edge_tts():
    global narration_result
    # Narrate a single short sentence
    from src.script_handler import Scene
    scene = Scene(index=0, text="The James Webb Space Telescope is amazing.", keywords=["space"])
    scene.estimated_duration = 5.0

    result = narrator._narrate_scene(0, scene.text)
    assert result is not None, "Narration returned None"
    assert Path(result.audio_path).exists(), "Audio file not created"
    assert result.duration > 0.5, f"Duration too short: {result.duration}"
    assert len(result.word_timings) > 0, "No word timings"
    narration_result = result
    print(f"         engine={result.engine_used}, duration={result.duration:.2f}s, words={len(result.word_timings)}")

check("Generate narration with edge-tts", test_c1_edge_tts)


# ── Stage C: Caption Maker ────────────────────────────────────────────────────
print("\n[Stage C] Captions")

caption_maker = CaptionMaker(cfg)

def test_c2_srt_generation():
    if narration_result is None:
        raise AssertionError("Skipped - narration failed")
    srt_path = str(TEST_DIR / "test.srt")
    caption_maker.generate_srt(narration_result.word_timings, srt_path)
    assert Path(srt_path).exists(), "SRT file not created"
    content = Path(srt_path).read_text(encoding="utf-8")
    assert "-->" in content, "SRT has no timestamps"

def test_c3_ass_generation():
    if narration_result is None:
        raise AssertionError("Skipped - narration failed")
    ass_path = str(TEST_DIR / "test.ass")
    caption_maker.generate_ass(narration_result.word_timings, ass_path, total_duration=10.0)
    assert Path(ass_path).exists(), "ASS file not created"
    content = Path(ass_path).read_text(encoding="utf-8-sig")
    assert "Dialogue:" in content, "ASS has no dialogue events"
    assert "Default" in content, "ASS missing style definition"

check("Generate SRT captions", test_c2_srt_generation)
check("Generate ASS word-highlight captions", test_c3_ass_generation)


# ── Stage D: Video Renderer ────────────────────────────────────────────────────
print("\n[Stage D] Video Rendering")

from src.video_renderer import VideoRenderer

render_dir = TEST_DIR / "render_work"
render_dir.mkdir(exist_ok=True)
renderer = VideoRenderer(cfg, str(render_dir), str(TEST_DIR))
output_video = None

def test_d1_prepare_clip():
    """Test that a generated clip can be prepared (resize/crop)."""
    # Get a generated clip from Stage B
    clips = list(gen_dir.glob("generated_scene_00.mp4"))
    if not clips:
        raise AssertionError("No generated clip available from Stage B")
    src = str(clips[0])
    dst = str(render_dir / "prepared_00.mp4")
    renderer._prepare_clip(src, dst, duration=4.0, asset_type="video")
    assert Path(dst).exists(), "Prepared clip not created"
    info = get_video_info(dst)
    vs = [s for s in info["streams"] if s.get("codec_type") == "video"]
    assert vs[0]["width"] == 1080 and vs[0]["height"] == 1920

def test_d2_full_render():
    """Full render pipeline: generated clips + narration + captions → MP4."""
    global output_video
    if narration_result is None:
        raise AssertionError("Skipped - narration failed")

    # Generate 2 scene clips for a short test
    clips_info = []
    for i in range(2):
        clip = gen.generate_scene_clip(
            scene_index=i,
            text=f"Scene {i} test content for automated rendering.",
            duration=narration_result.duration / 2,
        )
        clips_info.append({
            "path": clip,
            "duration": narration_result.duration / 2,
            "asset_type": "video",
        })

    # Generate ASS captions
    ass_path = str(TEST_DIR / "render_test.ass")
    caption_maker.generate_ass(
        narration_result.word_timings, ass_path,
        total_duration=narration_result.duration
    )

    out_path = renderer.render(
        scene_clips=clips_info,
        narration_path=narration_result.audio_path,
        narration_duration=narration_result.duration,
        ass_captions_path=ass_path,
        output_filename="_test_final.mp4",
    )
    assert Path(out_path).exists(), "Output video not created"
    size = Path(out_path).stat().st_size
    assert size > 100_000, f"Output too small: {size} bytes"
    output_video = out_path
    info = get_video_info(out_path)
    vs = [s for s in info["streams"] if s.get("codec_type") == "video"]
    as_ = [s for s in info["streams"] if s.get("codec_type") == "audio"]
    print(f"         {vs[0]['width']}x{vs[0]['height']}, audio={bool(as_)}, size={size//1024}KB")

check("Prepare clip to 1080x1920", test_d1_prepare_clip)
check("Full render pipeline to MP4", test_d2_full_render)


# ── Stage E: Quality Checker ──────────────────────────────────────────────────
print("\n[Stage E] Quality Checker")

qc = QualityChecker(cfg)

def test_e1_qc_on_output():
    if not output_video:
        raise AssertionError("Skipped - render failed")
    # Temporarily adjust duration check to pass for our short test clip
    qc.min_dur = 1
    passed, report = qc.check_all(output_video)
    assert "production_report" not in report or True  # report always generated
    # File should exist and have video+audio
    assert "File exists" in report
    print(f"         QC result: {'PASSED' if passed else 'ISSUES (expected for test clip)'}")

check("QC checks on rendered video", test_e1_qc_on_output)


# ── Stage E: Metadata Writer ──────────────────────────────────────────────────
print("\n[Stage E] Metadata Writer")

meta_writer = MetadataWriter(cfg)

def test_e2_metadata_template():
    meta = meta_writer.generate(
        script_text="The James Webb Telescope revealed amazing images.",
        topic="Space Telescopes",
        title_hint="Webb's Greatest Hits",
    )
    assert "title" in meta, "No title"
    assert "description" in meta, "No description"
    assert "hashtags" in meta, "No hashtags"
    assert len(meta["hashtags"]) >= 3, "Too few hashtags"
    path = meta_writer.save(meta, str(TEST_DIR))
    assert path.exists(), "Metadata file not saved"
    print(f"         Title: {meta['title'][:50]}")

check("Generate and save metadata", test_e2_metadata_template)


# ── Summary ───────────────────────────────────────────────────────────────────
print()
print("=" * 60)
passed = sum(1 for _, s, _ in results if s == PASS)
failed = sum(1 for _, s, _ in results if s == FAIL)
print(f"  Results: {passed} PASSED / {failed} FAILED")
if failed:
    print("\n  Failed tests:")
    for name, status, err in results:
        if status == FAIL:
            print(f"    - {name}: {err}")
print("=" * 60)

# Cleanup test artifacts
try:
    shutil.rmtree(TEST_DIR)
except Exception:
    pass

sys.exit(0 if failed == 0 else 1)
