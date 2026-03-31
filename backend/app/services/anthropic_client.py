"""Instrumented Anthropic client wrappers for APM tracing."""

from contextlib import asynccontextmanager

import anthropic
import elasticapm

from backend.app.config import settings


def get_anthropic_client() -> anthropic.Anthropic:
    """Get a sync Anthropic client."""
    return anthropic.Anthropic(api_key=settings.anthropic_api_key)


def get_async_anthropic_client() -> anthropic.AsyncAnthropic:
    """Get an async Anthropic client."""
    return anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)


def traced_messages_create(client: anthropic.Anthropic, **kwargs) -> anthropic.types.Message:
    """Wrapper around client.messages.create() that adds an APM span with token usage."""
    model = kwargs.get("model", "unknown")

    with elasticapm.capture_span(
        f"Anthropic {model}",
        span_type="external",
        span_subtype="anthropic",
        span_action="chat",
        labels={"model": model, "max_tokens": kwargs.get("max_tokens", 0)},
    ):
        response = client.messages.create(**kwargs)
        if response.usage:
            elasticapm.label(
                input_tokens=response.usage.input_tokens,
                output_tokens=response.usage.output_tokens,
            )
        return response


@asynccontextmanager
async def traced_async_stream(client: anthropic.AsyncAnthropic, **kwargs):
    """Async context manager wrapping client.messages.stream() with APM labels."""
    model = kwargs.get("model", "unknown")

    elasticapm.label(
        model=model,
        max_tokens=kwargs.get("max_tokens", 0),
        streaming=True,
    )

    async with client.messages.stream(**kwargs) as stream:
        yield stream

    final_message = stream.get_final_message()
    if final_message.usage:
        elasticapm.label(
            input_tokens=final_message.usage.input_tokens,
            output_tokens=final_message.usage.output_tokens,
        )
