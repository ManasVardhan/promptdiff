"""Tests for the release audit trail (promptdiff release history)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from promptdiff.cli import cli
from promptdiff.releases import AUDIT_FILE, AuditEntry, ReleaseManager
from promptdiff.store import PromptStore


@pytest.fixture()
def store(tmp_path: Path) -> PromptStore:
    s = PromptStore(tmp_path)
    s.init()
    s.add("support-agent", "You are a helpful support agent.\n", message="first")
    s.add("support-agent", "You are a concise, helpful support agent.\n", message="second")
    s.add("summarizer", "Summarize the following text.\n")
    return s


@pytest.fixture()
def manager(store: PromptStore) -> ReleaseManager:
    return ReleaseManager(store)


class TestAuditRecording:
    def test_verify_records_entry(self, manager: ReleaseManager) -> None:
        manager.create("prod", "support-agent")
        manager.verify("prod")
        entries = manager.history()
        assert len(entries) == 1
        entry = entries[0]
        assert entry.release == "prod"
        assert entry.prompt == "support-agent"
        assert entry.version == 2
        assert entry.store_ok is True
        assert entry.deployed_ok is None
        assert entry.ok is True
        assert entry.timestamp

    def test_verify_records_deployed_outcome(self, manager: ReleaseManager) -> None:
        manager.create("prod", "support-agent")
        manager.verify("prod", deployed_content="You are a concise, helpful support agent.\n")
        manager.verify("prod", deployed_content="tampered\n")
        entries = manager.history()
        assert len(entries) == 2
        assert entries[0].deployed_ok is True
        assert entries[0].ok is True
        assert entries[1].deployed_ok is False
        assert entries[1].ok is False
        assert entries[1].problems

    def test_verify_record_false_skips_audit(self, manager: ReleaseManager) -> None:
        manager.create("prod", "support-agent")
        manager.verify("prod", record=False)
        assert manager.history() == []

    def test_audit_log_is_append_only_jsonl(
        self, manager: ReleaseManager, store: PromptStore
    ) -> None:
        manager.create("prod", "support-agent")
        manager.verify("prod")
        manager.verify("prod")
        audit_path = store.store_path / AUDIT_FILE
        lines = [line for line in audit_path.read_text().splitlines() if line.strip()]
        assert len(lines) == 2
        for line in lines:
            data = json.loads(line)
            assert data["release"] == "prod"

    def test_store_tamper_recorded_as_failure(
        self, manager: ReleaseManager, store: PromptStore
    ) -> None:
        manager.create("prod", "support-agent")
        # Tamper with stored version content behind the release's back.
        store._version_path("support-agent", 2).write_text("tampered\n")
        result = manager.verify("prod")
        assert result.store_ok is False
        entries = manager.history()
        assert entries[-1].store_ok is False
        assert entries[-1].ok is False


class TestHistoryFiltering:
    def test_history_filters_by_release(self, manager: ReleaseManager) -> None:
        manager.create("prod", "support-agent")
        manager.create("staging", "summarizer")
        manager.verify("prod")
        manager.verify("staging")
        manager.verify("prod")
        assert len(manager.history()) == 3
        prod_entries = manager.history(name="prod")
        assert len(prod_entries) == 2
        assert all(e.release == "prod" for e in prod_entries)

    def test_history_limit_returns_most_recent(self, manager: ReleaseManager) -> None:
        manager.create("prod", "support-agent")
        for _ in range(5):
            manager.verify("prod")
        entries = manager.history(limit=2)
        assert len(entries) == 2

    def test_history_empty_without_log(self, manager: ReleaseManager) -> None:
        assert manager.history() == []

    def test_history_skips_corrupt_lines(
        self, manager: ReleaseManager, store: PromptStore
    ) -> None:
        manager.create("prod", "support-agent")
        manager.verify("prod")
        audit_path = store.store_path / AUDIT_FILE
        with audit_path.open("a") as fh:
            fh.write("not json\n")
            fh.write('{"missing": "fields"}\n')
        entries = manager.history()
        assert len(entries) == 1

    def test_entry_roundtrip(self) -> None:
        entry = AuditEntry(
            timestamp="2026-08-16T00:00:00+00:00",
            release="prod",
            prompt="support-agent",
            version=2,
            store_ok=True,
            deployed_ok=False,
            ok=False,
            problems=["mismatch"],
        )
        assert AuditEntry.from_dict(entry.to_dict()) == entry


class TestHistoryCLI:
    def _init_with_release(self, runner: CliRunner) -> None:
        assert runner.invoke(cli, ["init"]).exit_code == 0
        assert runner.invoke(
            cli, ["add", "support-agent"], input="You are helpful.\n"
        ).exit_code == 0
        assert runner.invoke(
            cli, ["release", "create", "prod", "support-agent"]
        ).exit_code == 0

    def test_history_empty_message(self, tmp_path: Path) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem(temp_dir=tmp_path):
            self._init_with_release(runner)
            result = runner.invoke(cli, ["release", "history"])
            assert result.exit_code == 0
            assert "No verify outcomes recorded" in result.output

    def test_verify_then_history_table(self, tmp_path: Path) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem(temp_dir=tmp_path):
            self._init_with_release(runner)
            assert runner.invoke(cli, ["release", "verify", "prod"]).exit_code == 0
            result = runner.invoke(cli, ["release", "history"])
            assert result.exit_code == 0
            assert "prod" in result.output
            assert "PASS" in result.output

    def test_history_json_output(self, tmp_path: Path) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem(temp_dir=tmp_path):
            self._init_with_release(runner)
            runner.invoke(cli, ["release", "verify", "prod"])
            result = runner.invoke(cli, ["release", "history", "--json-output"])
            assert result.exit_code == 0
            data = json.loads(result.output)
            assert len(data) == 1
            assert data[0]["release"] == "prod"
            assert data[0]["ok"] is True

    def test_history_filter_and_limit(self, tmp_path: Path) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem(temp_dir=tmp_path):
            self._init_with_release(runner)
            for _ in range(3):
                runner.invoke(cli, ["release", "verify", "prod"])
            result = runner.invoke(
                cli, ["release", "history", "prod", "--limit", "2", "--json-output"]
            )
            assert result.exit_code == 0
            data = json.loads(result.output)
            assert len(data) == 2

    def test_failed_verify_shows_fail(self, tmp_path: Path) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem(temp_dir=tmp_path):
            self._init_with_release(runner)
            deployed = Path("deployed.txt")
            deployed.write_text("tampered\n")
            result = runner.invoke(
                cli, ["release", "verify", "prod", "--file", str(deployed)]
            )
            assert result.exit_code == 1
            result = runner.invoke(cli, ["release", "history"])
            assert result.exit_code == 0
            assert "FAIL" in result.output
            assert "MISMATCH" in result.output
