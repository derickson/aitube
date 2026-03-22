"""Clean up scraped markdown content using Claude to remove navigation/footer garbage."""

import logging
import re

import anthropic

from backend.app.config import settings

logger = logging.getLogger(__name__)


def _pre_clean(markdown: str) -> str:
    """Deterministic pre-cleaning to strip obvious non-article patterns."""
    lines = markdown.split("\n")
    cleaned_lines = []
    for line in lines:
        stripped = line.strip()

        # Skip lines that are just "See all" / "See all ..." links
        if re.match(r'^See all\b', stripped, re.IGNORECASE):
            continue

        # Skip breadcrumb lines
        if stripped.startswith("Breadcrumb"):
            continue

        # Skip "Skip to" links
        if re.match(r'^Skip to ', stripped, re.IGNORECASE):
            continue

        # Skip "Subscribe" / "No thanks" standalone
        if stripped in ("Subscribe", "No thanks", "SubscribeNo thanks", "Copy link", "Share"):
            continue

        # Skip "Follow Us" and social media link lines
        if stripped == "Follow Us":
            continue

        # Skip standalone nav items like "* Home * Innovation & AI"
        if stripped.startswith("* ") and stripped.count(" * ") >= 2:
            nav_words = {"Home", "Products", "Company news", "Feed", "Subscribe",
                         "Innovation & AI", "Products & platforms"}
            if any(w in stripped for w in nav_words):
                continue

        # Skip "Learn more:" lines
        if stripped == "Learn more:":
            continue

        # Skip lines that are just repeated blog link text
        if re.match(r'^(Google \w+ blog|Waze blog)', stripped):
            continue

        # Skip "Jump to position N" lines
        if re.match(r'^Jump to position \d+', stripped):
            continue

        # Skip "Sorry, your browser doesn't support embedded videos" lines
        if "your browser doesn't support embedded videos" in stripped:
            continue

        # Skip "Let's stay in touch" / newsletter prompts
        if "stay in touch" in stripped.lower():
            continue

        # Skip "POSTED IN:" labels
        if stripped == "POSTED IN:":
            continue

        cleaned_lines.append(line)

    return "\n".join(cleaned_lines)


def _trim_footer(markdown: str) -> str:
    """Cut off content after 'Related stories' section."""
    # Common footer markers
    for marker in ["Related stories", "Related articles", "Follow Us"]:
        idx = markdown.find(marker)
        if idx > 0 and idx > len(markdown) * 0.3:  # Only if it's past 30% of content
            return markdown[:idx].rstrip()
    return markdown


async def cleanup_article_markdown(markdown: str, title: str) -> dict[str, str | None]:
    """
    Clean scraped markdown: deterministic pre-clean, then Claude for final polish.
    Also extracts the first usable image URL.

    Returns {"markdown": cleaned_text, "image_url": url_or_none}.
    """
    if not markdown or not markdown.strip():
        return {"markdown": markdown, "image_url": None}

    # Step 1: Deterministic pre-clean
    pre_cleaned = _pre_clean(markdown)
    pre_cleaned = _trim_footer(pre_cleaned)

    # Step 2: If no API key, return pre-cleaned result
    if not settings.anthropic_api_key:
        return {"markdown": pre_cleaned, "image_url": _extract_first_image(pre_cleaned)}

    # Step 3: Head+Tail LLM cleanup
    # Garbage concentrates at the start (nav) and end (footer/related).
    # Send both ends to the LLM; preserve the clean middle as-is.
    CHUNK = 6000
    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)

    if len(pre_cleaned) <= CHUNK * 2:
        # Short enough to send whole thing
        llm_input = pre_cleaned
        has_middle = False
    else:
        head = pre_cleaned[:CHUNK]
        tail = pre_cleaned[-CHUNK:]
        llm_input = head + "\n\n[... MIDDLE OF ARTICLE OMITTED - THIS IS CLEAN ARTICLE BODY ...]\n\n" + tail
        has_middle = True
        middle = pre_cleaned[CHUNK:-CHUNK]

    try:
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=8000,
            messages=[{
                "role": "user",
                "content": f"""Clean up this scraped article markdown. {"I'm showing you the HEAD and TAIL of the article (the middle is clean article body)." if has_middle else ""}

Remove all non-article content from {"both ends" if has_middle else "the text"}:
- Site navigation, menus, breadcrumbs, sidebars
- Header links, search bars, "skip to content" links
- Footer content (copyright, related links, social media links, newsletter signups)
- Share buttons, "copy link" text, "x.comFacebookLinkedInMail" share link text
- Cookie banners, subscription prompts
- Repeated navigation structures
- "Read AI-generated summary" sections and their generated summaries
- Author attribution blocks that appear before the article body (keep inline author mentions)
- Category tags, "POSTED IN:" sections
- Standalone "Read more" lines that aren't part of article prose
- Audio player widgets and "Listen to article" controls

Keep ONLY the actual article body content including headings, paragraphs, images (IMPORTANT: preserve all ![...](...) image markdown), and lists that are part of the article itself.

{"Return the cleaned HEAD, then the exact marker [... MIDDLE ...], then the cleaned TAIL." if has_middle else ""}Return ONLY the cleaned markdown, nothing else. Do not add any commentary.

Article title for reference: {title}

---
{llm_input}""",
            }],
        )

        cleaned = response.content[0].text.strip()

        # Reassemble with middle if we split
        if has_middle:
            marker = "[... MIDDLE ...]"
            marker_idx = cleaned.find(marker)
            if marker_idx >= 0:
                cleaned_head = cleaned[:marker_idx].rstrip()
                cleaned_tail = cleaned[marker_idx + len(marker):].lstrip()
                cleaned = cleaned_head + "\n\n" + middle + "\n\n" + cleaned_tail
            else:
                # LLM didn't include marker — use cleaned output + middle + trimmed tail
                cleaned = cleaned + "\n\n" + middle

        logger.info("Cleaned article markdown for: %s (before: %d, after: %d chars)",
                     title[:50], len(markdown), len(cleaned))

        return {"markdown": cleaned, "image_url": _extract_first_image(cleaned)}

    except Exception as e:
        logger.warning("Article cleanup failed for %s: %s", title[:50], e)
        return {"markdown": pre_cleaned, "image_url": _extract_first_image(pre_cleaned)}


def _extract_first_image(markdown: str) -> str | None:
    """Extract the first image URL from markdown content."""
    # Match markdown images: ![alt](url)
    match = re.search(r'!\[.*?\]\((https?://[^\s\)]+)\)', markdown)
    if match:
        return match.group(1)

    # Match HTML img tags: <img src="url">
    match = re.search(r'<img[^>]+src=["\']?(https?://[^\s"\'>\)]+)', markdown)
    if match:
        return match.group(1)

    return None
