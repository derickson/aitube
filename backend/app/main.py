import logging
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.app.config import settings
from backend.app.features import enabled_ids, require_feature
from backend.app.routers import app_config, subscriptions, content, playback, polling, chat, watchlist, add_content, consumption_report, consumption_stats, topic_flow, quarantine, engagement, embeddings, category, search_explain, detox
from backend.app.services import apm
from backend.app.services.elasticsearch import close_es_client, ensure_indices
from backend.app.services.playback_buffer import playback_buffer
from backend.app.services.work_mirror import close_work_client
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


# Fail at boot, not on the first request, if DEPLOYMENT or a FEATURES_* id is invalid.
_enabled = enabled_ids()


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Deployment %s, features: %s", settings.deployment, ", ".join(sorted(_enabled)))
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
    await close_work_client()


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

# Every API route belongs to a feature (backend/app/features.py); a disabled feature
# answers 404. Routers that mix reads and writes (subscriptions, content, playback,
# add_content, category) tag each endpoint themselves; the rest are tagged whole here.
# tests/test_features.py fails if any /api route is left unclassified.
app.include_router(app_config.router)
app.include_router(subscriptions.router)
app.include_router(content.router)
app.include_router(playback.router)
app.include_router(add_content.router)
app.include_router(category.router)
app.include_router(polling.router, dependencies=[require_feature("polling")])
app.include_router(chat.router, dependencies=[require_feature("chat")])
app.include_router(watchlist.router, dependencies=[require_feature("external_api")])
app.include_router(consumption_report.router, dependencies=[require_feature("external_api")])
app.include_router(consumption_stats.router, dependencies=[require_feature("watch_time")])
app.include_router(topic_flow.router, dependencies=[require_feature("topic_flow")])
app.include_router(quarantine.router, dependencies=[require_feature("external_api")])
app.include_router(engagement.router, dependencies=[require_feature("engagement")])
app.include_router(embeddings.router, dependencies=[require_feature("embeddings")])
app.include_router(search_explain.router, dependencies=[require_feature("search_explain")])
app.include_router(detox.router, dependencies=[require_feature("detox")])


@app.get("/health/")
async def health():
    return {"status": "ok", "deployment": settings.deployment}


def start():
    uvicorn.run(
        "backend.app.main:app",
        host=settings.host,
        port=settings.port,
        reload=True,
    )
