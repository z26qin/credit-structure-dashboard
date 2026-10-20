from pathlib import Path

import pytest

from creditdash.config import ModelSettings, Settings, load_model_settings
from creditdash.errors import ConfigError


def test_user_agent_requires_email(monkeypatch):
    monkeypatch.setenv("SEC_USER_AGENT", "no email here")
    with pytest.raises(ConfigError, match="SEC_USER_AGENT"):
        Settings.from_env().user_agent()


def test_user_agent_ok(monkeypatch):
    monkeypatch.setenv("SEC_USER_AGENT", "Jane Doe jane@example.com")
    assert Settings.from_env().user_agent() == "Jane Doe jane@example.com"


def test_defaults(monkeypatch):
    for k in ["LLM_BASE_URL", "LLM_MODEL", "CREDITDASH_HOME", "CREDITDASH_OUT", "LLM_MAX_INPUT_TOKENS", "SEC_MAX_RPS"]:
        monkeypatch.delenv(k, raising=False)
    s = Settings.from_env()
    assert (s.llm_base_url, s.llm_model, s.llm_max_input_tokens) == ("https://api.deepseek.com", "deepseek-flash", 100000)
    assert (s.home, s.out, s.sec_max_rps) == (Path(".creditdash"), Path("out"), 5.0)


def test_model_settings_file():
    assert load_model_settings() == ModelSettings(stress_haircut=0.30, horizon_years=10, ipf_max_rounds=50)
