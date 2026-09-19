import asyncio
import logging
from collections import OrderedDict
from datetime import datetime, timezone

from backend.app.services.elasticsearch import WATCH_TIME_INDEX, get_es_client

logger = logging.getLogger(__name__)

MAX_TRACKED_ITEMS = 1000
# Frontend pings every 5s; cap a bit above that so a seek/scrub jump (or a
# resume from a previously-stored position) can't get counted as watched time.
MAX_DELTA_SECONDS = 15.0
KNOWN_TYPES = ("youtube_channel", "podcast", "rss")


class WatchTimeTracker:
    """Turns absolute playhead positions (reported every 5s by the player)
    into accumulated watched-seconds per UTC hour, by diffing against the
    last position seen for each item. Mirrors PlaybackBuffer's buffer +
    15-minute-flush shape, but the last-position map is never cleared on
    flush — only LRU-evicted — so deltas stay continuous across flushes."""

    def __init__(self):
        self._last_position: OrderedDict[str, float] = OrderedDict()
        self._hour_buckets: dict[str, dict] = {}
        self._task: asyncio.Task | None = None

    def record(
        self,
        content_item_id: str,
        position: float,
        content_type: str | None,
        now: datetime,
    ) -> None:
        """Record a playhead sighting and fold the clamped delta since the
        last sighting into the current UTC hour bucket."""
        last = self._last_position.get(content_item_id)
        if content_item_id in self._last_position:
            self._last_position.move_to_end(content_item_id)
        elif len(self._last_position) >= MAX_TRACKED_ITEMS:
            self._last_position.popitem(last=False)
        self._last_position[content_item_id] = position

        if last is None:
            return  # no baseline yet for this item this process lifetime
        delta = min(max(position - last, 0.0), MAX_DELTA_SECONDS)
        if delta <= 0:
            return

        hour_key = now.astimezone(timezone.utc).replace(
            minute=0, second=0, microsecond=0
        ).isoformat()
        bucket = self._hour_buckets.setdefault(
            hour_key, {"total_seconds": 0.0, "by_type": {}}
        )
        bucket["total_seconds"] += delta
        if content_type in KNOWN_TYPES:
            bucket["by_type"][content_type] = bucket["by_type"].get(content_type, 0.0) + delta

    async def flush(self) -> None:
        """Bulk-increment each accumulated hour bucket into Elasticsearch."""
        if not self._hour_buckets:
            return

        buckets = self._hour_buckets
        self._hour_buckets = {}
        logger.info("Flushing %d watch-time bucket(s) to Elasticsearch", len(buckets))

        es = get_es_client()
        for hour_key, data in buckets.items():
            try:
                await es.update(
                    index=WATCH_TIME_INDEX,
                    id=hour_key,
                    script={
                        "source": (
                            "ctx._source.total_seconds += params.delta;"
                            "for (entry in params.by_type.entrySet()) {"
                            "  def k = entry.getKey();"
                            "  ctx._source.by_type[k] = "
                            "    (ctx._source.by_type.containsKey(k) ? ctx._source.by_type[k] : 0) + entry.getValue();"
                            "}"
                        ),
                        "params": {"delta": data["total_seconds"], "by_type": data["by_type"]},
                    },
                    upsert={
                        "hour": hour_key,
                        "total_seconds": data["total_seconds"],
                        "by_type": data["by_type"],
                    },
                )
            except Exception as e:
                logger.error("Failed to flush watch-time bucket %s: %s", hour_key, e)

    async def _scheduler(self) -> None:
        """Flush at :00, :15, :30, :45 of each hour — same cadence as PlaybackBuffer."""
        while True:
            now = datetime.now(timezone.utc)
            total_seconds = now.minute * 60 + now.second
            next_slot = ((total_seconds // 900) + 1) * 900
            wait = next_slot - total_seconds
            await asyncio.sleep(wait)
            await self.flush()

    def start(self) -> None:
        self._task = asyncio.create_task(self._scheduler())
        logger.info("Watch-time tracker scheduler started")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        await self.flush()
        logger.info("Watch-time tracker stopped and flushed")


# Module-level singleton used by the playback router and app lifespan
watch_time_tracker = WatchTimeTracker()
