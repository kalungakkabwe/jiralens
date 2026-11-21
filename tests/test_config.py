"""Configuration validation, and the guarantee that the token cannot leak."""

from __future__ import annotations

from pathlib import Path

import pytest

from jiralens.config import Settings, env_file_problems, load_settings
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


# ---------------------------------------------------------------------------
# .env problems that would otherwise produce a misleading error
# ---------------------------------------------------------------------------


class TestEnvFileProblems:
    """Each case here cost real debugging time before this check existed."""

    def _write(self, tmp_path: Path, body: str) -> Path:
        path = tmp_path / ".env"
        path.write_text(body, encoding="utf-8")
        return path

    def test_a_clean_file_reports_nothing(self, tmp_path: Path) -> None:
        path = self._write(
            tmp_path,
            "# a comment\nJIRA_BASE_URL=https://x.atlassian.net\nJIRA_EMAIL=a@b.com\n",
        )
        assert env_file_problems(path, environ={}) == []

    def test_a_missing_file_reports_nothing(self, tmp_path: Path) -> None:
        assert env_file_problems(tmp_path / "nope.env", environ={}) == []

    def test_a_bare_line_is_flagged_with_its_line_number(self, tmp_path: Path) -> None:
        """Pasting over REPLACE_ME usually deletes the KEY= too, leaving a bare
        credential the parser silently skips."""
        path = self._write(tmp_path, "JIRA_EMAIL=a@b.com\nATATTsomeverylongtokenvalue\n")
        problems = env_file_problems(path, environ={})
        assert len(problems) == 1
        assert "line 2" in problems[0]
        assert "KEY=" in problems[0]

    def test_the_bare_line_message_says_the_value_is_not_reaching_jiralens(
        self, tmp_path: Path
    ) -> None:
        path = self._write(tmp_path, "sometoken\n")
        assert "not reaching JiraLens" in env_file_problems(path, environ={})[0]

    def test_comments_and_blanks_are_not_flagged(self, tmp_path: Path) -> None:
        path = self._write(tmp_path, "# just a comment\n\n   \n# another\n")
        assert env_file_problems(path, environ={}) == []

    def test_a_leftover_placeholder_is_flagged(self, tmp_path: Path) -> None:
        path = self._write(tmp_path, "JIRA_API_TOKEN=REPLACE_ME\n")
        problems = env_file_problems(path, environ={})
        assert any("placeholder" in p and "JIRA_API_TOKEN" in p for p in problems)

    def test_a_shadowing_environment_variable_is_flagged(self, tmp_path: Path) -> None:
        """Environment wins over .env, so a stale export makes edits do nothing."""
        path = self._write(tmp_path, "JIRA_API_TOKEN=from-the-file\n")
        problems = env_file_problems(path, environ={"JIRA_API_TOKEN": "stale-export"})
        assert len(problems) == 1
        assert "overrides" in problems[0]
        assert "new terminal" in problems[0]

    def test_a_matching_environment_variable_is_not_flagged(self, tmp_path: Path) -> None:
        path = self._write(tmp_path, "JIRA_API_TOKEN=same\n")
        assert env_file_problems(path, environ={"JIRA_API_TOKEN": "same"}) == []

    def test_unrelated_environment_variables_are_ignored(self, tmp_path: Path) -> None:
        path = self._write(tmp_path, "EDITOR=vim\n")
        assert env_file_problems(path, environ={"EDITOR": "nano"}) == []

    def test_no_secret_value_appears_in_any_message(self, tmp_path: Path) -> None:
        secret = "super-secret-token-value-123456"
        path = self._write(tmp_path, f"JIRA_API_TOKEN={secret}\n{secret}\n")
        for problem in env_file_problems(path, environ={"JIRA_API_TOKEN": "other"}):
            assert secret not in problem

    def test_several_problems_are_all_reported(self, tmp_path: Path) -> None:
        path = self._write(
            tmp_path, "JIRA_API_TOKEN=REPLACE_ME\nbare-line-here\nJIRA_EMAIL=a@b.com\n"
        )
        problems = env_file_problems(path, environ={"JIRA_EMAIL": "different@b.com"})
        assert len(problems) == 3
