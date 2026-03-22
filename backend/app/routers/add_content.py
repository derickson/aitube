"""Router for adding individual content items by URL."""
from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel

from backend.app.services.content_ingestor import check_already_ingested, ingest_url, preview_url

router = APIRouter(prefix="/api/add-content", tags=["add-content"])


class AddContentRequest(BaseModel):
    url: str


@router.post("/preview/")
async def preview_content(req: AddContentRequest):
    """Fetch metadata preview for a URL without ingesting it."""
    url = req.url.strip()
    if not url:
        raise HTTPException(status_code=400, detail="URL is required")

    already_id = await check_already_ingested(url)
    preview = await preview_url(url)
    return {**preview, "already_ingested_id": already_id}


@router.post("/ingest/")
async def ingest_content(req: AddContentRequest, background_tasks: BackgroundTasks):
    """Queue ingestion of a single content item by URL as a background task."""
    url = req.url.strip()
    if not url:
        raise HTTPException(status_code=400, detail="URL is required")

    already_id = await check_already_ingested(url)
    if already_id:
        return {"status": "already_exists", "id": already_id}

    background_tasks.add_task(ingest_url, url)
    return {"status": "queued", "message": "Content is being processed and will appear on your timeline when ready."}
