import logging
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.app.config import settings
from backend.app.routers import subscriptions, content, playback, polling, chat, watchlist, add_content, consumption_report, consumption_stats, topic_flow, quarantine, engagement, embeddings, category, search_explain
from backend.app.services import apm
from backend.app.services.elasticsearch import close_es_client, ensure_indices
from backend.app.services.playback_buffer import playback_buffer
from backend.app.services.watch_time_tracker import watch_time_tracker

if settings.elastic_apm_server_url:
    from elasticapm.contrib.starlette import make_apm_client, ElasticAPM

# Without a root handler, INFO logs from services (poller, summarizer, content-dlp
# client) were silently dropped in Docker; uvicorn only configures its own loggers.
apm.configure_logging()
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
    watch_time_tracker.start()
    yield
    await watch_time_tracker.stop()
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

apm_config = apm.client_config("aitube-backend")
if apm_config:
    apm_config["CAPTURE_BODY"] = "transactions"
    apm_client = make_apm_client(apm_config)
    app.add_middleware(ElasticAPM, client=apm_client)

app.include_router(subscriptions.router)
app.include_router(content.router)
app.include_router(playback.router)
app.include_router(polling.router)
app.include_router(chat.router)
app.include_router(watchlist.router)
app.include_router(add_content.router)
app.include_router(consumption_report.router)
app.include_router(consumption_stats.router)
app.include_router(topic_flow.router)
app.include_router(quarantine.router)
app.include_router(engagement.router)
app.include_router(embeddings.router)
app.include_router(category.router)
app.include_router(search_explain.router)


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
