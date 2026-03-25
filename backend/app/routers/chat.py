"""Streaming chat endpoint for content Q&A."""

import json
import logging

import anthropic
import elasticapm
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from backend.app.config import settings
from backend.app.services.agents import build_system_prompt, get_agent, get_agents
from backend.app.services.anthropic_client import get_async_anthropic_client
from backend.app.services.elasticsearch import CONTENT_ITEMS_INDEX, get_es_client

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/chat", tags=["chat"])


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    messages: list[ChatMessage]
    agent_id: str = "default"


@router.get("/agents/")
async def list_agents():
    return [{"id": a.id, "name": a.name} for a in get_agents()]


@router.post("/{item_id}/stream/")
async def stream_chat(item_id: str, req: ChatRequest):
    if not settings.anthropic_api_key:
        raise HTTPException(status_code=500, detail="Anthropic API key not configured")

    agent = get_agent(req.agent_id)
    if not agent:
        raise HTTPException(status_code=400, detail=f"Unknown agent: {req.agent_id}")

    es = get_es_client()
    try:
        doc = await es.get(index=CONTENT_ITEMS_INDEX, id=item_id)
    except Exception:
        raise HTTPException(status_code=404, detail="Content item not found")

    source = doc["_source"]
    transcript_text = ""
    if source.get("transcript"):
        transcript_text = source["transcript"].get("text", "")

    system_prompt = build_system_prompt(
        agent,
        title=source.get("title", ""),
        content_type=source.get("type", ""),
        summary=source.get("summary", "") or "",
        transcript=transcript_text,
        content_markdown=source.get("content_markdown", "") or "",
    )

    messages = [{"role": m.role, "content": m.content} for m in req.messages]

    async def event_stream():
        client = get_async_anthropic_client()
        elasticapm.label(anthropic_model=agent.model, anthropic_streaming=True)
        try:
            async with client.messages.stream(
                model=agent.model,
                max_tokens=2048,
                system=system_prompt,
                messages=messages,
            ) as stream:
                async for text in stream.text_stream:
                    yield f"data: {json.dumps({'token': text})}\n\n"
            yield f"data: {json.dumps({'done': True})}\n\n"
        except Exception as e:
            logger.error("Chat stream error: %s", e)
            yield f"data: {json.dumps({'error': str(e)})}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"},
    )
