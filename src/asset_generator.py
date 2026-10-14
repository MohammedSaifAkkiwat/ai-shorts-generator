"""
src/asset_generator.py
Stage B+D — Fallback Visual Generator (Pillow-based).

When no suitable licensed stock asset is available, this module generates
visually rich scene cards using Pillow:
- Gradient backgrounds with animated motion via FFmpeg
- Large centered title text
- Scene index indicator
- Optional subtle geometric overlay

No external paid API is used. All assets produced here are original works
owned by the user.
"""

from __future__ import annotations

import logging
import math
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)

# Shorts resolution
W, H = 1080, 1920

# Curated gradient palettes (start_color, end_color)
PALETTES = [
    ("#0f0c29", "#302b63"),   # Deep purple
    ("#1a1a2e", "#16213e"),   # Dark navy
    ("#0d324d", "#7f5a83"),   # Ocean to violet
    ("#2c3e50", "#3498db"),   # Midnight blue
    ("#134e5e", "#71b280"),   # Teal to green
    ("#1d2671", "#c33764"),   # Navy to pink
    ("#0f2027", "#2c5364"),   # Dark ocean
    ("#16222a", "#3a6073"),   # Steel blue
]


class AssetGenerator:
    """Generate gradient scene cards and short animated clips as fallback visuals."""

    def __init__(self, config: dict, output_dir: str | Path):
        self.cfg = config
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.font_path = self._find_font()

    # ── Public API ────────────────────────────────────────────────────────────

    def generate_scene_clip(
        self,
        scene_index: int,
        text: str,
        duration: float,
        keywords: Optional[list[str]] = None,
    ) -> str:
        """
        Generate a short animated gradient video clip for a scene.
        Returns local path to the generated .mp4 file.
        """
        palette = PALETTES[scene_index % len(PALETTES)]
        frame_path = self._render_gradient_frame(scene_index, text, palette)

        output_path = self.output_dir / f"generated_scene_{scene_index:02d}.mp4"
        self._animate_frame_to_video(frame_path, str(output_path), duration, palette)
        return str(output_path)

    def generate_title_card(
        self,
        title: str,
        duration: float = 2.0,
    ) -> str:
        """Generate an opening title card clip."""
        palette = ("#1d2671", "#c33764")
        frame_path = self._render_title_frame(title, palette)
        output_path = self.output_dir / "title_card.mp4"
        self._animate_frame_to_video(frame_path, str(output_path), duration, palette)
        return str(output_path)

    # ── Frame rendering ───────────────────────────────────────────────────────

    def _render_gradient_frame(
        self,
        scene_index: int,
        text: str,
        palette: tuple[str, str],
    ) -> str:
        """Render a single 1080×1920 gradient PNG frame with text."""
        img = self._make_gradient(W, H, palette[0], palette[1])
        draw = ImageDraw.Draw(img)

        # Subtle radial glow in center
        self._draw_radial_glow(draw)

        # Geometric decoration lines
        self._draw_decorations(draw, scene_index)

        # Main text (wrapped)
        self._draw_wrapped_text(draw, text, y_center=H // 2)

        # Scene counter (subtle, bottom right)
        self._draw_scene_counter(draw, scene_index)

        # Save
        frame_path = self.output_dir / f"frame_scene_{scene_index:02d}.png"
        img.save(str(frame_path), "PNG")
        return str(frame_path)

    def _render_title_frame(self, title: str, palette: tuple[str, str]) -> str:
        img = self._make_gradient(W, H, palette[0], palette[1])
        draw = ImageDraw.Draw(img)
        self._draw_radial_glow(draw)
        self._draw_decorations(draw, 0)
        self._draw_wrapped_text(draw, title, y_center=H // 2, font_size=110)
        frame_path = self.output_dir / "frame_title.png"
        img.save(str(frame_path), "PNG")
        return str(frame_path)

    # ── Image helpers ─────────────────────────────────────────────────────────

    def _make_gradient(
        self, width: int, height: int, color1: str, color2: str
    ) -> Image.Image:
        """Create a smooth vertical gradient image."""
        img = Image.new("RGB", (width, height))
        draw = ImageDraw.Draw(img)
        r1, g1, b1 = self._hex_to_rgb(color1)
        r2, g2, b2 = self._hex_to_rgb(color2)
        for y in range(height):
            t = y / height
            r = int(r1 + (r2 - r1) * t)
            g = int(g1 + (g2 - g1) * t)
            b = int(b1 + (b2 - b1) * t)
            draw.line([(0, y), (width, y)], fill=(r, g, b))
        return img

    def _draw_radial_glow(self, draw: ImageDraw.ImageDraw):
        """Draw a subtle circular glow in the center."""
        cx, cy = W // 2, H // 2
        for r in range(400, 0, -10):
            alpha = int(15 * (1 - r / 400))
            draw.ellipse(
                [(cx - r, cy - r), (cx + r, cy + r)],
                fill=(255, 255, 255, alpha),
            )

    def _draw_decorations(self, draw: ImageDraw.ImageDraw, scene_index: int):
        """Draw subtle geometric accent lines."""
        # Thin horizontal accent lines
        accent_color = (255, 255, 255, 30)
        for y_offset in [-200, 200]:
            y = H // 2 + y_offset
            draw.line([(80, y), (W - 80, y)], fill=(200, 200, 200, 40), width=1)

        # Corner accent
        margin = 60
        length = 80
        line_color = (255, 255, 255, 60)
        # Top-left
        draw.line([(margin, margin), (margin + length, margin)], fill=line_color, width=2)
        draw.line([(margin, margin), (margin, margin + length)], fill=line_color, width=2)
        # Bottom-right
        draw.line([(W - margin, H - margin), (W - margin - length, H - margin)], fill=line_color, width=2)
        draw.line([(W - margin, H - margin), (W - margin, H - margin - length)], fill=line_color, width=2)

    def _draw_wrapped_text(
        self,
        draw: ImageDraw.ImageDraw,
        text: str,
        y_center: int,
        font_size: int = 72,
        max_width: int = 900,
    ):
        """Draw centered, wrapped white text with drop shadow."""
        font = self._load_font(font_size)
        shadow_font = self._load_font(font_size)

        # Word wrap
        words = text.split()
        lines = []
        current = []
        for word in words:
            test_line = " ".join(current + [word])
            bbox = draw.textbbox((0, 0), test_line, font=font)
            w = bbox[2] - bbox[0]
            if w > max_width and current:
                lines.append(" ".join(current))
                current = [word]
            else:
                current.append(word)
        if current:
            lines.append(" ".join(current))

        # Calculate total height
        line_height = font_size + 16
        total_height = len(lines) * line_height
        y_start = y_center - total_height // 2

        for i, line in enumerate(lines):
            y = y_start + i * line_height
            bbox = draw.textbbox((0, 0), line, font=font)
            text_w = bbox[2] - bbox[0]
            x = (W - text_w) // 2

            # Drop shadow
            draw.text((x + 3, y + 3), line, font=shadow_font, fill=(0, 0, 0, 160))
            # Main text
            draw.text((x, y), line, font=font, fill=(255, 255, 255, 255))

    def _draw_scene_counter(self, draw: ImageDraw.ImageDraw, scene_index: int):
        """Draw a subtle scene counter badge."""
        font = self._load_font(36)
        label = f"#{scene_index + 1}"
        bbox = draw.textbbox((0, 0), label, font=font)
        w = bbox[2] - bbox[0]
        x = W - 80 - w
        y = H - 100
        draw.rectangle([(x - 12, y - 8), (x + w + 12, y + 44)], fill=(0, 0, 0, 80))
        draw.text((x, y), label, font=font, fill=(200, 200, 200, 200))

    # ── Video generation ──────────────────────────────────────────────────────

    def _animate_frame_to_video(
        self,
        frame_path: str,
        output_path: str,
        duration: float,
        palette: tuple[str, str],
    ):
        """
        Use FFmpeg to create a slow-zoom animated video from a static frame.
        Applies a subtle Ken Burns zoom effect.
        """
        duration = max(duration, 2.0)
        # Scale slightly above 1080×1920 so zoom doesn't reveal edges
        scale_w = int(W * 1.05)
        scale_h = int(H * 1.05)

        # Ken Burns: slow zoom from 1.0 → 1.05 over the clip duration
        vf = (
            f"scale={scale_w}:{scale_h},"
            f"zoompan=z='min(zoom+0.0015,1.05)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
            f":d={int(duration * 30)}:s={W}x{H}:fps=30,"
            f"setsar=1"
        )

        cmd = [
            "ffmpeg", "-y",
            "-loop", "1",
            "-i", frame_path,
            "-vf", vf,
            "-t", str(duration),
            "-c:v", "libx264",
            "-preset", "fast",
            "-crf", "23",
            "-pix_fmt", "yuv420p",
            output_path,
        ]
        try:
            subprocess.run(
                cmd, check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            logger.info(f"Generated scene clip: {output_path}")
        except FileNotFoundError:
            logger.error("FFmpeg not found. Cannot generate animated clip.")
            raise
        except subprocess.CalledProcessError as e:
            logger.error(f"FFmpeg failed for {frame_path}: {e}")
            raise

    # ── Utility helpers ───────────────────────────────────────────────────────

    def _find_font(self) -> Optional[str]:
        """Find a bold font: project assets → system fonts → None (uses PIL default)."""
        project_font = Path("assets/fonts/Montserrat-Bold.ttf")
        if project_font.exists():
            return str(project_font)

        # Common Windows system fonts (bold variants)
        candidates = [
            r"C:\Windows\Fonts\arialbd.ttf",
            r"C:\Windows\Fonts\calibrib.ttf",
            r"C:\Windows\Fonts\verdanab.ttf",
            r"C:\Windows\Fonts\trebucbd.ttf",
        ]
        for c in candidates:
            if Path(c).exists():
                logger.info(f"Using system font: {c}")
                return c

        logger.warning("No bold font found; using PIL default (quality reduced)")
        return None

    def _load_font(self, size: int) -> ImageFont.FreeTypeFont:
        """Load font at given size, falling back to PIL default."""
        if self.font_path:
            try:
                return ImageFont.truetype(self.font_path, size)
            except Exception:
                pass
        return ImageFont.load_default()

    @staticmethod
    def _hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
        h = hex_color.lstrip("#")
        return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
