"""Generate brief AI summaries of content using Claude."""

import logging

import anthropic

from backend.app.config import settings

logger = logging.getLogger(__name__)


async def summarize_content(
    title: str,
    content_type: str,
    transcript_text: str,
    description: str = "",
    author: str = "",
) -> str | None:
    """
    Generate a brief summary that clarifies what the content is actually about,
    cutting through clickbait titles to surface the real topic, opinion, or thesis.

    Returns a 2-3 sentence summary, or None if summarization fails.
    """
    if not settings.anthropic_api_key:
        return None

    if not transcript_text and not description:
        return None

    # Use first ~3000 chars of transcript to keep token usage reasonable
    source_text = transcript_text[:3000] if transcript_text else description[:1000]

    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)

    type_label = {
        "video": "YouTube video",
        "podcast_episode": "podcast episode",
        "article": "article",
    }.get(content_type, "content")

    try:
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=200,
            messages=[{
                "role": "user",
                "content": f"""Summarize this {type_label} in 2-3 sentences. Your goal is to clarify what it's actually about — cut through any clickbait or vague titling to tell the reader the real topic, the creator's opinion or thesis, and what they'll get from it. Be direct and specific.

Title: {title}
{f"By: {author}" if author else ""}
{f"Description: {description[:300]}" if description else ""}

Content:
{source_text}""",
            }],
        )

        summary = response.content[0].text.strip()
        logger.info("Generated summary for: %s (%d chars)", title[:50], len(summary))
        return summary

    except Exception as e:
        logger.warning("Summarization failed for %s: %s", title[:50], e)
        return None
