"""Settings: provider key lookup and the spellings we accept for each.

A key that is present but silently ignored is a confusing way to fail, so the
common alternate spellings are pinned here.
"""

import pytest

from buildsleuth.config import Settings
from buildsleuth.llm.registry import Provider


@pytest.mark.parametrize(
    ("env_var", "provider"),
    [
        ("BUILDSLEUTH_GEMINI_API_KEY", Provider.GEMINI),
        ("BUILDSLEUTH_GOOGLE_API_KEY", Provider.GEMINI),
        ("BUILDSLEUTH_OPENROUTER_API_KEY", Provider.OPENROUTER),
        ("BUILDSLEUTH_OPEN_ROUTER_API_KEY", Provider.OPENROUTER),
        ("BUILDSLEUTH_GROQ_API_KEY", Provider.GROQ),
    ],
)
def test_accepted_spellings_reach_their_provider(
    env_var: str, provider: Provider, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(env_var, "test-key-value")
    assert Settings(_env_file=None).api_key_for(provider) == "test-key-value"


def test_missing_key_is_none_rather_than_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BUILDSLEUTH_GROQ_API_KEY", raising=False)
    assert Settings(_env_file=None).api_key_for(Provider.GROQ) is None


def test_local_provider_needs_no_key() -> None:
    assert Settings(_env_file=None).api_key_for(Provider.OLLAMA) is None


def test_key_is_not_exposed_by_repr(monkeypatch: pytest.MonkeyPatch) -> None:
    """A settings object can end up in a log line or a traceback."""
    monkeypatch.setenv("BUILDSLEUTH_GEMINI_API_KEY", "sk-super-secret")
    settings = Settings(_env_file=None)

    assert "sk-super-secret" not in repr(settings)
    assert "sk-super-secret" not in str(settings)
    assert settings.api_key_for(Provider.GEMINI) == "sk-super-secret"
