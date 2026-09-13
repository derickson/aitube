"""`hermes -z` reports upstream API errors on stdout with a zero exit status.

Those must be treated as failures, not as a summary — otherwise the error text
is stored as the item's summary and the item becomes invisible to
`retry_failed_summaries` (which keys off `summary_error_code`).

Pure unit tests — the SSH subprocess is faked.
"""

from __future__ import annotations

import pytest

from backend.app.config import settings
from backend.app.services import hermes_client
from backend.app.services.hermes_client import looks_like_error_response
from backend.app.services.summary_errors import RETRYABLE_SUMMARY_ERROR_CODES, SummaryErrorCode


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

    text, error, code = await hermes_client.run_oneshot("prompt")

    assert text is None, "error payload must not be returned as a summary"
    # A status-line payload comes back verbatim; one caught by the length gate is
    # wrapped with the reason, but must still carry the payload for triage.
    assert payload in error
    assert code in RETRYABLE_SUMMARY_ERROR_CODES


@pytest.mark.asyncio
async def test_normal_response_still_succeeds(monkeypatch):
    monkeypatch.setattr("asyncio.create_subprocess_exec", _fake_exec(REAL_SUMMARY.encode()))

    text, error, code = await hermes_client.run_oneshot("prompt")

    assert text == REAL_SUMMARY.strip()
    assert error is None
    assert code is None


@pytest.mark.asyncio
async def test_summary_mentioning_http_status_mid_text_is_not_rejected(monkeypatch):
    """Only a *leading* status line marks an error — a summary may discuss HTTP codes."""
    body = REAL_SUMMARY + "- It also explains what an HTTP 404 means for crawlers\n"
    monkeypatch.setattr("asyncio.create_subprocess_exec", _fake_exec(body.encode()))

    text, error, code = await hermes_client.run_oneshot("prompt")

    assert text == body.strip()
    assert error is None
    assert code is None


@pytest.mark.asyncio
async def test_short_non_http_response_is_rejected(monkeypatch):
    """The 503 payload has no status-line prefix; length is what catches it."""
    payload = "API call failed after 3 retries: HTTP 503: Service Unavailable"
    monkeypatch.setattr("asyncio.create_subprocess_exec", _fake_exec(payload.encode()))

    text, error, code = await hermes_client.run_oneshot("prompt")

    assert text is None
    assert "too short" in error and payload in error
    assert code == SummaryErrorCode.TOO_SHORT_RESPONSE


@pytest.mark.asyncio
async def test_empty_response_is_rejected(monkeypatch):
    monkeypatch.setattr("asyncio.create_subprocess_exec", _fake_exec(b"   \n  "))

    text, error, code = await hermes_client.run_oneshot("prompt")

    assert text is None
    assert error == "empty response"
    assert code == SummaryErrorCode.EMPTY_RESPONSE


# Real payload found stored as a summary for a video whose description was padded
# with sponsor/contact boilerplate (293 chars — clears _MIN_SUMMARY_CHARS by raw
# length, so only phrase-matching the refusal itself catches it).
REFUSAL_RESPONSE = (
    "I appreciate you sharing this, but I'm unable to summarize the video because "
    "you've only provided the title, creator name, and description—not the actual "
    "video content itself. The description is vague (\"there are a lot of terms "
    "that the D&D community has made\") and doesn't include a transcript or "
    "detailed breakdown of what's discussed.\n\n"
    "To give you an accurate summary with 5 specific takeaways, I would need:\n"
    "- A transcript of the video, or\n"
    "- Detailed notes about what arguments/examples the creator covers, or\n"
    "- The actual video link (which I cannot access, but you could transcribe or describe)\n\n"
    "If you can provide the video's transcript or a more detailed outline of the "
    "content, I'd be happy to create the summary and bullet points you're looking for!"
)


@pytest.mark.asyncio
async def test_model_refusal_is_rejected_despite_clearing_length_gate(monkeypatch):
    assert len(REFUSAL_RESPONSE) >= 200, "must clear the length gate to prove phrase-matching is load-bearing"
    monkeypatch.setattr("asyncio.create_subprocess_exec", _fake_exec(REFUSAL_RESPONSE.encode()))

    text, error, code = await hermes_client.run_oneshot("prompt")

    assert text is None, "a refusal must not be stored as the summary"
    assert code == SummaryErrorCode.INSUFFICIENT_CONTENT
    assert code not in RETRYABLE_SUMMARY_ERROR_CODES


# ---- looks_like_error_response: shared with scripts/repair_error_summaries -----


@pytest.mark.parametrize("payload", API_ERRORS)
def test_predicate_flags_error_payloads(payload):
    result = looks_like_error_response(payload)
    assert result is not None
    detail, code = result
    assert payload in detail
    assert code in RETRYABLE_SUMMARY_ERROR_CODES


def test_predicate_accepts_a_real_summary():
    assert looks_like_error_response(REAL_SUMMARY) is None


def test_predicate_does_not_gate_on_bullet_format():
    """A prose-only summary of adequate length is valid — measured at 103 of 3,233
    stored summaries having no bullets at all, so format must not be the gate."""
    prose = "A" * 250
    assert looks_like_error_response(prose) is None


@pytest.mark.parametrize("payload", API_ERRORS)
def test_api_errors_are_retryable(payload):
    """Once persisted as summary_error_code, retry_failed_summaries must pick them up."""
    _detail, code = looks_like_error_response(payload)
    assert code in RETRYABLE_SUMMARY_ERROR_CODES


@pytest.mark.parametrize("code", [
    SummaryErrorCode.INSUFFICIENT_CONTENT,
    SummaryErrorCode.NOT_CONFIGURED,
    SummaryErrorCode.UNKNOWN_ERROR,
])
def test_terminal_error_codes_are_not_retryable(code):
    """These only clear via a transcript/content backfill finding real material
    (or a config change), never via the blind retry_failed_summaries pass."""
    assert code not in RETRYABLE_SUMMARY_ERROR_CODES
