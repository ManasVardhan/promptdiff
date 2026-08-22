"""Tests for prompt version pinning (promptdiff pin)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from promptdiff.cli import cli
from promptdiff.pins import (
    LOCK_FILE,
    STATUS_DRIFTED,
    STATUS_MISSING,
    STATUS_MODIFIED,
    STATUS_OK,
    Pin,
    PinCheckResult,
    PinError,
    PinManager,
)
from promptdiff.releases import release_checksum
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
def manager(store: PromptStore) -> PinManager:
    return PinManager(store)


class TestPinManagerAdd:
    def test_add_latest(self, manager: PinManager, store: PromptStore) -> None:
        pin = manager.add("support-agent")
        assert pin.prompt == "support-agent"
        assert pin.version == 2
        assert pin.checksum == release_checksum(
            "You are a concise, helpful support agent.\n"
        )
        assert (store.root / LOCK_FILE).exists()

    def test_add_specific_version(self, manager: PinManager) -> None:
        pin = manager.add("support-agent", version=1)
        assert pin.version == 1
        assert pin.checksum == release_checksum("You are a helpful support agent.\n")

    def test_add_unknown_prompt_raises(self, manager: PinManager) -> None:
        with pytest.raises(FileNotFoundError):
            manager.add("nope")

    def test_add_unknown_version_raises(self, manager: PinManager) -> None:
        with pytest.raises(FileNotFoundError):
            manager.add("support-agent", version=99)

    def test_repin_overwrites(self, manager: PinManager) -> None:
        manager.add("support-agent", version=1)
        manager.add("support-agent", version=2)
        pins = manager.list_pins()
        assert len(pins) == 1
        assert pins[0].version == 2

    def test_lockfile_is_sorted_json(self, manager: PinManager, store: PromptStore) -> None:
        manager.add("support-agent")
        manager.add("summarizer")
        data = json.loads((store.root / LOCK_FILE).read_text())
        assert list(data["pins"]) == ["summarizer", "support-agent"]

    def test_custom_lock_path(self, store: PromptStore, tmp_path: Path) -> None:
        lock = tmp_path / "custom.lock"
        manager = PinManager(store, lock_path=lock)
        manager.add("summarizer")
        assert lock.exists()
        assert not (store.root / LOCK_FILE).exists()


class TestPinManagerRemoveListGet:
    def test_remove(self, manager: PinManager) -> None:
        manager.add("support-agent")
        manager.remove("support-agent")
        assert manager.list_pins() == []

    def test_remove_unpinned_raises(self, manager: PinManager) -> None:
        with pytest.raises(PinError):
            manager.remove("support-agent")

    def test_list_empty(self, manager: PinManager) -> None:
        assert manager.list_pins() == []

    def test_list_sorted(self, manager: PinManager) -> None:
        manager.add("support-agent")
        manager.add("summarizer")
        assert [p.prompt for p in manager.list_pins()] == ["summarizer", "support-agent"]

    def test_get(self, manager: PinManager) -> None:
        manager.add("summarizer")
        pin = manager.get("summarizer")
        assert pin.version == 1

    def test_get_unpinned_raises(self, manager: PinManager) -> None:
        with pytest.raises(PinError):
            manager.get("summarizer")

    def test_invalid_lockfile_raises(self, manager: PinManager) -> None:
        manager.lock_path.write_text("not json{")
        with pytest.raises(PinError):
            manager.list_pins()

    def test_invalid_pins_section_raises(self, manager: PinManager) -> None:
        manager.lock_path.write_text(json.dumps({"pins": [1, 2]}))
        with pytest.raises(PinError):
            manager.list_pins()


class TestPinManagerCheck:
    def test_check_ok(self, manager: PinManager) -> None:
        manager.add("support-agent")
        results = manager.check()
        assert len(results) == 1
        assert results[0].status == STATUS_OK
        assert results[0].ok is True
        assert results[0].latest_version == 2
        assert results[0].problems == []

    def test_check_no_pins_raises(self, manager: PinManager) -> None:
        with pytest.raises(PinError):
            manager.check()

    def test_check_unpinned_prompt_raises(self, manager: PinManager) -> None:
        manager.add("summarizer")
        with pytest.raises(PinError):
            manager.check("support-agent")

    def test_check_drift(self, manager: PinManager, store: PromptStore) -> None:
        manager.add("support-agent")
        store.add("support-agent", "A newer prompt.\n")
        result = manager.check()[0]
        assert result.status == STATUS_DRIFTED
        assert result.ok is False
        assert result.latest_version == 3
        assert "drifted" in result.problems[0]

    def test_check_modified_in_place(self, manager: PinManager, store: PromptStore) -> None:
        manager.add("support-agent")
        store._version_path("support-agent", 2).write_text("tampered\n")
        result = manager.check()[0]
        assert result.status == STATUS_MODIFIED
        assert result.ok is False
        assert "does not match" in result.problems[0]

    def test_check_missing_prompt(self, manager: PinManager, store: PromptStore) -> None:
        manager.add("summarizer")
        store.delete_prompt("summarizer")
        result = manager.check()[0]
        assert result.status == STATUS_MISSING
        assert result.ok is False

    def test_check_missing_version(self, manager: PinManager, store: PromptStore) -> None:
        manager.add("support-agent", version=1)
        store._version_path("support-agent", 1).unlink()
        result = manager.check()[0]
        assert result.status == STATUS_MISSING

    def test_check_single_prompt_filter(self, manager: PinManager, store: PromptStore) -> None:
        manager.add("support-agent")
        manager.add("summarizer")
        store.add("summarizer", "Summarize briefly.\n")
        results = manager.check("support-agent")
        assert len(results) == 1
        assert results[0].status == STATUS_OK

    def test_check_mixed_results(self, manager: PinManager, store: PromptStore) -> None:
        manager.add("support-agent")
        manager.add("summarizer")
        store.add("summarizer", "Summarize briefly.\n")
        statuses = {r.pin.prompt: r.status for r in manager.check()}
        assert statuses == {"support-agent": STATUS_OK, "summarizer": STATUS_DRIFTED}

    def test_result_to_dict(self, manager: PinManager) -> None:
        manager.add("summarizer")
        data = manager.check()[0].to_dict()
        assert data["status"] == STATUS_OK
        assert data["ok"] is True
        assert data["pin"]["prompt"] == "summarizer"
        assert data["latest_version"] == 1


class TestPinManagerUpdate:
    def test_update_all(self, manager: PinManager, store: PromptStore) -> None:
        manager.add("support-agent", version=1)
        manager.add("summarizer")
        store.add("summarizer", "Summarize briefly.\n")
        updated = manager.update()
        assert {p.prompt: p.version for p in updated} == {
            "summarizer": 2,
            "support-agent": 2,
        }
        assert all(r.status == STATUS_OK for r in manager.check())

    def test_update_single(self, manager: PinManager, store: PromptStore) -> None:
        manager.add("support-agent", version=1)
        manager.add("summarizer")
        manager.update("support-agent")
        assert manager.get("support-agent").version == 2
        assert manager.get("summarizer").version == 1

    def test_update_no_pins_raises(self, manager: PinManager) -> None:
        with pytest.raises(PinError):
            manager.update()

    def test_update_unpinned_raises(self, manager: PinManager) -> None:
        manager.add("summarizer")
        with pytest.raises(PinError):
            manager.update("support-agent")


class TestPinRoundTrip:
    def test_pin_dict_round_trip(self) -> None:
        pin = Pin(prompt="a", version=3, checksum="c" * 64)
        assert Pin.from_dict(pin.to_dict()) == pin

    def test_result_ok_property(self) -> None:
        pin = Pin(prompt="a", version=1, checksum="c" * 64)
        assert PinCheckResult(pin=pin, status=STATUS_OK).ok is True
        assert PinCheckResult(pin=pin, status=STATUS_DRIFTED).ok is False


class TestPinCLI:
    def _init_with_prompt(self, runner: CliRunner) -> None:
        assert runner.invoke(cli, ["init"]).exit_code == 0
        assert runner.invoke(
            cli, ["add", "support-agent"], input="You are helpful.\n"
        ).exit_code == 0

    def test_pin_add_and_list(self, tmp_path: Path) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem(temp_dir=tmp_path):
            self._init_with_prompt(runner)
            result = runner.invoke(cli, ["pin", "add", "support-agent"])
            assert result.exit_code == 0
            assert "Pinned support-agent v1" in result.output
            listing = runner.invoke(cli, ["pin", "list"])
            assert listing.exit_code == 0
            assert "support-agent" in listing.output

    def test_pin_add_unknown_prompt(self, tmp_path: Path) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem(temp_dir=tmp_path):
            assert runner.invoke(cli, ["init"]).exit_code == 0
            result = runner.invoke(cli, ["pin", "add", "nope"])
            assert result.exit_code == 1
            assert "not found" in result.output

    def test_pin_list_empty(self, tmp_path: Path) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem(temp_dir=tmp_path):
            assert runner.invoke(cli, ["init"]).exit_code == 0
            result = runner.invoke(cli, ["pin", "list"])
            assert result.exit_code == 0
            assert "No pins yet" in result.output

    def test_pin_list_json(self, tmp_path: Path) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem(temp_dir=tmp_path):
            self._init_with_prompt(runner)
            runner.invoke(cli, ["pin", "add", "support-agent"])
            result = runner.invoke(cli, ["pin", "list", "--json-output"])
            assert result.exit_code == 0
            data = json.loads(result.output)
            assert data[0]["prompt"] == "support-agent"
            assert data[0]["version"] == 1

    def test_pin_check_ok(self, tmp_path: Path) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem(temp_dir=tmp_path):
            self._init_with_prompt(runner)
            runner.invoke(cli, ["pin", "add", "support-agent"])
            result = runner.invoke(cli, ["pin", "check"])
            assert result.exit_code == 0
            assert "All 1 pins match" in result.output

    def test_pin_check_drift_exits_1(self, tmp_path: Path) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem(temp_dir=tmp_path):
            self._init_with_prompt(runner)
            runner.invoke(cli, ["pin", "add", "support-agent"])
            assert runner.invoke(
                cli, ["add", "support-agent"], input="You are extra helpful.\n"
            ).exit_code == 0
            result = runner.invoke(cli, ["pin", "check"])
            assert result.exit_code == 1
            assert "DRIFTED" in result.output
            assert "pin update" in result.output

    def test_pin_check_no_pins_exits_1(self, tmp_path: Path) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem(temp_dir=tmp_path):
            assert runner.invoke(cli, ["init"]).exit_code == 0
            result = runner.invoke(cli, ["pin", "check"])
            assert result.exit_code == 1
            assert "No pins found" in result.output

    def test_pin_check_json(self, tmp_path: Path) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem(temp_dir=tmp_path):
            self._init_with_prompt(runner)
            runner.invoke(cli, ["pin", "add", "support-agent"])
            assert runner.invoke(
                cli, ["add", "support-agent"], input="You are extra helpful.\n"
            ).exit_code == 0
            result = runner.invoke(cli, ["pin", "check", "--json-output"])
            assert result.exit_code == 1
            data = json.loads(result.output)
            assert data[0]["status"] == "drifted"
            assert data[0]["latest_version"] == 2

    def test_pin_update_then_check_passes(self, tmp_path: Path) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem(temp_dir=tmp_path):
            self._init_with_prompt(runner)
            runner.invoke(cli, ["pin", "add", "support-agent"])
            runner.invoke(cli, ["add", "support-agent"], input="You are extra helpful.\n")
            result = runner.invoke(cli, ["pin", "update"])
            assert result.exit_code == 0
            assert "Pinned support-agent v2" in result.output
            assert runner.invoke(cli, ["pin", "check"]).exit_code == 0

    def test_pin_rm(self, tmp_path: Path) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem(temp_dir=tmp_path):
            self._init_with_prompt(runner)
            runner.invoke(cli, ["pin", "add", "support-agent"])
            result = runner.invoke(cli, ["pin", "rm", "support-agent"])
            assert result.exit_code == 0
            assert "Removed pin" in result.output
            assert runner.invoke(cli, ["pin", "rm", "support-agent"]).exit_code == 1

    def test_pin_custom_lockfile(self, tmp_path: Path) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem(temp_dir=tmp_path):
            self._init_with_prompt(runner)
            result = runner.invoke(
                cli, ["pin", "add", "support-agent", "--lockfile", "ci.lock"]
            )
            assert result.exit_code == 0
            assert Path("ci.lock").exists()
            check = runner.invoke(cli, ["pin", "check", "--lockfile", "ci.lock"])
            assert check.exit_code == 0
