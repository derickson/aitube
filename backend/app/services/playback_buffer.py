import asyncio
import logging
from collections import OrderedDict
from datetime import datetime, timezone

from backend.app.services.elasticsearch import PLAYBACK_STATE_INDEX, get_es_client

logger = logging.getLogger(__name__)

MAX_BUFFER_SIZE = 1000
FLUSH_CHUNK_SIZE = 100


class PlaybackBuffer:
    def __init__(self):
        self._buffer: OrderedDict[str, dict] = OrderedDict()
        self._task: asyncio.Task | None = None

    def update(self, content_item_id: str, doc: dict) -> None:
        """Add or update a playback state entry in the buffer."""
        if content_item_id in self._buffer:
            self._buffer.move_to_end(content_item_id)
        elif len(self._buffer) >= MAX_BUFFER_SIZE:
            # Evict oldest entry to stay within cap
            self._buffer.popitem(last=False)
        self._buffer[content_item_id] = doc

    def get(self, content_item_id: str) -> dict | None:
        """Return the buffered playback state for a content item, or None."""
        return self._buffer.get(content_item_id)

    async def flush(self) -> None:
        """Bulk-write all buffered playback states to Elasticsearch."""
        if not self._buffer:
            return

        items = list(self._buffer.items())
        logger.info("Flushing %d playback state(s) to Elasticsearch", len(items))

        es = get_es_client()
        flushed_ids: list[str] = []

        for i in range(0, len(items), FLUSH_CHUNK_SIZE):
            chunk = items[i : i + FLUSH_CHUNK_SIZE]
            operations: list[dict] = []
            for content_item_id, doc in chunk:
                operations.append(
                    {"index": {"_index": PLAYBACK_STATE_INDEX, "_id": content_item_id}}
                )
                operations.append(doc)

            try:
                resp = await es.bulk(operations=operations)
                if resp.get("errors"):
                    logger.warning("Bulk playback flush had errors: %s", resp)
                else:
                    flushed_ids.extend(cid for cid, _ in chunk)
            except Exception as e:
                logger.error("Failed to flush playback state chunk: %s", e)

        for cid in flushed_ids:
            self._buffer.pop(cid, None)

    async def _scheduler(self) -> None:
        """Flush at :00, :15, :30, :45 of each hour."""
        while True:
            now = datetime.now(timezone.utc)
            total_seconds = now.minute * 60 + now.second
            next_slot = ((total_seconds // 900) + 1) * 900
            wait = next_slot - total_seconds
            await asyncio.sleep(wait)
            await self.flush()

    def start(self) -> None:
        """Start the background flush scheduler."""
        self._task = asyncio.create_task(self._scheduler())
        logger.info("Playback buffer scheduler started")

    async def stop(self) -> None:
        """Cancel the scheduler and flush any remaining buffered state."""
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        await self.flush()
        logger.info("Playback buffer stopped and flushed")


# Module-level singleton used by routers and lifespan
playback_buffer = PlaybackBuffer()
