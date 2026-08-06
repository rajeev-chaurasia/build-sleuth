"""Model registry: the single place a model id maps to a provider, endpoint, quirks and price.

BuildSleuth is free-tier-first, so every entry here bills at zero. The list
prices are recorded alongside so a scorecard can quote what the same run would
have cost on the paid tier, which is the number a reader actually compares.
"""

from dataclasses import dataclass
from enum import StrEnum

from buildsleuth.llm.types import Usage

TOKENS_PER_MILLION = 1_000_000
FREE = 0.0
NO_API_KEY_ENV_VAR = ""

GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai"
GROQ_BASE_URL = "https://api.groq.com/openai/v1"
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
OLLAMA_BASE_URL = "http://localhost:11434/v1"


class Provider(StrEnum):
    GEMINI = "gemini"
    GROQ = "groq"
    OPENROUTER = "openrouter"
    OLLAMA = "ollama"


API_KEY_ENV_VARS: dict[Provider, str] = {
    Provider.GEMINI: "BUILDSLEUTH_GEMINI_API_KEY",
    Provider.GROQ: "BUILDSLEUTH_GROQ_API_KEY",
    Provider.OPENROUTER: "BUILDSLEUTH_OPENROUTER_API_KEY",
    Provider.OLLAMA: NO_API_KEY_ENV_VAR,
}


@dataclass(frozen=True, slots=True)
class ProviderQuirks:
    """What a provider can do and how hard we are allowed to lean on it.

    A `None` limit means the provider publishes no cap on that axis, not that
    the cap is zero. `tpd` counts input plus output tokens together.
    """

    native_json_schema: bool
    supports_tools: bool
    rpm: int | None
    rpd: int | None
    tpd: int | None
    context_window: int
    max_retries: int


@dataclass(frozen=True, slots=True)
class ModelSpec:
    """One selectable model. `name` is our CLI id, `api_model` is the provider's."""

    name: str
    api_model: str
    provider: Provider
    base_url: str
    quirks: ProviderQuirks
    usd_per_million_input: float
    usd_per_million_output: float
    list_usd_per_million_input: float
    list_usd_per_million_output: float


# Google publishes free-tier limits in the AI Studio console rather than the
# docs, and reported figures vary by account. These are the conservative end,
# so a run refuses early rather than taking a 429 midway.
GEMINI_FLASH_RPM = 10
GEMINI_FLASH_RPD = 1000
GEMINI_FLASH_LITE_RPM = 15
GEMINI_FLASH_LITE_RPD = 1000
GEMINI_CONTEXT_WINDOW = 1_048_576

MODEL_SPECS: tuple[ModelSpec, ...] = (
    # The newest Flash model is not on the free tier: an unbilled key gets a
    # 429 on the first request. Kept for anyone running with billing enabled.
    ModelSpec(
        name="gemini-3.6-flash",
        api_model="gemini-3.6-flash",
        provider=Provider.GEMINI,
        base_url=GEMINI_BASE_URL,
        quirks=ProviderQuirks(
            native_json_schema=True,
            supports_tools=True,
            rpm=GEMINI_FLASH_RPM,
            rpd=GEMINI_FLASH_RPD,
            tpd=None,
            context_window=GEMINI_CONTEXT_WINDOW,
            max_retries=3,
        ),
        usd_per_million_input=FREE,
        usd_per_million_output=FREE,
        list_usd_per_million_input=0.30,
        list_usd_per_million_output=2.50,
    ),
    ModelSpec(
        name="gemini-3.5-flash",
        api_model="gemini-3.5-flash",
        provider=Provider.GEMINI,
        base_url=GEMINI_BASE_URL,
        quirks=ProviderQuirks(
            native_json_schema=True,
            supports_tools=True,
            rpm=GEMINI_FLASH_RPM,
            rpd=GEMINI_FLASH_RPD,
            tpd=None,
            context_window=GEMINI_CONTEXT_WINDOW,
            max_retries=3,
        ),
        usd_per_million_input=FREE,
        usd_per_million_output=FREE,
        list_usd_per_million_input=0.30,
        list_usd_per_million_output=2.50,
    ),
    ModelSpec(
        name="gemini-3.1-flash-lite",
        api_model="gemini-3.1-flash-lite",
        provider=Provider.GEMINI,
        base_url=GEMINI_BASE_URL,
        quirks=ProviderQuirks(
            native_json_schema=True,
            supports_tools=True,
            rpm=GEMINI_FLASH_LITE_RPM,
            rpd=GEMINI_FLASH_LITE_RPD,
            tpd=None,
            context_window=GEMINI_CONTEXT_WINDOW,
            max_retries=3,
        ),
        usd_per_million_input=FREE,
        usd_per_million_output=FREE,
        list_usd_per_million_input=0.10,
        list_usd_per_million_output=0.40,
    ),
    ModelSpec(
        name="groq-llama-3.3-70b",
        api_model="llama-3.3-70b-versatile",
        provider=Provider.GROQ,
        base_url=GROQ_BASE_URL,
        # Groq gates json_schema to its gpt-oss models; Llama 3.3 gets plain
        # JSON mode, so the schema has to travel in the prompt instead.
        quirks=ProviderQuirks(
            native_json_schema=False,
            supports_tools=True,
            rpm=30,
            rpd=1000,
            tpd=100_000,
            context_window=131_072,
            max_retries=4,
        ),
        usd_per_million_input=FREE,
        usd_per_million_output=FREE,
        list_usd_per_million_input=0.59,
        list_usd_per_million_output=0.79,
    ),
    ModelSpec(
        name="openrouter-gpt-oss-20b",
        api_model="openai/gpt-oss-20b:free",
        provider=Provider.OPENROUTER,
        base_url=OPENROUTER_BASE_URL,
        # 50 requests/day is the un-topped-up allowance, so this model cannot
        # carry a full eval sweep on its own.
        quirks=ProviderQuirks(
            native_json_schema=True,
            supports_tools=True,
            rpm=20,
            rpd=50,
            tpd=None,
            context_window=131_072,
            max_retries=4,
        ),
        usd_per_million_input=FREE,
        usd_per_million_output=FREE,
        list_usd_per_million_input=0.03,
        list_usd_per_million_output=0.13,
    ),
    # Same provider and free tier as the 20b entry, roughly six times the
    # parameters, so the pair answers whether size helps on this task.
    ModelSpec(
        name="openrouter-nemotron-120b",
        api_model="nvidia/nemotron-3-super-120b-a12b:free",
        provider=Provider.OPENROUTER,
        base_url=OPENROUTER_BASE_URL,
        quirks=ProviderQuirks(
            native_json_schema=True,
            supports_tools=True,
            rpm=20,
            rpd=50,
            tpd=None,
            context_window=262_144,
            max_retries=2,
        ),
        usd_per_million_input=FREE,
        usd_per_million_output=FREE,
        list_usd_per_million_input=0.10,
        list_usd_per_million_output=0.40,
    ),
    ModelSpec(
        name="ollama-llama3.1-8b",
        api_model="llama3.1:8b",
        provider=Provider.OLLAMA,
        base_url=OLLAMA_BASE_URL,
        # Ollama maps response_format json_schema onto its native `format`
        # parameter; older builds predate that and only honour json_object.
        quirks=ProviderQuirks(
            native_json_schema=True,
            supports_tools=True,
            rpm=None,
            rpd=None,
            tpd=None,
            context_window=131_072,
            max_retries=1,
        ),
        usd_per_million_input=FREE,
        usd_per_million_output=FREE,
        list_usd_per_million_input=FREE,
        list_usd_per_million_output=FREE,
    ),
)

MODEL_REGISTRY: dict[str, ModelSpec] = {spec.name: spec for spec in MODEL_SPECS}


class UnknownModelError(ValueError):
    """The requested model id is not in the registry."""


def get_model_spec(name: str) -> ModelSpec:
    """Look up a model by its BuildSleuth id, listing the valid ids on a miss."""
    spec = MODEL_REGISTRY.get(name)
    if spec is None:
        valid = ", ".join(sorted(MODEL_REGISTRY))
        raise UnknownModelError(f"unknown model {name!r}; valid models are: {valid}")
    return spec


def api_key_env_var(provider: Provider) -> str:
    """Environment variable holding this provider's key, empty for local Ollama."""
    return API_KEY_ENV_VARS[provider]


def _cost(usage: Usage, per_million_input: float, per_million_output: float) -> float:
    return (
        usage.input_tokens * per_million_input + usage.output_tokens * per_million_output
    ) / TOKENS_PER_MILLION


def estimate_cost_usd(spec: ModelSpec, usage: Usage) -> float:
    """What this usage actually costs us, which is zero on every free tier."""
    return _cost(usage, spec.usd_per_million_input, spec.usd_per_million_output)


def estimate_list_cost_usd(spec: ModelSpec, usage: Usage) -> float:
    """What this usage would cost at the provider's published paid rate."""
    return _cost(usage, spec.list_usd_per_million_input, spec.list_usd_per_million_output)
