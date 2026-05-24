"""Head-to-head evaluation of summarization engines: Haiku vs Hermes (GPT-5.4 mini).

Builds one prompt, runs both engines on it, applies deterministic format checks, and
asks a neutral LLM judge (Claude Sonnet) to score them blind + A/B-randomized (to kill
position bias). Used by scripts/eval_summarizers.py (offline batch) and by the optional
production shadow eval (summary_eval_shadow_rate).
"""

import json
import logging
import random
import re
import time
from datetime import datetime, timezone
from typing import Any

from backend.app.config import settings
from backend.app.services.anthropic_client import get_anthropic_client, traced_messages_create
from backend.app.services.hermes_client import run_oneshot
from backend.app.services.summarizer import (
    _build_summary_prompt,
    _build_timestamped_transcript,
    _postprocess_summary,
    summarize_via_haiku,
)

logger = logging.getLogger(__name__)

JUDGE_MODEL = "claude-sonnet-4-6"

_TIMESTAMP_RE = re.compile(r"\[\d{1,2}:\d{2}(?::\d{2})?\]")


def _source_from_item(item: dict[str, Any]) -> tuple[str, bool]:
    """Derive (source_text, has_timestamps) from a content-item doc, mirroring summarize_content."""
    content_type = item.get("type", "")
    transcript = item.get("transcript") if isinstance(item.get("transcript"), dict) else {}
    chunks = transcript.get("chunks")
    text = transcript.get("text", "")
    has_timestamps = bool(chunks)
    if has_timestamps and content_type in ("video", "podcast_episode"):
        return _build_timestamped_transcript(chunks), True
    fallback = text or item.get("content_markdown", "")
    desc = (item.get("metadata") or {}).get("description", "")
    return (fallback[:100000] if fallback else desc[:2000]), False


def _format_violations(summary: str, *, has_timestamps: bool) -> list[str]:
    """Deterministic, no-LLM checks against the prompt's required structure."""
    summary = (summary or "").strip()
    if not summary:
        return ["empty"]

    violations: list[str] = []
    bullets = [ln for ln in summary.splitlines() if ln.lstrip().startswith("- ")]
    if len(bullets) != 5:
        violations.append(f"bullets={len(bullets)} (want 5)")

    lead = summary.split("\n-", 1)[0].strip()
    sentences = [s for s in re.split(r"(?<=[.!?])\s+", lead) if s.strip()]
    if not (1 <= len(sentences) <= 4):
        violations.append(f"lead_sentences={len(sentences)} (want 2-3)")

    if has_timestamps and not _TIMESTAMP_RE.search(summary):
        violations.append("missing_timestamps")

    return violations


def _extract_json(raw: str) -> dict[str, Any]:
    """Pull the first JSON object out of an LLM response (handles ```json fences)."""
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("```", 2)[1]
        if raw.startswith("json"):
            raw = raw[4:]
    start, end = raw.find("{"), raw.rfind("}")
    if start == -1 or end == -1:
        raise ValueError(f"no JSON object in judge response: {raw[:200]}")
    return json.loads(raw[start:end + 1])


async def _judge(source_text: str, summary_a: str, summary_b: str) -> dict[str, Any]:
    """Neutral LLM judge. A/B are pre-randomized by the caller, so this stays unbiased."""
    client = get_anthropic_client()
    prompt = f"""You are a strict, impartial judge comparing two AI-generated summaries (A and B) of the SAME source content. Score each from 1 (poor) to 5 (excellent) on:
- faithfulness: grounded in the source, no hallucinated claims
- specificity: cuts through clickbait to the real topic/thesis; concrete not vague
- format: a 2-3 sentence lead summary, then exactly 5 markdown "- " bullets
- conciseness: no padding or filler

Then pick the better overall summary. Respond with ONLY a JSON object, no prose:
{{"winner": "A" | "B" | "tie", "scores": {{"A": {{"faithfulness": n, "specificity": n, "format": n, "conciseness": n}}, "B": {{"faithfulness": n, "specificity": n, "format": n, "conciseness": n}}}}, "rationale": "one or two sentences"}}

SOURCE CONTENT:
{source_text[:30000]}

SUMMARY A:
{summary_a}

SUMMARY B:
{summary_b}"""
    response = traced_messages_create(
        client,
        model=JUDGE_MODEL,
        max_tokens=600,
        messages=[{"role": "user", "content": prompt}],
    )
    return _extract_json(response.content[0].text)


async def compare_engines(item: dict[str, Any]) -> dict[str, Any]:
    """Run both engines on one content item, score them, and return an eval record."""
    title = item.get("title", "")
    content_type = item.get("type", "")
    meta = item.get("metadata") or {}
    source_text, has_timestamps = _source_from_item(item)
    prompt = _build_summary_prompt(
        title, content_type, source_text,
        meta.get("description", ""), meta.get("author", ""), has_timestamps,
    )

    t0 = time.perf_counter()
    haiku = await summarize_via_haiku(prompt, title)
    haiku_ms = round((time.perf_counter() - t0) * 1000, 1)

    t0 = time.perf_counter()
    hermes_raw = await run_oneshot(prompt, model=settings.hermes_model)
    hermes_ms = round((time.perf_counter() - t0) * 1000, 1)
    hermes = _postprocess_summary(hermes_raw) if hermes_raw else None

    record: dict[str, Any] = {
        "item_id": item.get("id") or item.get("_id"),
        "external_id": item.get("external_id"),
        "title": title,
        "type": content_type,
        "evaluated_at": datetime.now(timezone.utc).isoformat(),
        "haiku": {
            "summary": haiku,
            "latency_ms": haiku_ms,
            "format_violations": _format_violations(haiku or "", has_timestamps=has_timestamps),
        },
        "hermes": {
            "summary": hermes,
            "model": settings.hermes_model,
            "latency_ms": hermes_ms,
            "format_violations": _format_violations(hermes or "", has_timestamps=has_timestamps),
        },
        "judge": None,
    }

    # Judge only when both engines produced output.
    if haiku and hermes:
        swap = random.random() < 0.5
        a, b = (hermes, haiku) if swap else (haiku, hermes)
        a_engine, b_engine = ("hermes", "haiku") if swap else ("haiku", "hermes")
        try:
            j = await _judge(source_text, a, b)
            winner = {"A": a_engine, "B": b_engine}.get(j.get("winner"), "tie")
            scores: dict[str, Any] = {}
            if isinstance(j.get("scores"), dict):
                scores[a_engine] = j["scores"].get("A")
                scores[b_engine] = j["scores"].get("B")
            record["judge"] = {
                "engine": JUDGE_MODEL,
                "winner": winner,
                "scores": scores,
                "rationale": j.get("rationale", ""),
            }
        except Exception as e:
            logger.warning("Judge failed for %s: %s", title[:50], e)

    return record
