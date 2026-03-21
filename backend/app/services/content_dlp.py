import asyncio
import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


async def run_content_dlp(*args: str) -> dict[str, Any]:
    """Run a content-dlp subcommand and return parsed JSON output."""
    cmd = ["content-dlp", *args]
    logger.info("Running: %s", " ".join(cmd))

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()

    if stderr:
        logger.info("content-dlp stderr: %s", stderr.decode().strip())

    if proc.returncode != 0:
        raise RuntimeError(
            f"content-dlp exited with code {proc.returncode}: {stderr.decode().strip()}"
        )

    return json.loads(stdout.decode())


async def fetch_youtube(url: str, *, no_audio: bool = True, transcript: bool = False) -> dict[str, Any]:
    """Fetch YouTube video metadata (and optionally transcript)."""
    args = ["youtube"]
    if no_audio:
        args.append("--no-audio")
    if transcript:
        args.append("--transcript")
    args.append(url)
    return await run_content_dlp(*args)


async def fetch_podcast(
    feed_url: str,
    *,
    episodes: int = 5,
    no_audio: bool = True,
    transcript: bool = False,
) -> dict[str, Any]:
    """Fetch recent podcast episodes from an RSS feed."""
    args = ["podcast", "--episodes", str(episodes)]
    if no_audio:
        args.append("--no-audio")
    if transcript:
        args.append("--transcript")
    args.append(feed_url)
    return await run_content_dlp(*args)


async def fetch_webscrape(url: str) -> dict[str, Any]:
    """Scrape a web page and return markdown content."""
    return await run_content_dlp("webscrape", url)
