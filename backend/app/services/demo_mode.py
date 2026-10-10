"""Demo Mode: restrict every content-bearing endpoint to one category.

Demo Mode is a per-browser toggle in the frontend's settings menu. While it's
on, the frontend's fetch wrapper sends `X-Demo-Mode: 1` on every API call, and
each router that reads content items takes the `DemoMode` dependency and ANDs
`demo_filter()` into its query, so the whole site (timeline, search, predictions,
topic flow, embeddings, subscriptions, analytics) only ever shows the work-safe
"AI and Software" category (content_classifier.DEMO_CATEGORY_SLUG).

It's a presentation filter, not access control: the header is trivially
settable, and the perimeter is nginx basic-auth as for the rest of the API.
"""

from collections import Counter
from typing import Annotated, Any

from fastapi import Depends, Header

from backend.app.services.content_classifier import demo_category_label


def _demo_mode(x_demo_mode: Annotated[str | None, Header()] = None) -> bool:
    return x_demo_mode == "1"


DemoMode = Annotated[bool, Depends(_demo_mode)]


async def demo_filter(demo: bool) -> dict[str, Any] | None:
    """The ES filter clause for Demo Mode, or None when it's off."""
    if not demo:
        return None
    return {"term": {"category": await demo_category_label()}}


async def demo_filters(demo: bool) -> list[dict[str, Any]]:
    """demo_filter as a list, for splatting into a bool `filter` array."""
    clause = await demo_filter(demo)
    return [clause] if clause else []


# A clustering run spans the whole corpus, and a cluster's label (top terms) describes
# all of its members. A cluster that is mostly out of scope would leak an off-topic
# label through a few stray in-scope items, so under Demo Mode a cluster is kept
# only if at least this share of its members are in scope; the rest of its in-scope
# points are shown as unclustered.
DEMO_CLUSTER_MIN_SHARE = 0.5


def demo_cluster_sizes(run_clusters: list[dict[str, Any]], in_scope: dict[str, int]) -> dict[str, int]:
    """Demo Mode: {cluster_id: in-scope size} for the clusters to keep, given the
    run's cluster docs and the in-scope item count per cluster_id."""
    full_size = {c["id"]: int(c.get("size", 0)) for c in run_clusters}
    return {
        cid: n for cid, n in in_scope.items()
        if n >= DEMO_CLUSTER_MIN_SHARE * max(full_size.get(cid, n), 1)
    }


def count_by_cluster(cluster_ids: list[str | None]) -> dict[str, int]:
    return dict(Counter(cid for cid in cluster_ids if cid))


async def in_demo_scope(demo: bool, source: dict[str, Any]) -> bool:
    """Whether an already-fetched content item may be shown under Demo Mode."""
    if not demo:
        return True
    return source.get("category") == await demo_category_label()
