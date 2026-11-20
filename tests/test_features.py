"""Feature registry: deployment sets, env overrides, and API route coverage.

Pure unit tests: no backend, ES or network. Routes are exercised through
FastAPI's TestClient only for endpoints that 404 before touching ES.
"""

from __future__ import annotations

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from backend.app import features
from backend.app.config import settings
from backend.app.main import app


@pytest.fixture
def deployment(monkeypatch):
    def _set(name: str, enable: str = "", disable: str = "", work_es: str = ""):
        monkeypatch.setattr(settings, "deployment", name)
        monkeypatch.setattr(settings, "features_enable", enable)
        monkeypatch.setattr(settings, "features_disable", disable)
        monkeypatch.setattr(settings, "work_elasticsearch_url", work_es)

    return _set


def _route_features(route: APIRoute) -> set[str]:
    return {
        getattr(dep.dependency, "feature_id")
        for dep in route.dependencies
        if hasattr(dep.dependency, "feature_id")
    }


# ---- coverage ---------------------------------------------------------------


def test_every_api_route_has_exactly_one_feature():
    unclassified, doubled = [], []
    for route in app.routes:
        if not isinstance(route, APIRoute) or not route.path.startswith("/api/"):
            continue
        found = _route_features(route)
        label = f"{sorted(route.methods)} {route.path}"
        if not found:
            unclassified.append(label)
        elif len(found) > 1:
            doubled.append(f"{label}: {sorted(found)}")
    assert not unclassified, "Routes without require_feature(): " + "; ".join(unclassified)
    assert not doubled, "Routes with several features: " + "; ".join(doubled)


def test_every_page_feature_has_a_route_and_ids_are_unique():
    ids = [f.id for f in features.FEATURES]
    assert len(ids) == len(set(ids))
    for f in features.FEATURES:
        if f.kind in ("nav", "settings"):
            assert f.route and f.route.startswith("/") and f.route.endswith("/"), f.id


# ---- deployment sets --------------------------------------------------------


def test_prod_enables_everything_except_mirror_without_work_cluster(deployment):
    deployment("prod")
    assert features.enabled_ids() == {f.id for f in features.FEATURES} - {"work_mirror"}


def test_prod_with_work_cluster_enables_mirror(deployment):
    deployment("prod", work_es="https://work.example:9200")
    assert features.is_enabled("work_mirror")


def test_work_is_read_only(deployment):
    deployment("work", work_es="https://should-not-matter")
    ids = features.enabled_ids()
    assert ids == {"core", "timeline", "search", "search_explain"}
    # Nothing that writes, ingests, costs money or exposes personal data.
    for fid in ("user_state", "content", "chat", "external_api", "polling", "work_mirror",
                "quarantine", "watch_time", "engagement", "category_settings", "detox"):
        assert fid not in ids


def test_env_overrides(deployment):
    deployment("work", enable="topic_flow,embeddings", disable="search_explain")
    ids = features.enabled_ids()
    assert {"topic_flow", "embeddings"} <= ids
    assert "search_explain" not in ids


def test_core_cannot_be_disabled(deployment):
    deployment("work", disable="core")
    assert features.is_enabled("core")


def test_unknown_ids_and_deployment_are_rejected(deployment):
    deployment("work", enable="nope")
    with pytest.raises(ValueError, match="nope"):
        features.enabled_ids()
    deployment("staging")
    with pytest.raises(ValueError, match="staging"):
        features.enabled_ids()


# ---- API enforcement ---------------------------------------------------------


def test_config_endpoint_lists_enabled_features(deployment):
    deployment("work")
    # No `with`: skip the lifespan, which would try to reach Elasticsearch.
    body = TestClient(app).get("/api/config/").json()
    assert body["deployment"] == "work"
    assert [f["id"] for f in body["features"]] == ["core", "timeline", "search", "search_explain"]
    assert body["features"][1]["route"] == "/"


@pytest.mark.parametrize(
    "method,path",
    [
        ("POST", "/api/polling/trigger/"),
        ("POST", "/api/subscriptions/"),
        ("PATCH", "/api/subscriptions/abc/"),
        ("DELETE", "/api/content/abc/"),
        ("PUT", "/api/content/abc/consumed/?consumed=true"),
        ("PUT", "/api/content/abc/interest/?interest=up"),
        ("PUT", "/api/playback/abc/"),
        ("POST", "/api/content/abc/quarantine/"),
        ("POST", "/api/add-content/ingest/"),
        ("POST", "/api/chat/abc/stream/"),
        ("PUT", "/api/category/settings/"),
        ("POST", "/api/detox/"),
        ("GET", "/api/watchlist/"),
        ("GET", "/api/engagement/report/"),
        ("GET", "/api/topic-flow/latest/"),
    ],
)
def test_work_deployment_404s_disabled_endpoints(deployment, method, path):
    deployment("work")
    resp = TestClient(app).request(method, path, json={})
    assert resp.status_code == 404, resp.text


def test_mirror_flag_rejected_without_work_cluster(deployment):
    deployment("prod")  # no WORK_ELASTICSEARCH_URL -> work_mirror off
    resp = TestClient(app).patch("/api/subscriptions/abc/", json={"mirror_to_work": True})
    assert resp.status_code == 400
    assert "not configured" in resp.json()["detail"]
