"""Structured error codes for summarization failures.

Shared by hermes_client, summarizer, feed_poller's retry/backfill passes, and
repair_error_summaries.py so retry/repair decisions branch on a code instead of
regex-matching free-text error messages.
"""

from enum import Enum


class SummaryErrorCode(str, Enum):
    INSUFFICIENT_CONTENT = "insufficient_content"  # pre-flight skip, no LLM called
    NOT_CONFIGURED = "not_configured"
    EMPTY_RESPONSE = "empty_response"
    TOO_SHORT_RESPONSE = "too_short_response"
    UPSTREAM_HTTP_ERROR = "upstream_http_error"  # Hermes in-band HTTP error (quota cooldown etc.)
    RATE_LIMITED = "rate_limited"
    TIMEOUT = "timeout"
    SSH_ERROR = "ssh_error"
    UNKNOWN_ERROR = "unknown_error"


# Errors worth a blind retry pass (transient). INSUFFICIENT_CONTENT is deliberately
# excluded — it only becomes eligible again when a transcript/content backfill finds
# real content, handled by backfill_missing_transcripts/backfill_missing_summaries.
RETRYABLE_SUMMARY_ERROR_CODES = frozenset({
    SummaryErrorCode.UPSTREAM_HTTP_ERROR,
    SummaryErrorCode.RATE_LIMITED,
    SummaryErrorCode.TIMEOUT,
    SummaryErrorCode.SSH_ERROR,
    SummaryErrorCode.EMPTY_RESPONSE,
    SummaryErrorCode.TOO_SHORT_RESPONSE,
})

# Below this many characters of source material, no engine is asked to summarize —
# shared with the pre-flight gate in summarizer.py and the retroactive repair script.
# Matches hermes_client._MIN_SUMMARY_CHARS: a legitimate summary can't be reliably
# derived from less input than the summary itself would need to be.
MIN_SOURCE_CHARS = 200
