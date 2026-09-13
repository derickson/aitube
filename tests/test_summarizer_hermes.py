"""Unit tests for Hermes-first summarization + eval un-blinding.

Pure unit tests — no live backend/ES/SSH. Everything external (Hermes SSH, Haiku,
the Sonnet judge) is monkeypatched, so these run anywhere.
"""

from __future__ import annotations

import pytest

from backend.app.config import settings
from backend.app.services import summarizer, summary_eval
from backend.app.services.summary_errors import SummaryErrorCode

# summarize_content's pre-flight gate skips every engine below MIN_SOURCE_CHARS (200);
# these tests need real transcript text so the mocked engines actually get exercised.
LONG_TEXT = "body text here. " * 20  # 340 chars


def _ap(value):
    """Make an async function that ignores args and returns `value`."""
    async def _f(*args, **kwargs):
        return value
    return _f


class _FakeMessage:
    def __init__(self, text: str):
        self.content = [type("Block", (), {"text": text})()]


# ---- summarize_via_haiku: validates its own output, not just Hermes's -------


@pytest.mark.asyncio
async def test_haiku_rejects_its_own_refusal(monkeypatch):
    """A refusal is fluent English over 200 chars, so only phrase-matching (not
    length) catches it — and Haiku's raw output wasn't checked at all before."""
    refusal = (
        "I appreciate you sharing this, but I'm unable to summarize the video "
        "because you've only provided the title, creator name, and description—"
        "not the actual video content itself, and there is nothing further to go on."
    )
    monkeypatch.setattr(settings, "anthropic_api_key", "x")
    monkeypatch.setattr(
        summarizer, "traced_messages_create", lambda client, **kw: _FakeMessage(refusal)
    )

    summary, error, code = await summarizer.summarize_via_haiku("prompt", "T")

    assert summary is None
    assert code == SummaryErrorCode.INSUFFICIENT_CONTENT


@pytest.mark.asyncio
async def test_haiku_accepts_a_real_summary(monkeypatch):
    real = "A" * 250
    monkeypatch.setattr(settings, "anthropic_api_key", "x")
    monkeypatch.setattr(
        summarizer, "traced_messages_create", lambda client, **kw: _FakeMessage(real)
    )

    summary, error, code = await summarizer.summarize_via_haiku("prompt", "T")

    assert summary == real
    assert error is None
    assert code is None


# ---- summarize_content: Hermes-first with Haiku fallback --------------------


@pytest.mark.asyncio
async def test_hermes_used_when_it_succeeds(monkeypatch):
    monkeypatch.setattr(settings, "hermes_enabled", True)
    monkeypatch.setattr(settings, "anthropic_api_key", "x")
    monkeypatch.setattr("backend.app.services.hermes_client.run_oneshot",
                        _ap(("## Summary\nHermes wins\n- a\n- b", None, None)))

    haiku_called = False
    async def _haiku(prompt, title=""):
        nonlocal haiku_called
        haiku_called = True
        return "HAIKU", None, None
    monkeypatch.setattr(summarizer, "summarize_via_haiku", _haiku)

    out, err, code = await summarizer.summarize_content(
        title="T", content_type="article", transcript_text=LONG_TEXT)

    assert out == "Hermes wins\n- a\n- b"   # postprocessed: "## Summary\n" stripped
    assert err is None
    assert code is None
    assert haiku_called is False            # Hermes win short-circuits Haiku


@pytest.mark.asyncio
async def test_falls_back_to_haiku_when_hermes_returns_none(monkeypatch):
    monkeypatch.setattr(settings, "hermes_enabled", True)
    monkeypatch.setattr(settings, "anthropic_api_key", "x")

    hermes_called = False
    async def _hermes(prompt, model=None):
        nonlocal hermes_called
        hermes_called = True
        return (
            None,
            "HTTP 404: The model gpt-5.4-mini does not exist or you do not have access to it.",
            SummaryErrorCode.UPSTREAM_HTTP_ERROR,
        )
    monkeypatch.setattr("backend.app.services.hermes_client.run_oneshot", _hermes)
    monkeypatch.setattr(summarizer, "summarize_via_haiku", _ap(("HAIKU OUTPUT", None, None)))

    out, err, code = await summarizer.summarize_content(
        title="T", content_type="article", transcript_text=LONG_TEXT)

    assert out == "HAIKU OUTPUT"
    assert err is None                      # Haiku recovered, so no error is surfaced
    assert code is None
    assert hermes_called is True            # Hermes was tried first


@pytest.mark.asyncio
async def test_error_surfaced_when_both_engines_fail(monkeypatch):
    monkeypatch.setattr(settings, "hermes_enabled", True)
    monkeypatch.setattr(settings, "anthropic_api_key", "x")

    hermes_error = "HTTP 404: The model gpt-5.4-mini does not exist or you do not have access to it."
    monkeypatch.setattr(
        "backend.app.services.hermes_client.run_oneshot",
        _ap((None, hermes_error, SummaryErrorCode.UPSTREAM_HTTP_ERROR)),
    )
    monkeypatch.setattr(
        summarizer, "summarize_via_haiku",
        _ap((None, "haiku exploded", SummaryErrorCode.UNKNOWN_ERROR)),
    )

    out, err, code = await summarizer.summarize_content(
        title="T", content_type="article", transcript_text=LONG_TEXT)

    assert out is None
    assert err == "haiku exploded"          # Haiku ran last — its error is the proximate cause
    assert code == SummaryErrorCode.UNKNOWN_ERROR


@pytest.mark.asyncio
async def test_hermes_skipped_when_disabled(monkeypatch):
    monkeypatch.setattr(settings, "hermes_enabled", False)
    monkeypatch.setattr(settings, "anthropic_api_key", "x")

    hermes_called = False
    async def _hermes(prompt, model=None):
        nonlocal hermes_called
        hermes_called = True
        return "should not be used", None, None
    monkeypatch.setattr("backend.app.services.hermes_client.run_oneshot", _hermes)
    monkeypatch.setattr(summarizer, "summarize_via_haiku", _ap(("HAIKU", None, None)))

    out, err, code = await summarizer.summarize_content(
        title="T", content_type="article", transcript_text=LONG_TEXT)

    assert out == "HAIKU"
    assert err is None
    assert code is None
    assert hermes_called is False           # disabled → never call Hermes


# ---- summarize_content: pre-flight content-sufficiency gate -----------------


@pytest.mark.asyncio
async def test_insufficient_content_skips_every_engine(monkeypatch):
    """A video whose transcript never arrived, left with just a one-line description,
    must not reach any LLM — that would waste tokens on a request that can only
    produce a refusal or a hallucinated non-summary."""
    monkeypatch.setattr(settings, "hermes_enabled", True)
    monkeypatch.setattr(settings, "anthropic_api_key", "x")

    called = False
    async def _should_not_be_called(*args, **kwargs):
        nonlocal called
        called = True
        return "should not happen", None, None
    monkeypatch.setattr("backend.app.services.hermes_client.run_oneshot", _should_not_be_called)
    monkeypatch.setattr(summarizer, "summarize_via_haiku", _should_not_be_called)

    out, err, code = await summarizer.summarize_content(
        title="T", content_type="video", transcript_text="",
        description="there are a lot of terms that the D&D community has made")

    assert out is None
    assert err is None
    assert code == SummaryErrorCode.INSUFFICIENT_CONTENT
    assert called is False


@pytest.mark.asyncio
async def test_sufficient_description_alone_still_reaches_an_engine(monkeypatch):
    monkeypatch.setattr(settings, "hermes_enabled", False)
    monkeypatch.setattr(settings, "anthropic_api_key", "x")
    monkeypatch.setattr(summarizer, "summarize_via_haiku", _ap(("HAIKU", None, None)))

    out, err, code = await summarizer.summarize_content(
        title="T", content_type="article", transcript_text="",
        description="x" * 250)

    assert out == "HAIKU"
    assert err is None
    assert code is None


# ---- summary_eval: A/B un-blinding maps winner to the right engine ----------


@pytest.mark.asyncio
@pytest.mark.parametrize("rand_value,expected_winner", [
    (0.9, "haiku"),    # swap=False -> A=haiku;  judge says "A" -> haiku
    (0.1, "hermes"),   # swap=True  -> A=hermes; judge says "A" -> hermes
])
async def test_judge_unblinding(monkeypatch, rand_value, expected_winner):
    monkeypatch.setattr(settings, "hermes_model", "gpt-5.4-mini")
    monkeypatch.setattr(summary_eval, "summarize_via_haiku", _ap(("haiku summary\n- x", None, None)))
    monkeypatch.setattr(summary_eval, "run_oneshot", _ap(("hermes summary\n- y", None, None)))
    monkeypatch.setattr(summary_eval.random, "random", lambda: rand_value)
    # Judge always picks position "A"; un-blinding must resolve it to the right engine.
    monkeypatch.setattr(summary_eval, "_judge", _ap({
        "winner": "A",
        "scores": {"A": {"faithfulness": 5}, "B": {"faithfulness": 3}},
        "rationale": "A is better",
    }))

    rec = await summary_eval.compare_engines(
        {"type": "article", "title": "T", "content_markdown": "some source text"})

    assert rec["judge"]["winner"] == expected_winner
    # The winning engine got A's score (5), not B's (3).
    assert rec["judge"]["scores"][expected_winner]["faithfulness"] == 5
