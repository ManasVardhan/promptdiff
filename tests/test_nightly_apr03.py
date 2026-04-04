"""Nightly improvement tests (April 3, 2026).

Tests for: __main__.py, export command, search command, import command.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from promptdiff.cli import cli
from promptdiff.store import PromptStore


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def store(tmp_path: Path) -> PromptStore:
    """Create an initialized store with some test prompts."""
    s = PromptStore(tmp_path)
    s.init()
    s.add("chat-v1", "You are a helpful assistant.", message="initial")
    s.add("chat-v1", "You are a helpful, concise assistant.", message="made concise")
    s.add("summarizer", "Summarize the following text: {text}", message="initial")
    return s


class TestMainModule:
    """Test python -m promptdiff works."""

    def test_main_module_runs(self) -> None:
        """python -m promptdiff --help should exit 0."""
        result = subprocess.run(
            [sys.executable, "-m", "promptdiff", "--help"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0
        assert "promptdiff" in result.stdout.lower()

    def test_main_module_version(self) -> None:
        """python -m promptdiff --version should print version."""
        result = subprocess.run(
            [sys.executable, "-m", "promptdiff", "--version"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0
        assert "0.1.1" in result.stdout


class TestExportCommand:
    """Test the export CLI command."""

    def test_export_all_json(self, runner: CliRunner, store: PromptStore) -> None:
        with runner.isolated_filesystem():
            with patch("promptdiff.cli._get_store", return_value=store):
                result = runner.invoke(cli, ["export"])
                assert result.exit_code == 0
                data = json.loads(result.output)
                assert len(data) == 2
                names = [d["name"] for d in data]
                assert "chat-v1" in names
                assert "summarizer" in names

    def test_export_single_prompt(self, runner: CliRunner, store: PromptStore) -> None:
        with runner.isolated_filesystem():
            with patch("promptdiff.cli._get_store", return_value=store):
                result = runner.invoke(cli, ["export", "chat-v1"])
                assert result.exit_code == 0
                data = json.loads(result.output)
                assert len(data) == 1
                assert data[0]["name"] == "chat-v1"
                assert len(data[0]["versions"]) == 2

    def test_export_to_file(self, runner: CliRunner, store: PromptStore, tmp_path: Path) -> None:
        outfile = str(tmp_path / "export.json")
        with patch("promptdiff.cli._get_store", return_value=store):
            result = runner.invoke(cli, ["export", "-o", outfile])
            assert result.exit_code == 0
            assert Path(outfile).exists()
            data = json.loads(Path(outfile).read_text())
            assert len(data) == 2

    def test_export_jsonl(self, runner: CliRunner, store: PromptStore) -> None:
        with runner.isolated_filesystem():
            with patch("promptdiff.cli._get_store", return_value=store):
                result = runner.invoke(cli, ["export", "--format", "jsonl"])
                assert result.exit_code == 0
                lines = [ln for ln in result.output.strip().splitlines() if ln.strip()]
                assert len(lines) == 2
                for line in lines:
                    record = json.loads(line)
                    assert "name" in record
                    assert "versions" in record

    def test_export_nonexistent_prompt(self, runner: CliRunner, store: PromptStore) -> None:
        with runner.isolated_filesystem():
            with patch("promptdiff.cli._get_store", return_value=store):
                result = runner.invoke(cli, ["export", "nonexistent"])
                assert result.exit_code != 0

    def test_export_empty_store(self, runner: CliRunner, tmp_path: Path) -> None:
        empty_store = PromptStore(tmp_path / "empty")
        empty_store.init()
        with patch("promptdiff.cli._get_store", return_value=empty_store):
            result = runner.invoke(cli, ["export"])
            assert result.exit_code == 0
            assert "No prompts" in result.output

    def test_export_preserves_metadata(self, runner: CliRunner, store: PromptStore) -> None:
        store.add("with-meta", "prompt text", message="test", metadata={"model": "gpt-4"})
        with patch("promptdiff.cli._get_store", return_value=store):
            result = runner.invoke(cli, ["export", "with-meta"])
            data = json.loads(result.output)
            v = data[0]["versions"][0]
            assert v["metadata"]["model"] == "gpt-4"
            assert v["message"] == "test"
            assert v["content_hash"]
            assert v["timestamp"]


class TestSearchCommand:
    """Test the search CLI command."""

    def test_search_by_name(self, runner: CliRunner, store: PromptStore) -> None:
        with patch("promptdiff.cli._get_store", return_value=store):
            result = runner.invoke(cli, ["search", "chat"])
            assert result.exit_code == 0
            assert "chat-v1" in result.output
            assert "name" in result.output

    def test_search_no_match(self, runner: CliRunner, store: PromptStore) -> None:
        with patch("promptdiff.cli._get_store", return_value=store):
            result = runner.invoke(cli, ["search", "zzzznonexistent"])
            assert result.exit_code == 0
            assert "No prompts matching" in result.output

    def test_search_by_content(self, runner: CliRunner, store: PromptStore) -> None:
        with patch("promptdiff.cli._get_store", return_value=store):
            result = runner.invoke(cli, ["search", "concise", "--content"])
            assert result.exit_code == 0
            assert "chat-v1" in result.output
            assert "content" in result.output

    def test_search_by_tag(self, runner: CliRunner, store: PromptStore) -> None:
        from promptdiff.registry import PromptRegistry
        registry = PromptRegistry(store)
        registry.set_tags("chat-v1", ["production", "chatbot"])
        with patch("promptdiff.cli._get_store", return_value=store):
            result = runner.invoke(cli, ["search", "chatbot"])
            assert result.exit_code == 0
            assert "chat-v1" in result.output
            assert "tag" in result.output

    def test_search_tag_filter(self, runner: CliRunner, store: PromptStore) -> None:
        from promptdiff.registry import PromptRegistry
        registry = PromptRegistry(store)
        registry.set_tags("chat-v1", ["production"])
        registry.set_tags("summarizer", ["experimental"])
        with patch("promptdiff.cli._get_store", return_value=store):
            result = runner.invoke(cli, ["search", "summ", "--tag", "experimental"])
            assert result.exit_code == 0
            assert "summarizer" in result.output

    def test_search_tag_filter_excludes(self, runner: CliRunner, store: PromptStore) -> None:
        from promptdiff.registry import PromptRegistry
        registry = PromptRegistry(store)
        registry.set_tags("chat-v1", ["production"])
        with patch("promptdiff.cli._get_store", return_value=store):
            # Search for summarizer but filter by "production" tag (only chat-v1 has it)
            result = runner.invoke(cli, ["search", "summ", "--tag", "production"])
            assert result.exit_code == 0
            assert "No prompts matching" in result.output

    def test_search_empty_store(self, runner: CliRunner, tmp_path: Path) -> None:
        empty_store = PromptStore(tmp_path / "empty")
        empty_store.init()
        with patch("promptdiff.cli._get_store", return_value=empty_store):
            result = runner.invoke(cli, ["search", "anything"])
            assert result.exit_code == 0
            assert "No prompts" in result.output

    def test_search_case_insensitive(self, runner: CliRunner, store: PromptStore) -> None:
        with patch("promptdiff.cli._get_store", return_value=store):
            result = runner.invoke(cli, ["search", "CHAT"])
            assert result.exit_code == 0
            assert "chat-v1" in result.output


class TestImportCommand:
    """Test the import CLI command."""

    def test_import_json(self, runner: CliRunner, tmp_path: Path) -> None:
        target_store = PromptStore(tmp_path / "target")
        target_store.init()

        export_data = [
            {
                "name": "imported-prompt",
                "tags": ["test"],
                "versions": [
                    {
                        "version": 1,
                        "content": "Hello world",
                        "message": "first",
                        "timestamp": "2026-01-01T00:00:00+00:00",
                        "content_hash": "abc123",
                        "metadata": {},
                    }
                ],
            }
        ]
        import_file = tmp_path / "import.json"
        import_file.write_text(json.dumps(export_data))

        with patch("promptdiff.cli._get_store", return_value=target_store):
            result = runner.invoke(cli, ["import", str(import_file)])
            assert result.exit_code == 0
            assert "Imported 1 version" in result.output

        assert "imported-prompt" in target_store.list_prompts()
        v = target_store.get_version("imported-prompt", 1)
        assert v.content == "Hello world"

    def test_import_jsonl(self, runner: CliRunner, tmp_path: Path) -> None:
        target_store = PromptStore(tmp_path / "target")
        target_store.init()

        records = [
            {"name": "p1", "tags": [], "versions": [{"version": 1, "content": "A", "message": ""}]},
            {"name": "p2", "tags": [], "versions": [{"version": 1, "content": "B", "message": ""}]},
        ]
        import_file = tmp_path / "import.jsonl"
        import_file.write_text("\n".join(json.dumps(r) for r in records))

        with patch("promptdiff.cli._get_store", return_value=target_store):
            result = runner.invoke(cli, ["import", str(import_file)])
            assert result.exit_code == 0
            assert "Imported 2 version" in result.output
            assert len(target_store.list_prompts()) == 2

    def test_import_skips_existing(self, runner: CliRunner, store: PromptStore, tmp_path: Path) -> None:
        export_data = [
            {
                "name": "chat-v1",
                "tags": [],
                "versions": [{"version": 1, "content": "overwrite attempt", "message": ""}],
            }
        ]
        import_file = tmp_path / "import.json"
        import_file.write_text(json.dumps(export_data))

        with patch("promptdiff.cli._get_store", return_value=store):
            result = runner.invoke(cli, ["import", str(import_file)])
            assert result.exit_code == 0
            assert "skipped 1" in result.output.lower()

    def test_import_merge_existing(self, runner: CliRunner, store: PromptStore, tmp_path: Path) -> None:
        export_data = [
            {
                "name": "chat-v1",
                "tags": [],
                "versions": [{"version": 99, "content": "brand new version", "message": "merged"}],
            }
        ]
        import_file = tmp_path / "import.json"
        import_file.write_text(json.dumps(export_data))

        with patch("promptdiff.cli._get_store", return_value=store):
            result = runner.invoke(cli, ["import", str(import_file), "--merge"])
            assert result.exit_code == 0
            assert "Imported 1 version" in result.output

    def test_import_with_tags(self, runner: CliRunner, tmp_path: Path) -> None:
        target_store = PromptStore(tmp_path / "target")
        target_store.init()

        export_data = [
            {
                "name": "tagged-prompt",
                "tags": ["alpha", "beta"],
                "versions": [{"version": 1, "content": "tagged content", "message": ""}],
            }
        ]
        import_file = tmp_path / "import.json"
        import_file.write_text(json.dumps(export_data))

        with patch("promptdiff.cli._get_store", return_value=target_store):
            result = runner.invoke(cli, ["import", str(import_file)])
            assert result.exit_code == 0

        from promptdiff.registry import PromptRegistry
        registry = PromptRegistry(target_store)
        tags = registry.get_tags("tagged-prompt")
        assert "alpha" in tags
        assert "beta" in tags

    def test_import_single_object(self, runner: CliRunner, tmp_path: Path) -> None:
        """Import a single JSON object (not array)."""
        target_store = PromptStore(tmp_path / "target")
        target_store.init()

        export_data = {
            "name": "solo",
            "tags": [],
            "versions": [{"version": 1, "content": "solo prompt", "message": ""}],
        }
        import_file = tmp_path / "import.json"
        import_file.write_text(json.dumps(export_data))

        with patch("promptdiff.cli._get_store", return_value=target_store):
            result = runner.invoke(cli, ["import", str(import_file)])
            assert result.exit_code == 0
            assert "Imported 1 version" in result.output


class TestExportImportRoundtrip:
    """Test that export -> import preserves data."""

    def test_roundtrip(self, runner: CliRunner, store: PromptStore, tmp_path: Path) -> None:
        export_file = str(tmp_path / "roundtrip.json")

        # Export
        with patch("promptdiff.cli._get_store", return_value=store):
            result = runner.invoke(cli, ["export", "-o", export_file])
            assert result.exit_code == 0

        # Import into fresh store
        target_store = PromptStore(tmp_path / "target")
        target_store.init()

        with patch("promptdiff.cli._get_store", return_value=target_store):
            result = runner.invoke(cli, ["import", export_file])
            assert result.exit_code == 0

        # Verify all prompts were transferred
        assert set(target_store.list_prompts()) == set(store.list_prompts())
        for name in store.list_prompts():
            orig_versions = store.list_versions(name)
            new_versions = target_store.list_versions(name)
            assert len(new_versions) == len(orig_versions)
            for orig, new in zip(orig_versions, new_versions):
                assert orig.content == new.content
