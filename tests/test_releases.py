"""Tests for signed prompt releases (promptdiff release)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from promptdiff.cli import cli
from promptdiff.releases import (
    Release,
    ReleaseError,
    ReleaseManager,
    release_checksum,
)
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


class TestReleaseManager:
    def test_create_pins_latest_version(self, manager: ReleaseManager) -> None:
        rel = manager.create("prod-2026-08", "support-agent", message="go live")
        assert rel.version == 2
        assert rel.prompt == "support-agent"
        assert rel.checksum == release_checksum(
            "You are a concise, helpful support agent.\n"
        )
        assert rel.message == "go live"
        assert rel.created

    def test_create_specific_version(self, manager: ReleaseManager) -> None:
        rel = manager.create("prod-v1", "support-agent", version=1)
        assert rel.version == 1
        assert rel.checksum == release_checksum("You are a helpful support agent.\n")

    def test_create_duplicate_rejected(self, manager: ReleaseManager) -> None:
        manager.create("prod", "support-agent")
        with pytest.raises(ReleaseError, match="already exists"):
            manager.create("prod", "summarizer")

    def test_create_force_replaces(self, manager: ReleaseManager) -> None:
        manager.create("prod", "support-agent")
        rel = manager.create("prod", "summarizer", force=True)
        assert rel.prompt == "summarizer"
        assert manager.get("prod").prompt == "summarizer"

    def test_create_unknown_prompt(self, manager: ReleaseManager) -> None:
        with pytest.raises(FileNotFoundError):
            manager.create("prod", "nope")

    def test_create_unknown_version(self, manager: ReleaseManager) -> None:
        with pytest.raises(FileNotFoundError):
            manager.create("prod", "support-agent", version=99)

    def test_create_invalid_name(self, manager: ReleaseManager) -> None:
        with pytest.raises(ReleaseError, match="Invalid release name"):
            manager.create("bad/name", "support-agent")

    def test_get_unknown(self, manager: ReleaseManager) -> None:
        with pytest.raises(ReleaseError, match="not found"):
            manager.get("nope")

    def test_list_sorted_by_created(self, manager: ReleaseManager) -> None:
        manager.create("b-release", "support-agent")
        manager.create("a-release", "summarizer")
        names = [r.name for r in manager.list_releases()]
        assert names == ["b-release", "a-release"]

    def test_delete(self, manager: ReleaseManager) -> None:
        manager.create("prod", "support-agent")
        manager.delete("prod")
        assert manager.list_releases() == []
        with pytest.raises(ReleaseError):
            manager.delete("prod")

    def test_content(self, manager: ReleaseManager) -> None:
        manager.create("prod-v1", "support-agent", version=1)
        assert manager.content("prod-v1") == "You are a helpful support agent.\n"

    def test_verify_ok(self, manager: ReleaseManager) -> None:
        manager.create("prod", "support-agent")
        result = manager.verify("prod")
        assert result.ok
        assert result.store_ok
        assert result.deployed_ok is None
        assert result.problems == []

    def test_verify_deployed_match(self, manager: ReleaseManager) -> None:
        manager.create("prod", "support-agent")
        result = manager.verify(
            "prod", deployed_content="You are a concise, helpful support agent.\n"
        )
        assert result.ok
        assert result.deployed_ok is True

    def test_verify_deployed_mismatch(self, manager: ReleaseManager) -> None:
        manager.create("prod", "support-agent")
        result = manager.verify("prod", deployed_content="Something else entirely.")
        assert not result.ok
        assert result.store_ok
        assert result.deployed_ok is False
        assert any("Deployed content" in p for p in result.problems)

    def test_verify_detects_store_tampering(
        self, manager: ReleaseManager, store: PromptStore
    ) -> None:
        manager.create("prod", "support-agent")
        # Tamper with the stored version content behind promptdiff's back
        store._version_path("support-agent", 2).write_text("EVIL PROMPT")
        result = manager.verify("prod")
        assert not result.ok
        assert not result.store_ok
        assert any("modified after" in p for p in result.problems)

    def test_verify_missing_version(
        self, manager: ReleaseManager, store: PromptStore
    ) -> None:
        manager.create("prod", "support-agent")
        store._version_path("support-agent", 2).unlink()
        result = manager.verify("prod")
        assert not result.store_ok

    def test_release_roundtrip_dict(self) -> None:
        rel = Release(
            name="prod", prompt="p", version=3, checksum="abc",
            created="2026-08-10T00:00:00+00:00", message="m",
        )
        assert Release.from_dict(rel.to_dict()) == rel

    def test_uninitialized_store(self, tmp_path: Path) -> None:
        manager = ReleaseManager(PromptStore(tmp_path / "empty"))
        with pytest.raises(RuntimeError, match="Not a promptdiff repository"):
            manager.list_releases()


class TestReleaseCli:
    def _cli(self, store: PromptStore, *args: str, input: str | None = None):
        runner = CliRunner()
        return runner.invoke(
            cli, ["--store", str(store.root), *args], input=input
        )

    def test_create_and_list(self, store: PromptStore) -> None:
        result = self._cli(store, "release", "create", "prod-2026-08", "support-agent",
                           "-m", "go live")
        assert result.exit_code == 0
        assert "Released 'prod-2026-08': support-agent v2" in result.output

        result = self._cli(store, "release", "list")
        assert result.exit_code == 0
        assert "prod-2026-08" in result.output
        assert "go live" in result.output

    def test_list_json(self, store: PromptStore) -> None:
        self._cli(store, "release", "create", "prod", "support-agent")
        result = self._cli(store, "release", "list", "--json-output")
        data = json.loads(result.output)
        assert data[0]["name"] == "prod"
        assert data[0]["version"] == 2
        assert len(data[0]["checksum"]) == 64

    def test_list_empty(self, store: PromptStore) -> None:
        result = self._cli(store, "release", "list")
        assert result.exit_code == 0
        assert "No releases yet" in result.output

    def test_create_duplicate_fails(self, store: PromptStore) -> None:
        self._cli(store, "release", "create", "prod", "support-agent")
        result = self._cli(store, "release", "create", "prod", "summarizer")
        assert result.exit_code == 1
        assert "already exists" in result.output

    def test_show(self, store: PromptStore) -> None:
        self._cli(store, "release", "create", "prod", "support-agent", "-v", "1")
        result = self._cli(store, "release", "show", "prod")
        assert result.exit_code == 0
        assert "support-agent v1" in result.output
        assert "You are a helpful support agent." in result.output

    def test_show_raw(self, store: PromptStore) -> None:
        self._cli(store, "release", "create", "prod", "support-agent", "-v", "1")
        result = self._cli(store, "release", "show", "prod", "--raw")
        assert result.output == "You are a helpful support agent.\n"

    def test_verify_ok(self, store: PromptStore) -> None:
        self._cli(store, "release", "create", "prod", "support-agent")
        result = self._cli(store, "release", "verify", "prod")
        assert result.exit_code == 0
        assert "OK" in result.output

    def test_verify_file_match(self, store: PromptStore, tmp_path: Path) -> None:
        self._cli(store, "release", "create", "prod", "support-agent")
        deployed = tmp_path / "deployed.txt"
        deployed.write_text("You are a concise, helpful support agent.\n")
        result = self._cli(store, "release", "verify", "prod", "--file", str(deployed))
        assert result.exit_code == 0

    def test_verify_file_mismatch_exits_1(self, store: PromptStore, tmp_path: Path) -> None:
        self._cli(store, "release", "create", "prod", "support-agent")
        deployed = tmp_path / "deployed.txt"
        deployed.write_text("tampered content")
        result = self._cli(store, "release", "verify", "prod", "--file", str(deployed))
        assert result.exit_code == 1
        assert "MISMATCH" in result.output

    def test_verify_stdin(self, store: PromptStore) -> None:
        self._cli(store, "release", "create", "prod", "support-agent")
        result = self._cli(
            store, "release", "verify", "prod", "--stdin",
            input="You are a concise, helpful support agent.\n",
        )
        assert result.exit_code == 0

    def test_verify_json_output(self, store: PromptStore) -> None:
        self._cli(store, "release", "create", "prod", "support-agent")
        result = self._cli(store, "release", "verify", "prod", "--json-output")
        data = json.loads(result.output)
        assert data["ok"] is True
        assert data["store_ok"] is True

    def test_verify_unknown_release(self, store: PromptStore) -> None:
        result = self._cli(store, "release", "verify", "ghost")
        assert result.exit_code == 1
        assert "not found" in result.output

    def test_diff_releases(self, store: PromptStore) -> None:
        self._cli(store, "release", "create", "v1", "support-agent", "-v", "1")
        self._cli(store, "release", "create", "v2", "support-agent", "-v", "2")
        result = self._cli(store, "release", "diff", "v1", "v2")
        assert result.exit_code == 0
        assert "concise" in result.output
        assert "Text similarity" in result.output

    def test_rm_with_confirm(self, store: PromptStore) -> None:
        self._cli(store, "release", "create", "prod", "support-agent")
        result = self._cli(store, "release", "rm", "prod", "-y")
        assert result.exit_code == 0
        assert "Deleted" in result.output
        result = self._cli(store, "release", "list")
        assert "No releases yet" in result.output

    def test_rm_aborted(self, store: PromptStore) -> None:
        self._cli(store, "release", "create", "prod", "support-agent")
        result = self._cli(store, "release", "rm", "prod", input="n\n")
        assert "Aborted" in result.output
        assert "prod" in self._cli(store, "release", "list").output
