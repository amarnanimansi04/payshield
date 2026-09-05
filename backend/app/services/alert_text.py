"""
PAYSHIELD — alert text generation (port of the former src/lib/alertText.ts)

This is the ONLY place in the entire codebase an LLM is called.
Detection, classification, and action selection are all
deterministic/statistical, computed BEFORE this function is ever
invoked - this function only turns an already-made decision into
readable prose for a merchant. If the Groq call fails, we fall back to
a templated string - the alert still fires, just with flatter
language, because the DECISION was never the LLM's job to begin with.
"""

from __future__ import annotations

import logging

import httpx

from app.core.config import get_settings
from app.models.schemas import AgentState, DetectionResult

logger = logging.getLogger(__name__)

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"


async def generate_alert_text(
    segment: str, state: AgentState, detection: DetectionResult, observed_amount_affected: int
) -> tuple[str, str]:
    """Returns (text, source) where source is "llm" or "template"."""
    settings = get_settings()
    if not settings.groq_api_key:
        return _templated_fallback(segment, detection, observed_amount_affected), "template"

    prompt = _build_prompt(segment, state, detection, observed_amount_affected)
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            res = await client.post(
                GROQ_URL,
                headers={"Authorization": f"Bearer {settings.groq_api_key}"},
                json={
                    "model": "llama-3.3-70b-versatile",
                    "messages": [
                        {
                            "role": "system",
                            "content": (
                                "You write short, factual merchant-facing payment-ops alerts. "
                                "Use only the numbers given to you - never invent a statistic. "
                                "Never claim a 'probability' or 'confidence' that the anomaly is "
                                "real beyond calling it statistically significant. Two to three "
                                "sentences, plain language, no exclamation points, no hype."
                            ),
                        },
                        {"role": "user", "content": prompt},
                    ],
                    "temperature": 0.2,
                    "max_tokens": 200,
                },
            )
        if res.status_code != 200:
            logger.error("Groq call failed: %s %s", res.status_code, res.text)
            return _templated_fallback(segment, detection, observed_amount_affected), "template"
        data = res.json()
        text = (data.get("choices") or [{}])[0].get("message", {}).get("content", "").strip()
        if text:
            return text, "llm"
        return _templated_fallback(segment, detection, observed_amount_affected), "template"
    except Exception:  # noqa: BLE001 - any network/parse failure falls back, never crashes the cycle
        logger.exception("Groq call threw")
        return _templated_fallback(segment, detection, observed_amount_affected), "template"


def _build_prompt(segment: str, state: AgentState, detection: DetectionResult, observed_amount_affected: int) -> str:
    dominant = detection.dominant_error_source or {}
    return "\n".join(
        [
            f"Segment: {segment}",
            f"State: {state}",
            f"Current failure rate: {detection.current_rate * 100:.1f}%",
            f"Baseline: {detection.ewma_baseline * 100:.1f}%",
            f"Deviation: {detection.sigma_deviation:.1f} sigma",
            f"Statistical significance score: {detection.significance_score * 100:.0f}% "
            f"(a normal-distribution mapping of the sigma deviation, NOT a calibrated "
            f"probability that this is real)",
            f"Sustained for: {detection.sustained_significant_windows} consecutive window(s)",
            f"Dominant error source: {dominant.get('source', 'unknown')} "
            f"({dominant.get('share', 0) * 100:.0f}% of failures this window)",
            f"Observed affected failed volume: Rs {observed_amount_affected / 100:.0f}",
            "",
            "Write a short alert for the merchant explaining what's happening and what PayShield "
            "has done in response (suppressed its OWN downstream recovery workflow for this "
            "segment, since retrying individually won't fix a systemic issue - do not imply this "
            "pauses Razorpay's own retry engine).",
        ]
    )


def _templated_fallback(segment: str, detection: DetectionResult, observed_amount_affected: int) -> str:
    return (
        f'Payment failures in segment "{segment}" are running at {detection.current_rate * 100:.1f}%, '
        f"versus a normal baseline of {detection.ewma_baseline * 100:.1f}% "
        f"({detection.sigma_deviation:.1f} sigma above expected, sustained for "
        f"{detection.sustained_significant_windows} window(s)). This looks systemic rather than "
        f"customer-specific, so PayShield has suppressed its own downstream recovery workflow for "
        f"this segment and flagged it for review. Observed affected failed volume so far: "
        f"Rs {observed_amount_affected / 100:.0f}."
    )
