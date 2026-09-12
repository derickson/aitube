"""Sanitize free text pulled out of feed XML before it reaches Elasticsearch.

Feeds routinely wrap titles and descriptions in CDATA sections and embed HTML
(either as real markup inside CDATA or as escaped entities). BeautifulSoup's
HTML parser unwraps most CDATA sections into `CData` nodes, but `<title>` and
`<textarea>` are RCDATA elements in HTML, so their contents are handed back as
raw text — which is why feed titles arrive looking like
`<![CDATA[Trust, but benchmark...]]>`.

`clean_feed_text` normalizes all of that into plain text: no CDATA wrappers, no
markup, entities resolved, control characters dropped.
"""

import html
import re

_CDATA_RE = re.compile(r"<!\[CDATA\[(.*?)\]\]>", re.DOTALL)
_UNTERMINATED_CDATA_RE = re.compile(r"<!\[CDATA\[(.*)\Z", re.DOTALL)
# Only things that actually look like markup — a bare "I <3 you > 5" in prose
# must survive intact.
_TAG_RE = re.compile(r"<\s*/?[a-zA-Z][^>]*>|<\s*[!?][^>]*>", re.DOTALL)
_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
# Everything below 0x20 except tab/newline/carriage-return, plus the C1 block.
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")
_HORIZONTAL_WS_RE = re.compile(r"[^\S\n]+")
_BLANK_LINES_RE = re.compile(r"\n{3,}")

# Entities/markup can be nested a level or two deep in sloppy feeds
# (e.g. CDATA containing escaped markup). Bounded so a pathological
# input can't spin here.
_MAX_PASSES = 3


def strip_cdata(text: str) -> str:
    """Replace every `<![CDATA[...]]>` section with its literal contents."""
    if "<![CDATA[" not in text:
        return text
    text = _CDATA_RE.sub(lambda m: m.group(1), text)
    # Truncated feed or a section split across parser nodes: keep the payload.
    return _UNTERMINATED_CDATA_RE.sub(lambda m: m.group(1), text)


def clean_feed_text(value: str | None, *, strip_html: bool = True) -> str:
    """Normalize a raw text value from feed XML into safe, markup-free text.

    Unwraps CDATA, resolves HTML entities, and (by default) strips any markup
    left behind, collapsing whitespace. Set `strip_html=False` for values that
    are not prose — URLs, GUIDs, dates — where `<` cannot legitimately appear
    and angle brackets should be preserved verbatim.
    """
    if not value or not isinstance(value, str):
        return ""

    text = value
    for _ in range(_MAX_PASSES):
        before = text
        text = strip_cdata(text)
        if strip_html:
            text = _COMMENT_RE.sub(" ", text)
            text = _TAG_RE.sub(" ", text)
        text = html.unescape(text)
        if text == before:
            break

    # One last pass: unescaping can reveal markup that was double-encoded.
    if strip_html:
        text = strip_cdata(text)
        text = _COMMENT_RE.sub(" ", text)
        text = _TAG_RE.sub(" ", text)

    text = _CONTROL_RE.sub("", text)
    # Collapse runs of spaces/tabs but keep line breaks — podcast descriptions
    # use them for structure.
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _HORIZONTAL_WS_RE.sub(" ", text)
    text = _BLANK_LINES_RE.sub("\n\n", text)
    return "\n".join(line.strip() for line in text.split("\n")).strip()
