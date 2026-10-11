"""Centralized settings, sourced from environment / .env file."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="ALION_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # If set, used directly (e.g. postgresql+psycopg://user:pw@host/db).
    # If unset, we build a sqlite URL from db_path. This is the DI seam that
    # lets us swap SQLite for Postgres without touching the domain or adapters.
    database_url: str | None = Field(default=None)
    db_path: Path = Field(default=Path("./data/alion.db"))
    log_level: str = Field(default="INFO")
    # The LLM behind coach advice: any OpenAI-compatible server — LM Studio,
    # Ollama on this or another computer (http://<host>:11434/v1, or an SSH
    # tunnel to it), or OpenAI. Unset → coach.llm_client's defaults (local LM
    # Studio). ALION_LM_STUDIO_URL is the older name for the URL.
    llm_url: str | None = Field(
        default=None, validation_alias=AliasChoices("ALION_LLM_URL", "ALION_LM_STUDIO_URL")
    )
    llm_model: str | None = Field(default=None)
    llm_api_key: str | None = Field(default=None)

    @property
    def effective_database_url(self) -> str:
        if self.database_url:
            return self.database_url
        return f"sqlite:///{self.db_path}"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
