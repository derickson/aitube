"""`hermes -z` reports upstream API errors on stdout with a zero exit status.

Those must be treated as failures, not as a summary — otherwise the error text
is stored as the item's summary and the item becomes invisible to
`retry_failed_summaries` (which keys off `summary_error`).

Pure unit tests — the SSH subprocess is faked.
"""

from __future__ import annotations

import pytest

from backend.app.config import settings
from backend.app.services import hermes_client
from backend.app.services.feed_poller import _is_retryable_summary_error
from backend.app.services.hermes_client import looks_like_error_response


class _FakeProc:
    def __init__(self, stdout: bytes, stderr: bytes = b"", returncode: int = 0):
        self._stdout = stdout
        self._stderr = stderr
        self.returncode = returncode

    async def communicate(self, input=None):
        return self._stdout, self._stderr


def _fake_exec(stdout: bytes, returncode: int = 0):
    async def _f(*args, **kwargs):
        return _FakeProc(stdout, returncode=returncode)
    return _f


@pytest.fixture(autouse=True)
def _enable_hermes(monkeypatch):
    monkeypatch.setattr(settings, "hermes_enabled", True)
    monkeypatch.setattr(settings, "hermes_ssh_target", "hermes")
    monkeypatch.setattr(settings, "hermes_ssh_opts", "")


API_ERRORS = [
    "HTTP 404: The model `gpt-5.5` does not exist or you do not have access to it.",
    'HTTP 400: {"detail":"The \'gpt-9\' model is not supported when using Codex with a ChatGPT account."}',
    "HTTP 429: rate limited",
    # Real payload found stored as a summary; note it does NOT start with "HTTP",
    # which is why length is the load-bearing check rather than the status-line regex.
    "API call failed after 3 retries: HTTP 503: Service Unavailable",
]

# Long enough to clear _MIN_SUMMARY_CHARS, shaped like the prompt's output.
REAL_SUMMARY = (
    "This video argues that tabletop GMs should prepare worlds rather than fixed stories, "
    "because players reliably wander off any planned path. The creator walks through how to "
    "build locations and NPC motivations that react honestly to player choices.\n"
    "- Prep situations and motivations, not a sequence of scenes\n"
    "- Let NPCs pursue goals independently of the party\n"
    "- Reward clever play instead of protecting prewritten reveals\n"
    "- Keep a roster of locations ready to drop in anywhere\n"
    "- Share the surprise with the table rather than scripting it\n"
)


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", API_ERRORS)
async def test_api_error_on_stdout_is_a_failure(monkeypatch, payload):
    monkeypatch.setattr("asyncio.create_subprocess_exec", _fake_exec(payload.encode()))

    text, error = await hermes_client.run_oneshot("prompt")

    assert text is None, "error payload must not be returned as a summary"
    # A status-line payload comes back verbatim; one caught by the length gate is
    # wrapped with the reason, but must still carry the payload for triage.
    assert payload in error


@pytest.mark.asyncio
async def test_normal_response_still_succeeds(monkeypatch):
    monkeypatch.setattr("asyncio.create_subprocess_exec", _fake_exec(REAL_SUMMARY.encode()))

    text, error = await hermes_client.run_oneshot("prompt")

    assert text == REAL_SUMMARY.strip()
    assert error is None


@pytest.mark.asyncio
async def test_summary_mentioning_http_status_mid_text_is_not_rejected(monkeypatch):
    """Only a *leading* status line marks an error — a summary may discuss HTTP codes."""
    body = REAL_SUMMARY + "- It also explains what an HTTP 404 means for crawlers\n"
    monkeypatch.setattr("asyncio.create_subprocess_exec", _fake_exec(body.encode()))

    text, error = await hermes_client.run_oneshot("prompt")

    assert text == body.strip()
    assert error is None


@pytest.mark.asyncio
async def test_short_non_http_response_is_rejected(monkeypatch):
    """The 503 payload has no status-line prefix; length is what catches it."""
    payload = "API call failed after 3 retries: HTTP 503: Service Unavailable"
    monkeypatch.setattr("asyncio.create_subprocess_exec", _fake_exec(payload.encode()))

    text, error = await hermes_client.run_oneshot("prompt")

    assert text is None
    assert "too short" in error and payload in error


@pytest.mark.asyncio
async def test_empty_response_is_rejected(monkeypatch):
    monkeypatch.setattr("asyncio.create_subprocess_exec", _fake_exec(b"   \n  "))

    text, error = await hermes_client.run_oneshot("prompt")

    assert text is None
    assert error == "empty response"


# ---- looks_like_error_response: shared with scripts/repair_error_summaries -----


@pytest.mark.parametrize("payload", API_ERRORS)
def test_predicate_flags_error_payloads(payload):
    assert looks_like_error_response(payload) is not None


def test_predicate_accepts_a_real_summary():
    assert looks_like_error_response(REAL_SUMMARY) is None


def test_predicate_does_not_gate_on_bullet_format():
    """A prose-only summary of adequate length is valid — measured at 103 of 3,233
    stored summaries having no bullets at all, so format must not be the gate."""
    prose = "A" * 250
    assert looks_like_error_response(prose) is None


@pytest.mark.parametrize("payload", API_ERRORS)
def test_api_errors_are_retryable(payload):
    """Once persisted as summary_error, retry_failed_summaries must pick them up."""
    assert _is_retryable_summary_error(payload) is True


@pytest.mark.parametrize("error", ["timed out after 120s", "empty response", "ssh spawn failed: boom"])
def test_local_failures_are_not_retryable(error):
    assert _is_retryable_summary_error(error) is False
