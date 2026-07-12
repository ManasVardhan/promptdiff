"""Tests for file tracking, sync, and git hook integration."""

from __future__ import annotations

import os
import stat
import subprocess

import pytest
from click.testing import CliRunner

from promptdiff import hooks
from promptdiff.cli import cli
from promptdiff.hooks import HOOK_MARKER
from promptdiff.store import PromptStore
from promptdiff.tracking import FileTracker


def make_store(tmp_path):
    store = PromptStore(tmp_path)
    store.init()
    return store


def write_prompt_file(tmp_path, name="prompt.txt", content="You are a helpful assistant."):
    path = tmp_path / name
    path.write_text(content)
    return path


class TestFileTracker:
    def test_track_snapshots_initial_version(self, tmp_path):
        store = make_store(tmp_path)
        path = write_prompt_file(tmp_path)
        tracker = FileTracker(store)

        result = tracker.track("assistant", path)

        assert result.status == "added"
        assert result.version == 1
        assert store.get_version("assistant").content == "You are a helpful assistant."

    def test_track_stores_relative_path_inside_root(self, tmp_path):
        store = make_store(tmp_path)
        path = write_prompt_file(tmp_path, "prompts/system.txt".replace("/", "_"))
        tracker = FileTracker(store)

        tracker.track("sys", path)

        assert tracker.list_tracked()["sys"] == path.name

    def test_track_stores_absolute_path_outside_root(self, tmp_path):
        store = make_store(tmp_path / "repo")
        (tmp_path / "repo").mkdir(exist_ok=True)
        store.init()
        outside = write_prompt_file(tmp_path, "outside.txt")
        tracker = FileTracker(store)

        tracker.track("out", outside)

        stored = tracker.list_tracked()["out"]
        assert os.path.isabs(stored)

    def test_track_missing_file_raises(self, tmp_path):
        store = make_store(tmp_path)
        tracker = FileTracker(store)
        with pytest.raises(FileNotFoundError):
            tracker.track("ghost", tmp_path / "missing.txt")

    def test_track_uninitialized_store_raises(self, tmp_path):
        store = PromptStore(tmp_path)
        path = write_prompt_file(tmp_path)
        tracker = FileTracker(store)
        with pytest.raises(RuntimeError):
            tracker.track("x", path)

    def test_retrack_updates_path(self, tmp_path):
        store = make_store(tmp_path)
        a = write_prompt_file(tmp_path, "a.txt", "alpha")
        b = write_prompt_file(tmp_path, "b.txt", "beta")
        tracker = FileTracker(store)

        tracker.track("p", a)
        tracker.track("p", b)

        assert tracker.list_tracked()["p"] == "b.txt"
        assert store.get_version("p").content == "beta"

    def test_untrack_removes_entry_keeps_versions(self, tmp_path):
        store = make_store(tmp_path)
        path = write_prompt_file(tmp_path)
        tracker = FileTracker(store)
        tracker.track("assistant", path)

        tracker.untrack("assistant")

        assert tracker.list_tracked() == {}
        assert store.get_version("assistant").version == 1

    def test_untrack_unknown_raises(self, tmp_path):
        store = make_store(tmp_path)
        tracker = FileTracker(store)
        with pytest.raises(KeyError):
            tracker.untrack("nope")

    def test_status_in_sync_modified_missing(self, tmp_path):
        store = make_store(tmp_path)
        path = write_prompt_file(tmp_path)
        tracker = FileTracker(store)
        tracker.track("assistant", path)

        assert tracker.status("assistant", "prompt.txt") == "in sync"

        path.write_text("changed")
        assert tracker.status("assistant", "prompt.txt") == "modified"

        path.unlink()
        assert tracker.status("assistant", "prompt.txt") == "missing"

    def test_sync_adds_version_on_change(self, tmp_path):
        store = make_store(tmp_path)
        path = write_prompt_file(tmp_path)
        tracker = FileTracker(store)
        tracker.track("assistant", path)

        path.write_text("You are a terse assistant.")
        results = tracker.sync()

        assert len(results) == 1
        assert results[0].status == "added"
        assert results[0].version == 2
        assert store.get_version("assistant").content == "You are a terse assistant."

    def test_sync_unchanged_is_noop(self, tmp_path):
        store = make_store(tmp_path)
        path = write_prompt_file(tmp_path)
        tracker = FileTracker(store)
        tracker.track("assistant", path)

        results = tracker.sync()

        assert results[0].status == "unchanged"
        assert results[0].version == 1
        assert len(store.list_versions("assistant")) == 1

    def test_sync_missing_file_reported_not_raised(self, tmp_path):
        store = make_store(tmp_path)
        path = write_prompt_file(tmp_path)
        tracker = FileTracker(store)
        tracker.track("assistant", path)
        path.unlink()

        results = tracker.sync()

        assert results[0].status == "missing"
        assert results[0].version is None

    def test_sync_custom_message(self, tmp_path):
        store = make_store(tmp_path)
        path = write_prompt_file(tmp_path)
        tracker = FileTracker(store)
        tracker.track("assistant", path)

        path.write_text("v2 content")
        tracker.sync(message="pre-commit snapshot")

        assert store.get_version("assistant").message == "pre-commit snapshot"

    def test_sync_multiple_tracked_sorted(self, tmp_path):
        store = make_store(tmp_path)
        a = write_prompt_file(tmp_path, "a.txt", "aaa")
        b = write_prompt_file(tmp_path, "b.txt", "bbb")
        tracker = FileTracker(store)
        tracker.track("beta", b)
        tracker.track("alpha", a)

        results = tracker.sync()

        assert [r.name for r in results] == ["alpha", "beta"]

    def test_sync_empty_tracking_map(self, tmp_path):
        store = make_store(tmp_path)
        tracker = FileTracker(store)
        assert tracker.sync() == []


class TestTrackCli:
    def test_track_command(self, tmp_path, monkeypatch):
        make_store(tmp_path)
        write_prompt_file(tmp_path)
        monkeypatch.chdir(tmp_path)

        result = CliRunner().invoke(cli, ["track", "assistant", "prompt.txt"])

        assert result.exit_code == 0
        assert "Tracking 'assistant'" in result.output
        assert "v1" in result.output

    def test_track_missing_file_errors(self, tmp_path, monkeypatch):
        make_store(tmp_path)
        monkeypatch.chdir(tmp_path)
        result = CliRunner().invoke(cli, ["track", "assistant", "missing.txt"])
        assert result.exit_code == 2  # click Path(exists=True)

    def test_track_uninitialized_errors(self, tmp_path, monkeypatch):
        write_prompt_file(tmp_path)
        monkeypatch.chdir(tmp_path)
        result = CliRunner().invoke(cli, ["track", "assistant", "prompt.txt"])
        assert result.exit_code == 1
        assert "Not a promptdiff repository" in result.output

    def test_untrack_command(self, tmp_path, monkeypatch):
        make_store(tmp_path)
        write_prompt_file(tmp_path)
        monkeypatch.chdir(tmp_path)
        runner = CliRunner()
        runner.invoke(cli, ["track", "assistant", "prompt.txt"])

        result = runner.invoke(cli, ["untrack", "assistant"])

        assert result.exit_code == 0
        assert "Stopped tracking" in result.output

    def test_untrack_unknown_errors(self, tmp_path, monkeypatch):
        make_store(tmp_path)
        monkeypatch.chdir(tmp_path)
        result = CliRunner().invoke(cli, ["untrack", "ghost"])
        assert result.exit_code == 1
        assert "not tracked" in result.output

    def test_tracked_lists_status(self, tmp_path, monkeypatch):
        make_store(tmp_path)
        path = write_prompt_file(tmp_path)
        monkeypatch.chdir(tmp_path)
        runner = CliRunner()
        runner.invoke(cli, ["track", "assistant", "prompt.txt"])
        path.write_text("edited")

        result = runner.invoke(cli, ["tracked"])

        assert result.exit_code == 0
        assert "assistant" in result.output
        assert "modified" in result.output

    def test_tracked_empty(self, tmp_path, monkeypatch):
        make_store(tmp_path)
        monkeypatch.chdir(tmp_path)
        result = CliRunner().invoke(cli, ["tracked"])
        assert result.exit_code == 0
        assert "No tracked files" in result.output

    def test_sync_command_adds_version(self, tmp_path, monkeypatch):
        make_store(tmp_path)
        path = write_prompt_file(tmp_path)
        monkeypatch.chdir(tmp_path)
        runner = CliRunner()
        runner.invoke(cli, ["track", "assistant", "prompt.txt"])
        path.write_text("new content")

        result = runner.invoke(cli, ["sync"])

        assert result.exit_code == 0
        assert "Synced 'assistant' -> v2" in result.output
        assert "Synced 1 of 1" in result.output

    def test_sync_quiet_silent_when_unchanged(self, tmp_path, monkeypatch):
        make_store(tmp_path)
        write_prompt_file(tmp_path)
        monkeypatch.chdir(tmp_path)
        runner = CliRunner()
        runner.invoke(cli, ["track", "assistant", "prompt.txt"])

        result = runner.invoke(cli, ["sync", "--quiet"])

        assert result.exit_code == 0
        assert result.output == ""

    def test_sync_reports_missing_file(self, tmp_path, monkeypatch):
        make_store(tmp_path)
        path = write_prompt_file(tmp_path)
        monkeypatch.chdir(tmp_path)
        runner = CliRunner()
        runner.invoke(cli, ["track", "assistant", "prompt.txt"])
        path.unlink()

        result = runner.invoke(cli, ["sync"])

        assert result.exit_code == 0
        assert "Missing file" in result.output

    def test_sync_no_tracked_files(self, tmp_path, monkeypatch):
        make_store(tmp_path)
        monkeypatch.chdir(tmp_path)
        result = CliRunner().invoke(cli, ["sync"])
        assert result.exit_code == 0
        assert "No tracked files" in result.output


class TestHooks:
    def _init_git(self, tmp_path):
        subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
        return tmp_path / ".git"

    def test_find_git_dir(self, tmp_path):
        git_dir = self._init_git(tmp_path)
        nested = tmp_path / "a" / "b"
        nested.mkdir(parents=True)
        assert hooks.find_git_dir(nested) == git_dir

    def test_find_git_dir_missing_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            hooks.find_git_dir(tmp_path)

    def test_install_creates_executable_hook(self, tmp_path):
        self._init_git(tmp_path)
        path = hooks.install_hook(tmp_path)

        assert path.exists()
        content = path.read_text()
        assert HOOK_MARKER in content
        assert "promptdiff sync --quiet" in content
        assert path.stat().st_mode & stat.S_IXUSR

    def test_install_refuses_foreign_hook(self, tmp_path):
        self._init_git(tmp_path)
        hook = tmp_path / ".git" / "hooks" / "pre-commit"
        hook.parent.mkdir(parents=True, exist_ok=True)
        hook.write_text("#!/bin/sh\necho custom hook\n")

        with pytest.raises(FileExistsError):
            hooks.install_hook(tmp_path)

    def test_install_force_overwrites_foreign_hook(self, tmp_path):
        self._init_git(tmp_path)
        hook = tmp_path / ".git" / "hooks" / "pre-commit"
        hook.parent.mkdir(parents=True, exist_ok=True)
        hook.write_text("#!/bin/sh\necho custom hook\n")

        path = hooks.install_hook(tmp_path, force=True)
        assert HOOK_MARKER in path.read_text()

    def test_install_idempotent_on_own_hook(self, tmp_path):
        self._init_git(tmp_path)
        hooks.install_hook(tmp_path)
        path = hooks.install_hook(tmp_path)
        assert HOOK_MARKER in path.read_text()

    def test_uninstall_removes_own_hook(self, tmp_path):
        self._init_git(tmp_path)
        path = hooks.install_hook(tmp_path)
        hooks.uninstall_hook(tmp_path)
        assert not path.exists()

    def test_uninstall_refuses_foreign_hook(self, tmp_path):
        self._init_git(tmp_path)
        hook = tmp_path / ".git" / "hooks" / "pre-commit"
        hook.parent.mkdir(parents=True, exist_ok=True)
        hook.write_text("#!/bin/sh\necho custom hook\n")

        with pytest.raises(RuntimeError):
            hooks.uninstall_hook(tmp_path)
        assert hook.exists()

    def test_uninstall_missing_hook_raises(self, tmp_path):
        self._init_git(tmp_path)
        with pytest.raises(FileNotFoundError):
            hooks.uninstall_hook(tmp_path)

    def test_is_installed(self, tmp_path):
        assert hooks.is_installed(tmp_path) is False
        self._init_git(tmp_path)
        assert hooks.is_installed(tmp_path) is False
        hooks.install_hook(tmp_path)
        assert hooks.is_installed(tmp_path) is True


class TestHookCli:
    def _init_git(self, tmp_path):
        subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)

    def test_hook_install_command(self, tmp_path, monkeypatch):
        self._init_git(tmp_path)
        monkeypatch.chdir(tmp_path)
        result = CliRunner().invoke(cli, ["hook", "install"])
        assert result.exit_code == 0
        assert "Installed pre-commit hook" in result.output

    def test_hook_install_no_git_errors(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        result = CliRunner().invoke(cli, ["hook", "install"])
        assert result.exit_code == 1
        assert "No git repository" in result.output

    def test_hook_install_foreign_requires_force(self, tmp_path, monkeypatch):
        self._init_git(tmp_path)
        hook = tmp_path / ".git" / "hooks" / "pre-commit"
        hook.parent.mkdir(parents=True, exist_ok=True)
        hook.write_text("#!/bin/sh\necho other\n")
        monkeypatch.chdir(tmp_path)
        runner = CliRunner()

        blocked = runner.invoke(cli, ["hook", "install"])
        forced = runner.invoke(cli, ["hook", "install", "--force"])

        assert blocked.exit_code == 1
        assert "--force" in blocked.output
        assert forced.exit_code == 0

    def test_hook_status_and_uninstall(self, tmp_path, monkeypatch):
        self._init_git(tmp_path)
        monkeypatch.chdir(tmp_path)
        runner = CliRunner()

        assert "No promptdiff pre-commit hook" in runner.invoke(cli, ["hook", "status"]).output
        runner.invoke(cli, ["hook", "install"])
        assert "is installed" in runner.invoke(cli, ["hook", "status"]).output

        result = runner.invoke(cli, ["hook", "uninstall"])
        assert result.exit_code == 0
        assert "Removed pre-commit hook" in result.output

    def test_hook_uninstall_missing_errors(self, tmp_path, monkeypatch):
        self._init_git(tmp_path)
        monkeypatch.chdir(tmp_path)
        result = CliRunner().invoke(cli, ["hook", "uninstall"])
        assert result.exit_code == 1


class TestEndToEndGitCommit:
    """A real git commit should trigger the hook and snapshot tracked prompts."""

    def test_pre_commit_hook_snapshots_prompt(self, tmp_path):
        import sys

        # Make sure the hook can find the promptdiff executable from this venv
        bin_dir = os.path.dirname(sys.executable)
        env = {
            **os.environ,
            "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
            "GIT_AUTHOR_NAME": "Test",
            "GIT_AUTHOR_EMAIL": "t@example.com",
            "GIT_COMMITTER_NAME": "Test",
            "GIT_COMMITTER_EMAIL": "t@example.com",
        }

        def git(*args):
            subprocess.run(["git", *args], cwd=tmp_path, check=True, env=env, capture_output=True)

        git("init", "-q")
        store = make_store(tmp_path)
        prompt = write_prompt_file(tmp_path, content="version one")
        tracker = FileTracker(store)
        tracker.track("assistant", prompt)
        hooks.install_hook(tmp_path)

        git("add", "-A")
        git("commit", "-q", "-m", "initial")

        # Edit the prompt and commit again; hook should snapshot v2
        prompt.write_text("version two")
        git("add", "prompt.txt")
        git("commit", "-q", "-m", "edit prompt")

        latest = store.get_version("assistant")
        assert latest.version == 2
        assert latest.content == "version two"
