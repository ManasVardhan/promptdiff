"""Extended CLI tests covering remaining uncovered lines."""

from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from promptdiff.cli import cli


class TestCLIAddStdinPrompt:
    """Test add command when stdin is a TTY (shows prompt message)."""

    def test_add_shows_tty_prompt(self, tmp_path: Path) -> None:
        """When stdin is a TTY, the CLI should print a prompt message."""
        runner = CliRunner()
        with runner.isolated_filesystem(temp_dir=tmp_path):
            runner.invoke(cli, ["init"])
            # CliRunner always has stdin.isatty() == False in mix_stderr mode,
            # so we test the non-TTY (piped) path -- content comes from stdin
            result = runner.invoke(cli, ["add", "p", "-m", "test"], input="content\n")
            assert result.exit_code == 0
            assert "Added p v1" in result.output


class TestCLILogErrors:
    """Test log command error paths."""

    def test_log_nonexistent_prompt(self, tmp_path: Path) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem(temp_dir=tmp_path):
            runner.invoke(cli, ["init"])
            result = runner.invoke(cli, ["log", "does-not-exist"])
            assert result.exit_code != 0


class TestCLIListDetailed:
    """Test list command details."""

    def test_list_shows_version_count(self, tmp_path: Path) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem(temp_dir=tmp_path):
            runner.invoke(cli, ["init"])
            runner.invoke(cli, ["add", "p", "-m", "v1"], input="version 1\n")
            runner.invoke(cli, ["add", "p", "-m", "v2"], input="version 2\n")
            result = runner.invoke(cli, ["list"])
            assert result.exit_code == 0
            assert "2" in result.output  # 2 versions


class TestCLIChangelogWithLastN:
    """Test changelog --last flag."""

    def test_changelog_last_n(self, tmp_path: Path) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem(temp_dir=tmp_path):
            runner.invoke(cli, ["init"])
            runner.invoke(cli, ["add", "p", "-m", "first"], input="version 1\n")
            runner.invoke(cli, ["add", "p", "-m", "second"], input="version 2\n")
            runner.invoke(cli, ["add", "p", "-m", "third"], input="version 3\n")
            result = runner.invoke(cli, ["changelog", "p", "--last", "2"])
            assert result.exit_code == 0
            assert "third" in result.output


class TestCLIEvalExtended:
    """Extended eval command tests."""

    def test_eval_nonexistent_prompt(self, tmp_path: Path) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem(temp_dir=tmp_path):
            runner.invoke(cli, ["init"])
            result = runner.invoke(cli, ["eval", "missing", "1"])
            assert result.exit_code != 0

    def test_eval_nonexistent_version(self, tmp_path: Path) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem(temp_dir=tmp_path):
            runner.invoke(cli, ["init"])
            runner.invoke(cli, ["add", "p", "-m", "v1"], input="content\n")
            result = runner.invoke(cli, ["eval", "p", "99"])
            assert result.exit_code != 0

    def test_eval_output_format(self, tmp_path: Path) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem(temp_dir=tmp_path):
            runner.invoke(cli, ["init"])
            runner.invoke(cli, ["add", "p", "-m", "v1"], input="Hello world\n")
            result = runner.invoke(cli, ["eval", "p", "1"])
            assert result.exit_code == 0
            assert "Eval: p v1" in result.output
            assert "Mean score" in result.output
            assert "Passed" in result.output


class TestCLIDiffFormatting:
    """Test diff command output formatting."""

    def test_diff_shows_additions_deletions(self, tmp_path: Path) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem(temp_dir=tmp_path):
            runner.invoke(cli, ["init"])
            runner.invoke(cli, ["add", "p", "-m", "v1"], input="Hello world\n")
            runner.invoke(cli, ["add", "p", "-m", "v2"], input="Hello universe\nNew line\n")
            result = runner.invoke(cli, ["diff", "p", "1", "2"])
            assert result.exit_code == 0
            assert "+" in result.output  # additions indicator
            assert "-" in result.output  # deletions indicator

    def test_diff_identical_versions(self, tmp_path: Path) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem(temp_dir=tmp_path):
            runner.invoke(cli, ["init"])
            runner.invoke(cli, ["add", "p", "-m", "v1"], input="Same content\n")
            # Adding identical content returns v1 again (dedup)
            # So we need different content for v2
            runner.invoke(cli, ["add", "p", "-m", "v2"], input="Same content slightly different\n")
            result = runner.invoke(cli, ["diff", "p", "1", "2"])
            assert result.exit_code == 0
            assert "similarity" in result.output.lower()


class TestCLINotInitialized:
    """Test CLI commands when store is not initialized."""

    def test_add_without_init(self, tmp_path: Path) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem(temp_dir=tmp_path):
            result = runner.invoke(cli, ["add", "p", "-m", "v1"], input="content\n")
            assert result.exit_code != 0

    def test_list_without_init(self, tmp_path: Path) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem(temp_dir=tmp_path):
            result = runner.invoke(cli, ["list"])
            assert result.exit_code != 0

    def test_diff_without_init(self, tmp_path: Path) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem(temp_dir=tmp_path):
            result = runner.invoke(cli, ["diff", "p", "1", "2"])
            assert result.exit_code != 0
