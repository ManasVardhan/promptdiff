"""Tests for the doctor integrity sweep (promptdiff doctor)."""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from promptdiff.cli import cli
from promptdiff.doctor import run_doctor
from promptdiff.pins import PinManager
from promptdiff.releases import ReleaseManager
from promptdiff.remote import add_remote
from promptdiff.store import PromptStore
from promptdiff.tracking import FileTracker


@pytest.fixture
def store(tmp_path):
    s = PromptStore(tmp_path)
    s.init()
    return s


def _categories(report):
    return [i.category for i in report.issues]


class TestHealthyStore:
    def test_empty_store_is_healthy(self, store):
        report = run_doctor(store)
        assert report.ok
        assert report.issues == []

    def test_populated_store_is_healthy(self, store, tmp_path):
        store.add("greet", "Hello {name}", message="v1")
        store.add("greet", "Hi {name}", message="v2")
        store.add("farewell", "Bye {name}")
        src = tmp_path / "prompt.txt"
        src.write_text("Hi {name}")
        FileTracker(store).track("greet", src)
        PinManager(store).add("greet")
        ReleaseManager(store).create("prod-1", "greet")
        report = run_doctor(store)
        assert report.ok
        assert report.prompts_checked == 2
        assert report.pins_checked == 1
        assert report.releases_checked == 1
        assert report.tracked_checked == 1

    def test_uninitialized_store_raises(self, tmp_path):
        with pytest.raises(RuntimeError):
            run_doctor(PromptStore(tmp_path / "nowhere"))


class TestStoreChecks:
    def test_orphan_version_file_detected(self, store):
        store.add("greet", "Hello")
        (store.prompts_path / "greet" / "v9.txt").write_text("stray")
        report = run_doctor(store)
        assert "orphan_version_file" in _categories(report)
        assert not report.ok

    def test_orphan_version_file_fixed(self, store):
        store.add("greet", "Hello")
        (store.prompts_path / "greet" / "v2.txt").write_text("stray")
        report = run_doctor(store, fix=True)
        # Recovery registers the version and repairs latest_version.
        assert all(i.fixed for i in report.issues)
        assert report.ok
        assert store.get_version("greet", 2).content == "stray"
        assert store.get_version("greet").version == 2
        # Store is healthy afterwards.
        assert run_doctor(store).ok

    def test_missing_version_file_detected(self, store):
        store.add("greet", "Hello")
        store.add("greet", "Hi")
        (store.prompts_path / "greet" / "v1.txt").unlink()
        report = run_doctor(store)
        assert "missing_version_file" in _categories(report)
        issue = next(i for i in report.issues if i.category == "missing_version_file")
        assert not issue.fixable

    def test_version_hash_mismatch_detected(self, store):
        store.add("greet", "Hello")
        (store.prompts_path / "greet" / "v1.txt").write_text("tampered")
        report = run_doctor(store)
        assert "version_hash_mismatch" in _categories(report)

    def test_hash_mismatch_never_fixed(self, store):
        store.add("greet", "Hello")
        (store.prompts_path / "greet" / "v1.txt").write_text("tampered")
        report = run_doctor(store, fix=True)
        issue = next(i for i in report.issues if i.category == "version_hash_mismatch")
        assert not issue.fixed
        assert not report.ok

    def test_latest_mismatch_detected_and_fixed(self, store):
        store.add("greet", "Hello")
        store.add("greet", "Hi")
        meta_path = store.prompts_path / "greet" / "meta.json"
        meta = json.loads(meta_path.read_text())
        meta["latest_version"] = 1
        meta_path.write_text(json.dumps(meta))
        report = run_doctor(store)
        assert "latest_mismatch" in _categories(report)
        report = run_doctor(store, fix=True)
        assert report.ok
        assert store.get_version("greet").version == 2

    def test_corrupt_meta_detected(self, store):
        store.add("greet", "Hello")
        (store.prompts_path / "greet" / "meta.json").write_text("{not json")
        report = run_doctor(store)
        assert "corrupt_meta" in _categories(report)

    def test_empty_orphan_dir_fixed(self, store):
        (store.prompts_path / "ghost").mkdir()
        report = run_doctor(store)
        assert "orphan_dir" in _categories(report)
        report = run_doctor(store, fix=True)
        assert report.ok
        assert not (store.prompts_path / "ghost").exists()

    def test_nonempty_orphan_dir_not_fixed(self, store):
        ghost = store.prompts_path / "ghost"
        ghost.mkdir()
        (ghost / "v1.txt").write_text("content")
        report = run_doctor(store, fix=True)
        issue = next(i for i in report.issues if i.category == "orphan_dir")
        assert not issue.fixable
        assert not issue.fixed
        assert ghost.exists()
        assert (ghost / "v1.txt").read_text() == "content"


class TestTrackedChecks:
    def test_missing_tracked_file_warns(self, store, tmp_path):
        src = tmp_path / "p.txt"
        src.write_text("Hello")
        FileTracker(store).track("greet", src)
        src.unlink()
        report = run_doctor(store)
        issue = next(i for i in report.issues if i.category == "tracked_file_missing")
        assert issue.severity == "warning"

    def test_tracked_prompt_missing_from_store_warns(self, store, tmp_path):
        src = tmp_path / "p.txt"
        src.write_text("Hello")
        FileTracker(store).track("greet", src)
        store.delete_prompt("greet")
        report = run_doctor(store)
        assert "tracked_prompt_missing" in _categories(report)


class TestPinChecks:
    def test_drifted_pin_warns(self, store):
        store.add("greet", "Hello")
        PinManager(store).add("greet")
        store.add("greet", "Hi")
        report = run_doctor(store)
        issue = next(i for i in report.issues if i.category == "pin_drifted")
        assert issue.severity == "warning"

    def test_modified_pin_errors(self, store):
        store.add("greet", "Hello")
        PinManager(store).add("greet")
        (store.prompts_path / "greet" / "v1.txt").write_text("tampered")
        report = run_doctor(store)
        assert "pin_modified" in _categories(report)

    def test_missing_pin_errors(self, store):
        store.add("greet", "Hello")
        PinManager(store).add("greet")
        store.delete_prompt("greet")
        report = run_doctor(store)
        assert "pin_missing" in _categories(report)

    def test_corrupt_lockfile_errors(self, store):
        (store.root / "promptdiff.lock").write_text("{oops")
        report = run_doctor(store)
        assert "corrupt_lockfile" in _categories(report)

    def test_no_lockfile_is_fine(self, store):
        store.add("greet", "Hello")
        assert run_doctor(store).ok


class TestReleaseChecks:
    def test_release_mismatch_detected(self, store):
        store.add("greet", "Hello")
        ReleaseManager(store).create("prod-1", "greet")
        (store.prompts_path / "greet" / "v1.txt").write_text("tampered")
        report = run_doctor(store)
        assert "release_mismatch" in _categories(report)

    def test_release_missing_version_detected(self, store):
        store.add("greet", "Hello")
        ReleaseManager(store).create("prod-1", "greet")
        store.delete_prompt("greet")
        report = run_doctor(store)
        assert "release_missing" in _categories(report)


class TestRemoteChecks:
    def test_stale_dir_remote_warns(self, store, tmp_path):
        gone = tmp_path / "elsewhere"
        gone.mkdir()
        add_remote(store, "backup", str(gone))
        gone.rmdir()
        report = run_doctor(store)
        issue = next(i for i in report.issues if i.category == "stale_remote")
        assert issue.severity == "warning"

    def test_live_dir_remote_ok(self, store, tmp_path):
        target = tmp_path / "elsewhere"
        target.mkdir()
        add_remote(store, "backup", str(target))
        assert run_doctor(store).ok

    def test_http_remote_skipped(self, store):
        add_remote(store, "hub", "https://example.com/prompts.json")
        assert run_doctor(store).ok


class TestDoctorCLI:
    def _run(self, tmp_path, *args):
        return CliRunner().invoke(cli, ["--store", str(tmp_path), *args])

    def test_healthy_store_exits_zero(self, store, tmp_path):
        store.add("greet", "Hello")
        result = self._run(tmp_path, "doctor")
        assert result.exit_code == 0
        assert "healthy" in result.output

    def test_problems_exit_one(self, store, tmp_path):
        store.add("greet", "Hello")
        (store.prompts_path / "greet" / "v1.txt").write_text("tampered")
        result = self._run(tmp_path, "doctor")
        assert result.exit_code == 1
        assert "ERROR" in result.output

    def test_fix_hint_shown_for_fixable_issues(self, store, tmp_path):
        store.add("greet", "Hello")
        (store.prompts_path / "greet" / "v2.txt").write_text("stray")
        result = self._run(tmp_path, "doctor")
        assert result.exit_code == 1
        assert "--fix" in result.output

    def test_fix_repairs_and_exits_zero(self, store, tmp_path):
        store.add("greet", "Hello")
        (store.prompts_path / "greet" / "v2.txt").write_text("stray")
        result = self._run(tmp_path, "doctor", "--fix")
        assert result.exit_code == 0
        assert "FIXED" in result.output
        assert "Repaired" in result.output

    def test_json_output(self, store, tmp_path):
        store.add("greet", "Hello")
        result = self._run(tmp_path, "doctor", "--json-output")
        assert result.exit_code == 0
        payload = json.loads(result.output)
        assert payload["ok"] is True
        assert payload["checked"]["prompts"] == 1

    def test_json_output_with_issues(self, store, tmp_path):
        store.add("greet", "Hello")
        (store.prompts_path / "greet" / "v1.txt").write_text("tampered")
        result = self._run(tmp_path, "doctor", "--json-output")
        assert result.exit_code == 1
        payload = json.loads(result.output)
        assert payload["ok"] is False
        assert payload["outstanding_count"] == 1
        assert payload["issues"][0]["category"] == "version_hash_mismatch"

    def test_uninitialized_exits_one(self, tmp_path):
        result = self._run(tmp_path / "empty", "doctor")
        assert result.exit_code == 1

    def test_alternate_lockfile(self, store, tmp_path):
        store.add("greet", "Hello")
        lock = tmp_path / "alt.lock"
        PinManager(store, lock_path=lock).add("greet")
        store.add("greet", "Hi")
        result = self._run(tmp_path, "doctor", "--lockfile", str(lock))
        assert result.exit_code == 1
        assert "drifted" in result.output.lower()
