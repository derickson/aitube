"""Serve the built frontend from FastAPI (single-container image only).

On smaug the frontend is its own nginx container and the reverse proxy strips
``/aitube`` before requests reach the API. The work image is one container with
one process, so this module takes over both jobs when ``SERVE_FRONTEND_DIR`` is set:

- ``/aitube/api/*`` is rewritten to ``/api/*`` (what the proxy used to do), so the
  frontend's ``/aitube/api`` base works unchanged.
- ``/aitube/*`` serves files from the Vite build, falling back to ``index.html`` for
  client-side routes (what the nginx ``try_files`` did).
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, RedirectResponse
from starlette.types import ASGIApp, Receive, Scope, Send

BASE = "/aitube"
_API_PREFIX = f"{BASE}/api/"


class StripBasePathForApi:
    """ASGI middleware: ``/aitube/api/...`` -> ``/api/...``."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope["path"].startswith(_API_PREFIX):
            scope = dict(scope)
            scope["path"] = scope["path"][len(BASE):]
            raw = scope.get("raw_path")
            if raw and raw.startswith(_API_PREFIX.encode()):
                scope["raw_path"] = raw[len(BASE):]
        await self.app(scope, receive, send)


def mount_frontend(app: FastAPI, dist_dir: str) -> None:
    root = Path(dist_dir).resolve()
    index = root / "index.html"
    if not index.is_file():
        raise RuntimeError(f"SERVE_FRONTEND_DIR={dist_dir} has no index.html (build the frontend first)")

    app.add_middleware(StripBasePathForApi)

    @app.get("/", include_in_schema=False)
    @app.get(BASE, include_in_schema=False)
    async def _to_app():
        return RedirectResponse(f"{BASE}/", status_code=301)

    @app.get(BASE + "/{path:path}", include_in_schema=False)
    async def _frontend(path: str):
        if path.startswith("api/"):
            raise HTTPException(status_code=404)  # unknown API route, not a page
        if path:
            candidate = (root / path).resolve()
            if candidate.is_relative_to(root) and candidate.is_file():
                # Vite fingerprints everything under assets/, so it can be cached forever.
                immutable = candidate.is_relative_to(root / "assets")
                headers = {"Cache-Control": "public, max-age=31536000, immutable"} if immutable else None
                return FileResponse(candidate, headers=headers)
        # Client-side route: the SPA decides (including its own NotFound page).
        return FileResponse(index, headers={"Cache-Control": "no-cache"})
