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


async def download_and_transcribe(audio_url: str) -> dict[str, Any]:
    """Download an audio file from a URL and transcribe it locally."""
    import tempfile
    import httpx

    # Download audio to a temp file
    logger.info("Downloading audio from %s", audio_url)
    async with httpx.AsyncClient(follow_redirects=True, timeout=300) as client:
        resp = await client.get(audio_url, headers={"User-Agent": "AITube/0.1"})
        resp.raise_for_status()

    # Determine extension from content-type or URL
    ct = resp.headers.get("content-type", "")
    if "mpeg" in ct or audio_url.endswith(".mp3"):
        ext = ".mp3"
    elif "mp4" in ct or "m4a" in ct:
        ext = ".m4a"
    elif "wav" in ct:
        ext = ".wav"
    else:
        ext = ".mp3"

    with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as f:
        f.write(resp.content)
        tmp_path = f.name

    logger.info("Downloaded %d bytes to %s, starting transcription", len(resp.content), tmp_path)

    try:
        result = await run_content_dlp("transcribe", "--force", tmp_path)
    finally:
        import os
        os.unlink(tmp_path)

    # content-dlp transcribe returns {text, chunks, model} at top level
    # Wrap it in a transcript key for consistency with other subcommands
    if "transcript" not in result and "text" in result:
        result = {"transcript": {"text": result.get("text", ""), "chunks": result.get("chunks", [])}}

    return result


async def fetch_webscrape(url: str) -> dict[str, Any]:
    """Scrape a web page and return markdown content."""
    return await run_content_dlp("webscrape", url)
