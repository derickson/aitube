"""Unit tests for feed text sanitization (CDATA, markup, entities).

Pure unit tests — no network or ES involved.
"""

from __future__ import annotations

import pytest
from bs4 import BeautifulSoup

from backend.app.services.feed_poller import _parse_rss_feed_entry
from backend.app.services.feed_text import clean_feed_text, strip_cdata


@pytest.mark.parametrize("raw,expected", [
    # The bug: <title> is RCDATA for the HTML parser, so CDATA arrives verbatim.
    (
        "<![CDATA[Trust, but benchmark: How we let an AI agent optimize Elasticsearch]]>",
        "Trust, but benchmark: How we let an AI agent optimize Elasticsearch",
    ),
    ("<![CDATA[one]]> and <![CDATA[two]]>", "one and two"),
    ("<![CDATA[truncated section", "truncated section"),  # malformed / split feed
    ("<![CDATA[AT&T + Q&amp;A]]>", "AT&T + Q&A"),
    ("Plain title", "Plain title"),
    (None, ""),
    ("", ""),
])
def test_cdata_is_unwrapped(raw, expected):
    assert clean_feed_text(raw) == expected


@pytest.mark.parametrize("raw,expected", [
    ("<p>Some <b>bold</b> text</p>", "Some bold text"),
    ("&lt;p&gt;escaped markup&lt;/p&gt;", "escaped markup"),
    ("<![CDATA[<p>markup in cdata</p>]]>", "markup in cdata"),
    ("before<!-- a comment -->after", "before after"),
    ("System Mastery 324 &#8211; Zenescope&#8217;s", "System Mastery 324 – Zenescope’s"),
    ("control\x00chars\x07gone", "controlcharsgone"),
])
def test_markup_and_entities_are_removed(raw, expected):
    assert clean_feed_text(raw) == expected


def test_line_breaks_are_preserved_but_collapsed():
    assert clean_feed_text("Episode 1\n\n\n\nMain   topic:  stuff") == "Episode 1\n\nMain topic: stuff"


def test_strip_html_false_keeps_angle_brackets():
    assert clean_feed_text("<![CDATA[a < b > c]]>", strip_html=False) == "a < b > c"


def test_clean_feed_text_is_idempotent():
    once = clean_feed_text("<![CDATA[<p>Hello &amp; welcome</p>]]>")
    assert clean_feed_text(once) == once


def test_strip_cdata_leaves_plain_text_untouched():
    assert strip_cdata("no cdata here") == "no cdata here"


RSS_ITEM = """
<rss><channel><item>
  <title><![CDATA[Trust, but benchmark: How we let an AI agent optimize Elasticsearch]]></title>
  <description><![CDATA[<img src="https://example.com/cover.png" />We built a <b>harness</b> &amp; ran it.]]></description>
  <guid>https://example.com/blog/post-1</guid>
  <pubDate>Fri, 11 Sep 2026 00:00:00 GMT</pubDate>
</item></channel></rss>
"""


def test_rss_entry_is_free_of_feed_markup():
    soup = BeautifulSoup(RSS_ITEM, "html.parser")
    parsed = _parse_rss_feed_entry(soup.find("item"), "https://example.com/feed")

    assert parsed["title"] == "Trust, but benchmark: How we let an AI agent optimize Elasticsearch"
    assert parsed["description"] == "We built a harness & ran it."
    # The <img> inside the CDATA is still usable as a thumbnail fallback
    assert parsed["thumbnail_url"] == "https://example.com/cover.png"
    for value in (parsed["title"], parsed["description"]):
        assert "CDATA" not in value and "<" not in value


def test_rss_external_id_hashes_the_raw_guid():
    """external_id is the dedup key for already-ingested items — it must not
    shift just because the text cleaner changed."""
    import hashlib

    soup = BeautifulSoup(RSS_ITEM, "html.parser")
    parsed = _parse_rss_feed_entry(soup.find("item"), "https://example.com/feed")
    expected = hashlib.md5(b"https://example.com/blog/post-1").hexdigest()[:12]
    assert parsed["content_id"] == f"rss_{expected}"
