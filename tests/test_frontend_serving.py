"""Single-container frontend serving: /aitube/api rewrite, static files, SPA fallback."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.frontend import mount_frontend


@pytest.fixture
def client(tmp_path):
    (tmp_path / "index.html").write_text("<html>app</html>")
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "index-abc.js").write_text("console.log(1)")
    (tmp_path / "images").mkdir()
    (tmp_path / "images" / "logo.png").write_bytes(b"png")
    (tmp_path.parent / "secret.txt").write_text("nope")

    app = FastAPI(redirect_slashes=False)

    @app.get("/api/thing/")
    async def thing():
        return {"ok": True}

    mount_frontend(app, str(tmp_path))
    return TestClient(app, follow_redirects=False)


def test_api_reachable_under_base_path_and_directly(client):
    assert client.get("/aitube/api/thing/").json() == {"ok": True}
    assert client.get("/api/thing/").json() == {"ok": True}


def test_unknown_api_route_is_404_not_index(client):
    assert client.get("/aitube/api/nope/").status_code == 404


def test_static_files_and_cache_headers(client):
    js = client.get("/aitube/assets/index-abc.js")
    assert js.text == "console.log(1)"
    assert "immutable" in js.headers["cache-control"]
    assert client.get("/aitube/images/logo.png").content == b"png"


def test_spa_fallback_for_client_routes(client):
    for path in ("/aitube/", "/aitube/search/", "/aitube/no/such/page"):
        resp = client.get(path)
        assert resp.status_code == 200 and resp.text == "<html>app</html>"
        assert resp.headers["cache-control"] == "no-cache"


def test_redirects_to_base(client):
    assert client.get("/").headers["location"] == "/aitube/"
    assert client.get("/aitube").headers["location"] == "/aitube/"


def test_no_path_traversal(client):
    resp = client.get("/aitube/..%2Fsecret.txt")
    assert "nope" not in resp.text


def test_missing_build_fails_fast(tmp_path):
    with pytest.raises(RuntimeError, match="index.html"):
        mount_frontend(FastAPI(), str(tmp_path / "missing"))
