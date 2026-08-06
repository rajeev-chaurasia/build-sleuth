"""Runtime settings, loaded from environment variables with the BUILDSLEUTH_ prefix.

Every secret enters the process here and nowhere else. Values are SecretStr so
an accidental log or repr prints a placeholder instead of the key.
"""

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from buildsleuth.llm.registry import Provider


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="BUILDSLEUTH_", env_file=".env", extra="ignore")

    github_token: SecretStr | None = None
    gemini_api_key: SecretStr | None = None
    groq_api_key: SecretStr | None = None
    openrouter_api_key: SecretStr | None = None

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
