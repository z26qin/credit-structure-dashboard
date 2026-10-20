"""Settings from the environment (and `.env`), and model settings from `config/model.yaml`.

An explicitly exported variable wins over `.env`.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel

from .errors import ConfigError

ENV_FILE = Path(".env")
MODEL_SETTINGS_FILE = Path("config/model.yaml")


@dataclass(frozen=True)
class Settings:
    sec_user_agent: str
    sec_max_rps: float
    home: Path
    out: Path
    llm_api_key: str | None
    llm_base_url: str
    llm_model: str
    llm_max_input_tokens: int

    @classmethod
    def from_env(cls) -> Settings:
        if ENV_FILE.exists():
            load_dotenv(ENV_FILE, override=False)
        env = os.environ
        return cls(
            sec_user_agent=env.get("SEC_USER_AGENT", "").strip(),
            sec_max_rps=float(env.get("SEC_MAX_RPS", "5")),
            home=Path(env.get("CREDITDASH_HOME", ".creditdash")),
            out=Path(env.get("CREDITDASH_OUT", "out")),
            llm_api_key=env.get("LLM_API_KEY") or None,
            llm_base_url=env.get("LLM_BASE_URL", "https://api.deepseek.com").rstrip("/"),
            llm_model=env.get("LLM_MODEL", "deepseek-flash"),
            llm_max_input_tokens=int(env.get("LLM_MAX_INPUT_TOKENS", "100000")),
        )

    def user_agent(self) -> str:
        if not self.sec_user_agent or "@" not in self.sec_user_agent:
            raise ConfigError("Set SEC_USER_AGENT='Your Name you@example.com' in .env. "
                              "The SEC requires a User-Agent with a contact email.")
        return self.sec_user_agent


class ModelSettings(BaseModel):
    stress_haircut: float = 0.30
    horizon_years: int = 10
    ipf_max_rounds: int = 50


def load_model_settings(path: Path = MODEL_SETTINGS_FILE) -> ModelSettings:
    if not path.exists():
        return ModelSettings()
    return ModelSettings(**(yaml.safe_load(path.read_text()) or {}))
