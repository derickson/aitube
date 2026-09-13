"""Generate brief AI summaries of content.

Primary engine is Hermes (GPT-5.4 mini via SSH) when enabled; otherwise, or on any
Hermes failure, falls back to Claude Haiku. Both engines run the identical prompt
built by `_build_summary_prompt`, so the only variable is the model.
"""

import asyncio
import logging
from typing import Any

import anthropic

from backend.app.config import settings
from backend.app.services.anthropic_client import get_anthropic_client, traced_messages_create
from backend.app.services.summary_errors import MIN_SOURCE_CHARS, SummaryErrorCode

logger = logging.getLogger(__name__)


def _format_timestamp(seconds: float) -> str:
    """Format seconds into H:MM:SS or M:SS."""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    if h > 0:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def _build_timestamped_transcript(chunks: list[dict[str, Any]], max_chars: int = 100000) -> str:
    """Build a transcript string with timestamps from chunks."""
    lines = []
    total = 0
    for chunk in chunks:
        ts = _format_timestamp(chunk.get("start", 0))
        line = f"[{ts}] {chunk.get('text', '')}"
        total += len(line) + 1
        if total > max_chars:
            break
        lines.append(line)
    return "\n".join(lines)


def _build_summary_prompt(
    title: str,
    content_type: str,
    source_text: str,
    description: str = "",
    author: str = "",
    has_timestamps: bool = False,
) -> str:
    """Build the summarization prompt shared by every engine (Hermes and Haiku)."""
    type_label = {
        "video": "YouTube video",
        "podcast_episode": "podcast episode",
        "article": "article",
    }.get(content_type, "content")

    timestamp_instruction = ""
    if has_timestamps and content_type in ("video", "podcast_episode"):
        timestamp_instruction = """
Include timestamps in [M:SS] or [H:MM:SS] format at the start of each bullet point, indicating where that topic begins. Use the timestamps from the transcript."""

    return f"""Summarize this {type_label}. Your goal is to clarify what it's actually about — cut through any clickbait or vague titling to tell the reader the real topic, the creator's opinion or thesis, and what they'll get from it.

First, write a 2-3 sentence summary that is direct and specific.

Then, list exactly 5 key insights or takeaway learnings from the full content. Each bullet should be a concise but meaningful sentence describing a specific insight, argument, or conclusion.{timestamp_instruction}

Format the bullets as a markdown list (- item).

Title: {title}
{f"By: {author}" if author else ""}
{f"Description: {description[:300]}" if description else ""}

Content:
{source_text}"""


def _postprocess_summary(summary: str) -> str:
    """Strip common unwanted heading prefixes the model sometimes adds."""
    summary = summary.strip()
    for prefix in ("## Summary\n", "## Summary\r\n", "**Summary:**\n", "**Summary**\n"):
        if summary.startswith(prefix):
            return summary[len(prefix):].lstrip()
    return summary


async def summarize_via_haiku(
    prompt: str, title: str = ""
) -> tuple[str | None, str | None, SummaryErrorCode | None]:
    """Run the summary prompt through Claude Haiku, with retries on rate limits.

    Returns (summary, error, error_code). summary is None if Anthropic is unconfigured
    or fails, in which case error carries a short description of why.
    """
    if not settings.anthropic_api_key:
        return None, "anthropic not configured", SummaryErrorCode.NOT_CONFIGURED

    client = get_anthropic_client()
    max_retries = 3
    for attempt in range(max_retries):
        try:
            response = traced_messages_create(
                client,
                model="claude-haiku-4-5-20251001",
                max_tokens=800,
                messages=[{"role": "user", "content": prompt}],
            )
            summary = _postprocess_summary(response.content[0].text)
            from backend.app.services.hermes_client import looks_like_error_response
            failure = looks_like_error_response(summary)
            if failure:
                detail, code = failure
                logger.warning("Haiku returned no usable summary for %s: %s", title[:50], detail[:200])
                return None, detail, code
            logger.info("Generated summary via Haiku for: %s (%d chars)", title[:50], len(summary))
            return summary, None, None

        except anthropic.RateLimitError:
            if attempt < max_retries - 1:
                logger.warning("Rate limited (429) summarizing %s, retrying in 5s (attempt %d/%d)",
                               title[:50], attempt + 1, max_retries)
                await asyncio.sleep(5)
            else:
                logger.warning("Rate limited (429) summarizing %s, all %d attempts exhausted",
                               title[:50], max_retries)
                return None, "rate limited (429), retries exhausted", SummaryErrorCode.RATE_LIMITED

        except Exception as e:
            logger.warning("Summarization failed for %s: %s", title[:50], e)
            return None, str(e)[:300], SummaryErrorCode.UNKNOWN_ERROR

    return None, "unknown failure", SummaryErrorCode.UNKNOWN_ERROR


async def summarize_content(
    title: str,
    content_type: str,
    transcript_text: str,
    description: str = "",
    author: str = "",
    transcript_chunks: list[dict[str, Any]] | None = None,
) -> tuple[str | None, str | None, SummaryErrorCode | None]:
    """
    Generate a brief summary that clarifies what the content is actually about,
    cutting through clickbait titles to surface the real topic, opinion, or thesis.

    Tries Hermes (GPT-5.4 mini) first when enabled, falling back to Claude Haiku on
    any Hermes failure. Returns (summary, error, error_code): summary has a bullet-point
    breakdown (with timestamps for video/podcast), or is None if content is too thin to
    summarize or all engines fail / are unconfigured. error carries the last engine's
    failure message for humans; error_code is the structured signal callers should
    persist and branch retry logic on.
    """
    if not settings.anthropic_api_key and not settings.hermes_enabled:
        return None, None, None

    if not transcript_text and not description:
        return None, None, None

    has_timestamps = bool(transcript_chunks)

    # Build source text — prefer timestamped chunks for video/podcast
    if has_timestamps and content_type in ("video", "podcast_episode"):
        source_text = _build_timestamped_transcript(transcript_chunks)
    else:
        source_text = transcript_text[:100000] if transcript_text else description[:2000]

    # Too little material to summarize (e.g. a video whose transcript never came through,
    # leaving only a one-line description) — skip every engine rather than spend tokens on
    # a request that can only produce a refusal or a hallucinated non-summary.
    if len(source_text.strip()) < MIN_SOURCE_CHARS:
        return None, None, SummaryErrorCode.INSUFFICIENT_CONTENT

    prompt = _build_summary_prompt(
        title, content_type, source_text, description, author, has_timestamps
    )

    # 1) Try Hermes (no Anthropic spend). Any miss falls through to Haiku.
    hermes_error = None
    hermes_code = None
    if settings.hermes_enabled:
        from backend.app.services.hermes_client import run_oneshot
        hermes_text, hermes_error, hermes_code = await run_oneshot(prompt)
        if hermes_text:
            logger.info("Generated summary via Hermes for: %s (%d chars)", title[:50], len(hermes_text))
            return _postprocess_summary(hermes_text), None, None
        logger.info("Hermes summary unavailable for %s, falling back to Haiku (%s)", title[:50], hermes_error)

    # 2) Fall back to Haiku.
    haiku_text, haiku_error, haiku_code = await summarize_via_haiku(prompt, title)
    if haiku_text:
        return haiku_text, None, None
    return None, haiku_error or hermes_error, haiku_code or hermes_code
