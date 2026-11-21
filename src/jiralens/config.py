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

#: Prefix Atlassian gives tokens belonging to a **service account**. These differ from
#: user API tokens in two ways that matter: they authenticate with a Bearer header
#: rather than ``email:token`` basic auth, and they must be called through the
#: api.atlassian.com gateway. A service-account token sent as basic auth returns 401,
#: which reads exactly like a mistyped credential — hence the explicit detection.
SERVICE_ACCOUNT_TOKEN_PREFIX = "ATST"  # noqa: S105 - a four-character prefix, not a secret

AUTH_SCHEMES = frozenset({"auto", "basic", "bearer"})


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

    #: ``auto`` picks bearer for a service-account token and basic otherwise, which is
    #: right in every case we know of. Override only if Atlassian changes the rules
    #: before this code catches up.
    jira_auth_scheme: str = "auto"

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

    @field_validator("jira_auth_scheme")
    @classmethod
    def _validate_auth_scheme(cls, value: str) -> str:
        scheme = value.strip().lower()
        if scheme not in AUTH_SCHEMES:
            raise ValueError(f"JIRA_AUTH_SCHEME must be one of {sorted(AUTH_SCHEMES)}")
        return scheme

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

    @property
    def uses_service_account_token(self) -> bool:
        """True when the token belongs to an Atlassian service account."""
        if self.jira_api_token is None:
            return False
        return self.jira_api_token.get_secret_value().startswith(SERVICE_ACCOUNT_TOKEN_PREFIX)

    @property
    def auth_scheme(self) -> str:
        """``bearer`` or ``basic`` — the resolved scheme, after applying ``auto``.

        Service-account tokens are Bearer credentials; the account's email is not part
        of the exchange at all, which is why basic auth returns 401 for them however
        correct the email looks.
        """
        if self.jira_auth_scheme != "auto":
            return self.jira_auth_scheme
        return "bearer" if self.uses_service_account_token else "basic"

    def secret_values(self) -> list[str]:
        """Literal strings that must never reach a log line or a report."""
        values: list[str] = []
        if self.jira_api_token is not None:
            token = self.jira_api_token.get_secret_value()
            if token:
                values.append(token)
        return values

    def require_live(self) -> tuple[str, str | None, SecretStr]:
        """Return the credentials needed for live Jira access, or explain what is missing.

        What counts as "missing" depends on the scheme. Bearer auth sends only the
        token, so an email is neither required nor used; demanding one there would
        send people hunting for a value that changes nothing.

        Returns:
            ``(api_base_url, email_or_none, token)``.

        Raises:
            ConfigurationError: if live access is disabled, a credential is absent, or
                a service-account token is configured without the gateway it requires.
                Messages name environment variables, never their values.
        """
        if self.jiralens_live_jira_disabled:
            raise ConfigurationError(
                "Live Jira access is disabled by JIRALENS_LIVE_JIRA_DISABLED. "
                "This deployment can only run in demo mode."
            )

        required: list[tuple[str, object]] = [
            ("JIRA_BASE_URL", self.jira_base_url),
            ("JIRA_API_TOKEN", self.jira_api_token),
        ]
        if self.auth_scheme == "basic":
            required.append(("JIRA_EMAIL", self.jira_email))

        missing = [name for name, present in required if not present]
        if missing:
            raise ConfigurationError(
                "Missing required configuration: "
                + ", ".join(missing)
                + ". Copy .env.example to .env and fill these in locally."
            )

        if self.uses_service_account_token and self.jira_cloud_id is None:
            raise ConfigurationError(
                "This is a service account API token, which Atlassian only accepts "
                "through the api.atlassian.com gateway — so JIRA_CLOUD_ID must be set. "
                "Find yours at https://<your-site>.atlassian.net/_edge/tenant_info"
            )

        api_base = self.api_base_url
        assert api_base is not None  # narrowed by the missing-check above
        assert self.jira_api_token is not None
        email = self.jira_email if self.auth_scheme == "basic" else None
        return api_base, email, self.jira_api_token


def load_settings(**overrides: object) -> Settings:
    """Load settings, converting pydantic validation failures into ConfigurationError."""
    try:
        return Settings(**overrides)  # type: ignore[arg-type]
    except Exception as exc:  # pydantic.ValidationError, but keep the surface narrow
        raise ConfigurationError(f"Invalid configuration: {exc}") from exc


__all__ = ["ATLASSIAN_GATEWAY", "DEFAULT_TIMEZONE", "Settings", "load_settings"]
