"""Feature registry: which pages, menu items and capabilities each deployment has.

AITube runs as two deployments from the same code and the same image:

- ``prod`` (smaug): the active instance. Polls feeds, ingests, summarizes, and owns
  every personal page (watch time, engagement, quarantine, detox, ...).
- ``work``: a passive public demo. It only serves content that prod mirrored into
  its Elasticsearch cluster, so anything that writes, ingests, costs API money, or
  exposes personal data is off.

``DEPLOYMENT`` picks the set; ``FEATURES_ENABLE`` / ``FEATURES_DISABLE`` (comma
lists of ids) override single features at deploy time without a rebuild.

This is enforced in two places from the same table:

- **API:** every route carries ``require_feature(id)`` (see ``main.py``). A disabled
  feature answers 404, so on the public work deployment a write endpoint doesn't
  merely lack a button, it doesn't exist. ``tests/test_features.py`` fails if any
  API route is missing a feature, so new endpoints must be classified.
- **UI:** ``GET /api/config/`` returns the enabled features, and the frontend builds
  its nav, settings menu, routes and in-page controls from that list.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import cache
from typing import Literal

from fastapi import Depends, HTTPException
from fastapi.params import Depends as DependsParam

from backend.app.config import settings

Deployment = Literal["prod", "work"]
Kind = Literal["core", "nav", "settings", "toggle", "capability"]

PROD: frozenset[Deployment] = frozenset({"prod"})
BOTH: frozenset[Deployment] = frozenset({"prod", "work"})


@dataclass(frozen=True)
class Feature:
    id: str
    label: str
    kind: Kind
    deployments: frozenset[Deployment]
    route: str | None = None  # frontend route (nav / settings pages)


# Order matters: nav and settings entries render in this order.
FEATURES: tuple[Feature, ...] = (
    # Always on: what any timeline needs (content search + item GET, subscription
    # names for the source facet, taxonomy labels, playback reads, /api/config/).
    Feature("core", "Core", "core", BOTH),
    # Top nav tabs
    Feature("timeline", "Timeline", "nav", BOTH, "/"),
    Feature("search", "Search", "nav", BOTH, "/search/"),
    # Cluster runs span the whole prod corpus; on work their labels would leak
    # content that was never mirrored.
    Feature("topic_flow", "Topic Flow", "nav", PROD, "/topic-flow/"),
    Feature("embeddings", "Embeddings", "nav", PROD, "/embeddings/"),
    Feature("prediction", "Prediction", "nav", PROD, "/prediction/"),
    # Settings menu switches
    Feature("demo_mode", "Demo Mode", "toggle", PROD),
    Feature("zen_mode", "Zen Mode", "toggle", PROD),
    # Settings menu pages
    Feature("content", "Content", "settings", PROD, "/subscriptions/"),
    Feature("quarantine", "Quarantine", "settings", PROD, "/quarantine/"),
    Feature("watch_time", "Watch Time", "settings", PROD, "/watch-time/"),
    Feature("engagement", "Engagement", "settings", PROD, "/engagement/"),
    Feature("search_explain", "Search Explain", "settings", BOTH, "/search-explain/"),
    Feature("category_settings", "Category Settings", "settings", PROD, "/category-settings/"),
    Feature("detox", "Dopamine Detox", "settings", PROD, "/detox/"),
    # In-page capabilities
    Feature("work_mirror", "Mirror to work", "capability", PROD),
    Feature("user_state", "Consumed / viewed / interest / playback writes", "capability", PROD),
    Feature("chat", "Chat", "capability", PROD),
    # Callers outside the UI: aitube-sync, Hermes/Rex, the prediction model.
    Feature("external_api", "External integration API", "capability", PROD),
    Feature("polling", "Feed polling trigger", "capability", PROD),
)

FEATURES_BY_ID: dict[str, Feature] = {f.id: f for f in FEATURES}


def _id_list(raw: str) -> set[str]:
    ids = {part.strip() for part in raw.split(",") if part.strip()}
    unknown = ids - FEATURES_BY_ID.keys()
    if unknown:
        raise ValueError(f"Unknown feature id(s): {', '.join(sorted(unknown))}")
    return ids


def enabled_ids() -> set[str]:
    """Feature ids enabled for the current deployment, after env overrides.

    Read on every call (it's a few set operations), so tests can monkeypatch
    ``settings`` without cache busting.
    """
    deployment = settings.deployment
    if deployment not in ("prod", "work"):
        raise ValueError(f"DEPLOYMENT must be 'prod' or 'work', got {deployment!r}")
    ids = {f.id for f in FEATURES if deployment in f.deployments}
    ids |= _id_list(settings.features_enable)
    ids -= _id_list(settings.features_disable)
    ids.add("core")  # can't be disabled; the app is nothing without it
    if "work_mirror" in ids and not settings.work_elasticsearch_url:
        ids.discard("work_mirror")  # nothing to mirror to
    return ids


def is_enabled(feature_id: str) -> bool:
    if feature_id not in FEATURES_BY_ID:
        raise KeyError(f"Unknown feature id: {feature_id}")
    return feature_id in enabled_ids()


def enabled_features() -> list[Feature]:
    ids = enabled_ids()
    return [f for f in FEATURES if f.id in ids]


@cache
def require_feature(feature_id: str) -> DependsParam:
    """A route dependency that 404s when ``feature_id`` is disabled.

    Use as ``dependencies=[require_feature("chat")]`` on a route decorator or on
    ``include_router``. Cached so every use of one id is the same callable.
    """
    if feature_id not in FEATURES_BY_ID:
        raise KeyError(f"Unknown feature id: {feature_id}")

    def _check() -> None:
        if not is_enabled(feature_id):
            raise HTTPException(status_code=404, detail="Not Found")

    _check.feature_id = feature_id  # type: ignore[attr-defined]  # read by the coverage test
    _check.__name__ = f"require_feature_{feature_id}"
    return Depends(_check)
