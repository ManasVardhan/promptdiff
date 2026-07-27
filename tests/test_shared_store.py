"""Tests for the shared store option (--store / PROMPTDIFF_STORE) and list --tag."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from promptdiff.cli import cli, main


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def _init_shared(runner: CliRunner, shared: Path) -> None:
    result = runner.invoke(cli, ["--store", str(shared), "init"])
    assert result.exit_code == 0


class TestStoreFlag:
    def test_init_creates_store_at_given_path(self, runner: CliRunner, tmp_path: Path) -> None:
        shared = tmp_path / "registry"
        with runner.isolated_filesystem(temp_dir=tmp_path):
            _init_shared(runner, shared)
            assert (shared / ".promptdiff").exists()
            assert not Path(".promptdiff").exists()

    def test_add_and_show_from_another_directory(self, runner: CliRunner, tmp_path: Path) -> None:
        shared = tmp_path / "registry"
        with runner.isolated_filesystem(temp_dir=tmp_path):
            _init_shared(runner, shared)
            result = runner.invoke(
                cli,
                ["--store", str(shared), "add", "greet", "-m", "v1"],
                input="Hello {name}\n",
            )
            assert result.exit_code == 0
        # A different project directory sees the same prompts.
        with runner.isolated_filesystem(temp_dir=tmp_path):
            result = runner.invoke(cli, ["--store", str(shared), "show", "greet", "--raw"])
            assert result.exit_code == 0
            assert "Hello {name}" in result.output

    def test_default_still_uses_cwd_after_flag_invocation(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        shared = tmp_path / "registry"
        with runner.isolated_filesystem(temp_dir=tmp_path):
            _init_shared(runner, shared)
            # Without the flag, the cwd (uninitialized) store is used again.
            result = runner.invoke(cli, ["list"])
            assert result.exit_code != 0 or "Not a promptdiff repository" in result.output

    def test_commands_error_cleanly_when_shared_store_missing(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        with runner.isolated_filesystem(temp_dir=tmp_path):
            result = runner.invoke(
                cli, ["--store", str(tmp_path / "nowhere"), "add", "x"], input="hi\n"
            )
            assert result.exit_code != 0


class TestStoreEnvVar:
    def test_env_var_selects_store(self, runner: CliRunner, tmp_path: Path) -> None:
        shared = tmp_path / "registry"
        env = {"PROMPTDIFF_STORE": str(shared)}
        with runner.isolated_filesystem(temp_dir=tmp_path):
            result = runner.invoke(cli, ["init"], env=env)
            assert result.exit_code == 0
            assert (shared / ".promptdiff").exists()
            assert not Path(".promptdiff").exists()

            result = runner.invoke(cli, ["add", "greet", "-m", "v1"], input="Hi\n", env=env)
            assert result.exit_code == 0
            result = runner.invoke(cli, ["list"], env=env)
            assert result.exit_code == 0
            assert "greet" in result.output

    def test_flag_overrides_env_var(self, runner: CliRunner, tmp_path: Path) -> None:
        env_store = tmp_path / "env-registry"
        flag_store = tmp_path / "flag-registry"
        env = {"PROMPTDIFF_STORE": str(env_store)}
        with runner.isolated_filesystem(temp_dir=tmp_path):
            result = runner.invoke(cli, ["--store", str(flag_store), "init"], env=env)
            assert result.exit_code == 0
            assert (flag_store / ".promptdiff").exists()
            assert not (env_store / ".promptdiff").exists()


class TestListTagFilter:
    def _seed(self, runner: CliRunner, shared: Path) -> None:
        _init_shared(runner, shared)
        base = ["--store", str(shared)]
        assert (
            runner.invoke(
                cli, base + ["add", "summarizer", "-t", "prod", "-t", "rag"], input="Summarize.\n"
            ).exit_code
            == 0
        )
        assert (
            runner.invoke(
                cli, base + ["add", "classifier", "-t", "experimental"], input="Classify.\n"
            ).exit_code
            == 0
        )
        assert runner.invoke(cli, base + ["add", "untagged"], input="Plain.\n").exit_code == 0

    def test_list_shows_tags_column(self, runner: CliRunner, tmp_path: Path) -> None:
        shared = tmp_path / "registry"
        with runner.isolated_filesystem(temp_dir=tmp_path):
            self._seed(runner, shared)
            result = runner.invoke(cli, ["--store", str(shared), "list"])
            assert result.exit_code == 0
            assert "Tags" in result.output
            assert "prod" in result.output
            assert "experimental" in result.output

    def test_list_filters_by_tag(self, runner: CliRunner, tmp_path: Path) -> None:
        shared = tmp_path / "registry"
        with runner.isolated_filesystem(temp_dir=tmp_path):
            self._seed(runner, shared)
            result = runner.invoke(cli, ["--store", str(shared), "list", "--tag", "prod"])
            assert result.exit_code == 0
            assert "summarizer" in result.output
            assert "classifier" not in result.output
            assert "untagged" not in result.output
            assert "tag: prod" in result.output

    def test_list_unknown_tag_message(self, runner: CliRunner, tmp_path: Path) -> None:
        shared = tmp_path / "registry"
        with runner.isolated_filesystem(temp_dir=tmp_path):
            self._seed(runner, shared)
            result = runner.invoke(cli, ["--store", str(shared), "list", "--tag", "missing"])
            assert result.exit_code == 0
            assert "No prompts with tag 'missing'" in result.output

    def test_short_tag_flag(self, runner: CliRunner, tmp_path: Path) -> None:
        shared = tmp_path / "registry"
        with runner.isolated_filesystem(temp_dir=tmp_path):
            self._seed(runner, shared)
            result = runner.invoke(cli, ["--store", str(shared), "list", "-t", "rag"])
            assert result.exit_code == 0
            assert "summarizer" in result.output
            assert "classifier" not in result.output


class TestMainEntryPoint:
    def test_uninitialized_store_prints_clean_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("PROMPTDIFF_STORE", raising=False)
        monkeypatch.setattr(sys, "argv", ["promptdiff", "list"])
        with pytest.raises(SystemExit) as exc:
            main()
        assert exc.value.code == 1
        out = capsys.readouterr().out
        assert "Not a promptdiff repository" in out
        assert "Traceback" not in out

    def test_success_path_passes_through(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("PROMPTDIFF_STORE", raising=False)
        monkeypatch.setattr(sys, "argv", ["promptdiff", "init"])
        with pytest.raises(SystemExit) as exc:
            main()
        assert not exc.value.code
        assert "Initialized" in capsys.readouterr().out


class TestCrossProjectWorkflow:
    def test_two_projects_share_one_registry(self, runner: CliRunner, tmp_path: Path) -> None:
        shared = tmp_path / "registry"
        env = {"PROMPTDIFF_STORE": str(shared)}
        # Project A registers a prompt.
        with runner.isolated_filesystem(temp_dir=tmp_path):
            runner.invoke(cli, ["init"], env=env)
            runner.invoke(cli, ["add", "shared-prompt", "-t", "team"], input="v1 text\n", env=env)
        # Project B evolves it and reads history.
        with runner.isolated_filesystem(temp_dir=tmp_path):
            result = runner.invoke(cli, ["add", "shared-prompt", "-m", "tweak"], input="v2 text\n", env=env)
            assert result.exit_code == 0
            result = runner.invoke(cli, ["log", "shared-prompt"], env=env)
            assert result.exit_code == 0
            assert "v1" in result.output and "v2" in result.output
            result = runner.invoke(cli, ["diff", "shared-prompt", "1", "2"], env=env)
            assert result.exit_code == 0
