"""
src/fact_checker.py
Stage E — Script Fact-Checking.

Responsibilities:
- Extract verifiable claims from the script using Gemini (or regex fallback).
- Search the web for each claim using DuckDuckGo.
- Cross-reference results and flag unverifiable or contradicted claims.
- Produce a fact_check_report.md with findings and source URLs.

IMPORTANT LIMITATION:
This is a best-effort automated fact-check. It is NOT a substitute for
human expert review. Users should review flagged claims before publishing.
"""

from __future__ import annotations

import logging
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


# ─── Data structures ──────────────────────────────────────────────────────────

@dataclass
class Claim:
    text: str
    scene_index: int
    status: str = "UNCHECKED"   # VERIFIED | UNVERIFIED | FLAGGED | UNCHECKED
    sources: list[str] = field(default_factory=list)
    notes: str = ""


# ─── Main class ───────────────────────────────────────────────────────────────

class FactChecker:
    """Extract and verify factual claims from the script."""

    def __init__(self, config: dict):
        self.cfg = config
        fc_cfg = config.get("fact_checking", {})
        self.enabled     = fc_cfg.get("enabled", True)
        self.engine      = fc_cfg.get("engine", "gemini")
        self.max_claims  = fc_cfg.get("max_claims_to_check", 10)
        self.flag_unverified = fc_cfg.get("flag_unverified", True)
        self._gemini_model = None

    # ── Public API ─────────────────────────────────────────────────────────────

    def check_script(
        self, scenes: list, topic: Optional[str] = None
    ) -> tuple[list[Claim], str]:
        """
        Fact-check all scenes. Returns (claims, report_markdown_text).
        """
        if not self.enabled:
            return [], "# Fact-Check Report\n\nFact-checking disabled in config.\n"

        full_text = " ".join(s.text for s in scenes)
        claims = self._extract_claims(full_text, scenes, topic)
        claims = claims[: self.max_claims]

        if not claims:
            report = self._build_report(claims, topic, found_count=0)
            return claims, report

        # Verify each claim
        for claim in claims:
            self._verify_claim(claim)
            time.sleep(0.5)   # Polite rate limiting

        report = self._build_report(claims, topic)
        return claims, report

    def save_report(self, report_text: str, output_dir: str | Path) -> Path:
        out = Path(output_dir) / "fact_check_report.md"
        out.write_text(report_text, encoding="utf-8")
        logger.info(f"Fact-check report saved: {out}")
        return out

    # ── Claim extraction ──────────────────────────────────────────────────────

    def _extract_claims(
        self, text: str, scenes: list, topic: Optional[str]
    ) -> list[Claim]:
        """Extract verifiable claims using Gemini or regex heuristics."""
        gemini_key = os.getenv("GEMINI_API_KEY", "")
        if self.engine == "gemini" and gemini_key:
            return self._extract_with_gemini(text, scenes, topic)
        return self._extract_with_regex(text, scenes)

    def _extract_with_gemini(
        self, text: str, scenes: list, topic: Optional[str]
    ) -> list[Claim]:
        """Use Gemini to identify verifiable factual claims."""
        try:
            import google.generativeai as genai
            import json as json_mod

            api_key = os.getenv("GEMINI_API_KEY", "")
            genai.configure(api_key=api_key)
            model_name = self.cfg.get("gemini", {}).get("model", "gemini-1.5-flash")
            model = genai.GenerativeModel(model_name)

            prompt = f"""You are a fact-checker reviewing a YouTube script.

Extract up to 8 specific, verifiable factual claims from the script below.
Focus on: names, dates, statistics, numbers, named events, specific achievements.
Ignore opinions, metaphors, or vague statements.

For each claim, identify which scene it appears in (0-indexed).

Return ONLY valid JSON (no markdown fences):
{{
  "claims": [
    {{"text": "The claim text here", "scene_index": 0}},
    ...
  ]
}}

Script:
{text}"""

            response = model.generate_content(prompt)
            raw = response.text.strip()
            raw = re.sub(r"^```json\s*", "", raw)
            raw = re.sub(r"```$", "", raw).strip()

            data = json_mod.loads(raw)
            claims = []
            for c in data.get("claims", []):
                claims.append(Claim(
                    text=c.get("text", ""),
                    scene_index=int(c.get("scene_index", 0)),
                ))
            logger.info(f"Gemini extracted {len(claims)} claims")
            return claims

        except Exception as e:
            logger.warning(f"Gemini claim extraction failed ({e}); using regex fallback")
            return self._extract_with_regex(text, scenes)

    def _extract_with_regex(self, text: str, scenes: list) -> list[Claim]:
        """Heuristic claim extraction: sentences with numbers, dates, names."""
        # Match sentences containing numbers, years, or capitalized multi-word phrases
        sentences = re.split(r"(?<=[.!?])\s+", text)
        claims = []
        for sentence in sentences:
            has_number = bool(re.search(r"\d+", sentence))
            has_proper = bool(re.search(r"[A-Z][a-z]+ [A-Z][a-z]+", sentence))
            has_year   = bool(re.search(r"\b(19|20)\d{2}\b", sentence))
            if has_number or has_proper or has_year:
                # Find which scene contains this sentence
                scene_idx = 0
                for scene in scenes:
                    if sentence[:30] in scene.text:
                        scene_idx = scene.index
                        break
                claims.append(Claim(text=sentence.strip(), scene_index=scene_idx))
        logger.info(f"Regex extracted {len(claims)} candidate claims")
        return claims

    # ── Claim verification ────────────────────────────────────────────────────

    def _verify_claim(self, claim: Claim):
        """Search DuckDuckGo for the claim and assess support."""
        gemini_key = os.getenv("GEMINI_API_KEY", "")
        if self.engine == "gemini" and gemini_key:
            self._verify_with_gemini(claim)
        else:
            self._verify_with_duckduckgo(claim)

    def _verify_with_gemini(self, claim: Claim):
        """Use Gemini's knowledge to verify the claim."""
        try:
            import google.generativeai as genai
            import json as json_mod

            api_key = os.getenv("GEMINI_API_KEY", "")
            genai.configure(api_key=api_key)
            model_name = self.cfg.get("gemini", {}).get("model", "gemini-1.5-flash")
            model = genai.GenerativeModel(model_name)

            prompt = f"""You are a careful fact-checker. Evaluate this claim:

"{claim.text}"

Based on your knowledge:
1. Is this claim accurate, inaccurate, or unverifiable?
2. Provide 1–2 sentences of context.
3. Note any important caveats (e.g., outdated info, approximate numbers).

Return ONLY valid JSON:
{{
  "status": "VERIFIED" | "FLAGGED" | "UNVERIFIED",
  "notes": "Brief explanation here",
  "confidence": "high" | "medium" | "low"
}}"""

            response = model.generate_content(prompt)
            raw = response.text.strip()
            raw = re.sub(r"^```json\s*", "", raw)
            raw = re.sub(r"```$", "", raw).strip()

            data = json_mod.loads(raw)
            claim.status = data.get("status", "UNVERIFIED")
            claim.notes  = data.get("notes", "")
            logger.info(f"Claim [{claim.status}]: {claim.text[:60]}")

        except Exception as e:
            logger.warning(f"Gemini verification failed: {e}")
            self._verify_with_duckduckgo(claim)

    def _verify_with_duckduckgo(self, claim: Claim):
        """Use DuckDuckGo search snippets to assess claim support."""
        try:
            from duckduckgo_search import DDGS
        except ImportError:
            logger.warning("duckduckgo-search not installed; skipping web verification")
            claim.status = "UNVERIFIED"
            claim.notes  = "Web fact-checking unavailable (package not installed)"
            return

        try:
            query = claim.text[:200]
            results = []
            with DDGS() as ddgs:
                for r in ddgs.text(query, max_results=3):
                    results.append(r)
                    claim.sources.append(r.get("href", ""))

            if results:
                # Simple heuristic: check if key words from claim appear in snippets
                claim_words = set(re.findall(r"\b[a-zA-Z]{4,}\b", claim.text.lower()))
                snippet_text = " ".join(
                    r.get("body", "") for r in results
                ).lower()
                matched = sum(1 for w in claim_words if w in snippet_text)
                coverage = matched / max(len(claim_words), 1)

                if coverage >= 0.5:
                    claim.status = "VERIFIED"
                    claim.notes  = f"Key terms found in {len(results)} web sources"
                else:
                    claim.status = "UNVERIFIED"
                    claim.notes  = f"Low overlap with web sources (coverage={coverage:.0%})"
            else:
                claim.status = "UNVERIFIED"
                claim.notes  = "No web results found for this claim"

            logger.info(f"DuckDuckGo [{claim.status}]: {claim.text[:60]}")
            time.sleep(0.3)   # Respect rate limits

        except Exception as e:
            logger.warning(f"DuckDuckGo search failed for claim: {e}")
            claim.status = "UNVERIFIED"
            claim.notes  = f"Search error: {str(e)[:100]}"

    # ── Report generation ─────────────────────────────────────────────────────

    def _build_report(
        self,
        claims: list[Claim],
        topic: Optional[str] = None,
        found_count: Optional[int] = None,
    ) -> str:
        verified   = [c for c in claims if c.status == "VERIFIED"]
        flagged    = [c for c in claims if c.status == "FLAGGED"]
        unverified = [c for c in claims if c.status == "UNVERIFIED"]

        lines = [
            "# Fact-Check Report",
            "",
            f"**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        ]
        if topic:
            lines.append(f"**Topic:** {topic}")
        lines += [
            f"**Claims checked:** {len(claims)}",
            f"**Verified:** {len(verified)} | **Flagged:** {len(flagged)} | **Unverified:** {len(unverified)}",
            "",
            "> ⚠️ This is an automated fact-check. Human review is required before publishing.",
            "",
        ]

        if flagged:
            lines += ["## ❌ Flagged Claims (require human review)", ""]
            for c in flagged:
                lines += [f"- **Claim:** {c.text}", f"  - *Notes:* {c.notes}", ""]

        if unverified:
            lines += ["## ⚠️ Unverified Claims", ""]
            for c in unverified:
                lines += [f"- **Claim:** {c.text}", f"  - *Notes:* {c.notes}", ""]

        if verified:
            lines += ["## ✅ Verified Claims", ""]
            for c in verified:
                notes = f" — {c.notes}" if c.notes else ""
                sources = ""
                if c.sources:
                    sources = "\n  - *Sources:* " + ", ".join(c.sources[:2])
                lines += [f"- {c.text}{notes}{sources}", ""]

        lines += [
            "---",
            "",
            "## Disclaimer",
            "",
            "This report was generated by an automated tool using Gemini AI and/or DuckDuckGo web search.",
            "It is provided as a starting point for human review and should not be treated as authoritative verification.",
            "Claims marked UNVERIFIED may be accurate but could not be confirmed by the automated system.",
            "The author is responsible for verifying all claims before publishing.",
        ]
        return "\n".join(lines)
