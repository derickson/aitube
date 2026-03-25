"""Generate brief AI summaries of content using Claude."""

import asyncio
import logging
from typing import Any

import anthropic

from backend.app.config import settings
from backend.app.services.anthropic_client import get_anthropic_client, traced_messages_create

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


async def summarize_content(
    title: str,
    content_type: str,
    transcript_text: str,
    description: str = "",
    author: str = "",
    transcript_chunks: list[dict[str, Any]] | None = None,
) -> str | None:
    """
    Generate a brief summary that clarifies what the content is actually about,
    cutting through clickbait titles to surface the real topic, opinion, or thesis.

    Returns a summary with bullet-point breakdown (with timestamps for
    video/podcast), or None if summarization fails.
    """
    if not settings.anthropic_api_key:
        return None

    if not transcript_text and not description:
        return None

    has_timestamps = bool(transcript_chunks)

    # Build source text — prefer timestamped chunks for video/podcast
    if has_timestamps and content_type in ("video", "podcast_episode"):
        source_text = _build_timestamped_transcript(transcript_chunks)
    else:
        source_text = transcript_text[:100000] if transcript_text else description[:2000]

    client = get_anthropic_client()

    type_label = {
        "video": "YouTube video",
        "podcast_episode": "podcast episode",
        "article": "article",
    }.get(content_type, "content")

    timestamp_instruction = ""
    if has_timestamps and content_type in ("video", "podcast_episode"):
        timestamp_instruction = """
Include timestamps in [M:SS] or [H:MM:SS] format at the start of each bullet point, indicating where that topic begins. Use the timestamps from the transcript."""
    elif content_type == "article":
        timestamp_instruction = ""

    max_retries = 3
    for attempt in range(max_retries):
        try:
            response = traced_messages_create(
                client,
                model="claude-haiku-4-5-20251001",
                max_tokens=800,
                messages=[{
                    "role": "user",
                    "content": f"""Summarize this {type_label}. Your goal is to clarify what it's actually about — cut through any clickbait or vague titling to tell the reader the real topic, the creator's opinion or thesis, and what they'll get from it.

First, write a 2-3 sentence summary that is direct and specific.

Then, list exactly 5 key insights or takeaway learnings from the full content. Each bullet should be a concise but meaningful sentence describing a specific insight, argument, or conclusion.{timestamp_instruction}

Format the bullets as a markdown list (- item).

Title: {title}
{f"By: {author}" if author else ""}
{f"Description: {description[:300]}" if description else ""}

Content:
{source_text}""",
                }],
            )

            summary = response.content[0].text.strip()
            # Strip common unwanted heading prefixes the model sometimes adds
            for prefix in ("## Summary\n", "## Summary\r\n", "**Summary:**\n", "**Summary**\n"):
                if summary.startswith(prefix):
                    summary = summary[len(prefix):].lstrip()
                    break
            logger.info("Generated summary for: %s (%d chars)", title[:50], len(summary))
            return summary

        except anthropic.RateLimitError:
            if attempt < max_retries - 1:
                logger.warning("Rate limited (429) summarizing %s, retrying in 5s (attempt %d/%d)",
                               title[:50], attempt + 1, max_retries)
                await asyncio.sleep(5)
            else:
                logger.warning("Rate limited (429) summarizing %s, all %d attempts exhausted",
                               title[:50], max_retries)
                return None

        except Exception as e:
            logger.warning("Summarization failed for %s: %s", title[:50], e)
            return None

    return None
