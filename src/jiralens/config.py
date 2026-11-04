"""Environment-backed settings.

The API token is held as a ``SecretStr`` so that it cannot leak through ``repr()``,
pydantic validation errors, tracebacks or logged model dumps.
"""

from __future__ import annotations

from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from jiralens.exceptions import ConfigurationError

DEFAULT_TIMEZONE = "Africa/Lusaka"
LOG_LEVELS = frozenset({"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"})


class Settings(BaseSettings):
    """Configuration loaded from the process environment and an optional ``.env``."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    jira_base_url: str | None = None
    jira_email: str | None = None
    jira_api_token: SecretStr | None = None
    jira_default_project: str = "DEMO"

    jiralens_timezone: str = DEFAULT_TIMEZONE
    jiralens_log_level: str = "INFO"
    jiralens_demo_mode: bool = False
    jiralens_live_jira_disabled: bool = False

    request_timeout_seconds: float = Field(default=30.0, gt=0)
    max_retry_attempts: int = Field(default=4, ge=1, le=10)

    @field_validator("jira_base_url")
    @classmethod
    def _validate_base_url(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        url = value.strip().rstrip("/")
        if not url.startswith("https://"):
            raise ValueError("JIRA_BASE_URL must start with https://")
        if "/" in url.removeprefix("https://"):
            raise ValueError(
                "JIRA_BASE_URL must be a bare site URL, e.g. https://acme.atlassian.net"
            )
        return url

    @field_validator("jiralens_timezone")
    @classmethod
    def _validate_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(
                f"Unknown timezone {value!r}. Use an IANA name, e.g. Africa/Lusaka."
            ) from exc
        return value

    @field_validator("jiralens_log_level")
    @classmethod
    def _validate_log_level(cls, value: str) -> str:
        level = value.strip().upper()
        if level not in LOG_LEVELS:
            raise ValueError(f"JIRALENS_LOG_LEVEL must be one of {sorted(LOG_LEVELS)}")
        return level

    @property
    def tzinfo(self) -> ZoneInfo:
        return ZoneInfo(self.jiralens_timezone)

    def secret_values(self) -> list[str]:
        """Literal strings that must never reach a log line or a report."""
        values: list[str] = []
        if self.jira_api_token is not None:
            token = self.jira_api_token.get_secret_value()
            if token:
                values.append(token)
        return values

    def require_live(self) -> tuple[str, str, SecretStr]:
        """Return the credentials needed for live Jira access, or explain what is missing.

        Raises:
            ConfigurationError: if live access is disabled or any credential is absent.
                The message names the environment variables, never their values.
        """
        if self.jiralens_live_jira_disabled:
            raise ConfigurationError(
                "Live Jira access is disabled by JIRALENS_LIVE_JIRA_DISABLED. "
                "This deployment can only run in demo mode."
            )
        missing = [
            name
            for name, present in (
                ("JIRA_BASE_URL", self.jira_base_url),
                ("JIRA_EMAIL", self.jira_email),
                ("JIRA_API_TOKEN", self.jira_api_token),
            )
            if not present
        ]
        if missing:
            raise ConfigurationError(
                "Missing required configuration: "
                + ", ".join(missing)
                + ". Copy .env.example to .env and fill these in locally."
            )
        assert self.jira_base_url is not None  # narrowed by the check above
        assert self.jira_email is not None
        assert self.jira_api_token is not None
        return self.jira_base_url, self.jira_email, self.jira_api_token


def load_settings(**overrides: object) -> Settings:
    """Load settings, converting pydantic validation failures into ConfigurationError."""
    try:
        return Settings(**overrides)  # type: ignore[arg-type]
    except Exception as exc:  # pydantic.ValidationError, but keep the surface narrow
        raise ConfigurationError(f"Invalid configuration: {exc}") from exc


__all__ = ["DEFAULT_TIMEZONE", "Settings", "load_settings"]
