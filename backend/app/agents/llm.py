"""LLM access for the agent graph: Anthropic direct, OpenRouter, or a deterministic fallback.

Three engines, one interface. `structured_completion()` asks for JSON matching a pydantic model and
returns the validated object, or raises `LLMUnavailable` so the caller can fall back — the graph
never sees a half-formed recommendation regardless of which provider is configured.

**Provider selection.** `FLOOD_LLM_PROVIDER` (`anthropic` | `openrouter` | `none`) decides
explicitly. With nothing set, a present `ANTHROPIC_API_KEY` wins over `OPENROUTER_API_KEY`, because
the direct path has fewer hops and native support for the features below. With neither, the graph
drafts rule-based.

**Why the two providers aren't interchangeable in code:**

* *Structured output.* Anthropic uses its native `output_config.format`; OpenRouter speaks the
  OpenAI-compatible `response_format: {type: "json_schema", ...}`. Both are pointed at the same
  pydantic schema, so callers see one contract.
* *Sampling.* Claude Opus 5 rejects `temperature`/`top_p`/`top_k` with a 400. OpenRouter advertises
  `temperature` for the same model because it normalizes across providers, but nothing here sends
  it either way — behavior is steered by prompt.
* *Thinking.* Adaptive thinking is on by default on Opus 5 and shares the `max_tokens` budget, so
  the budget is deliberately generous.

**Provenance is not optional here either.** OpenRouter may route a request to a different upstream
than the one named — that is its purpose — so the response's actual model is captured and reported
as `drafted_by` rather than the model that was requested. In a system where every number is
traceable, "which model wrote this" should not be a guess.
"""

from __future__ import annotations

import json
import os
from typing import Any

from pydantic import BaseModel, ValidationError

# --- providers ---------------------------------------------------------------

PROVIDER_ANTHROPIC = "anthropic"
PROVIDER_OPENROUTER = "openrouter"
ENGINE_RULE_BASED = "rule-based"

# Per the Claude API guidance: Opus 5 unless the caller explicitly picks another model.
DEFAULT_MODEL = os.environ.get("FLOOD_CLAUDE_MODEL", "claude-opus-5")

# OpenRouter namespaces model ids by vendor. Defaulting to the same model as the direct path keeps
# behavior comparable when switching providers; override for any of its 400+ others.
DEFAULT_OPENROUTER_MODEL = os.environ.get("FLOOD_OPENROUTER_MODEL", "anthropic/claude-opus-5")
OPENROUTER_BASE_URL = os.environ.get(
    "FLOOD_OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"
)

# Thinking is on by default on Opus 5 and shares this budget with the response text.
DEFAULT_MAX_TOKENS = int(os.environ.get("FLOOD_CLAUDE_MAX_TOKENS", "16000"))

_ANTHROPIC_VARS = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")
_OPENROUTER_VARS = ("OPENROUTER_API_KEY",)


def _truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes"}


def _has(vars_: tuple[str, ...]) -> bool:
    return any(os.environ.get(v) for v in vars_)


def active_provider() -> str | None:
    """Which provider will serve requests, or None for the rule-based fallback."""
    if _truthy("FLOOD_DISABLE_LLM"):
        return None

    requested = os.environ.get("FLOOD_LLM_PROVIDER", "").strip().lower()
    if requested in {"none", "rule-based", "rule_based"}:
        return None
    if requested == PROVIDER_ANTHROPIC:
        return PROVIDER_ANTHROPIC
    if requested == PROVIDER_OPENROUTER:
        return PROVIDER_OPENROUTER
    if requested:
        raise ValueError(
            f"unknown FLOOD_LLM_PROVIDER {requested!r}; "
            f"expected {PROVIDER_ANTHROPIC!r}, {PROVIDER_OPENROUTER!r}, or 'none'"
        )

    # Auto-detect. FLOOD_FORCE_LLM covers an `ant auth login` profile, which authenticates the
    # Anthropic SDK without setting an environment variable.
    if _has(_ANTHROPIC_VARS) or _truthy("FLOOD_FORCE_LLM"):
        return PROVIDER_ANTHROPIC
    if _has(_OPENROUTER_VARS):
        return PROVIDER_OPENROUTER
    return None


def llm_available() -> bool:
    """True when some LLM provider is configured."""
    return active_provider() is not None


def configured_model() -> str:
    """The model id that will be requested, before any provider-side routing."""
    provider = active_provider()
    if provider == PROVIDER_OPENROUTER:
        return DEFAULT_OPENROUTER_MODEL
    return DEFAULT_MODEL


def active_engine() -> str:
    """Engine label recorded on every recommendation and shown in the UI.

    `claude-opus-5`, `openrouter:anthropic/claude-opus-5`, or `rule-based` — a responder should be
    able to tell at a glance what produced the draft, including which gateway it went through.
    """
    provider = active_provider()
    if provider is None:
        return ENGINE_RULE_BASED
    if provider == PROVIDER_OPENROUTER:
        return f"{PROVIDER_OPENROUTER}:{DEFAULT_OPENROUTER_MODEL}"
    return DEFAULT_MODEL


class LLMUnavailable(RuntimeError):
    """A completion could not be produced; callers fall back to rule-based output."""


# --- schema ------------------------------------------------------------------


def _schema_for(model_cls: type[BaseModel]) -> dict[str, Any]:
    """JSON Schema for a pydantic model, tightened for strict structured outputs.

    Both providers require `additionalProperties: false` with every property listed in `required`,
    and both reject numeric/string constraints, so those are stripped rather than sent and refused.
    """
    schema = model_cls.model_json_schema()

    def clean(node: Any) -> Any:
        if isinstance(node, dict):
            node.pop("default", None)
            for unsupported in (
                "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum",
                "multipleOf", "minLength", "maxLength", "pattern", "minItems", "maxItems",
            ):
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
    """Text from a LangChain message whose content may be a string or a list of blocks."""
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


def _served_by(response: Any, fallback: str) -> str:
    """The model that actually answered.

    OpenRouter can route to a different upstream than requested, so the response is the authority
    on what produced the text — not the request.
    """
    metadata = getattr(response, "response_metadata", None) or {}
    served = metadata.get("model_name") or metadata.get("model")
    return str(served) if served else fallback


# --- clients -----------------------------------------------------------------


def build_anthropic_model(
    *,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    effort: str | None = None,
    output_schema: dict[str, Any] | None = None,
):
    """ChatAnthropic against the Anthropic API directly.

    `temperature` is never passed — Opus 5 returns a 400 for it. Structured output rides in
    `output_config`, Anthropic's native mechanism, rather than LangChain's
    `with_structured_output()` (which forces `tool_choice`, whose interaction with adaptive thinking
    is not worth gambling on).
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


def build_openrouter_model(
    *,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    effort: str | None = None,
    output_schema: dict[str, Any] | None = None,
):
    """ChatOpenAI pointed at OpenRouter's OpenAI-compatible endpoint.

    `response_format` with `strict: true` is the OpenAI-compatible equivalent of Anthropic's
    `output_config.format`; `_schema_for()` already emits a schema that satisfies its constraints.
    `reasoning.effort` is OpenRouter's cross-vendor knob for thinking depth, ignored by models that
    don't support it rather than erroring.

    Note `temperature` is not set here either. OpenRouter advertises it for Opus 5 because it
    normalizes parameters across vendors, but keeping both paths prompt-steered means switching
    provider doesn't quietly change behavior.
    """
    from langchain_openai import ChatOpenAI

    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        raise LLMUnavailable("OPENROUTER_API_KEY is not set")

    model_kwargs: dict[str, Any] = {}
    if output_schema:
        model_kwargs["response_format"] = {
            "type": "json_schema",
            "json_schema": {
                "name": "flood_response_schema",
                "strict": True,
                "schema": output_schema,
            },
        }

    return ChatOpenAI(
        model=DEFAULT_OPENROUTER_MODEL,
        base_url=OPENROUTER_BASE_URL,
        api_key=api_key,
        max_tokens=max_tokens,
        # langchain-openai 1.x targets OpenAI's Responses API by default. OpenRouter serves
        # /chat/completions only, and against the Responses path the client fails while parsing a
        # perfectly valid reply ("'NoneType' object is not iterable"), which reads like a server
        # fault rather than a protocol mismatch. Pin the Chat Completions API explicitly.
        use_responses_api=False,
        # `reasoning` is an OpenRouter extension, not an OpenAI parameter. Setting the client's
        # `reasoning=` field sends it as a named argument, which the structured-output `.parse()`
        # path rejects outright ("unexpected keyword argument 'reasoning'"); `extra_body` is the
        # supported channel for provider-specific fields and reaches the wire untouched. Models
        # that don't implement it ignore it rather than erroring.
        extra_body={"reasoning": {"effort": effort}} if effort else None,
        # OpenRouter uses these for app attribution on its dashboards; harmless elsewhere.
        default_headers={
            "HTTP-Referer": os.environ.get(
                "FLOOD_OPENROUTER_SITE_URL", "https://github.com/flood-response-coordinator"
            ),
            "X-Title": os.environ.get("FLOOD_OPENROUTER_APP_NAME", "Flood Response Coordinator"),
        },
        model_kwargs=model_kwargs or {},
        timeout=float(os.environ.get("FLOOD_LLM_TIMEOUT", "180")),
        max_retries=2,
    )


def build_chat_model(
    *,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    effort: str | None = None,
    output_schema: dict[str, Any] | None = None,
):
    """Chat client for whichever provider is configured."""
    provider = active_provider()
    if provider == PROVIDER_OPENROUTER:
        return build_openrouter_model(
            max_tokens=max_tokens, effort=effort, output_schema=output_schema
        )
    if provider == PROVIDER_ANTHROPIC:
        return build_anthropic_model(
            max_tokens=max_tokens, effort=effort, output_schema=output_schema
        )
    raise LLMUnavailable("no LLM provider configured")


# --- completion --------------------------------------------------------------


def _strip_code_fence(text: str) -> str:
    """Remove a ```json fence if a model wrapped its JSON in one.

    Strict structured output shouldn't produce this, but OpenRouter fronts hundreds of models of
    varying compliance, and recovering here is cheaper than discarding an otherwise good draft.
    """
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped
    body = stripped.split("\n", 1)[1] if "\n" in stripped else ""
    if body.rstrip().endswith("```"):
        body = body.rstrip()[: -len("```")]
    return body.strip()


def structured_completion(
    model_cls: type[BaseModel],
    system: str,
    user: str,
    *,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    effort: str | None = None,
) -> BaseModel:
    """Ask the configured provider for JSON matching `model_cls`; return the validated object.

    Raises `LLMUnavailable` on any transport, parsing, or validation failure so the caller falls
    back rather than surfacing a partial recommendation.
    """
    provider = active_provider()
    if provider is None:
        raise LLMUnavailable("no LLM provider configured")

    try:
        chat = build_chat_model(
            max_tokens=max_tokens, effort=effort, output_schema=_schema_for(model_cls)
        )
        response = chat.invoke([("system", system), ("human", user)])
    except LLMUnavailable:
        raise
    except Exception as exc:  # transport, auth, rate limit — all fall back identically
        raise LLMUnavailable(f"{provider}: {type(exc).__name__}: {exc}") from exc

    text = _strip_code_fence(_extract_text(response))
    if not text:
        raise LLMUnavailable(f"{provider}: model returned no text content")

    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise LLMUnavailable(f"{provider}: response was not valid JSON: {exc.msg}") from exc

    try:
        parsed = model_cls.model_validate(payload)
    except ValidationError as exc:
        raise LLMUnavailable(
            f"{provider}: response did not match {model_cls.__name__}: {exc}"
        ) from exc

    # Record what actually served the request, which may differ from what was asked for.
    served = _served_by(response, configured_model())
    setattr(parsed, "_served_by_model", served)
    return parsed


def engine_label_for(result: Any) -> str:
    """`drafted_by` for a completion, preferring the model that actually served it."""
    provider = active_provider()
    if provider is None:
        return ENGINE_RULE_BASED
    served = getattr(result, "_served_by_model", None)
    if provider == PROVIDER_OPENROUTER:
        return f"{PROVIDER_OPENROUTER}:{served or DEFAULT_OPENROUTER_MODEL}"
    return str(served or DEFAULT_MODEL)
