"""Topic-flow clustering pipeline.

Implements the methodology from
https://www.elastic.co/search-labs/blog/unsupervised-document-clustering-elasticsearch-jina-embeddings
adapted for an AITube-sized corpus (thousands, not millions, of docs):

  1. Fetch recent content from ES (rolling window).
  2. Embed missing docs via Jina with task="clustering" (cached on doc).
  3. Pick centroid seeds via density-probed greedy diversification (numpy).
  4. Classify each doc to its nearest seed above similarity threshold.
  5. Dissolve clusters smaller than min_size to noise.
  6. Label each cluster via ES `significant_text` aggregation.
  7. Project all embeddings to 2D via UMAP for the UI scatter plot.
  8. Persist per-doc assignments + a cluster-run summary doc.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import random
import re
import time
from datetime import datetime, timezone
from typing import Any

import numpy as np

from backend.app.config import settings
from backend.app.services.elasticsearch import (
    CLUSTER_RUNS_INDEX,
    CONTENT_ITEMS_INDEX,
    get_es_client,
)
from backend.app.services.jina_embeddings import embed_clustering

logger = logging.getLogger(__name__)

_STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "of", "for", "to", "in", "on",
    "with", "is", "are", "was", "were", "be", "been", "being", "as", "at",
    "by", "from", "this", "that", "these", "those", "it", "its", "if",
    "their", "his", "her", "they", "them", "we", "our", "you", "your",
    "i", "me", "my", "he", "she", "him", "what", "which", "who", "whom",
    "how", "when", "where", "why", "not", "no", "so", "than", "then",
    "do", "does", "did", "has", "have", "had", "will", "would", "could",
    "should", "can", "may", "might", "just", "about", "into", "over",
    "out", "up", "down", "off", "all", "any", "more", "most", "some",
    "such", "only", "own", "same", "very", "also", "new", "one", "two",
}


def _build_clustering_text(doc: dict[str, Any]) -> str:
    title = (doc.get("title") or "").strip()
    summary = (doc.get("summary") or "").strip()
    if title and summary:
        return f"{title}\n{summary}"
    return title or summary


async def fetch_corpus(days: int, cap: int = 10000) -> list[dict[str, Any]]:
    """Return recent content items, including existing embeddings if any."""
    es = get_es_client()
    fields = [
        "subscription_id", "external_id", "type", "title", "url",
        "published_at", "discovered_at", "duration_seconds", "thumbnail_url",
        "summary", "interest_score", "user_interest", "consumed", "viewed",
        "clustering_vector", "clustering_vector_model",
    ]
    docs: list[dict[str, Any]] = []
    search_after: list[Any] | None = None
    page_size = 500
    while len(docs) < cap:
        body: dict[str, Any] = {
            "size": min(page_size, cap - len(docs)),
            "_source": fields,
            "query": {
                "bool": {
                    "filter": [
                        {"range": {"published_at": {"gte": f"now-{days}d"}}},
                        {"exists": {"field": "title"}},
                    ],
                }
            },
            "sort": [
                {"published_at": {"order": "desc", "missing": "_last"}},
                {"external_id": {"order": "asc", "missing": "_last"}},
            ],
        }
        if search_after:
            body["search_after"] = search_after
        resp = await es.search(index=CONTENT_ITEMS_INDEX, body=body)
        hits = resp["hits"]["hits"]
        if not hits:
            break
        for h in hits:
            src = h["_source"]
            src["id"] = h["_id"]
            text = _build_clustering_text(src)
            if not text:
                continue
            docs.append(src)
        search_after = hits[-1].get("sort")
        if len(hits) < page_size:
            break
    logger.info("fetch_corpus: %d docs in last %dd", len(docs), days)
    return docs


async def _embed_missing(docs: list[dict[str, Any]]) -> int:
    """Embed any docs lacking a current-model embedding; bulk-update ES.

    Returns the number of new embeddings written.
    """
    model = settings.jina_clustering_model
    todo = [
        d for d in docs
        if not d.get("clustering_vector")
        or d.get("clustering_vector_model") != model
    ]
    if not todo:
        return 0
    logger.info("embedding %d docs (model=%s)", len(todo), model)
    texts = [_build_clustering_text(d) for d in todo]
    vectors = await embed_clustering(texts)

    es = get_es_client()
    bulk_lines: list[dict[str, Any]] = []
    for doc, vec in zip(todo, vectors):
        bulk_lines.append({"update": {"_index": CONTENT_ITEMS_INDEX, "_id": doc["id"]}})
        bulk_lines.append({"doc": {
            "clustering_vector": vec,
            "clustering_vector_model": model,
        }})
        doc["clustering_vector"] = vec
        doc["clustering_vector_model"] = model
    # Send in chunks to keep bulk request bodies bounded
    chunk = 100
    for i in range(0, len(bulk_lines), chunk * 2):
        body = bulk_lines[i : i + chunk * 2]
        resp = await es.bulk(operations=body, refresh=False)
        if resp.get("errors"):
            for item in resp.get("items", []):
                upd = item.get("update", {})
                if upd.get("error"):
                    logger.warning("bulk embed update error: %s", upd["error"])
    return len(todo)


def _l2_normalize(matrix: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return matrix / norms


def _pick_seeds(
    matrix: np.ndarray,
    rng: random.Random,
) -> list[int]:
    """Density-probed greedy seed selection. Returns row indices into matrix."""
    n = matrix.shape[0]
    if n == 0:
        return []
    probe_n = max(50, int(math.ceil(settings.cluster_density_sample_pct * n)))
    probe_n = min(probe_n, n)
    probe_idx = rng.sample(range(n), probe_n)

    # Density = mean of top-k cosine similarities (excluding self)
    k = min(10, n - 1) if n > 1 else 0
    probe_vecs = matrix[probe_idx]                # (P, D)
    sims = probe_vecs @ matrix.T                  # (P, N)
    # zero self-similarity (cosine of doc with itself is 1)
    for row, src in enumerate(probe_idx):
        sims[row, src] = -1.0
    if k > 0:
        topk = np.partition(sims, -k, axis=1)[:, -k:]
        density = topk.mean(axis=1)
    else:
        density = np.zeros(probe_n)

    order = np.argsort(-density)  # descending
    seeds: list[int] = []
    seed_vecs: list[np.ndarray] = []
    sep = settings.cluster_seed_separation
    max_seeds = settings.cluster_max_seeds
    for o in order:
        cand = probe_idx[o]
        cand_vec = matrix[cand]
        if seed_vecs:
            cos = np.max(np.stack(seed_vecs) @ cand_vec)
            if cos >= sep:
                continue
        seeds.append(cand)
        seed_vecs.append(cand_vec)
        if len(seeds) >= max_seeds:
            break
    logger.info("seed selection: %d seeds from %d probes", len(seeds), probe_n)
    return seeds


def _classify(matrix: np.ndarray, seed_indices: list[int]) -> tuple[np.ndarray, np.ndarray]:
    """Assign each row to its best seed; return (cluster_idx, top_score).

    cluster_idx is -1 when top_score < similarity_threshold (= noise).
    cluster_idx values otherwise are positions in seed_indices.
    """
    if not seed_indices:
        return np.full(matrix.shape[0], -1, dtype=np.int32), np.zeros(matrix.shape[0])
    seeds = matrix[seed_indices]                  # (S, D)
    scores = matrix @ seeds.T                     # (N, S)
    best = scores.argmax(axis=1)
    best_score = scores[np.arange(len(scores)), best]
    threshold = settings.cluster_similarity_threshold
    assignments = np.where(best_score >= threshold, best, -1).astype(np.int32)
    return assignments, best_score


def _dissolve_small(assignments: np.ndarray, min_size: int) -> np.ndarray:
    """Convert clusters smaller than min_size back to noise (-1)."""
    out = assignments.copy()
    unique, counts = np.unique(out[out >= 0], return_counts=True)
    too_small = set(int(c) for c, n in zip(unique, counts) if n < min_size)
    if not too_small:
        return out
    mask = np.isin(out, list(too_small))
    out[mask] = -1
    return out


def _compute_umap_sync(matrix: np.ndarray) -> np.ndarray:
    """2D UMAP projection. Blocking — call via asyncio.to_thread."""
    if matrix.shape[0] < 4:
        return np.zeros((matrix.shape[0], 2), dtype=np.float32)
    import umap  # imported lazily so the server module loads even if not installed

    reducer = umap.UMAP(
        n_components=2,
        n_neighbors=min(settings.umap_neighbors, matrix.shape[0] - 1),
        min_dist=settings.umap_min_dist,
        metric="cosine",
        random_state=42,
    )
    return reducer.fit_transform(matrix).astype(np.float32)


async def _label_clusters(
    docs: list[dict[str, Any]],
    cluster_member_ids: dict[str, list[str]],
) -> dict[str, dict[str, Any]]:
    """Run significant_text on title+summary per cluster, return label & terms.

    Returns {cluster_id: {label, top_terms[]}}.
    """
    es = get_es_client()
    out: dict[str, dict[str, Any]] = {}
    titles_by_id = {d["id"]: d.get("title") or "" for d in docs}

    for cluster_id, ids in cluster_member_ids.items():
        if not ids:
            continue
        body = {
            "size": 0,
            "query": {"ids": {"values": ids[:500]}},
            "aggs": {
                "title_terms": {
                    "significant_text": {
                        "field": "title",
                        "size": 8,
                        "filter_duplicate_text": True,
                    }
                },
                "summary_terms": {
                    "significant_text": {
                        "field": "summary",
                        "size": 8,
                        "filter_duplicate_text": True,
                    }
                },
            },
        }
        try:
            resp = await es.search(index=CONTENT_ITEMS_INDEX, body=body)
        except Exception as exc:
            logger.warning("significant_text failed for %s: %s", cluster_id, exc)
            out[cluster_id] = {"label": "", "top_terms": []}
            continue
        terms: list[str] = []
        seen: set[str] = set()
        for agg in ("title_terms", "summary_terms"):
            for b in resp.get("aggregations", {}).get(agg, {}).get("buckets", []):
                t = str(b.get("key", "")).strip().lower()
                if not _is_good_term(t) or t in seen:
                    continue
                seen.add(t)
                terms.append(t)
                if len(terms) >= 6:
                    break
            if len(terms) >= 6:
                break
        if terms:
            label = " · ".join(terms[:3])
        else:
            # fallback: shortest representative title
            fallback = min(
                (titles_by_id.get(i, "") for i in ids if titles_by_id.get(i)),
                key=len,
                default="(no label)",
            )
            label = fallback[:60]
        out[cluster_id] = {"label": label, "top_terms": terms}
    return out


_TOKEN_RX = re.compile(r"^[a-z][a-z\-']{1,}$")


def _is_good_term(token: str) -> bool:
    if not token or token in _STOPWORDS:
        return False
    if not _TOKEN_RX.match(token):
        return False
    if len(token) < 3:
        return False
    return True


def _clean_title(text: str, max_words: int = 5) -> str:
    """Normalize an LLM-proposed topic title to <= max_words, no surrounding quotes/punct."""
    cleaned = text.strip()
    cleaned = re.sub(r"^[\s\-–—•*\"']+", "", cleaned)       # leading list markers / quotes
    cleaned = re.sub(r"[\s.;:,–—\-\"']+$", "", cleaned)     # trailing punctuation / quotes
    words = cleaned.split()
    return " ".join(words[:max_words])


def _extract_json_object(text: str) -> dict[str, Any] | None:
    """Pull the first {...} object out of an LLM response (tolerates code fences / prose)."""
    if not text:
        return None
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fenced:
        candidate = fenced.group(1)
    else:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            return None
        candidate = text[start : end + 1]
    try:
        obj = json.loads(candidate)
    except (json.JSONDecodeError, ValueError):
        return None
    return obj if isinstance(obj, dict) else None


def _build_naming_prompt(
    cluster_member_ids: dict[str, list[str]],
    cluster_info: dict[str, dict[str, Any]],
    titles_by_id: dict[str, str],
    rng: random.Random,
    sample_titles: int,
) -> str:
    """Compose a single prompt that asks Hermes to title every cluster at once."""
    blocks: list[str] = []
    for cid in sorted(cluster_member_ids.keys()):
        ids = cluster_member_ids[cid]
        terms = cluster_info.get(cid, {}).get("top_terms", [])
        titles = [t for t in (titles_by_id.get(i, "").strip() for i in ids) if t]
        if len(titles) > sample_titles:
            titles = rng.sample(titles, sample_titles)
        kw = ", ".join(terms) if terms else "(none)"
        title_lines = "\n".join(f"  - {t}" for t in titles) or "  (none)"
        blocks.append(f"Cluster {cid}:\n  keywords: {kw}\n  sample titles:\n{title_lines}")

    return (
        "You are labeling topic clusters from a personal media feed "
        "(YouTube videos, podcasts, articles).\n"
        "For each cluster below, write a concise topic title capturing what its items share.\n\n"
        "Rules:\n"
        "- At most 5 words per title.\n"
        "- Title Case. No surrounding quotes, no trailing punctuation, no numbering.\n"
        '- Specific and human-readable (e.g. "Local LLM Tooling", not "AI Stuff").\n'
        "- Base the title only on that cluster's keywords and sample titles.\n\n"
        "Return ONLY a JSON object mapping each cluster id to its title, for example:\n"
        '{"c00": "Local LLM Tooling", "c01": "Home Espresso Gear"}\n\n'
        + "\n\n".join(blocks)
    )


async def _name_clusters_via_hermes(
    cluster_member_ids: dict[str, list[str]],
    cluster_info: dict[str, dict[str, Any]],
    titles_by_id: dict[str, str],
    rng: random.Random,
    sample_titles: int = 20,
) -> dict[str, str]:
    """Ask Hermes for a <=5-word title per cluster. Returns {cluster_id: title}.

    Best-effort: returns {} on any failure (disabled, ssh error, unparseable) so the
    caller keeps the significant-term labels.
    """
    if not settings.hermes_enabled or not cluster_member_ids:
        return {}

    prompt = _build_naming_prompt(
        cluster_member_ids, cluster_info, titles_by_id, rng, sample_titles
    )
    from backend.app.services.hermes_client import run_oneshot

    try:
        resp = await run_oneshot(prompt)
    except Exception as exc:  # never let naming break a clustering run
        logger.warning("Hermes cluster naming raised: %s", exc)
        return {}
    if not resp:
        logger.info("Hermes cluster naming returned nothing; keeping term labels")
        return {}

    obj = _extract_json_object(resp)
    if not obj:
        logger.warning("Hermes cluster naming output was unparseable")
        return {}

    names: dict[str, str] = {}
    for cid in cluster_member_ids:
        raw = obj.get(cid)
        if isinstance(raw, str):
            title = _clean_title(raw)
            if title:
                names[cid] = title
    logger.info("Hermes named %d/%d clusters", len(names), len(cluster_member_ids))
    return names


async def _bulk_write_assignments(
    docs: list[dict[str, Any]],
    cluster_of: dict[str, str | None],
    coords: np.ndarray,
    run_id: str,
) -> None:
    es = get_es_client()
    ops: list[dict[str, Any]] = []
    for i, d in enumerate(docs):
        doc_id = d["id"]
        cid = cluster_of.get(doc_id)
        ops.append({"update": {"_index": CONTENT_ITEMS_INDEX, "_id": doc_id}})
        ops.append({"doc": {
            "cluster_id": cid,
            "cluster_run_id": run_id,
            "umap_x": float(coords[i, 0]),
            "umap_y": float(coords[i, 1]),
        }})
    chunk = 200
    for i in range(0, len(ops), chunk * 2):
        body = ops[i : i + chunk * 2]
        resp = await es.bulk(operations=body, refresh=False)
        if resp.get("errors"):
            for item in resp.get("items", []):
                upd = item.get("update", {})
                if upd.get("error"):
                    logger.warning("bulk assign update error: %s", upd["error"])


async def _persist_run(
    run_id: str,
    cluster_info: dict[str, dict[str, Any]],
    cluster_member_ids: dict[str, list[str]],
    doc_count: int,
    noise_count: int,
) -> None:
    es = get_es_client()
    clusters_blob = [
        {
            "id": cid,
            "label": cluster_info.get(cid, {}).get("label", cid),
            "top_terms": cluster_info.get(cid, {}).get("top_terms", []),
            "size": len(member_ids),
            "representative_item_ids": member_ids[:5],
        }
        for cid, member_ids in sorted(
            cluster_member_ids.items(), key=lambda kv: -len(kv[1])
        )
    ]
    await es.index(
        index=CLUSTER_RUNS_INDEX,
        id=run_id,
        document={
            "run_id": run_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "lookback_days": settings.cluster_lookback_days,
            "doc_count": doc_count,
            "noise_count": noise_count,
            "embedding_model": settings.jina_clustering_model,
            "params": {
                "similarity_threshold": settings.cluster_similarity_threshold,
                "seed_separation": settings.cluster_seed_separation,
                "min_size": settings.cluster_min_size,
                "max_seeds": settings.cluster_max_seeds,
            },
            "clusters": clusters_blob,
        },
        refresh=True,
    )


async def rebuild_clusters() -> dict[str, Any]:
    """Full clustering pipeline. Returns summary stats."""
    t0 = time.time()
    run_id = f"run-{int(t0)}"
    docs = await fetch_corpus(days=settings.cluster_lookback_days)
    if not docs:
        logger.warning("rebuild_clusters: empty corpus, nothing to do")
        return {"run_id": run_id, "doc_count": 0, "clusters": 0, "noise": 0}

    embedded = await _embed_missing(docs)
    logger.info("embeddings: %d new", embedded)

    # Drop any docs that still lack an embedding (e.g. empty text)
    docs = [d for d in docs if d.get("clustering_vector")]
    if not docs:
        logger.warning("rebuild_clusters: no embeddable docs")
        return {"run_id": run_id, "doc_count": 0, "clusters": 0, "noise": 0}

    matrix = _l2_normalize(np.array([d["clustering_vector"] for d in docs], dtype=np.float32))
    rng = random.Random(42)
    seeds = _pick_seeds(matrix, rng)
    assignments, _ = _classify(matrix, seeds)
    assignments = _dissolve_small(assignments, settings.cluster_min_size)

    cluster_member_ids: dict[str, list[str]] = {}
    cluster_of: dict[str, str | None] = {}
    for i, d in enumerate(docs):
        a = int(assignments[i])
        cid: str | None = f"c{a:02d}" if a >= 0 else None
        cluster_of[d["id"]] = cid
        if cid is not None:
            cluster_member_ids.setdefault(cid, []).append(d["id"])

    coords = await asyncio.to_thread(_compute_umap_sync, matrix)
    cluster_info = await _label_clusters(docs, cluster_member_ids)

    # Drop clusters whose label is empty (no distinguishing vocabulary)
    to_dissolve = {cid for cid, info in cluster_info.items() if not info.get("top_terms")}
    if to_dissolve:
        for cid in to_dissolve:
            for doc_id in cluster_member_ids.get(cid, []):
                cluster_of[doc_id] = None
            cluster_member_ids.pop(cid, None)
            cluster_info.pop(cid, None)
        logger.info("dissolved %d label-less clusters", len(to_dissolve))

    # Upgrade the term-based labels to LLM-written topic titles via Hermes. The
    # significant-term label stays as the fallback whenever Hermes is off or fails.
    titles_by_id = {d["id"]: d.get("title") or "" for d in docs}
    hermes_names = await _name_clusters_via_hermes(
        cluster_member_ids, cluster_info, titles_by_id, rng
    )
    for cid, name in hermes_names.items():
        if cid in cluster_info:
            cluster_info[cid]["label"] = name

    noise_count = sum(1 for cid in cluster_of.values() if cid is None)
    await _bulk_write_assignments(docs, cluster_of, coords, run_id)
    await _persist_run(
        run_id=run_id,
        cluster_info=cluster_info,
        cluster_member_ids=cluster_member_ids,
        doc_count=len(docs),
        noise_count=noise_count,
    )
    elapsed = time.time() - t0
    summary = {
        "run_id": run_id,
        "doc_count": len(docs),
        "clusters": len(cluster_member_ids),
        "noise": noise_count,
        "embedded_new": embedded,
        "elapsed_seconds": round(elapsed, 1),
    }
    logger.info("rebuild_clusters done: %s", summary)
    return summary
