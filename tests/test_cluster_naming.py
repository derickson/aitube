"""Unit tests for Hermes-driven topic-cluster naming.

Pure unit tests — the only external dependency (Hermes SSH) is monkeypatched,
so these run anywhere without a backend, ES, or SSH.
"""

from __future__ import annotations

import random

import pytest

from backend.app.config import settings
from backend.app.services import clustering


# ---- _clean_title -----------------------------------------------------------


def test_clean_title_truncates_to_five_words():
    assert (
        clustering._clean_title('"Local LLM Tooling and Inference Servers".')
        == "Local LLM Tooling and Inference"
    )


def test_clean_title_strips_bullets_and_quotes():
    assert clustering._clean_title("- 'Home Espresso Gear'") == "Home Espresso Gear"


def test_clean_title_respects_custom_word_cap():
    assert clustering._clean_title("one two three", max_words=2) == "one two"


# ---- _extract_json_object ---------------------------------------------------


def test_extract_json_object_handles_code_fences():
    out = clustering._extract_json_object('here:\n```json\n{"c00": "A", "c01": "B"}\n```')
    assert out == {"c00": "A", "c01": "B"}


def test_extract_json_object_handles_bare_object_with_prose():
    assert clustering._extract_json_object('Sure! {"c00": "A"} hope that helps') == {"c00": "A"}


def test_extract_json_object_returns_none_on_garbage():
    assert clustering._extract_json_object("no json here") is None
    assert clustering._extract_json_object("") is None


# ---- _build_naming_prompt ---------------------------------------------------


def test_build_naming_prompt_includes_keywords_and_samples():
    member_ids = {"c00": ["a", "b"], "c01": ["c"]}
    info = {"c00": {"top_terms": ["ai", "ml"]}, "c01": {"top_terms": ["coffee"]}}
    titles = {"a": "Intro to AI", "b": "ML Basics", "c": "Espresso 101"}
    prompt = clustering._build_naming_prompt(member_ids, info, titles, random.Random(0), 20)
    assert "Cluster c00" in prompt and "ai, ml" in prompt
    assert "Intro to AI" in prompt and "Espresso 101" in prompt
    assert "JSON object" in prompt
    assert "5 words" in prompt


def test_build_naming_prompt_samples_at_most_n_titles():
    member_ids = {"c00": [str(i) for i in range(100)]}
    info = {"c00": {"top_terms": ["x"]}}
    titles = {str(i): f"Title {i}" for i in range(100)}
    prompt = clustering._build_naming_prompt(member_ids, info, titles, random.Random(0), 20)
    bullet_lines = [l for l in prompt.splitlines() if l.startswith("  - Title ")]
    assert len(bullet_lines) == 20


# ---- _name_clusters_via_hermes ----------------------------------------------


@pytest.mark.asyncio
async def test_name_clusters_parses_and_truncates(monkeypatch):
    monkeypatch.setattr(settings, "hermes_enabled", True)

    async def _fake(prompt, **kw):
        return '```json\n{"c00": "Local LLM Tooling", "c01": "Way Too Many Words In This Title"}\n```'

    monkeypatch.setattr("backend.app.services.hermes_client.run_oneshot", _fake)
    names = await clustering._name_clusters_via_hermes(
        {"c00": ["a"], "c01": ["b"]},
        {"c00": {"top_terms": ["ai"]}, "c01": {"top_terms": ["x"]}},
        {"a": "t1", "b": "t2"},
        random.Random(0),
    )
    assert names["c00"] == "Local LLM Tooling"
    assert len(names["c01"].split()) == 5  # truncated to 5 words


@pytest.mark.asyncio
async def test_name_clusters_disabled_returns_empty(monkeypatch):
    monkeypatch.setattr(settings, "hermes_enabled", False)
    names = await clustering._name_clusters_via_hermes(
        {"c00": ["a"]}, {"c00": {"top_terms": ["ai"]}}, {"a": "t"}, random.Random(0),
    )
    assert names == {}


@pytest.mark.asyncio
async def test_name_clusters_falls_back_on_unparseable_output(monkeypatch):
    monkeypatch.setattr(settings, "hermes_enabled", True)

    async def _fake(prompt, **kw):
        return "sorry, I can't help with that"

    monkeypatch.setattr("backend.app.services.hermes_client.run_oneshot", _fake)
    names = await clustering._name_clusters_via_hermes(
        {"c00": ["a"]}, {"c00": {"top_terms": ["ai"]}}, {"a": "t"}, random.Random(0),
    )
    assert names == {}


@pytest.mark.asyncio
async def test_name_clusters_swallows_hermes_exceptions(monkeypatch):
    monkeypatch.setattr(settings, "hermes_enabled", True)

    async def _boom(prompt, **kw):
        raise RuntimeError("ssh exploded")

    monkeypatch.setattr("backend.app.services.hermes_client.run_oneshot", _boom)
    names = await clustering._name_clusters_via_hermes(
        {"c00": ["a"]}, {"c00": {"top_terms": ["ai"]}}, {"a": "t"}, random.Random(0),
    )
    assert names == {}
