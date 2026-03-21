"""Detect advertisements at the start of podcast transcripts using Claude."""

import json
import logging
from typing import Any

import anthropic

from backend.app.config import settings

logger = logging.getLogger(__name__)


async def detect_ad_end(chunks: list[dict[str, Any]]) -> float | None:
    """
    Analyze the first ~90 seconds of transcript chunks to find where
    an opening advertisement ends and actual content begins.

    Returns the timestamp (in seconds) where content starts, or None
    if no ad was detected.
    """
    if not chunks or not settings.anthropic_api_key:
        return None

    # Gather chunks from roughly the first 90 seconds
    early_chunks = []
    for chunk in chunks:
        if chunk.get("start", 0) > 90:
            break
        early_chunks.append(chunk)

    if not early_chunks:
        return None

    # Format chunks for the prompt
    transcript_text = "\n".join(
        f"[{chunk['start']:.1f}s] {chunk['text']}"
        for chunk in early_chunks
    )

    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)

    try:
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=256,
            messages=[{
                "role": "user",
                "content": f"""Analyze this podcast transcript opening. Determine if it starts with an advertisement, sponsor read, or promotional segment before the actual episode content begins.

Transcript (with timestamps):
{transcript_text}

Respond with ONLY a JSON object:
- If there IS an ad/sponsor at the start: {{"has_ad": true, "content_starts_at": <timestamp in seconds where the actual content begins>}}
- If there is NO ad at the start: {{"has_ad": false}}

Look for transitions like "From the New York Times...", "Welcome to...", "Today on...", theme music descriptions, or host introductions that signal the real content is starting after a sponsor message.""",
            }],
        )

        result_text = response.content[0].text.strip()
        # Parse JSON from response, handling possible markdown code blocks
        if result_text.startswith("```"):
            result_text = result_text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        result = json.loads(result_text)

        if result.get("has_ad") and result.get("content_starts_at") is not None:
            skip_to = float(result["content_starts_at"])
            logger.info("Ad detected, content starts at %.1fs", skip_to)
            return skip_to

        logger.info("No ad detected at start")
        return None

    except Exception as e:
        logger.warning("Ad detection failed: %s", e)
        return None
