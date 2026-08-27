from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


def _secret(value: SecretStr | None, path: Path | None, name: str) -> str | None:
    if value is not None:
        return value.get_secret_value()
    if path is not None:
        try:
            return path.read_text(encoding="utf-8").rstrip("\r\n")
        except OSError as exc:
            raise ValueError(f"unable to read {name} file {path}: {exc}") from exc
    return None


class ApiConfig(StrictModel):
    host: str
    username: str = "admin"
    password: SecretStr | None = None
    password_file: Path | None = None
    verify_ssl: bool = True
    timeout_seconds: float = Field(default=30, gt=0)
    timeout_retries: int = Field(default=3, ge=0, alias="timeout_error_retries")

    def resolved_password(self) -> str:
        value = _secret(self.password, self.password_file, "Deco password")
        if value is None:
            raise ValueError("api.password or api.password_file is required")
        return value


class MetricsConfig(StrictModel):
    collection_interval: float = Field(default=60, gt=0)


class ProbesConfig(StrictModel):
    enabled: bool = True
    interval: float = Field(default=30, gt=0)
    attempts: int = Field(default=1, ge=1)
    timeout: float = Field(default=2, gt=0)


class LogsConfig(StrictModel):
    enabled: bool = False
    poll_interval: float = Field(default=60, gt=0)
    level: Literal[
        "ALL", "ALERT", "CRITICAL", "ERROR", "WARNING", "NOTICE", "INFO", "DEBUG"
    ] = "INFO"
    timezone_fallback: str = "UTC"
    page_size: int = Field(default=100, ge=1)
    state_path: Path = Path("/data/log-watermark.json")


class LokiConfig(StrictModel):
    url: str | None = None
    tenant_id: str | None = None
    username: str | None = None
    password: SecretStr | None = None
    password_file: Path | None = None
    bearer_token: SecretStr | None = None
    bearer_token_file: Path | None = None
    verify_ssl: bool = True
    timeout_seconds: float = Field(default=10, gt=0)
    batch_size: int = Field(default=500, ge=1)
    retries: int = Field(default=3, ge=0)

    @model_validator(mode="after")
    def validate_auth(self) -> LokiConfig:
        if (
            self.password is not None or self.password_file is not None
        ) and not self.username:
            raise ValueError(
                "loki.username is required when a Loki password is configured"
            )
        return self

    def resolved_password(self) -> str | None:
        return _secret(self.password, self.password_file, "Loki password")

    def resolved_bearer_token(self) -> str | None:
        return _secret(self.bearer_token, self.bearer_token_file, "Loki bearer token")


class ServerConfig(StrictModel):
    host: str = "0.0.0.0"
    port: int = Field(default=9100, ge=1, le=65535)


class LoggingConfig(StrictModel):
    level: str = "INFO"


class Config(StrictModel):
    instance: str = "deco"
    api: ApiConfig
    metrics: MetricsConfig = MetricsConfig()
    probes: ProbesConfig = ProbesConfig()
    logs: LogsConfig = LogsConfig()
    loki: LokiConfig = LokiConfig()
    server: ServerConfig = ServerConfig()
    logging: LoggingConfig = LoggingConfig()

    @model_validator(mode="after")
    def validate_log_destination(self) -> Config:
        if self.logs.enabled and not self.loki.url:
            raise ValueError("loki.url is required when logs.enabled is true")
        return self


_SECTIONS = {"api", "metrics", "probes", "logs", "loki", "server", "logging"}
_ENV_ALIASES = {
    "DECO_API_TIMEOUT_ERROR_RETRIES": ("api", "timeout_retries"),
    "DECO_API_TIMEOUT_RETRIES": ("api", "timeout_retries"),
    "DECO_METRICS_INTERVAL": ("metrics", "collection_interval"),
    "DECO_LOG_LEVEL": ("logging", "level"),
}


def _environment_overrides() -> dict[str, Any]:
    result: dict[str, Any] = {}
    if "DECO_INSTANCE" in os.environ:
        result["instance"] = os.environ["DECO_INSTANCE"]
    for env_name, value in os.environ.items():
        if env_name in _ENV_ALIASES:
            section, field = _ENV_ALIASES[env_name]
        elif env_name.startswith("DECO_"):
            remainder = env_name[5:].lower()
            section, separator, field = remainder.partition("_")
            if not separator or section not in _SECTIONS:
                continue
        else:
            continue
        result.setdefault(section, {})[field] = value
    return result


def load_config(config_path: str | Path | None = None) -> Config:
    final_path = Path(config_path or os.getenv("CONFIG_PATH", "config.yml"))
    data: dict[str, Any] = {}
    if final_path.exists():
        loaded = yaml.safe_load(final_path.read_text(encoding="utf-8")) or {}
        if not isinstance(loaded, dict):
            raise ValueError(f"configuration root in {final_path} must be a mapping")
        data.update(loaded)

    for key, value in _environment_overrides().items():
        if isinstance(value, dict):
            data.setdefault(key, {}).update(value)
        else:
            data[key] = value
    return Config.model_validate(data)
