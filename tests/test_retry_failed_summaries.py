"""Unit tests for the Hermes quota-cooldown retryable-error matcher.

Pure unit test — no ES/SSH involved.
"""

from __future__ import annotations

import pytest

from backend.app.services.feed_poller import _is_retryable_summary_error


@pytest.mark.parametrize("error", [
    "HTTP 404: The model gpt-5.4-mini does not exist or you do not have access to it.",
    "HTTP 404: The model gpt-6-nano does not exist or you do not have access to it.",
    "does NOT EXIST or you do not have access to it",  # case-insensitive
])
def test_retryable_error_matches_regardless_of_model_name(error):
    assert _is_retryable_summary_error(error) is True


@pytest.mark.parametrize("error", [
    None,
    "",
    "rate limited (429), retries exhausted",
    "timed out after 60s",
    "empty response",
])
def test_non_retryable_errors_do_not_match(error):
    assert _is_retryable_summary_error(error) is False
