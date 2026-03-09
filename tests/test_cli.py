"""Tests for the promptdiff CLI."""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from promptdiff.cli import cli


@pytest.fixture
def initialized_dir(tmp_path: Path) -> Path:
    """Create an initialized promptdiff directory."""
    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=tmp_path) as td:
        result = runner.invoke(cli, ["init"])
        assert result.exit_code == 0
        yield Path(td)


class TestCLIVersion:
    def test_version_flag(self) -> None:
        runner = CliRunner()
        result = runner.invoke(cli, ["--version"])
        assert result.exit_code == 0
        assert "0.1.1" in result.output

    def test_help(self) -> None:
        runner = CliRunner()
        result = runner.invoke(cli, ["--help"])
        assert result.exit_code == 0
        assert "promptdiff" in result.output


class TestCLIInit:
    def test_init_creates_store(self, tmp_path: Path) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem(temp_dir=tmp_path):
            result = runner.invoke(cli, ["init"])
            assert result.exit_code == 0
            assert "Initialized" in result.output
            assert Path(".promptdiff").exists()

    def test_init_already_initialized(self, tmp_path: Path) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem(temp_dir=tmp_path):
            runner.invoke(cli, ["init"])
            result = runner.invoke(cli, ["init"])
            assert result.exit_code == 0
            assert "Already initialized" in result.output


class TestCLIAdd:
    def test_add_from_stdin(self, tmp_path: Path) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem(temp_dir=tmp_path):
            runner.invoke(cli, ["init"])
            result = runner.invoke(cli, ["add", "test-prompt", "-m", "first"], input="Hello {name}\n")
            assert result.exit_code == 0
            assert "Added test-prompt v1" in result.output

    def test_add_from_file(self, tmp_path: Path) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem(temp_dir=tmp_path):
            runner.invoke(cli, ["init"])
            Path("prompt.txt").write_text("Hello {name}")
            result = runner.invoke(cli, ["add", "test-prompt", "-f", "prompt.txt", "-m", "from file"])
            assert result.exit_code == 0
            assert "Added test-prompt v1" in result.output

    def test_add_empty_content_fails(self, tmp_path: Path) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem(temp_dir=tmp_path):
            runner.invoke(cli, ["init"])
            result = runner.invoke(cli, ["add", "test-prompt"], input="   \n")
            assert result.exit_code != 0

    def test_add_with_tags(self, tmp_path: Path) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem(temp_dir=tmp_path):
            runner.invoke(cli, ["init"])
            result = runner.invoke(
                cli, ["add", "tagged", "-m", "tagged", "-t", "prod", "-t", "v1"],
                input="Prompt content\n",
            )
            assert result.exit_code == 0

    def test_add_multiple_versions(self, tmp_path: Path) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem(temp_dir=tmp_path):
            runner.invoke(cli, ["init"])
            runner.invoke(cli, ["add", "p", "-m", "v1"], input="version 1\n")
            result = runner.invoke(cli, ["add", "p", "-m", "v2"], input="version 2\n")
            assert result.exit_code == 0
            assert "v2" in result.output


class TestCLIDiff:
    def test_diff_command(self, tmp_path: Path) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem(temp_dir=tmp_path):
            runner.invoke(cli, ["init"])
            runner.invoke(cli, ["add", "p", "-m", "v1"], input="Hello world\n")
            runner.invoke(cli, ["add", "p", "-m", "v2"], input="Hello universe\n")
            result = runner.invoke(cli, ["diff", "p", "1", "2"])
            assert result.exit_code == 0
            assert "similarity" in result.output.lower()


class TestCLILog:
    def test_log_command(self, tmp_path: Path) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem(temp_dir=tmp_path):
            runner.invoke(cli, ["init"])
            runner.invoke(cli, ["add", "p", "-m", "initial"], input="content\n")
            result = runner.invoke(cli, ["log", "p"])
            assert result.exit_code == 0
            assert "initial" in result.output

    def test_log_no_versions(self, tmp_path: Path) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem(temp_dir=tmp_path):
            runner.invoke(cli, ["init"])
            result = runner.invoke(cli, ["log", "nonexistent"])
            assert result.exit_code != 0


class TestCLIList:
    def test_list_empty(self, tmp_path: Path) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem(temp_dir=tmp_path):
            runner.invoke(cli, ["init"])
            result = runner.invoke(cli, ["list"])
            assert result.exit_code == 0
            assert "No prompts" in result.output

    def test_list_with_prompts(self, tmp_path: Path) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem(temp_dir=tmp_path):
            runner.invoke(cli, ["init"])
            runner.invoke(cli, ["add", "alpha", "-m", "a"], input="A\n")
            runner.invoke(cli, ["add", "beta", "-m", "b"], input="B\n")
            result = runner.invoke(cli, ["list"])
            assert result.exit_code == 0
            assert "alpha" in result.output
            assert "beta" in result.output


class TestCLIChangelog:
    def test_changelog(self, tmp_path: Path) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem(temp_dir=tmp_path):
            runner.invoke(cli, ["init"])
            runner.invoke(cli, ["add", "p", "-m", "initial"], input="v1 content\n")
            runner.invoke(cli, ["add", "p", "-m", "improved"], input="v2 content updated\n")
            result = runner.invoke(cli, ["changelog", "p"])
            assert result.exit_code == 0
            assert "v1" in result.output
            assert "v2" in result.output


class TestCLIEval:
    def test_eval_demo(self, tmp_path: Path) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem(temp_dir=tmp_path):
            runner.invoke(cli, ["init"])
            runner.invoke(cli, ["add", "p", "-m", "test"], input="Hello world\n")
            result = runner.invoke(cli, ["eval", "p", "1"])
            assert result.exit_code == 0
            assert "score" in result.output.lower() or "Eval" in result.output
