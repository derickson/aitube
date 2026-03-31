import logging
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.app.config import settings
from backend.app.routers import subscriptions, content, playback, polling, chat, watchlist
from backend.app.services.elasticsearch import close_es_client, ensure_indices
from backend.app.services.playback_buffer import playback_buffer

if settings.elastic_apm_server_url:
    from elasticapm.contrib.starlette import make_apm_client, ElasticAPM

logger = logging.getLogger(__name__)

# Reduce noise from HTTP client and Elasticsearch transport during polling
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("elastic_transport.transport").setLevel(logging.WARNING)


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        await ensure_indices()
    except Exception as e:
        logger.warning("Could not connect to Elasticsearch on startup: %s", e)
    playback_buffer.start()
    yield
    await playback_buffer.stop()
    await close_es_client()


app = FastAPI(title="AITube", version="0.1.0", lifespan=lifespan, redirect_slashes=False)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:8103"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

if settings.elastic_apm_server_url:
    apm_config = {
        "SERVICE_NAME": "aitube-backend",
        "SERVER_URL": settings.elastic_apm_server_url,
        "ENVIRONMENT": settings.elastic_apm_environment,
        "CAPTURE_BODY": "transactions",
    }
    if settings.elastic_apm_api_key:
        apm_config["API_KEY"] = settings.elastic_apm_api_key
    elif settings.elastic_apm_secret_token:
        apm_config["SECRET_TOKEN"] = settings.elastic_apm_secret_token
    apm_client = make_apm_client(apm_config)
    app.add_middleware(ElasticAPM, client=apm_client)

app.include_router(subscriptions.router)
app.include_router(content.router)
app.include_router(playback.router)
app.include_router(polling.router)
app.include_router(chat.router)
app.include_router(watchlist.router)


@app.get("/health/")
async def health():
    return {"status": "ok"}


def start():
    uvicorn.run(
        "backend.app.main:app",
        host=settings.host,
        port=settings.port,
        reload=True,
    )
