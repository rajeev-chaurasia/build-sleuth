"""Runtime settings, loaded from environment variables with the BUILDSLEUTH_ prefix."""

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="BUILDSLEUTH_", env_file=".env", extra="ignore")

    github_token: SecretStr | None = None


def load_settings() -> Settings:
    return Settings()
