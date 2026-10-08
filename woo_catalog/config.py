"""Configuration loading for local WooCommerce credentials."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from dotenv import dotenv_values


class ConfigurationError(ValueError):
    """Raised when the local .env configuration is incomplete or unsafe."""


@dataclass(frozen=True)
class WooConfig:
    base_url: str
    consumer_key: str
    consumer_secret: str
    timeout_seconds: int = 30
    read_retry_attempts: int = 3
    read_retry_backoff_seconds: float = 0.5

    def __post_init__(self) -> None:
        parsed = urlparse(self.base_url)
        if parsed.scheme != "https" or not parsed.netloc:
            raise ConfigurationError("WOO_BASE_URL must be an HTTPS URL")
        if not self.consumer_key or not self.consumer_secret:
            raise ConfigurationError("WooCommerce API keys are required")
        if self.timeout_seconds <= 0:
            raise ConfigurationError("WOO_TIMEOUT_SECONDS must be positive")
        if not 1 <= self.read_retry_attempts <= 5:
            raise ConfigurationError("WOO_READ_RETRY_ATTEMPTS must be between 1 and 5")
        if not 0.1 <= self.read_retry_backoff_seconds <= 5:
            raise ConfigurationError("WOO_READ_RETRY_BACKOFF_SECONDS must be between 0.1 and 5")

    @property
    def api_base_url(self) -> str:
        return f"{self.base_url.rstrip('/')}/wp-json/wc/v3"

    @property
    def store_identity(self) -> str:
        parsed = urlparse(self.base_url)
        port = f":{parsed.port}" if parsed.port else ""
        return f"{parsed.scheme}://{parsed.hostname}{port}"

    @classmethod
    def from_env(cls, env_path: str | Path = ".env") -> "WooConfig":
        required = ("WOO_BASE_URL", "WOO_CONSUMER_KEY", "WOO_CONSUMER_SECRET")
        optional = (
            "WOO_TIMEOUT_SECONDS",
            "WOO_READ_RETRY_ATTEMPTS",
            "WOO_READ_RETRY_BACKOFF_SECONDS",
        )
        environment = {name: os.getenv(name) for name in (*required, *optional)}
        file_values = {} if all(environment[name] for name in required) else dotenv_values(env_path)

        def value(name: str, default: str | None = None) -> str | None:
            return environment.get(name) or file_values.get(name) or default

        missing = [name for name in required if not value(name)]
        if missing:
            raise ConfigurationError(f"Missing required environment variables: {', '.join(missing)}")
        try:
            timeout = int(value("WOO_TIMEOUT_SECONDS", "30") or "30")
        except ValueError as error:
            raise ConfigurationError("WOO_TIMEOUT_SECONDS must be an integer") from error
        try:
            retry_attempts = int(value("WOO_READ_RETRY_ATTEMPTS", "3") or "3")
        except ValueError as error:
            raise ConfigurationError("WOO_READ_RETRY_ATTEMPTS must be an integer") from error
        try:
            retry_backoff = float(value("WOO_READ_RETRY_BACKOFF_SECONDS", "0.5") or "0.5")
        except ValueError as error:
            raise ConfigurationError("WOO_READ_RETRY_BACKOFF_SECONDS must be a number") from error
        return cls(
            base_url=str(value("WOO_BASE_URL")),
            consumer_key=str(value("WOO_CONSUMER_KEY")),
            consumer_secret=str(value("WOO_CONSUMER_SECRET")),
            timeout_seconds=timeout,
            read_retry_attempts=retry_attempts,
            read_retry_backoff_seconds=retry_backoff,
        )
