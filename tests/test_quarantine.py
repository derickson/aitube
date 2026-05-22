"""Integration tests for the transcript + quarantine endpoints.

Requires:
  - Backend running on localhost:3103
  - Elasticsearch accessible and indices created

Perimeter auth is enforced by nginx basic-auth in production, not the
FastAPI layer, so these tests hit the backend directly without auth
headers — matching the production posture once past the proxy.

Tests seed fixture content items directly into Elasticsearch so we don't
have to rely on real YouTube ingestion. Each test cleans up its own
fixture doc and any audit events on teardown.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone

import httpx
import pytest


BASE_URL = "http://localhost:3103"
ES_URL = os.environ.get("ELASTICSEARCH_URL", "http://localhost:9200")
ES_API_KEY = os.environ.get("ELASTICSEARCH_API_KEY", "")

CONTENT_INDEX = os.environ.get("CONTENT_ITEMS_INDEX", "aitube-content-items")
EVENTS_INDEX = "aitube-quarantine-events"


# ---- fixtures ---------------------------------------------------------------


def _es_headers() -> dict[str, str]:
    h = {"Content-Type": "application/json"}
    if ES_API_KEY:
        h["Authorization"] = f"ApiKey {ES_API_KEY}"
    return h


@pytest.fixture()
def es():
    with httpx.Client(base_url=ES_URL, headers=_es_headers(), timeout=30) as c:
        yield c


def _make_doc(
    *,
    external_id: str,
    title: str = "Test video",
    transcript_text: str | None = "hello world",
    chunks: list[dict] | None = None,
    consumed: bool = False,
    viewed: bool = False,
    quarantined_at: str | None = None,
) -> dict:
    doc: dict = {
        "subscription_id": "test_subscription",
        "external_id": external_id,
        "type": "video",
        "title": title,
        "url": f"https://www.youtube.com/watch?v={external_id.removeprefix('yt_')}",
        "published_at": datetime.now(timezone.utc).isoformat(),
        "discovered_at": datetime.now(timezone.utc).isoformat(),
        "duration_seconds": 600,
        "thumbnail_url": "",
        "summary": "",
        "consumed": consumed,
        "viewed": viewed,
        "content_markdown": "",
        "content_dlp_cache_id": "",
        "metadata": {"author": "Test Channel", "description": "", "tags": [], "extras": {}},
    }
    if transcript_text is not None or chunks is not None:
        doc["transcript"] = {
            "text": transcript_text or "",
            "chunks": chunks or [],
        }
    if quarantined_at:
        doc["quarantined_at"] = quarantined_at
    return doc


@pytest.fixture()
def seeded(es: httpx.Client, request):
    """Yields a callable that indexes a fixture doc and tracks cleanup."""
    created: list[tuple[str, str]] = []  # (index, id)
    external_ids: list[str] = []

    def _seed(**kwargs) -> tuple[str, str]:
        doc_id = str(uuid.uuid4())
        ext = kwargs.get("external_id") or f"yt_test_{uuid.uuid4().hex[:8]}"
        kwargs["external_id"] = ext
        body = _make_doc(**kwargs)
        resp = es.put(f"/{CONTENT_INDEX}/_doc/{doc_id}?refresh=true", json=body)
        resp.raise_for_status()
        created.append((CONTENT_INDEX, doc_id))
        external_ids.append(ext)
        return doc_id, ext

    yield _seed

    # teardown — delete docs and any audit events tied to their external_ids
    for idx, did in created:
        try:
            es.delete(f"/{idx}/_doc/{did}?refresh=true")
        except Exception:
            pass
    if external_ids:
        try:
            es.post(
                f"/{EVENTS_INDEX}/_delete_by_query?refresh=true",
                json={"query": {"terms": {"external_id": external_ids}}},
            )
        except Exception:
            pass


# ---- tests ------------------------------------------------------------------


def test_transcript_available_returns_first_window(seeded):
    chunks = [
        {"text": "hello there.", "start": 0.0, "end": 2.5},
        {"text": "today we talk about AI.", "start": 2.5, "end": 7.0},
        {"text": "now into part two.", "start": 320.0, "end": 322.0},  # past 300s
    ]
    item_id, ext = seeded(
        external_id=f"yt_avail_{uuid.uuid4().hex[:6]}",
        transcript_text="hello there. today we talk about AI. now into part two.",
        chunks=chunks,
    )
    with httpx.Client(base_url=BASE_URL, timeout=30) as api:
        resp = api.get(
            f"/api/content/{item_id}/transcript/",
            params={"max_seconds": 300},
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True
    assert body["transcript_available"] is True
    assert body["content_item_id"] == item_id
    assert body["external_id"] == ext
    assert body["title"] == "Test video"
    assert body["author"] == "Test Channel"
    # 300s cap excludes the third chunk
    assert len(body["segments"]) == 2
    assert "now into part two" not in body["transcript"]
    assert "today we talk about AI" in body["transcript"]


def test_transcript_not_ready_returns_error_payload(seeded):
    item_id, _ = seeded(
        external_id=f"yt_notready_{uuid.uuid4().hex[:6]}",
        transcript_text=None,
        chunks=None,
    )
    with httpx.Client(base_url=BASE_URL, timeout=30) as api:
        resp = api.get(
            f"/api/content/{item_id}/transcript/",
        )
    # 200 with ok=false (clear error payload, not a 500)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is False
    assert body["error"] == "transcript_not_ready"
    assert body["content_item_id"] == item_id


def test_transcript_by_external_id(seeded):
    chunks = [{"text": "intro.", "start": 0.0, "end": 1.0}]
    _, ext = seeded(
        external_id=f"yt_byext_{uuid.uuid4().hex[:6]}",
        transcript_text="intro.",
        chunks=chunks,
    )
    with httpx.Client(base_url=BASE_URL, timeout=30) as api:
        resp = api.get(
            f"/api/content/by-external-id/{ext}/transcript/",
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["external_id"] == ext
    assert body["transcript_available"] is True


def test_quarantine_removes_item_from_timeline(seeded, es):
    item_id, ext = seeded(external_id=f"yt_quar_{uuid.uuid4().hex[:6]}")

    # Sanity check: appears in watchlist before quarantine
    with httpx.Client(base_url=BASE_URL, timeout=30) as api:
        wl_before = api.get("/api/watchlist/", params={"size": 200}).json()
    assert any(i["id"] == item_id for i in wl_before), \
        "fixture should appear in watchlist before quarantine"

    with httpx.Client(base_url=BASE_URL, timeout=30) as api:
        resp = api.post(
            f"/api/content/{item_id}/quarantine/",
            json={
                "reason_code": "business_funnel_ai_content",
                "reason": "First five minutes are an AI agency funnel.",
                "source": "hermes_aitube_transcript_judge",
                "mark_viewed": True,
                "watch_seconds": 0,
            },
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True
    assert body["quarantined"] is True
    assert body["already_quarantined"] is False
    assert body["removed_from_timeline"] is True
    assert body["viewed"] is True
    assert body["consumed"] is True
    assert body["watch_seconds"] == 0

    # Confirm it's gone from the watchlist
    with httpx.Client(base_url=BASE_URL, timeout=30) as api:
        wl_after = api.get("/api/watchlist/", params={"size": 200}).json()
    assert not any(i["id"] == item_id for i in wl_after), \
        "quarantined item should not appear in watchlist"

    # Confirm interest was NOT downvoted (no preference-model pollution)
    es.post(f"/{CONTENT_INDEX}/_refresh")
    doc = es.get(f"/{CONTENT_INDEX}/_doc/{item_id}").json()["_source"]
    assert doc.get("user_interest") is None
    assert doc["quarantine_reason_code"] == "business_funnel_ai_content"
    assert doc["quarantine_source"] == "hermes_aitube_transcript_judge"
    assert doc.get("quarantined_at")


def test_quarantine_does_not_increment_watch_time(seeded, es):
    item_id, _ = seeded(external_id=f"yt_nowatch_{uuid.uuid4().hex[:6]}")

    with httpx.Client(base_url=BASE_URL, timeout=30) as api:
        api.post(
            f"/api/content/{item_id}/quarantine/",
            json={
                "reason_code": "test_no_watch_time",
                "reason": "",
                "source": "pytest",
                "mark_viewed": True,
                "watch_seconds": 0,
            },
        ).raise_for_status()
        pb = api.get(f"/api/playback/{item_id}/")
    # No playback state should have been written
    assert pb.status_code == 200
    assert pb.json() in (None, {})


def test_quarantine_is_idempotent(seeded, es):
    item_id, ext = seeded(external_id=f"yt_idem_{uuid.uuid4().hex[:6]}")
    body = {
        "reason_code": "test_idempotent",
        "reason": "first call",
        "source": "pytest",
        "mark_viewed": True,
        "watch_seconds": 0,
    }
    with httpx.Client(base_url=BASE_URL, timeout=30) as api:
        r1 = api.post(f"/api/content/{item_id}/quarantine/", json=body)
        r2 = api.post(
            f"/api/content/{item_id}/quarantine/",
            json={**body, "reason": "second call"},
        )
    assert r1.status_code == 200 and r2.status_code == 200
    assert r1.json()["already_quarantined"] is False
    assert r2.json()["already_quarantined"] is True
    # Stored reason must still be the FIRST one (second call is a no-op)
    assert r2.json()["reason"] == "first call"

    # Confirm only ONE audit event was written
    es.post(f"/{EVENTS_INDEX}/_refresh")
    count = es.post(
        f"/{EVENTS_INDEX}/_count",
        json={"query": {"term": {"external_id": ext}}},
    ).json()["count"]
    assert count == 1, f"expected exactly one audit event, got {count}"


def test_quarantine_persists_reason_and_source(seeded, es):
    item_id, ext = seeded(external_id=f"yt_audit_{uuid.uuid4().hex[:6]}")
    with httpx.Client(base_url=BASE_URL, timeout=30) as api:
        api.post(
            f"/api/content/{item_id}/quarantine/",
            json={
                "reason_code": "business_funnel_ai_content",
                "reason": "Get-rich AI agency pitch.",
                "source": "hermes_aitube_transcript_judge",
            },
        ).raise_for_status()

    es.post(f"/{EVENTS_INDEX}/_refresh")
    hits = es.post(
        f"/{EVENTS_INDEX}/_search",
        json={"query": {"term": {"external_id": ext}}},
    ).json()["hits"]["hits"]
    assert len(hits) == 1
    src = hits[0]["_source"]
    assert src["content_item_id"] == item_id
    assert src["external_id"] == ext
    assert src["reason_code"] == "business_funnel_ai_content"
    assert src["reason"] == "Get-rich AI agency pitch."
    assert src["source"] == "hermes_aitube_transcript_judge"
    assert src.get("created_at")



