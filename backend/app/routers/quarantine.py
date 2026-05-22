"""Transcript retrieval + quarantine endpoints for trusted automation.

These endpoints let an external trusted caller (aitube-sync, Hermes/Rex
transcript judge, n8n) inspect the first ~5 minutes of transcript of an
ingested content item and, if necessary, quarantine it: remove it from
Dave's active timeline without adding watch-time pollution, and persist
an audit trail.

Auth: bearer token from settings.automation_token. The judgement layer
itself (deciding *what* is bad) lives outside AI Tube.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field

from backend.app.config import settings
from backend.app.services import content_cache
from backend.app.services.elasticsearch import (
    CONTENT_ITEMS_INDEX,
    QUARANTINE_EVENTS_INDEX,
    get_es_client,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/content", tags=["quarantine"])


# ---- auth -------------------------------------------------------------------


def require_automation_token(authorization: str | None = Header(default=None)) -> str:
    """Validate `Authorization: Bearer <token>` against settings.automation_token."""
    if not settings.automation_token:
        # Fail closed: if no token is configured, the endpoint is disabled.
        raise HTTPException(
            status_code=503,
            detail="automation_token not configured on this server",
        )
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")
    token = authorization.split(" ", 1)[1].strip()
    if token != settings.automation_token:
        raise HTTPException(status_code=401, detail="invalid bearer token")
    return token


# ---- response models --------------------------------------------------------


class TranscriptSegment(BaseModel):
    start_seconds: float
    end_seconds: float
    text: str


class TranscriptResponse(BaseModel):
    ok: bool = True
    content_item_id: str
    external_id: str | None = None
    title: str | None = None
    author: str | None = None
    transcript_available: bool
    max_seconds: float
    transcript: str = ""
    segments: list[TranscriptSegment] = []
    # Reconciliation/state fields aitube-sync/Hermes need
    viewed: bool = False
    consumed: bool = False
    user_interest: str | None = None
    quarantined: bool = False
    quarantine_reason_code: str | None = None
    quarantine_source: str | None = None


class TranscriptNotReadyResponse(BaseModel):
    ok: bool = False
    error: str = "transcript_not_ready"
    content_item_id: str
    external_id: str | None = None
    title: str | None = None


class QuarantineRequest(BaseModel):
    reason_code: str = Field(min_length=1, max_length=128)
    reason: str = Field(default="", max_length=2000)
    source: str = Field(min_length=1, max_length=128)
    mark_viewed: bool = True
    watch_seconds: float = 0.0  # accepted for API symmetry; never written to playback


class QuarantineResponse(BaseModel):
    ok: bool = True
    content_item_id: str
    external_id: str | None = None
    quarantined: bool = True
    already_quarantined: bool = False
    removed_from_timeline: bool = True
    viewed: bool = True
    consumed: bool = True
    watch_seconds: float = 0.0
    reason_code: str
    reason: str | None = None
    source: str | None = None
    quarantined_at: str | None = None


# ---- helpers ----------------------------------------------------------------


async def _resolve_by_external_id(external_id: str) -> tuple[str, dict[str, Any]]:
    """Return (content_item_id, source_doc) for the given external_id, or 404."""
    es = get_es_client()
    resp = await es.search(
        index=CONTENT_ITEMS_INDEX,
        body={
            "query": {"term": {"external_id": external_id}},
            "size": 1,
        },
    )
    hits = resp["hits"]["hits"]
    if not hits:
        raise HTTPException(
            status_code=404,
            detail=f"Content with external_id '{external_id}' not found",
        )
    return hits[0]["_id"], hits[0]["_source"]


async def _resolve_by_id(item_id: str) -> dict[str, Any]:
    es = get_es_client()
    try:
        resp = await es.get(index=CONTENT_ITEMS_INDEX, id=item_id)
    except Exception:
        raise HTTPException(status_code=404, detail="Content item not found")
    return resp["_source"]


def _build_transcript_response(
    *,
    item_id: str,
    source: dict[str, Any],
    max_seconds: float,
) -> TranscriptResponse | TranscriptNotReadyResponse:
    external_id = source.get("external_id")
    title = source.get("title")
    author = (source.get("metadata") or {}).get("author")

    transcript_obj = source.get("transcript")
    has_transcript = (
        isinstance(transcript_obj, dict)
        and (transcript_obj.get("text") or transcript_obj.get("chunks"))
    )
    if not has_transcript:
        return TranscriptNotReadyResponse(
            content_item_id=item_id,
            external_id=external_id,
            title=title,
        )

    chunks = transcript_obj.get("chunks") or []
    segments: list[TranscriptSegment] = []
    pieces: list[str] = []
    for c in chunks:
        start = float(c.get("start", 0) or 0)
        end = float(c.get("end", start) or start)
        if start > max_seconds:
            break
        text = (c.get("text") or "").strip()
        if not text:
            continue
        segments.append(TranscriptSegment(start_seconds=start, end_seconds=end, text=text))
        pieces.append(text)

    if pieces:
        transcript_text = " ".join(pieces)
    else:
        # Fall back to raw text (no chunk boundaries available). Approximate
        # the requested window by characters: ~15 chars/sec is a rough rate
        # for spoken English.
        full = transcript_obj.get("text", "") or ""
        approx_chars = max(int(max_seconds * 15), 500)
        transcript_text = full[:approx_chars]

    return TranscriptResponse(
        content_item_id=item_id,
        external_id=external_id,
        title=title,
        author=author,
        transcript_available=True,
        max_seconds=max_seconds,
        transcript=transcript_text,
        segments=segments,
        viewed=bool(source.get("viewed", False)),
        consumed=bool(source.get("consumed", False)),
        user_interest=source.get("user_interest"),
        quarantined=bool(source.get("quarantined_at")),
        quarantine_reason_code=source.get("quarantine_reason_code"),
        quarantine_source=source.get("quarantine_source"),
    )


async def _do_quarantine(
    *,
    item_id: str,
    source: dict[str, Any],
    body: QuarantineRequest,
) -> QuarantineResponse:
    es = get_es_client()
    external_id = source.get("external_id")

    # Idempotency: if quarantined_at is already set, don't write another event.
    if source.get("quarantined_at"):
        return QuarantineResponse(
            content_item_id=item_id,
            external_id=external_id,
            quarantined=True,
            already_quarantined=True,
            removed_from_timeline=bool(source.get("consumed", False)),
            viewed=bool(source.get("viewed", False)),
            consumed=bool(source.get("consumed", False)),
            watch_seconds=0.0,
            reason_code=source.get("quarantine_reason_code") or body.reason_code,
            reason=source.get("quarantine_reason"),
            source=source.get("quarantine_source"),
            quarantined_at=source.get("quarantined_at"),
        )

    now = datetime.now(timezone.utc).isoformat()

    # Optional excerpt hash for the audit row (first ~5 min of transcript text).
    excerpt_hash: str | None = None
    t = source.get("transcript")
    if isinstance(t, dict):
        excerpt_source: str | None = None
        chunks = t.get("chunks") or []
        if chunks:
            pieces = [
                (c.get("text") or "").strip()
                for c in chunks
                if float(c.get("start", 0) or 0) <= 300.0
            ]
            excerpt_source = " ".join(p for p in pieces if p) or None
        if not excerpt_source:
            excerpt_source = (t.get("text") or "")[:4500] or None
        if excerpt_source:
            excerpt_hash = hashlib.sha256(excerpt_source.encode("utf-8")).hexdigest()

    # Update the content item: mark consumed + viewed (removes from timeline),
    # and persist the quarantine metadata. Note: we deliberately do NOT touch
    # user_interest — thumbs-down would pollute Dave's preference model.
    doc_update = {
        "consumed": True,
        "viewed": True if body.mark_viewed else bool(source.get("viewed", False)),
        "quarantined_at": now,
        "quarantine_reason_code": body.reason_code,
        "quarantine_reason": body.reason or "",
        "quarantine_source": body.source,
    }
    await es.update(
        index=CONTENT_ITEMS_INDEX, id=item_id, doc=doc_update, refresh="wait_for",
    )

    # Append audit row. The events index has its own _id; calling quarantine
    # again on an already-quarantined item returns early above, so we don't
    # create duplicate audit entries.
    event_doc = {
        "content_item_id": item_id,
        "external_id": external_id,
        "source": body.source,
        "reason_code": body.reason_code,
        "reason": body.reason or "",
        "transcript_excerpt_sha256": excerpt_hash,
        "created_at": now,
    }
    await es.index(
        index=QUARANTINE_EVENTS_INDEX,
        id=str(uuid.uuid4()),
        document=event_doc,
    )

    content_cache.invalidate()
    logger.info(
        "Quarantined content_item_id=%s external_id=%s source=%s reason_code=%s",
        item_id, external_id, body.source, body.reason_code,
    )

    return QuarantineResponse(
        content_item_id=item_id,
        external_id=external_id,
        quarantined=True,
        already_quarantined=False,
        removed_from_timeline=True,
        viewed=True if body.mark_viewed else bool(source.get("viewed", False)),
        consumed=True,
        watch_seconds=0.0,
        reason_code=body.reason_code,
        reason=body.reason or "",
        source=body.source,
        quarantined_at=now,
    )


# ---- endpoints --------------------------------------------------------------


@router.get(
    "/{item_id}/transcript/",
    response_model=None,
    dependencies=[Depends(require_automation_token)],
)
async def get_transcript(
    item_id: str,
    max_seconds: float = Query(default=300.0, gt=0, le=86400),
) -> TranscriptResponse | TranscriptNotReadyResponse:
    source = await _resolve_by_id(item_id)
    return _build_transcript_response(
        item_id=item_id, source=source, max_seconds=max_seconds,
    )


@router.get(
    "/by-external-id/{external_id}/transcript/",
    response_model=None,
    dependencies=[Depends(require_automation_token)],
)
async def get_transcript_by_external_id(
    external_id: str,
    max_seconds: float = Query(default=300.0, gt=0, le=86400),
) -> TranscriptResponse | TranscriptNotReadyResponse:
    item_id, source = await _resolve_by_external_id(external_id)
    return _build_transcript_response(
        item_id=item_id, source=source, max_seconds=max_seconds,
    )


@router.post(
    "/{item_id}/quarantine/",
    response_model=QuarantineResponse,
    dependencies=[Depends(require_automation_token)],
)
async def quarantine_item(item_id: str, body: QuarantineRequest) -> QuarantineResponse:
    source = await _resolve_by_id(item_id)
    return await _do_quarantine(item_id=item_id, source=source, body=body)


@router.post(
    "/by-external-id/{external_id}/quarantine/",
    response_model=QuarantineResponse,
    dependencies=[Depends(require_automation_token)],
)
async def quarantine_by_external_id(
    external_id: str, body: QuarantineRequest,
) -> QuarantineResponse:
    item_id, source = await _resolve_by_external_id(external_id)
    return await _do_quarantine(item_id=item_id, source=source, body=body)
