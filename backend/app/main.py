import logging
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.app.config import settings
from backend.app.routers import subscriptions, content, playback, polling
from backend.app.services.elasticsearch import close_es_client, ensure_indices

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        await ensure_indices()
    except Exception as e:
        logger.warning("Could not connect to Elasticsearch on startup: %s", e)
    yield
    await close_es_client()


app = FastAPI(title="AITube", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:8103"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(subscriptions.router)
app.include_router(content.router)
app.include_router(playback.router)
app.include_router(polling.router)


@app.get("/health")
async def health():
    return {"status": "ok"}


def start():
    uvicorn.run(
        "backend.app.main:app",
        host=settings.host,
        port=settings.port,
        reload=True,
    )
