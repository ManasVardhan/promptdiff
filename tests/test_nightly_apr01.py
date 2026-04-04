"""Nightly improvement tests: Apr 1 2026.

Targets uncovered CLI paths (add from file, diff equal lines, log table,
empty content), embedding full_diff with mocked OpenAI, and eval edge cases.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from promptdiff.cli import cli
from promptdiff.diff import PromptDiff
from promptdiff.eval import EvalResult, PromptEvaluator, TestCase


class TestCLIAddFromFile:
    """Test the add command with --file flag."""

    def test_add_from_file(self, tmp_path: Path) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem(temp_dir=tmp_path):
            runner.invoke(cli, ["init"])
            prompt_file = Path("prompt.txt")
            prompt_file.write_text("This is a prompt from a file.\n")
            result = runner.invoke(cli, ["add", "file-test", "-m", "from file", "-f", str(prompt_file)])
            assert result.exit_code == 0
            assert "Added file-test v1" in result.output

    def test_add_from_file_with_tags(self, tmp_path: Path) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem(temp_dir=tmp_path):
            runner.invoke(cli, ["init"])
            prompt_file = Path("prompt.txt")
            prompt_file.write_text("Tagged prompt content.\n")
            result = runner.invoke(
                cli,
                ["add", "tagged", "-m", "with tags", "-f", str(prompt_file), "-t", "gpt4", "-t", "production"],
            )
            assert result.exit_code == 0
            assert "Added tagged v1" in result.output

    def test_add_empty_content_rejected(self, tmp_path: Path) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem(temp_dir=tmp_path):
            runner.invoke(cli, ["init"])
            result = runner.invoke(cli, ["add", "p", "-m", "empty"], input="\n  \n")
            assert result.exit_code != 0
            assert "Empty" in result.output

    def test_add_empty_file_rejected(self, tmp_path: Path) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem(temp_dir=tmp_path):
            runner.invoke(cli, ["init"])
            prompt_file = Path("empty.txt")
            prompt_file.write_text("   \n\n  ")
            result = runner.invoke(cli, ["add", "p", "-m", "empty file", "-f", str(prompt_file)])
            assert result.exit_code != 0


class TestCLIDiffEqualLines:
    """Test diff output when versions share common lines (covers 'equal' tag)."""

    def test_diff_with_shared_lines(self, tmp_path: Path) -> None:
        """Versions sharing lines should show equal (unchanged) lines in diff."""
        runner = CliRunner()
        with runner.isolated_filesystem(temp_dir=tmp_path):
            runner.invoke(cli, ["init"])
            v1_text = "You are a helpful assistant.\nBe concise.\nAnswer in English.\n"
            v2_text = "You are a helpful assistant.\nBe very concise and clear.\nAnswer in English.\n"
            runner.invoke(cli, ["add", "shared", "-m", "v1"], input=v1_text)
            runner.invoke(cli, ["add", "shared", "-m", "v2"], input=v2_text)
            result = runner.invoke(cli, ["diff", "shared", "1", "2"])
            assert result.exit_code == 0
            # "You are a helpful assistant." is the same - should appear as equal line
            assert "helpful assistant" in result.output
            assert "similarity" in result.output.lower()

    def test_diff_multiline_mixed_changes(self, tmp_path: Path) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem(temp_dir=tmp_path):
            runner.invoke(cli, ["init"])
            v1 = "Line 1\nLine 2\nLine 3\nLine 4\nLine 5\n"
            v2 = "Line 1\nChanged Line 2\nLine 3\nLine 4\nLine 5\nLine 6\n"
            runner.invoke(cli, ["add", "multi", "-m", "v1"], input=v1)
            runner.invoke(cli, ["add", "multi", "-m", "v2"], input=v2)
            result = runner.invoke(cli, ["diff", "multi", "1", "2"])
            assert result.exit_code == 0
            # Should show additions and deletions
            assert "+" in result.output or "-" in result.output


class TestCLILogTable:
    """Test the log command renders a proper version history table."""

    def test_log_single_version(self, tmp_path: Path) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem(temp_dir=tmp_path):
            runner.invoke(cli, ["init"])
            runner.invoke(cli, ["add", "myp", "-m", "initial"], input="Hello\n")
            result = runner.invoke(cli, ["log", "myp"])
            assert result.exit_code == 0
            assert "myp" in result.output
            assert "initial" in result.output

    def test_log_multiple_versions(self, tmp_path: Path) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem(temp_dir=tmp_path):
            runner.invoke(cli, ["init"])
            runner.invoke(cli, ["add", "myp", "-m", "first"], input="v1\n")
            runner.invoke(cli, ["add", "myp", "-m", "second"], input="v2\n")
            runner.invoke(cli, ["add", "myp", "-m", "third"], input="v3\n")
            result = runner.invoke(cli, ["log", "myp"])
            assert result.exit_code == 0
            assert "first" in result.output
            assert "second" in result.output
            assert "third" in result.output


class TestCLIInitIdempotent:
    """Test init command edge cases."""

    def test_init_already_initialized(self, tmp_path: Path) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem(temp_dir=tmp_path):
            result1 = runner.invoke(cli, ["init"])
            assert result1.exit_code == 0
            result2 = runner.invoke(cli, ["init"])
            assert result2.exit_code == 0
            assert "Already" in result2.output

    def test_list_empty(self, tmp_path: Path) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem(temp_dir=tmp_path):
            runner.invoke(cli, ["init"])
            result = runner.invoke(cli, ["list"])
            assert result.exit_code == 0
            assert "No prompts" in result.output


class TestFullDiffWithEmbeddings:
    """Test full_diff with use_embeddings=True (mocked OpenAI)."""

    def test_full_diff_embeddings(self) -> None:
        import types
        import sys

        d = PromptDiff()
        mock_resp = MagicMock()
        mock_resp.data = [
            MagicMock(embedding=[1.0, 0.0, 0.0]),
            MagicMock(embedding=[0.9, 0.1, 0.0]),
        ]

        # Create a fake openai module so patch("openai.OpenAI") works
        fake_openai = types.ModuleType("openai")
        fake_openai.OpenAI = MagicMock  # type: ignore[attr-defined]
        was_installed = "openai" in sys.modules
        old_mod = sys.modules.get("openai")
        sys.modules["openai"] = fake_openai
        try:
            with patch("openai.OpenAI") as MockOAI:
                mock_client = MagicMock()
                mock_client.embeddings.create.return_value = mock_resp
                MockOAI.return_value = mock_client
                result = d.full_diff("old text", "new text", use_embeddings=True)
                assert result.semantic_similarity is not None
                assert 0 <= result.semantic_similarity <= 1.0
        finally:
            if was_installed and old_mod is not None:
                sys.modules["openai"] = old_mod
            else:
                sys.modules.pop("openai", None)

    def test_full_diff_no_embeddings(self) -> None:
        d = PromptDiff()
        result = d.full_diff("old text", "new text", use_embeddings=False)
        # semantic_similarity may be populated by text-based fallback
        assert isinstance(result.similarity_ratio, float)
        assert result.similarity_ratio >= 0
        assert len(result.lines) > 0


class TestEvalEdgeCases:
    """Test evaluator edge cases."""

    def test_eval_empty_test_cases(self) -> None:
        evaluator = PromptEvaluator()
        result = evaluator.evaluate("p", 1, "content", [])
        assert isinstance(result, EvalResult)
        assert result.mean_score == 0.0

    def test_eval_multiple_weighted_cases(self) -> None:
        evaluator = PromptEvaluator()
        cases = [
            TestCase(name="high", input_vars={}, expected="exact", weight=3.0),
            TestCase(name="low", input_vars={}, expected="exact", weight=1.0),
        ]
        result = evaluator.evaluate("p", 1, "exact", cases)
        assert result.passed
        assert result.weighted_score > 0

    def test_eval_zero_similarity(self) -> None:
        evaluator = PromptEvaluator()
        cases = [
            TestCase(name="miss", input_vars={}, expected="completely different xyz abc", weight=1.0),
        ]
        result = evaluator.evaluate("p", 1, "nothing matches here at all", cases)
        # Score should be low but evaluation should still work
        assert isinstance(result.mean_score, float)


class TestCLIVersionAndHelp:
    """Test --version and --help flags via subprocess (covers entry point)."""

    def test_version_flag(self) -> None:
        runner = CliRunner()
        result = runner.invoke(cli, ["--version"])
        assert result.exit_code == 0
        assert "promptdiff" in result.output.lower() or "version" in result.output.lower()

    def test_help_flag(self) -> None:
        runner = CliRunner()
        result = runner.invoke(cli, ["--help"])
        assert result.exit_code == 0
        assert "diff" in result.output
        assert "add" in result.output

    def test_scan_help(self) -> None:
        runner = CliRunner()
        result = runner.invoke(cli, ["diff", "--help"])
        assert result.exit_code == 0
        assert "NAME" in result.output or "name" in result.output.lower()
