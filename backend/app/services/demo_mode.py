"""Demo Mode / Zen Mode: scope every content-bearing endpoint by one category.

Zen Mode is the inverse of Demo Mode: it hides the "AI and Software" category and
shows everything else. The two are mutually exclusive (Demo wins if both headers
arrive). Everywhere below, `demo` is the active mode: "demo", "zen" or None, so
existing truthiness checks mean "some scope is active".

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


def _demo_mode(
    x_demo_mode: Annotated[str | None, Header()] = None,
    x_zen_mode: Annotated[str | None, Header()] = None,
) -> str | None:
    if x_demo_mode == "1":
        return "demo"
    if x_zen_mode == "1":
        return "zen"
    return None


DemoMode = Annotated[str | None, Depends(_demo_mode)]


async def demo_filter(demo: str | None) -> dict[str, Any] | None:
    """The ES filter clause for Demo/Zen Mode, or None when neither is on."""
    if not demo:
        return None
    clause = {"term": {"category": await demo_category_label()}}
    if demo == "zen":
        return {"bool": {"must_not": [clause]}}
    return clause


async def demo_filters(demo: str | None) -> list[dict[str, Any]]:
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


async def in_demo_scope(demo: str | None, source: dict[str, Any]) -> bool:
    """Whether an already-fetched content item may be shown under Demo/Zen Mode."""
    if not demo:
        return True
    is_demo_category = source.get("category") == await demo_category_label()
    return is_demo_category if demo == "demo" else not is_demo_category
