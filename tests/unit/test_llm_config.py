"""Where coach advice's LLM requests go (coach.llm_client.llm_config): the
COACH_* environment first, then the project's .env, then OPENAI_*, then LM Studio."""

from __future__ import annotations

import pytest

from common.settings import Settings


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    for k in (
        "COACH_BASE_URL",
        "COACH_API_KEY",
        "COACH_MODEL",
        "OPENAI_BASE_URL",
        "OPENAI_API_KEY",
    ):
        monkeypatch.delenv(k, raising=False)
    return monkeypatch


def _settings(monkeypatch: pytest.MonkeyPatch, **values: str) -> None:
    from coach import llm_client

    s = Settings.model_construct(
        **{"llm_url": None, "llm_model": None, "llm_api_key": None, **values}
    )
    monkeypatch.setattr(llm_client, "get_settings", lambda: s)


def test_nothing_set_means_local_lm_studio(env: pytest.MonkeyPatch) -> None:
    from coach.llm_client import DEFAULT_MODEL, LM_STUDIO_URL, llm_config

    _settings(env)
    assert llm_config() == (LM_STUDIO_URL, "lm-studio", DEFAULT_MODEL)


def test_the_env_file_points_coach_advice_at_another_computer(env: pytest.MonkeyPatch) -> None:
    from coach.llm_client import llm_config

    _settings(
        env, llm_url="http://192.168.68.116:11434/v1", llm_model="gemma3:4b", llm_api_key="ollama"
    )
    env.setenv("OPENAI_BASE_URL", "http://elsewhere/v1")  # the generic one loses to .env
    assert llm_config() == ("http://192.168.68.116:11434/v1", "ollama", "gemma3:4b")


def test_coach_variables_in_the_environment_win(env: pytest.MonkeyPatch) -> None:
    from coach.llm_client import llm_config

    _settings(env, llm_url="http://192.168.68.116:11434/v1", llm_model="gemma3:4b")
    env.setenv("COACH_BASE_URL", "http://localhost:11435/v1")
    env.setenv("COACH_MODEL", "llama3.1:8b")
    url, _, model = llm_config()
    assert (url, model) == ("http://localhost:11435/v1", "llama3.1:8b")


def test_a_stray_ollama_openai_url_doesnt_hijack_the_default(env: pytest.MonkeyPatch) -> None:
    from coach.llm_client import LM_STUDIO_URL, llm_config

    _settings(env)
    env.setenv("OPENAI_BASE_URL", "http://localhost:11434/v1")
    assert llm_config()[0] == LM_STUDIO_URL


def test_the_older_env_name_for_the_url_still_works(env: pytest.MonkeyPatch) -> None:
    env.setenv("ALION_LM_STUDIO_URL", "http://localhost:11435/v1")
    assert Settings(_env_file=None).llm_url == "http://localhost:11435/v1"  # type: ignore[call-arg]
    env.setenv("ALION_LLM_URL", "http://192.168.68.116:11434/v1")
    assert Settings(_env_file=None).llm_url == "http://192.168.68.116:11434/v1"  # type: ignore[call-arg]
