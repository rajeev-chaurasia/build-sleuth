"""Runtime settings, loaded from environment variables with the BUILDSLEUTH_ prefix.

Every secret enters the process here and nowhere else. Values are SecretStr so
an accidental log or repr prints a placeholder instead of the key.
"""

from pydantic import AliasChoices, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from buildsleuth.llm.registry import Provider


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="BUILDSLEUTH_",
        env_file=".env",
        extra="ignore",
        case_sensitive=False,
    )

    github_token: SecretStr | None = None
    # Repositories this agent may open pull requests against, comma separated.
    # Empty means none, so a fresh checkout cannot write anywhere.
    pr_allowlist: str = ""
    gemini_api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "BUILDSLEUTH_GEMINI_API_KEY",
            "BUILDSLEUTH_GOOGLE_API_KEY",
        ),
    )
    groq_api_key: SecretStr | None = None
    # Provider names get spelled with and without a separator, and a key that
    # is present but ignored is a confusing way to fail.
    openrouter_api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "BUILDSLEUTH_OPENROUTER_API_KEY",
            "BUILDSLEUTH_OPEN_ROUTER_API_KEY",
        ),
    )

    def api_key_for(self, provider: Provider) -> str | None:
        """Key for a provider, or None when it has none configured or needs none."""
        secret = _PROVIDER_FIELDS.get(provider)
        value = getattr(self, secret) if secret else None
        return value.get_secret_value() if value is not None else None


# Maps a provider to the Settings field holding its key. Ollama runs locally
# and takes no key, so it is deliberately absent.
_PROVIDER_FIELDS: dict[Provider, str] = {
    Provider.GEMINI: "gemini_api_key",
    Provider.GROQ: "groq_api_key",
    Provider.OPENROUTER: "openrouter_api_key",
}


def load_settings() -> Settings:
    return Settings()
