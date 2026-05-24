"""Unit tests for Hermes-first summarization + eval un-blinding.

Pure unit tests — no live backend/ES/SSH. Everything external (Hermes SSH, Haiku,
the Sonnet judge) is monkeypatched, so these run anywhere.
"""

from __future__ import annotations

import pytest

from backend.app.config import settings
from backend.app.services import summarizer, summary_eval


def _ap(value):
    """Make an async function that ignores args and returns `value`."""
    async def _f(*args, **kwargs):
        return value
    return _f


# ---- summarize_content: Hermes-first with Haiku fallback --------------------


@pytest.mark.asyncio
async def test_hermes_used_when_it_succeeds(monkeypatch):
    monkeypatch.setattr(settings, "hermes_enabled", True)
    monkeypatch.setattr(settings, "anthropic_api_key", "x")
    monkeypatch.setattr("backend.app.services.hermes_client.run_oneshot",
                        _ap("## Summary\nHermes wins\n- a\n- b"))

    haiku_called = False
    async def _haiku(prompt, title=""):
        nonlocal haiku_called
        haiku_called = True
        return "HAIKU"
    monkeypatch.setattr(summarizer, "summarize_via_haiku", _haiku)

    out = await summarizer.summarize_content(
        title="T", content_type="article", transcript_text="body text here")

    assert out == "Hermes wins\n- a\n- b"   # postprocessed: "## Summary\n" stripped
    assert haiku_called is False            # Hermes win short-circuits Haiku


@pytest.mark.asyncio
async def test_falls_back_to_haiku_when_hermes_returns_none(monkeypatch):
    monkeypatch.setattr(settings, "hermes_enabled", True)
    monkeypatch.setattr(settings, "anthropic_api_key", "x")

    hermes_called = False
    async def _hermes(prompt, model=None):
        nonlocal hermes_called
        hermes_called = True
        return None
    monkeypatch.setattr("backend.app.services.hermes_client.run_oneshot", _hermes)
    monkeypatch.setattr(summarizer, "summarize_via_haiku", _ap("HAIKU OUTPUT"))

    out = await summarizer.summarize_content(
        title="T", content_type="article", transcript_text="body text here")

    assert out == "HAIKU OUTPUT"
    assert hermes_called is True            # Hermes was tried first


@pytest.mark.asyncio
async def test_hermes_skipped_when_disabled(monkeypatch):
    monkeypatch.setattr(settings, "hermes_enabled", False)
    monkeypatch.setattr(settings, "anthropic_api_key", "x")

    hermes_called = False
    async def _hermes(prompt, model=None):
        nonlocal hermes_called
        hermes_called = True
        return "should not be used"
    monkeypatch.setattr("backend.app.services.hermes_client.run_oneshot", _hermes)
    monkeypatch.setattr(summarizer, "summarize_via_haiku", _ap("HAIKU"))

    out = await summarizer.summarize_content(
        title="T", content_type="article", transcript_text="body text here")

    assert out == "HAIKU"
    assert hermes_called is False           # disabled → never call Hermes


# ---- summary_eval: A/B un-blinding maps winner to the right engine ----------


@pytest.mark.asyncio
@pytest.mark.parametrize("rand_value,expected_winner", [
    (0.9, "haiku"),    # swap=False -> A=haiku;  judge says "A" -> haiku
    (0.1, "hermes"),   # swap=True  -> A=hermes; judge says "A" -> hermes
])
async def test_judge_unblinding(monkeypatch, rand_value, expected_winner):
    monkeypatch.setattr(settings, "hermes_model", "gpt-5.4-mini")
    monkeypatch.setattr(summary_eval, "summarize_via_haiku", _ap("haiku summary\n- x"))
    monkeypatch.setattr(summary_eval, "run_oneshot", _ap("hermes summary\n- y"))
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
