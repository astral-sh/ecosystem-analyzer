import datetime as dt
from typing import Any
from unittest.mock import patch

import pytest
from mypy_primer.model import Project

from ecosystem_analyzer.installed_project import (
    InstalledProject,
    validate_exclude_newer,
)


def _make_project(**kwargs) -> Project:
    """Create a minimal Project for testing."""
    defaults: dict[str, Any] = {
        "location": "https://github.com/test/repo",
        "mypy_cmd": None,
        "pyright_cmd": None,
    }
    return Project(**(defaults | kwargs))


class TestValidateExcludeNewer:
    """Tests for the --exclude-newer timestamp validation."""

    def test_utc_z_suffix(self):
        """The format produced by `date -u +%Y-%m-%dT%H:%M:%SZ`."""
        result = validate_exclude_newer("2026-04-09T14:30:00Z")
        assert result == dt.datetime(2026, 4, 9, 14, 30, 0, tzinfo=dt.UTC)

    def test_utc_offset_zero(self):
        result = validate_exclude_newer("2026-04-09T14:30:00+00:00")
        assert result == dt.datetime(2026, 4, 9, 14, 30, 0, tzinfo=dt.UTC)

    def test_non_utc_offset_is_converted(self):
        result = validate_exclude_newer("2026-04-09T16:30:00+02:00")
        assert result == dt.datetime(2026, 4, 9, 14, 30, 0, tzinfo=dt.UTC)

    def test_with_fractional_seconds(self):
        result = validate_exclude_newer("2026-04-09T14:30:00.123456Z")
        assert result.year == 2026
        assert result.microsecond == 123456

    def test_rejects_naive_timestamp(self):
        """Timestamps without timezone info are rejected."""
        with pytest.raises(ValueError, match="missing timezone info"):
            validate_exclude_newer("2026-04-09T14:30:00")

    def test_rejects_date_only(self):
        """A bare date without time/timezone is rejected."""
        with pytest.raises(ValueError, match="missing timezone info"):
            validate_exclude_newer("2026-04-09")

    def test_rejects_garbage(self):
        with pytest.raises(ValueError, match="Invalid --exclude-newer timestamp"):
            validate_exclude_newer("not-a-timestamp")

    def test_rejects_empty_string(self):
        with pytest.raises(ValueError, match="Invalid --exclude-newer timestamp"):
            validate_exclude_newer("")

    def test_rejects_unix_timestamp(self):
        with pytest.raises(ValueError, match="Invalid --exclude-newer timestamp"):
            validate_exclude_newer("1712345678")


class TestInstalledProjectExcludeNewer:
    """Tests that InstalledProject validates --exclude-newer before doing work."""

    @patch.object(InstalledProject, "_install_dependencies")
    @patch.object(InstalledProject, "_clone_or_update")
    def test_rejects_invalid_timestamp_before_cloning(self, mock_clone, mock_install):
        """Invalid timestamps raise ValueError before any cloning or installing."""
        with pytest.raises(ValueError, match="Invalid --exclude-newer timestamp"):
            InstalledProject(_make_project(), exclude_newer="garbage")

        mock_clone.assert_not_called()
        mock_install.assert_not_called()

    @patch.object(InstalledProject, "_install_dependencies")
    @patch.object(InstalledProject, "_clone_or_update")
    def test_rejects_naive_timestamp_before_cloning(self, mock_clone, mock_install):
        with pytest.raises(ValueError, match="missing timezone info"):
            InstalledProject(_make_project(), exclude_newer="2026-04-09T14:30:00")

        mock_clone.assert_not_called()
        mock_install.assert_not_called()

    @patch.object(InstalledProject, "_install_dependencies")
    @patch.object(InstalledProject, "_clone_or_update")
    def test_accepts_valid_timestamp(self, mock_clone, mock_install):
        project = InstalledProject(
            _make_project(), exclude_newer="2026-04-09T14:30:00Z"
        )
        assert project._exclude_newer == "2026-04-09T14:30:00Z"
        mock_clone.assert_called_once()
        mock_install.assert_called_once()

    @patch.object(InstalledProject, "_install_dependencies")
    @patch.object(InstalledProject, "_clone_or_update")
    def test_none_exclude_newer_skips_validation(self, mock_clone, mock_install):
        project = InstalledProject(_make_project(), exclude_newer=None)
        assert project._exclude_newer is None
        mock_clone.assert_called_once()
        mock_install.assert_called_once()


class TestInstallDependenciesExcludeNewer:
    """Tests that --exclude-newer is correctly passed to uv pip install commands."""

    @patch("ecosystem_analyzer.installed_project.subprocess.run")
    @patch.object(InstalledProject, "_clone_or_update")
    def test_deps_include_exclude_newer(self, _mock_clone, mock_run):
        """When deps are specified, --exclude-newer appears in the pip install args."""
        InstalledProject(
            _make_project(deps=["requests", "six"]),
            exclude_newer="2026-04-09T14:30:00Z",
        )

        # First call is `uv venv`, second is `uv pip install`
        assert mock_run.call_count == 2
        pip_call_args = mock_run.call_args_list[1]
        cmd = pip_call_args.args[0]
        assert "--exclude-newer" in cmd
        idx = cmd.index("--exclude-newer")
        assert cmd[idx + 1] == "2026-04-09T14:30:00Z"

    @patch("ecosystem_analyzer.installed_project.subprocess.run")
    @patch.object(InstalledProject, "_clone_or_update")
    def test_deps_omit_exclude_newer_when_none(self, _mock_clone, mock_run):
        """When exclude_newer is None, --exclude-newer does not appear."""
        InstalledProject(_make_project(deps=["requests"]))

        assert mock_run.call_count == 2
        pip_call_args = mock_run.call_args_list[1]
        cmd = pip_call_args.args[0]
        assert "--exclude-newer" not in cmd

    @patch("ecosystem_analyzer.installed_project.subprocess.run")
    @patch.object(InstalledProject, "_clone_or_update")
    def test_install_cmd_includes_exclude_newer(self, _mock_clone, mock_run):
        """Custom install_cmd templates get --exclude-newer in the {install} placeholder."""
        InstalledProject(
            _make_project(install_cmd="{install} -e ."),
            exclude_newer="2026-04-09T14:30:00Z",
        )

        assert mock_run.call_count == 2
        install_call_args = mock_run.call_args_list[1]
        cmd_str = install_call_args.args[0]
        assert "--exclude-newer 2026-04-09T14:30:00Z" in cmd_str

    @patch("ecosystem_analyzer.installed_project.subprocess.run")
    @patch.object(InstalledProject, "_clone_or_update")
    def test_install_cmd_omits_exclude_newer_when_none(self, _mock_clone, mock_run):
        """Custom install_cmd without exclude_newer doesn't include the flag."""
        InstalledProject(_make_project(install_cmd="{install} -e ."))

        assert mock_run.call_count == 2
        install_call_args = mock_run.call_args_list[1]
        cmd_str = install_call_args.args[0]
        assert "--exclude-newer" not in cmd_str
