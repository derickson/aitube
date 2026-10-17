"""Search explain: shows *why* each result ranked where it did.

Production search (`content.list_content`) is hybrid when semantic search is
enabled: Reciprocal Rank Fusion over three retrievers (BM25 multi_match,
semantic on `semantic_headline`, semantic on `semantic_body`). Elasticsearch
doesn't hand back per-retriever detail from a single RRF call, so this endpoint
runs each retriever on its own, re-fuses them with the same formula
(score = sum over retrievers of 1 / (rank_constant + rank)) and reports every
component. The lexical leg additionally runs with `explain` so BM25 can be
attributed to individual fields and terms.
"""

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, Query
from pydantic import BaseModel

from backend.app.config import settings
from backend.app.models.content import ContentItemSummary
from backend.app.routers.content import build_lexical_query
from backend.app.services.demo_mode import DemoMode, demo_filters
from backend.app.services.elasticsearch import CONTENT_ITEMS_INDEX, get_es_client

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/search-explain", tags=["search-explain"])

RANK_CONSTANT = 60  # must match content.list_content
HL_PRE, HL_POST = "[[hl]]", "[[/hl]]"

_SOURCE_FIELDS = [
    "subscription_id", "external_id", "type", "title", "url",
    "published_at", "discovered_at", "duration_seconds",
    "thumbnail_url", "summary", "summary_error_code", "category", "subcategory",
    "interest_score", "user_interest", "consumed", "viewed", "engagement",
]


class TermContribution(BaseModel):
    term: str
    score: float


class FieldContribution(BaseModel):
    field: str
    boost: float
    score: float
    counted: bool  # best_fields keeps only the top field; the rest are shown for context
    terms: list[TermContribution]


class RetrieverResult(BaseModel):
    name: str  # "lexical" | "semantic_headline" | "semantic_body"
    label: str
    rank: int | None  # 1-based, None if not in this retriever's window
    score: float | None  # the retriever's native score (BM25 / similarity)
    rrf_contribution: float
    fields: list[FieldContribution] = []  # lexical only
    snippets: list[str] = []  # highlighted fragments; [[hl]]..[[/hl]] marks matches


class ExplainedItem(BaseModel):
    item: ContentItemSummary
    rank: int
    score: float  # final score (RRF sum, or BM25 when lexical-only)
    retrievers: list[RetrieverResult]


class SearchExplainResponse(BaseModel):
    query: str
    mode: str  # "hybrid" | "lexical"
    fusion: str | None
    rank_constant: int
    rank_window: int
    total: int
    retrievers: list[dict[str, str]]  # name/label/description of each leg
    items: list[ExplainedItem]


_FIELD_BOOSTS = {"title": 3.0, "summary": 2.0}


def _leaf_terms(
    node: dict[str, Any], out: list[tuple[str, str, float, bool]], counted: bool = True
) -> None:
    """Collect (field, term, score, counted) BM25 leaves.

    A `dis_max` ("max of:") only counts its best child (tie_breaker is 0 for
    best_fields); the losing fields are still collected with counted=False so
    the UI can show that they matched too.
    """
    desc = node.get("description", "")
    children = node.get("details") or []
    if desc.startswith("weight("):
        # weight(title:agent in 12) [PerFieldSimilarity], result of:
        inner = desc[len("weight("):].split(" in ", 1)[0]
        field, _, term = inner.partition(":")
        out.append((field, term, float(node.get("value", 0.0)), counted))
        return
    if not children:
        return
    if desc.startswith("max of") or desc.startswith("max plus"):
        best = max(children, key=lambda c: c.get("value", 0.0))
        for child in children:
            _leaf_terms(child, out, counted and child is best)
        return
    for child in children:
        _leaf_terms(child, out, counted)


def _field_contributions(explanation: dict[str, Any] | None) -> list[FieldContribution]:
    if not explanation:
        return []
    leaves: list[tuple[str, str, float, bool]] = []
    _leaf_terms(explanation, leaves)
    by_field: dict[str, FieldContribution] = {}
    for field, term, score, counted in leaves:
        fc = by_field.setdefault(
            field,
            FieldContribution(
                field=field, boost=_FIELD_BOOSTS.get(field, 1.0), score=0.0,
                counted=counted, terms=[],
            ),
        )
        fc.score += score
        fc.terms.append(TermContribution(term=term, score=score))
    for fc in by_field.values():
        fc.terms.sort(key=lambda t: t.score, reverse=True)
    return sorted(by_field.values(), key=lambda f: (not f.counted, -f.score))


def _snippets(hit: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for field, frags in (hit.get("highlight") or {}).items():
        for frag in frags[:2]:
            out.append(f"{field}: {frag}" if field not in ("semantic_headline", "semantic_body") else frag)
    return out


async def _run_leg(
    es: Any, query: dict[str, Any], window: int, *, explain: bool, highlight: dict[str, Any] | None
) -> list[dict[str, Any]]:
    body: dict[str, Any] = {
        "query": query,
        "size": window,
        "_source": {"includes": _SOURCE_FIELDS},
        "explain": explain,
    }
    if highlight:
        body["highlight"] = highlight
    try:
        resp = await es.search(index=CONTENT_ITEMS_INDEX, body=body)
    except Exception:
        if not highlight:
            raise
        # Highlighting is a nice-to-have (semantic_text highlight needs a recent ES)
        logger.warning("search-explain: highlight failed, retrying without", exc_info=True)
        body.pop("highlight")
        resp = await es.search(index=CONTENT_ITEMS_INDEX, body=body)
    return resp["hits"]["hits"]


@router.get("/", response_model=SearchExplainResponse)
async def search_explain(
    q: str,
    size: int = Query(default=25, ge=1, le=100),
    demo: DemoMode = False,
):
    es = get_es_client()
    filters = await demo_filters(demo)
    hybrid = settings.enable_semantic_search
    window = max(50, size)

    def wrap(inner: dict[str, Any]) -> dict[str, Any]:
        return {"bool": {"must": [inner], "filter": filters}} if filters else inner

    lexical_hl = {
        "pre_tags": [HL_PRE], "post_tags": [HL_POST],
        "fields": {
            "title": {"number_of_fragments": 0},
            "summary": {"fragment_size": 180, "number_of_fragments": 1},
            "content_markdown": {"fragment_size": 180, "number_of_fragments": 1},
            "interest_reasoning": {"fragment_size": 180, "number_of_fragments": 1},
        },
    }
    semantic_hl = lambda field: {  # noqa: E731
        "fields": {field: {"number_of_fragments": 2, "order": "score"}}
    }

    legs: list[tuple[str, str, dict[str, Any], bool, dict[str, Any] | None]] = [
        ("lexical", "Keyword (BM25)", wrap(build_lexical_query(q)), True, lexical_hl),
    ]
    if hybrid:
        legs += [
            ("semantic_headline", "Semantic: title + summary",
             wrap({"semantic": {"field": "semantic_headline", "query": q}}), False,
             semantic_hl("semantic_headline")),
            ("semantic_body", "Semantic: full text",
             wrap({"semantic": {"field": "semantic_body", "query": q}}), False,
             semantic_hl("semantic_body")),
        ]

    results = await asyncio.gather(
        *[_run_leg(es, query, window, explain=ex, highlight=hl) for _, _, query, ex, hl in legs]
    )

    # name -> doc id -> (rank, hit)
    ranked: dict[str, dict[str, tuple[int, dict[str, Any]]]] = {}
    for (name, *_), hits in zip(legs, results):
        ranked[name] = {h["_id"]: (i + 1, h) for i, h in enumerate(hits)}

    fused: dict[str, float] = {}
    for name, by_id in ranked.items():
        for doc_id, (rank, _) in by_id.items():
            fused[doc_id] = fused.get(doc_id, 0.0) + 1.0 / (RANK_CONSTANT + rank)

    lexical = ranked["lexical"]
    if hybrid:
        order = sorted(
            fused,
            key=lambda d: (-fused[d], lexical[d][0] if d in lexical else 10**9),
        )
    else:
        order = [d for d, _ in sorted(lexical.items(), key=lambda kv: kv[1][0])]

    items: list[ExplainedItem] = []
    for pos, doc_id in enumerate(order[:size], start=1):
        retrievers: list[RetrieverResult] = []
        source: dict[str, Any] | None = None
        final = 0.0
        for name, label, *_ in legs:
            entry = ranked[name].get(doc_id)
            if entry is None:
                retrievers.append(
                    RetrieverResult(name=name, label=label, rank=None, score=None, rrf_contribution=0.0)
                )
                continue
            rank, hit = entry
            source = source or hit["_source"]
            contribution = 1.0 / (RANK_CONSTANT + rank)
            if name == "lexical":
                final = hit["_score"] if not hybrid else final
            retrievers.append(
                RetrieverResult(
                    name=name,
                    label=label,
                    rank=rank,
                    score=hit.get("_score"),
                    rrf_contribution=contribution,
                    fields=_field_contributions(hit.get("_explanation")) if name == "lexical" else [],
                    snippets=_snippets(hit),
                )
            )
        if hybrid:
            final = fused[doc_id]
        items.append(
            ExplainedItem(
                item=ContentItemSummary(id=doc_id, **(source or {})),
                rank=pos,
                score=final,
                retrievers=retrievers,
            )
        )

    descriptions = {
        "lexical": "BM25 over title (×3), summary (×2), transcript/article text and interest reasoning, with typo-tolerant fuzzy matching.",
        "semantic_headline": "Meaning-based vector match against title + summary embeddings.",
        "semantic_body": "Meaning-based vector match against transcript/article text embeddings (best chunk).",
    }
    return SearchExplainResponse(
        query=q,
        mode="hybrid" if hybrid else "lexical",
        fusion="Reciprocal Rank Fusion: score = Σ 1 / (60 + rank) over each retriever" if hybrid else None,
        rank_constant=RANK_CONSTANT,
        rank_window=window,
        total=len(order),
        retrievers=[
            {"name": n, "label": l, "description": descriptions[n]} for n, l, *_ in legs
        ],
        items=items,
    )
