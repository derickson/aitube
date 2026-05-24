"""Run a prompt through the Hermes agent's one-shot CLI over SSH.

Hermes is an AI agent on a separate VPS. We shell out to its existing `hermes -z`
("oneshot") CLI, which prints only the final response text to stdout. The summary
prompt is piped over SSH stdin and read back remotely via "$(cat)", so it needs no
shell escaping regardless of size or content.

Used by summarizer.py to offload summarization off Claude Haiku. Any failure here
returns None so the caller can fall back to Haiku.
"""

import asyncio
import logging
import shlex

import elasticapm

from backend.app.config import settings

logger = logging.getLogger(__name__)


def _build_remote_command(use_model: str) -> str:
    """Build the remote `hermes` invocation. The prompt arrives on stdin via $(cat);
    only the (trusted, simple) profile/model values are interpolated, and they're quoted.

    Flag order mirrors the profile's auto-generated wrapper `exec hermes -p <profile> "$@"`:
    `-p` (profile) first, then `-t ''` (no toolsets), `-m` (model), `-z` (oneshot).
    """
    parts = ["hermes"]
    if settings.hermes_profile:
        parts.append(f"-p {shlex.quote(settings.hermes_profile)}")
    parts.append("-t ''")
    if use_model:
        parts.append(f"-m {shlex.quote(use_model)}")
    parts.append('-z "$(cat)"')
    return " ".join(parts)


async def run_oneshot(prompt: str, *, model: str | None = None) -> str | None:
    """Send `prompt` to `hermes -z` over SSH; return the response text, or None on any failure.

    None is returned (not raised) for every failure mode — disabled, connect/timeout,
    non-zero exit, empty output — so summarizer can fall back to Haiku cleanly.
    """
    if not settings.hermes_enabled or not settings.hermes_ssh_target:
        return None

    use_model = model if model is not None else settings.hermes_model
    remote_cmd = _build_remote_command(use_model)
    ssh_cmd = [
        "ssh",
        "-o", "BatchMode=yes",
        "-o", "ConnectTimeout=10",
        *shlex.split(settings.hermes_ssh_opts),
        settings.hermes_ssh_target,
        remote_cmd,
    ]

    with elasticapm.capture_span(
        "Hermes oneshot",
        span_type="external",
        span_subtype="ssh",
        labels={"model": use_model or "profile-default", "profile": settings.hermes_profile},
    ):
        try:
            proc = await asyncio.create_subprocess_exec(
                *ssh_cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(input=prompt.encode()),
                timeout=settings.hermes_timeout_seconds,
            )
        except asyncio.TimeoutError:
            logger.warning("Hermes timed out after %ds", settings.hermes_timeout_seconds)
            return None
        except OSError as e:
            logger.warning("Hermes ssh spawn failed: %s", e)
            return None

    if proc.returncode != 0:
        logger.warning(
            "Hermes ssh exited %s: %s",
            proc.returncode,
            stderr.decode(errors="replace")[:300],
        )
        return None

    text = stdout.decode(errors="replace").strip()
    return text or None
