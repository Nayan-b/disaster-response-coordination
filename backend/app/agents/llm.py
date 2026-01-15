"""Claude access for the agent graph, with a deterministic fallback.

Two things worth knowing about this module:

**Model configuration.** Claude Opus 5 rejects `temperature`, `top_p`, and `top_k` with a 400, so
none are ever set — behavior is steered by prompt alone. Thinking is on by default on Opus 5 and
`max_tokens` caps thinking *plus* response text, so the budget here is deliberately generous; a tight
one truncates mid-answer.

**Structured output via `output_config.format`.** Anthropic's native structured outputs are used
rather than LangChain's `with_structured_output()`, which forces `tool_choice` — a combination whose
interaction with adaptive thinking is not something to gamble on. `output_config.format` is
documented as compatible with extended thinking, so the JSON contract and the model's reasoning
coexist safely.

**The fallback is a feature, not a stub.** When no Anthropic credentials are present the graph runs
on `rule-based` engines that produce the same structured objects from the same tool results. That
keeps the whole pipeline — including the human-approval gate — runnable and testable without a key,
and every output records which engine drafted it so a responder is never misled about provenance.
"""

from __future__ import annotations

import json
import os
from typing import Any

from pydantic import BaseModel, ValidationError

# Per the Claude API guidance: use Opus 5 unless the caller explicitly picks another model.
DEFAULT_MODEL = os.environ.get("FLOOD_CLAUDE_MODEL", "claude-opus-5")

# Thinking is on by default on Opus 5 and shares this budget with the response text.
DEFAULT_MAX_TOKENS = int(os.environ.get("FLOOD_CLAUDE_MAX_TOKENS", "16000"))

ENGINE_RULE_BASED = "rule-based"

_CREDENTIAL_VARS = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")


def llm_available() -> bool:
    """True when Anthropic credentials are present in the environment.

    Note this only covers env-var credentials. An `ant auth login` profile also authenticates the
    underlying SDK; set FLOOD_FORCE_LLM=1 to use it.
    """
    if os.environ.get("FLOOD_FORCE_LLM", "").lower() in {"1", "true", "yes"}:
        return True
    if os.environ.get("FLOOD_DISABLE_LLM", "").lower() in {"1", "true", "yes"}:
        return False
    return any(os.environ.get(var) for var in _CREDENTIAL_VARS)


def active_engine() -> str:
    """Which engine will draft: the model id, or `rule-based`."""
    return DEFAULT_MODEL if llm_available() else ENGINE_RULE_BASED


def build_chat_model(
    *,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    effort: str | None = None,
    output_schema: dict[str, Any] | None = None,
):
    """Construct a ChatAnthropic client.

    `temperature` is deliberately never passed — Opus 5 returns a 400 for it. `effort` maps to
    `output_config.effort`; when an `output_schema` is given it rides in the same `output_config`.
    """
    from langchain_anthropic import ChatAnthropic

    output_config: dict[str, Any] = {}
    if effort:
        output_config["effort"] = effort
    if output_schema:
        output_config["format"] = {"type": "json_schema", "schema": output_schema}

    kwargs: dict[str, Any] = {"model": DEFAULT_MODEL, "max_tokens": max_tokens}
    if output_config:
        kwargs["output_config"] = output_config
    return ChatAnthropic(**kwargs)


def _schema_for(model_cls: type[BaseModel]) -> dict[str, Any]:
    """JSON Schema for a pydantic model, tightened for Anthropic structured outputs.

    Structured outputs require `additionalProperties: false` on every object and reject numeric or
    string constraints, so those are stripped rather than sent and rejected.
    """
    schema = model_cls.model_json_schema()

    def clean(node: Any) -> Any:
        if isinstance(node, dict):
            node.pop("default", None)
            for unsupported in ("minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum",
                                "multipleOf", "minLength", "maxLength", "pattern",
                                "minItems", "maxItems"):
                node.pop(unsupported, None)
            if node.get("type") == "object":
                node["additionalProperties"] = False
                if "properties" in node:
                    node["required"] = list(node["properties"])
            return {k: clean(v) for k, v in node.items()}
        if isinstance(node, list):
            return [clean(v) for v in node]
        return node

    return clean(schema)


def _extract_text(response: Any) -> str:
    """Pull the text out of a LangChain message whose content may be blocks or a string."""
    content = getattr(response, "content", response)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict):
                # Skip thinking blocks — only the answer text carries the JSON.
                if block.get("type") == "text":
                    parts.append(block.get("text", ""))
            elif isinstance(block, str):
                parts.append(block)
        return "".join(parts)
    return str(content)


class LLMUnavailable(RuntimeError):
    """Raised when a Claude call cannot be completed; callers fall back to rule-based output."""


def structured_completion(
    model_cls: type[BaseModel],
    system: str,
    user: str,
    *,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    effort: str | None = None,
) -> BaseModel:
    """Ask Claude for JSON matching `model_cls` and return the validated object.

    Raises `LLMUnavailable` on any transport, parsing, or validation failure so the caller can fall
    back rather than surfacing a half-formed recommendation.
    """
    if not llm_available():
        raise LLMUnavailable("no Anthropic credentials in the environment")

    try:
        chat = build_chat_model(
            max_tokens=max_tokens, effort=effort, output_schema=_schema_for(model_cls)
        )
        response = chat.invoke([("system", system), ("human", user)])
    except Exception as exc:  # transport, auth, rate limit — all fall back identically
        raise LLMUnavailable(f"{type(exc).__name__}: {exc}") from exc

    text = _extract_text(response).strip()
    if not text:
        raise LLMUnavailable("model returned no text content")

    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise LLMUnavailable(f"response was not valid JSON: {exc.msg}") from exc

    try:
        return model_cls.model_validate(payload)
    except ValidationError as exc:
        raise LLMUnavailable(f"response did not match {model_cls.__name__}: {exc}") from exc
