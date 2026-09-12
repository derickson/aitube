"""Run a prompt through the Hermes agent's one-shot CLI over SSH.

Hermes is an AI agent on a separate VPS. We shell out to its existing `hermes -z`
("oneshot") CLI, which prints only the final response text to stdout. The summary
prompt is piped over SSH stdin and read back remotely via "$(cat)", so it needs no
shell escaping regardless of size or content.

Used by summarizer.py to offload summarization off Claude Haiku. Any failure here
returns a None summary (with an error string) so the caller can fall back to Haiku —
including an upstream API error, which `hermes -z` reports on stdout with a zero exit
status rather than as a non-zero exit.
"""

import asyncio
import logging
import re
import shlex

import elasticapm

from backend.app.config import settings

logger = logging.getLogger(__name__)

# `hermes -z` prints upstream API errors to *stdout* and still exits 0, e.g.
#   HTTP 404: The model `gpt-5.5` does not exist or you do not have access to it.
#   HTTP 400: {"detail":"The '<model>' model is not supported ..."}
# so a zero exit status alone does not mean we got a summary. Without this check
# the error text is returned as the response and stored verbatim as the summary,
# which also hides the item from retry_failed_summaries (summary_error stays unset).
# Anchored: a real summary may legitimately *discuss* an HTTP status code.
_API_ERROR_RE = re.compile(r"^HTTP \d{3}\b")

# Not every error payload announces itself with a status line — one stored summary
# read "API call failed after 3 retries: HTTP 503: Service Unavailable". Length is
# the reliable discriminator. Measured over all 3,233 summaries in the index: the
# shortest legitimate summary is 307 chars, every observed error payload is under
# ~120, and this threshold rejects exactly one stored summary — the 503 above.
# Deliberately a length check and not a format check: gating on the prompt's
# "exactly 5 bullets" would reject 7.7% of real summaries for no added coverage.
_MIN_SUMMARY_CHARS = 200


def looks_like_error_response(text: str) -> str | None:
    """Return why `text` cannot be a summary, or None if it looks like one.

    Hermes reports upstream failures in-band (stdout, exit 0), so the response body
    is the only signal that anything went wrong. Shared with
    `scripts/repair_error_summaries.py` so detection and repair can't drift apart.
    """
    stripped = (text or "").strip()
    if not stripped:
        return "empty response"
    if _API_ERROR_RE.match(stripped):
        return stripped[:300]
    if len(stripped) < _MIN_SUMMARY_CHARS:
        return f"response too short to be a summary ({len(stripped)} chars): {stripped[:200]}"
    return None


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


async def run_oneshot(prompt: str, *, model: str | None = None) -> tuple[str | None, str | None]:
    """Send `prompt` to `hermes -z` over SSH; return (response_text, error).

    response_text is None (not raised) for every failure mode — disabled, connect/timeout,
    non-zero exit, empty output — so summarizer can fall back to Haiku cleanly. `error` carries
    a short description of what went wrong (e.g. the ssh/hermes stderr, which for a quota
    cooldown looks like "HTTP 404: The model <model> does not exist or you do not have access
    to it."), so callers can persist it for later triage/retry.
    """
    if not settings.hermes_enabled or not settings.hermes_ssh_target:
        return None, None

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
            return None, f"timed out after {settings.hermes_timeout_seconds}s"
        except OSError as e:
            logger.warning("Hermes ssh spawn failed: %s", e)
            return None, f"ssh spawn failed: {e}"

    if proc.returncode != 0:
        err_text = stderr.decode(errors="replace")[:300].strip()
        logger.warning("Hermes ssh exited %s: %s", proc.returncode, err_text)
        return None, err_text or f"ssh exited {proc.returncode}"

    text = stdout.decode(errors="replace").strip()
    failure = looks_like_error_response(text)
    if failure:
        logger.warning("Hermes returned no usable summary (exit 0): %s", failure[:200])
        return None, failure
    return text, None
