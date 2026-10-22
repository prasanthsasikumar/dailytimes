"""Agent configuration (agent.yaml)."""

from __future__ import annotations

import datetime as dt
import os
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RelayConfig(_M):
    url: str
    token: str | None = None
    token_env: str | None = "DAILYTIMES_TOKEN"

    @model_validator(mode="after")
    def _token(self) -> "RelayConfig":
        if not self.token and self.token_env:
            self.token = os.environ.get(self.token_env)
        if not self.token:
            raise ValueError(f"relay.token is not set (nor ${self.token_env})")
        return self


class OutputConfig(_M):
    profile: str
    adapter: str
    options: dict[str, Any] = Field(default_factory=dict)
    dry_run: bool = False


class PresenceConfig(_M):
    backend: Literal["manual", "simulated", "gpio", "ble"] = "manual"
    options: dict[str, Any] = Field(default_factory=dict)


class Rules(_M):
    print_after: dt.time = dt.time(6, 0)
    cooldown_minutes: float = Field(default=15, ge=0)
    fallback: Literal["retry", "placeholder", "skip"] = "retry"
    fallback_after: dt.time = dt.time(11, 0)

    @field_validator("print_after", "fallback_after", mode="before")
    @classmethod
    def _time(cls, v):
        if isinstance(v, int):  # YAML 1.1 reads 06:30 as sexagesimal 390
            return dt.time(v // 60, v % 60)
        return v


class AgentConfig(_M):
    relay: RelayConfig
    outputs: list[OutputConfig] = Field(min_length=1)
    presence: PresenceConfig = Field(default_factory=PresenceConfig)
    rules: Rules = Field(default_factory=Rules)
    timezone: str | None = None  # default: the device's timezone on the relay
    poll_interval_seconds: float = Field(default=300, ge=5)
    state_file: str = "~/.local/state/dailytimes/agent.json"

    @property
    def state_path(self) -> Path:
        return Path(os.path.expanduser(self.state_file))


def load_config(path: str | os.PathLike) -> AgentConfig:
    return AgentConfig.model_validate(yaml.safe_load(Path(path).read_text()))
