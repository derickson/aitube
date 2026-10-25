"""Elastic APM helpers shared by the API server and the cron scripts.

Everything here is a no-op when APM isn't configured (no ELASTIC_APM_SERVER_URL),
so callers never need to check.

Why `transaction()` exists: the ASGI middleware only traces the request itself.
Work spawned with `asyncio.create_task`, the periodic flush schedulers, and the
cron scripts run outside any request, so their ES/HTTP/LLM spans are either
dropped or tacked onto an already-finished request transaction. Wrapping each
unit of background work in its own transaction gives it its own timing, and
because httpx is instrumented, its calls to content-dlp carry a `traceparent`
header and show up as one distributed trace.
"""

import functools
import logging
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Awaitable, Callable, TypeVar

import elasticapm
from elasticapm.traces import execution_context

from backend.app.config import settings


def client_config(service_name: str) -> dict[str, Any] | None:
    """Agent config for `service_name`, or None when APM is not configured."""
    if not settings.elastic_apm_server_url:
        return None
    config: dict[str, Any] = {
        "SERVICE_NAME": service_name,
        "SERVER_URL": settings.elastic_apm_server_url,
        "ENVIRONMENT": settings.elastic_apm_environment,
    }
    if settings.elastic_apm_api_key:
        config["API_KEY"] = settings.elastic_apm_api_key
    elif settings.elastic_apm_secret_token:
        config["SECRET_TOKEN"] = settings.elastic_apm_secret_token
    return config


def init_script_client(service_name: str) -> elasticapm.Client | None:
    """Create and instrument an APM client for a standalone (cron) script."""
    config = client_config(service_name)
    if config is None:
        return None
    client = elasticapm.Client(config)
    elasticapm.instrument()
    return client


@asynccontextmanager
async def transaction(
    name: str,
    transaction_type: str = "background",
    labels: dict[str, Any] | None = None,
) -> AsyncIterator[None]:
    """Run the enclosed block as its own APM transaction.

    If another transaction is active (e.g. the request that spawned this task, whose
    context `create_task` copied), the new one is linked to it, and the previous
    transaction is restored on exit so the caller's tracing continues unharmed.
    """
    client = elasticapm.get_client()
    if client is None:
        yield
        return

    parent = execution_context.get_transaction()
    parent_span = execution_context.get_span()
    links = [parent.trace_parent] if parent is not None and parent.trace_parent else None
    client.begin_transaction(transaction_type, links=links)
    if labels:
        elasticapm.label(**labels)
    try:
        yield
    except Exception:
        client.capture_exception(handled=False)
        client.end_transaction(name, "failure")
        raise
    except BaseException:  # e.g. CancelledError on shutdown — not an error
        client.end_transaction(name, "cancelled")
        raise
    else:
        client.end_transaction(name, "success")
    finally:
        if parent is not None:
            execution_context.set_transaction(parent)
            execution_context.set_span(parent_span)


_T = TypeVar("_T")


def traced(
    name: str, transaction_type: str = "background"
) -> Callable[[Callable[..., Awaitable[_T]]], Callable[..., Awaitable[_T]]]:
    """Decorator form of `transaction()` for coroutine functions run via create_task."""

    def decorator(fn: Callable[..., Awaitable[_T]]) -> Callable[..., Awaitable[_T]]:
        @functools.wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> _T:
            async with transaction(name, transaction_type):
                return await fn(*args, **kwargs)

        return wrapper

    return decorator


class TraceFormatter(logging.Formatter):
    """Appends ` trace.id=… transaction.id=…` to lines logged inside a transaction.

    The agent's LogRecordFactory puts the ids on every record; lines logged outside
    a transaction are left unchanged rather than suffixed with `None`s.
    """

    def format(self, record: logging.LogRecord) -> str:
        line = super().format(record)
        trace_id = getattr(record, "elasticapm_trace_id", None)
        if trace_id:
            line += f" trace.id={trace_id} transaction.id={record.elasticapm_transaction_id}"
        return line


def configure_logging(level: int = logging.INFO) -> None:
    """Root logging with trace correlation; replaces `logging.basicConfig`."""
    handler = logging.StreamHandler()
    handler.setFormatter(TraceFormatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
