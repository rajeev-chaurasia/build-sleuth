import pytest

from buildsleuth.llm.registry import (
    MODEL_REGISTRY,
    Provider,
    UnknownModelError,
    api_key_env_var,
    estimate_cost_usd,
    estimate_list_cost_usd,
    get_model_spec,
)
from buildsleuth.llm.types import Usage


def test_every_spec_has_an_api_model_and_base_url() -> None:
    for name, spec in MODEL_REGISTRY.items():
        assert spec.name == name
        assert spec.api_model
        assert spec.base_url.startswith("http")
        assert not spec.base_url.endswith("/")
        assert spec.quirks.context_window > 0
        assert spec.quirks.max_retries >= 0


def test_every_spec_is_free_to_us() -> None:
    for spec in MODEL_REGISTRY.values():
        assert spec.usd_per_million_input == 0.0
        assert spec.usd_per_million_output == 0.0


def test_get_model_spec_returns_the_registered_entry() -> None:
    spec = get_model_spec("groq-llama-3.3-70b")
    assert spec.provider is Provider.GROQ
    assert spec.api_model == "llama-3.3-70b-versatile"


def test_unknown_model_error_lists_valid_names() -> None:
    with pytest.raises(UnknownModelError) as excinfo:
        get_model_spec("gpt-9-turbo")
    message = str(excinfo.value)
    assert "gpt-9-turbo" in message
    for name in MODEL_REGISTRY:
        assert name in message


def test_api_key_env_var_per_provider() -> None:
    assert api_key_env_var(Provider.GEMINI) == "BUILDSLEUTH_GEMINI_API_KEY"
    assert api_key_env_var(Provider.GROQ) == "BUILDSLEUTH_GROQ_API_KEY"
    assert api_key_env_var(Provider.OPENROUTER) == "BUILDSLEUTH_OPENROUTER_API_KEY"
    assert api_key_env_var(Provider.OLLAMA) == ""


def test_estimate_cost_usd_is_zero_on_a_free_tier() -> None:
    spec = get_model_spec("gemini-3.6-flash")
    assert estimate_cost_usd(spec, Usage(input_tokens=500_000, output_tokens=250_000)) == 0.0


def test_estimate_list_cost_usd_arithmetic() -> None:
    spec = get_model_spec("gemini-3.6-flash")
    # 0.5M in at $0.30/M is $0.15; 0.25M out at $2.50/M is $0.625.
    usage = Usage(input_tokens=500_000, output_tokens=250_000)
    assert estimate_list_cost_usd(spec, usage) == pytest.approx(0.775)


def test_estimate_list_cost_usd_for_groq() -> None:
    spec = get_model_spec("groq-llama-3.3-70b")
    # 200k in at $0.59/M is $0.118; 100k out at $0.79/M is $0.079.
    usage = Usage(input_tokens=200_000, output_tokens=100_000)
    assert estimate_list_cost_usd(spec, usage) == pytest.approx(0.197)


def test_local_model_has_no_list_price() -> None:
    spec = get_model_spec("ollama-llama3.1-8b")
    usage = Usage(input_tokens=1_000_000, output_tokens=1_000_000)
    assert estimate_list_cost_usd(spec, usage) == 0.0


def test_groq_llama_lacks_native_json_schema() -> None:
    assert get_model_spec("groq-llama-3.3-70b").quirks.native_json_schema is False
    assert get_model_spec("gemini-3.6-flash").quirks.native_json_schema is True
