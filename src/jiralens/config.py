"""Environment-backed settings.

The API token is held as a ``SecretStr`` so that it cannot leak through ``repr()``,
pydantic validation errors, tracebacks or logged model dumps.
"""

from __future__ import annotations

import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from jiralens.exceptions import ConfigurationError

DEFAULT_TIMEZONE = "Africa/Lusaka"
LOG_LEVELS = frozenset({"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"})

#: Scoped API tokens are called through Atlassian's gateway, not the site URL.
ATLASSIAN_GATEWAY = "https://api.atlassian.com"

UUID_PATTERN = re.compile(r"[0-9a-fA-F]{8}(-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}")


class Settings(BaseSettings):
    """Configuration loaded from the process environment and an optional ``.env``."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    #: The Jira *site* URL, e.g. https://acme.atlassian.net. Always required: issue
    #: links in every report are built from it, whichever token type is in use.
    jira_base_url: str | None = None
    jira_email: str | None = None
    jira_api_token: SecretStr | None = None
    jira_default_project: str = "DEMO"

    #: Set when using a **scoped** API token. Scoped tokens are called through
    #: Atlassian's gateway at api.atlassian.com/ex/jira/{cloudId} rather than the site
    #: URL, so the API base and the site URL are two different things. Leave unset for
    #: a classic unscoped token, which is called against the site URL directly.
    #: Find it at https://<your-site>.atlassian.net/_edge/tenant_info
    jira_cloud_id: str | None = None

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

    @field_validator("jira_cloud_id")
    @classmethod
    def _validate_cloud_id(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        cloud_id = value.strip()
        # A cloudId is a UUID. Catching a pasted URL or site name here produces a
        # useful message instead of a 404 from the gateway three calls later.
        if not UUID_PATTERN.fullmatch(cloud_id):
            raise ValueError(
                f"JIRA_CLOUD_ID must be a UUID, not {cloud_id!r}. Find yours at "
                "https://<your-site>.atlassian.net/_edge/tenant_info"
            )
        return cloud_id

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

    @property
    def uses_scoped_token(self) -> bool:
        """True when a cloud id is configured, meaning calls route via the gateway."""
        return self.jira_cloud_id is not None

    @property
    def api_base_url(self) -> str | None:
        """Where REST calls go.

        Scoped tokens are rejected by the site URL and unscoped tokens are rejected
        by the gateway, so getting this wrong produces a 401 that looks like a bad
        credential. The cloud id is what distinguishes the two.
        """
        if self.jira_base_url is None:
            return None
        if self.jira_cloud_id is None:
            return self.jira_base_url
        return f"{ATLASSIAN_GATEWAY}/ex/jira/{self.jira_cloud_id}"

    @property
    def site_url(self) -> str | None:
        """Where humans go. Issue links in reports are built from this, never from
        ``api_base_url`` — a gateway URL would produce dead links."""
        return self.jira_base_url

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


__all__ = ["ATLASSIAN_GATEWAY", "DEFAULT_TIMEZONE", "Settings", "load_settings"]
