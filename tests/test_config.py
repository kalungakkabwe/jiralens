"""Configuration validation, and the guarantee that the token cannot leak."""

from __future__ import annotations

import pytest

from jiralens.config import Settings, load_settings
from jiralens.exceptions import ConfigurationError

TOKEN = "super-secret-token-value-123456"


def _settings(**kw: object) -> Settings:
    defaults: dict[str, object] = {
        "_env_file": None,  # never read the developer's real .env during tests
        "jira_base_url": None,
        "jira_email": None,
        "jira_api_token": None,
    }
    return Settings(**{**defaults, **kw})  # type: ignore[arg-type]


class TestRequiredCredentials:
    def test_missing_everything_names_all_three_variables(self) -> None:
        with pytest.raises(ConfigurationError) as exc:
            _settings().require_live()
        message = str(exc.value)
        assert "JIRA_BASE_URL" in message
        assert "JIRA_EMAIL" in message
        assert "JIRA_API_TOKEN" in message

    def test_missing_token_only_names_just_the_token(self) -> None:
        with pytest.raises(ConfigurationError) as exc:
            _settings(
                jira_base_url="https://acme.atlassian.net", jira_email="a@b.com"
            ).require_live()
        message = str(exc.value)
        assert "JIRA_API_TOKEN" in message
        assert "JIRA_BASE_URL" not in message

    def test_complete_credentials_are_returned(self) -> None:
        url, email, token = _settings(
            jira_base_url="https://acme.atlassian.net",
            jira_email="a@b.com",
            jira_api_token=TOKEN,
        ).require_live()
        assert url == "https://acme.atlassian.net"
        assert email == "a@b.com"
        assert token.get_secret_value() == TOKEN

    def test_live_disabled_refuses_even_with_credentials(self) -> None:
        with pytest.raises(ConfigurationError, match="disabled"):
            _settings(
                jira_base_url="https://acme.atlassian.net",
                jira_email="a@b.com",
                jira_api_token=TOKEN,
                jiralens_live_jira_disabled=True,
            ).require_live()


class TestBaseUrl:
    def test_trailing_slash_is_stripped(self) -> None:
        assert _settings(jira_base_url="https://acme.atlassian.net/").jira_base_url == (
            "https://acme.atlassian.net"
        )

    def test_http_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="https"):
            _settings(jira_base_url="http://acme.atlassian.net")

    def test_url_with_a_path_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="bare site URL"):
            _settings(jira_base_url="https://acme.atlassian.net/jira/software")

    def test_blank_is_treated_as_absent(self) -> None:
        assert _settings(jira_base_url="   ").jira_base_url is None


class TestTimezone:
    def test_default_is_lusaka(self) -> None:
        assert _settings().jiralens_timezone == "Africa/Lusaka"

    def test_unknown_zone_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="Unknown timezone"):
            _settings(jiralens_timezone="Mars/Olympus_Mons")

    def test_tzinfo_round_trips(self) -> None:
        assert str(_settings(jiralens_timezone="America/Chicago").tzinfo) == "America/Chicago"


class TestLogLevel:
    def test_lowercase_is_normalised(self) -> None:
        assert _settings(jiralens_log_level="debug").jiralens_log_level == "DEBUG"

    def test_nonsense_level_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="JIRALENS_LOG_LEVEL"):
            _settings(jiralens_log_level="CHATTY")


class TestTokenNeverLeaks:
    """The token must not appear in repr, str, or a model dump."""

    def test_repr_does_not_contain_the_token(self) -> None:
        assert TOKEN not in repr(_settings(jira_api_token=TOKEN))

    def test_str_does_not_contain_the_token(self) -> None:
        assert TOKEN not in str(_settings(jira_api_token=TOKEN))

    def test_model_dump_does_not_contain_the_token(self) -> None:
        assert TOKEN not in str(_settings(jira_api_token=TOKEN).model_dump())

    def test_validation_error_does_not_contain_the_token(self) -> None:
        """A bad sibling field must not drag the token into the error text."""
        with pytest.raises(ConfigurationError) as exc:
            load_settings(_env_file=None, jira_api_token=TOKEN, jiralens_timezone="Nowhere/Nothing")
        assert TOKEN not in str(exc.value)

    def test_secret_values_exposes_the_token_for_the_log_filter(self) -> None:
        assert _settings(jira_api_token=TOKEN).secret_values() == [TOKEN]

    def test_secret_values_is_empty_without_a_token(self) -> None:
        assert _settings().secret_values() == []
