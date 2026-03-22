import logging

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel

from backend.app.services.content_ingestor import ingest_content_url, preview_content_url

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/add-content", tags=["add-content"])


class AddContentRequest(BaseModel):
    url: str


class ContentPreview(BaseModel):
    type: str
    url: str
    title: str
    description: str = ""
    thumbnail_url: str = ""
    published_at: str | None = None
    source_name: str = ""
    source_url: str = ""
    duration_seconds: float | None = None
    author: str = ""


class IngestResponse(BaseModel):
    status: str
    message: str


@router.post("/preview/", response_model=ContentPreview)
async def preview_content(data: AddContentRequest):
    """Fetch metadata preview for a URL without ingesting it."""
    try:
        result = await preview_content_url(data.url)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error("Preview failed for %s: %s", data.url, e)
        raise HTTPException(status_code=500, detail=f"Preview failed: {e}")
    return ContentPreview(**result)


async def _run_ingest(url: str) -> None:
    """Background task wrapper for ingest pipeline."""
    try:
        item_id = await ingest_content_url(url)
        logger.info("Background ingest complete for %s → %s", url, item_id)
    except Exception as e:
        logger.error("Background ingest failed for %s: %s", url, e)


@router.post("/ingest/", response_model=IngestResponse)
async def ingest_content(data: AddContentRequest, background_tasks: BackgroundTasks):
    """Start asynchronous ingestion of a single content URL."""
    background_tasks.add_task(_run_ingest, data.url)
    return IngestResponse(
        status="queued",
        message="Content is being processed and will appear on your timeline when ready.",
    )
