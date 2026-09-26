"""One interface over the LLM providers the pipeline can use.

``parse`` sends a system prompt and a user prompt and returns the reply parsed
into a Pydantic model, using Claude (Anthropic) or OpenAI, whichever key is
configured (see ``Settings.llm_provider``). It returns None when the model
declines; network and API errors are raised for the caller to handle.
"""
from __future__ import annotations

import logging
from typing import TypeVar

from pydantic import BaseModel

from .config import get_settings

log = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


def provider() -> str | None:
    return get_settings().llm_provider


def model() -> str:
    return get_settings().llm_model


def parse(system: str, prompt: str, schema: type[T], max_tokens: int = 16000) -> T | None:
    p = provider()
    if p == "anthropic":
        return _anthropic(system, prompt, schema, max_tokens)
    if p == "openai":
        return _openai(system, prompt, schema, max_tokens)
    raise RuntimeError("no LLM configured: set ANTHROPIC_API_KEY or OPENAI_API_KEY")


def _anthropic(system: str, prompt: str, schema: type[T], max_tokens: int) -> T | None:
    import anthropic

    client = anthropic.Anthropic(max_retries=4)
    response = client.beta.messages.parse(
        model=model(),
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": prompt}],
        output_format=schema,
        output_config={"effort": "low"},
        # On a safety decline, let the API re-run the request on a fallback model
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
    )
    if response.stop_reason == "refusal" or response.parsed_output is None:
        log.warning("Claude declined (stop_reason=%s)", response.stop_reason)
        return None
    return response.parsed_output


def _reasoning_model(name: str) -> bool:
    return name.startswith(("gpt-5", "gpt-6", "o1", "o3", "o4"))


def _openai(system: str, prompt: str, schema: type[T], max_tokens: int) -> T | None:
    import openai

    client = openai.OpenAI(max_retries=4)
    name = model()
    effort = get_settings().openai_reasoning_effort
    extra = {"reasoning": {"effort": effort}} if _reasoning_model(name) and effort else {}
    response = client.responses.parse(
        model=name,
        instructions=system,
        input=prompt,
        text_format=schema,
        max_output_tokens=max_tokens,
        **extra,
    )
    parsed = response.output_parsed
    if parsed is None:
        log.warning("OpenAI returned no parsed output (status=%s)", getattr(response, "status", None))
    return parsed
