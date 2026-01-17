"""Provider selection and per-provider request shape.

These are offline: no provider is ever called. They pin the routing rules and the two structured-
output dialects, because a silent switch to the wrong provider — or a schema the provider rejects —
degrades every recommendation to the rule-based fallback with only a caveat line to show for it.
"""

from __future__ import annotations

import pytest
from pydantic import BaseModel

from app.agents import llm

PROVIDER_VARS = (
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "OPENROUTER_API_KEY",
    "FLOOD_LLM_PROVIDER",
    "FLOOD_DISABLE_LLM",
    "FLOOD_FORCE_LLM",
    "FLOOD_OPENROUTER_MODEL",
)


@pytest.fixture
def clean_env(monkeypatch):
    """Start from no provider configuration, whatever the developer has exported."""
    for var in PROVIDER_VARS:
        monkeypatch.delenv(var, raising=False)
    return monkeypatch


class _Sample(BaseModel):
    headline: str
    count: int


# --- provider selection ------------------------------------------------------


def test_no_credentials_means_rule_based(clean_env):
    assert llm.active_provider() is None
    assert llm.llm_available() is False
    assert llm.active_engine() == llm.ENGINE_RULE_BASED


def test_anthropic_key_selects_anthropic(clean_env):
    clean_env.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    assert llm.active_provider() == llm.PROVIDER_ANTHROPIC
    assert llm.active_engine() == llm.DEFAULT_MODEL


def test_openrouter_key_selects_openrouter(clean_env):
    clean_env.setenv("OPENROUTER_API_KEY", "sk-or-test")
    assert llm.active_provider() == llm.PROVIDER_OPENROUTER
    assert llm.active_engine().startswith("openrouter:")


def test_anthropic_wins_when_both_keys_present(clean_env):
    """The direct path has fewer hops; the gateway is opt-in."""
    clean_env.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    clean_env.setenv("OPENROUTER_API_KEY", "sk-or-test")
    assert llm.active_provider() == llm.PROVIDER_ANTHROPIC


def test_explicit_provider_overrides_key_precedence(clean_env):
    clean_env.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    clean_env.setenv("OPENROUTER_API_KEY", "sk-or-test")
    clean_env.setenv("FLOOD_LLM_PROVIDER", "openrouter")
    assert llm.active_provider() == llm.PROVIDER_OPENROUTER


@pytest.mark.parametrize("value", ["none", "rule-based", "rule_based"])
def test_provider_can_be_disabled_explicitly(clean_env, value):
    clean_env.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    clean_env.setenv("FLOOD_LLM_PROVIDER", value)
    assert llm.active_provider() is None


def test_disable_flag_beats_a_present_key(clean_env):
    clean_env.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    clean_env.setenv("FLOOD_DISABLE_LLM", "1")
    assert llm.active_provider() is None


def test_unknown_provider_raises_rather_than_silently_falling_back(clean_env):
    """A typo in FLOOD_LLM_PROVIDER must be loud — silently drafting rule-based would hide it."""
    clean_env.setenv("OPENROUTER_API_KEY", "sk-or-test")
    clean_env.setenv("FLOOD_LLM_PROVIDER", "gemini")
    with pytest.raises(ValueError, match="unknown FLOOD_LLM_PROVIDER"):
        llm.active_provider()


def test_engine_label_names_the_gateway_and_model(clean_env):
    """A responder should see which gateway and model produced a draft, not just 'an LLM'."""
    clean_env.setenv("OPENROUTER_API_KEY", "sk-or-test")
    clean_env.setenv("FLOOD_OPENROUTER_MODEL", "openai/gpt-5.2")
    import importlib

    importlib.reload(llm)
    try:
        assert llm.active_engine() == "openrouter:openai/gpt-5.2"
    finally:
        importlib.reload(llm)


# --- schema ------------------------------------------------------------------


def test_schema_is_strict_for_both_providers():
    """Both dialects require additionalProperties:false and every property in `required`."""
    schema = llm._schema_for(_Sample)
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == {"headline", "count"}


def test_schema_strips_constraints_providers_reject():
    class Constrained(BaseModel):
        name: str = "x"

    schema = llm._schema_for(Constrained)
    assert "default" not in schema["properties"]["name"]


# --- request shape -----------------------------------------------------------


def test_anthropic_client_never_sends_sampling_params(clean_env):
    """Claude Opus 5 returns a 400 for temperature/top_p/top_k."""
    clean_env.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    client = llm.build_anthropic_model(effort="high", output_schema=llm._schema_for(_Sample))
    assert client.temperature is None
    assert client.top_p is None
    assert client.top_k is None


def test_anthropic_client_uses_native_output_config(clean_env):
    clean_env.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    client = llm.build_anthropic_model(effort="high", output_schema=llm._schema_for(_Sample))
    config = client.output_config or {}
    assert config["effort"] == "high"
    assert config["format"]["type"] == "json_schema"


def test_openrouter_client_uses_chat_completions_not_responses_api(clean_env):
    """langchain-openai defaults to OpenAI's Responses API, which OpenRouter does not serve."""
    clean_env.setenv("OPENROUTER_API_KEY", "sk-or-test")
    client = llm.build_openrouter_model(output_schema=llm._schema_for(_Sample))
    assert client.use_responses_api is False


def test_openrouter_client_sends_openai_style_response_format(clean_env):
    clean_env.setenv("OPENROUTER_API_KEY", "sk-or-test")
    client = llm.build_openrouter_model(output_schema=llm._schema_for(_Sample))
    fmt = client.model_kwargs["response_format"]
    assert fmt["type"] == "json_schema"
    assert fmt["json_schema"]["strict"] is True
    assert fmt["json_schema"]["schema"]["additionalProperties"] is False


def test_openrouter_effort_rides_in_extra_body(clean_env):
    """`reasoning` is an OpenRouter extension; as a named kwarg the .parse() path rejects it."""
    clean_env.setenv("OPENROUTER_API_KEY", "sk-or-test")
    client = llm.build_openrouter_model(effort="high", output_schema=llm._schema_for(_Sample))
    assert client.extra_body == {"reasoning": {"effort": "high"}}
    assert "reasoning" not in client.model_kwargs


def test_openrouter_client_never_sends_temperature(clean_env):
    """OpenRouter advertises temperature for Opus 5, but both paths stay prompt-steered."""
    clean_env.setenv("OPENROUTER_API_KEY", "sk-or-test")
    client = llm.build_openrouter_model(output_schema=llm._schema_for(_Sample))
    assert "temperature" not in client.model_kwargs


def test_openrouter_without_key_raises_llm_unavailable(clean_env):
    clean_env.setenv("FLOOD_LLM_PROVIDER", "openrouter")
    with pytest.raises(llm.LLMUnavailable, match="OPENROUTER_API_KEY"):
        llm.build_openrouter_model()


def test_structured_completion_refuses_without_a_provider(clean_env):
    with pytest.raises(llm.LLMUnavailable, match="no LLM provider"):
        llm.structured_completion(_Sample, "sys", "user")


# --- provenance --------------------------------------------------------------


def test_served_model_is_preferred_over_the_requested_one(clean_env):
    """OpenRouter may route elsewhere; `drafted_by` must name what actually answered."""
    clean_env.setenv("OPENROUTER_API_KEY", "sk-or-test")

    class _Response:
        response_metadata = {"model_name": "anthropic/claude-sonnet-5"}

    assert llm._served_by(_Response(), "fallback") == "anthropic/claude-sonnet-5"

    parsed = _Sample(headline="h", count=1)
    parsed._served_by_model = "anthropic/claude-sonnet-5"
    assert llm.engine_label_for(parsed) == "openrouter:anthropic/claude-sonnet-5"


def test_engine_label_is_rule_based_without_a_provider(clean_env):
    assert llm.engine_label_for(_Sample(headline="h", count=1)) == llm.ENGINE_RULE_BASED


# --- lenient parsing ---------------------------------------------------------


def test_code_fenced_json_is_recovered():
    """Strict mode shouldn't fence, but OpenRouter fronts hundreds of models of varying compliance."""
    assert llm._strip_code_fence('```json\n{"a": 1}\n```') == '{"a": 1}'
    assert llm._strip_code_fence('{"a": 1}') == '{"a": 1}'
