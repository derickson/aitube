"""Unit tests for Demo Mode scoping and Jev subcategory classification.

Pure unit tests — the taxonomy lookup and the Jev HTTP call are monkeypatched,
so these run anywhere without a backend, ES, or OpenRouter.
"""

from __future__ import annotations

import json

import httpx
import pytest

from backend.app.config import settings
from backend.app.services import content_classifier, demo_mode

TAXONOMY = [
    {
        "slug": "ai_software",
        "label": "AI and Software",
        "description": "AI things",
        "subcategories": [
            {"slug": "voice_ai", "label": "Voice AI", "description": "speech"},
            {"slug": "coding_agents", "label": "Coding Agents", "description": "code"},
        ],
    },
    {"slug": "tabletop_rpg", "label": "Tabletop RPG", "description": "D&D", "subcategories": []},
]


# ---- taxonomy defaults ------------------------------------------------------


def test_missing_subcategories_key_gets_bootstrap_defaults():
    [ai, rpg] = content_classifier._with_subcategories([
        {"slug": "ai_software", "label": "AI and Software", "description": "x"},
        {"slug": "tabletop_rpg", "label": "Tabletop RPG", "description": "y"},
    ])
    assert len(ai["subcategories"]) == len(content_classifier.SUBCATEGORIES["ai_software"])
    assert rpg["subcategories"] == []


def test_explicitly_empty_subcategories_are_kept_empty():
    [ai] = content_classifier._with_subcategories(
        [{"slug": "ai_software", "label": "AI and Software", "description": "x", "subcategories": []}]
    )
    assert ai["subcategories"] == []


def test_payload_asks_one_subcategory_question_per_category_with_subcategories():
    payload = content_classifier._build_payload("t", "d", "s", TAXONOMY)
    assert set(payload["questions"]) == {"category", "subcategory__ai_software"}
    assert set(payload["questions"]["subcategory__ai_software"]["criteria"]) == {"voice_ai", "coding_agents"}


# ---- classify_content -------------------------------------------------------


def _patch_jev(monkeypatch, answers: dict):
    async def fake_categories(force_refresh: bool = False):
        return TAXONOMY

    def handler(request: httpx.Request) -> httpx.Response:
        sent = json.loads(request.content)
        assert "subcategory__ai_software" in sent["questions"]
        return httpx.Response(200, json={"answers": answers})

    real_client = httpx.AsyncClient
    monkeypatch.setattr(settings, "openrouter_api_key", "test-key")
    monkeypatch.setattr(content_classifier, "get_categories", fake_categories)
    monkeypatch.setattr(
        content_classifier.httpx, "AsyncClient",
        lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw),
    )


@pytest.mark.asyncio
async def test_classify_returns_category_and_subcategory_labels(monkeypatch):
    _patch_jev(monkeypatch, {
        "category": {"choice": "ai_software"},
        "subcategory__ai_software": {"choice": "voice_ai"},
    })
    fields, error = await content_classifier.classify_content("Voice agents in prod")
    assert error is None
    assert fields == {"category": "AI and Software", "subcategory": "Voice AI"}


@pytest.mark.asyncio
async def test_classify_clears_subcategory_for_category_without_subcategories(monkeypatch):
    # Jev answers every question, so the AI subcategory answer must be discarded.
    _patch_jev(monkeypatch, {
        "category": {"choice": "tabletop_rpg"},
        "subcategory__ai_software": {"choice": "coding_agents"},
    })
    fields, error = await content_classifier.classify_content("Critical Role ep 12")
    assert error is None
    assert fields == {"category": "Tabletop RPG", "subcategory": None}


@pytest.mark.asyncio
async def test_classify_tolerates_unknown_subcategory_slug(monkeypatch):
    _patch_jev(monkeypatch, {
        "category": {"choice": "ai_software"},
        "subcategory__ai_software": {"choice": "not_a_topic"},
    })
    fields, error = await content_classifier.classify_content("Something AI")
    assert error is None
    assert fields == {"category": "AI and Software", "subcategory": None}


# ---- demo scoping -----------------------------------------------------------


@pytest.mark.asyncio
async def test_demo_filter_is_none_when_off():
    assert await demo_mode.demo_filter(False) is None
    assert await demo_mode.demo_filters(False) == []


@pytest.mark.asyncio
async def test_demo_filter_terms_on_demo_category_label(monkeypatch):
    async def fake_label():
        return "AI and Software"

    monkeypatch.setattr(demo_mode, "demo_category_label", fake_label)
    assert await demo_mode.demo_filter(True) == {"term": {"category": "AI and Software"}}
    assert await demo_mode.in_demo_scope(True, {"category": "AI and Software"})
    assert not await demo_mode.in_demo_scope(True, {"category": "News"})
    assert await demo_mode.in_demo_scope(False, {"category": "News"})


def test_demo_cluster_sizes_drops_mostly_out_of_scope_clusters():
    run_clusters = [{"id": "c00", "size": 400}, {"id": "c01", "size": 65}, {"id": "c02", "size": 10}]
    in_scope = demo_mode.count_by_cluster(["c00"] * 387 + ["c01"] * 2 + ["c02"] * 5 + [None] * 3)
    # c01 (2/65 in scope) would leak its off-topic label; c02 is exactly at the 50% share.
    assert demo_mode.demo_cluster_sizes(run_clusters, in_scope) == {"c00": 387, "c02": 5}
