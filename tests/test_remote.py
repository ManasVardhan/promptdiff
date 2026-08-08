"""Tests for remote registry backends (directory, git, HTTP) and the CLI."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from promptdiff import remote
from promptdiff.cli import cli
from promptdiff.registry import PromptRegistry
from promptdiff.store import PromptStore


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def local_store(tmp_path: Path) -> PromptStore:
    store = PromptStore(tmp_path / "local")
    store.init()
    return store


def _seed(store: PromptStore, name: str = "greet", n: int = 2, tags: list[str] | None = None):
    for i in range(1, n + 1):
        store.add(name, f"Hello v{i} from {name}", message=f"msg {i}")
    if tags:
        PromptRegistry(store).set_tags(name, tags)


class TestDetectBackend:
    def test_directory_paths(self) -> None:
        assert remote.detect_backend("/tmp/registry") == "dir"
        assert remote.detect_backend("../shared/store") == "dir"
        assert remote.detect_backend("~/prompts") == "dir"

    def test_git_urls(self) -> None:
        assert remote.detect_backend("git@github.com:me/prompts.git") == "git"
        assert remote.detect_backend("ssh://host/repo.git") == "git"
        assert remote.detect_backend("git://host/repo.git") == "git"
        assert remote.detect_backend("https://github.com/me/prompts.git") == "git"
        assert remote.detect_backend("/srv/git/prompts.git") == "git"

    def test_http_urls(self) -> None:
        assert remote.detect_backend("https://example.com/prompts.json") == "http"
        assert remote.detect_backend("http://internal/export") == "http"


class TestRemoteConfig:
    def test_add_and_load(self, local_store: PromptStore) -> None:
        remote.add_remote(local_store, "origin", "/tmp/registry")
        assert remote.load_remotes(local_store) == {"origin": "/tmp/registry"}

    def test_add_duplicate_raises(self, local_store: PromptStore) -> None:
        remote.add_remote(local_store, "origin", "/a")
        with pytest.raises(remote.RemoteError, match="already exists"):
            remote.add_remote(local_store, "origin", "/b")

    def test_remove(self, local_store: PromptStore) -> None:
        remote.add_remote(local_store, "origin", "/a")
        url = remote.remove_remote(local_store, "origin")
        assert url == "/a"
        assert remote.load_remotes(local_store) == {}

    def test_remove_missing_raises(self, local_store: PromptStore) -> None:
        with pytest.raises(remote.RemoteError, match="not found"):
            remote.remove_remote(local_store, "origin")

    def test_push_unknown_remote_lists_known(self, local_store: PromptStore) -> None:
        remote.add_remote(local_store, "backup", "/b")
        with pytest.raises(remote.RemoteError, match="Known remotes: backup"):
            remote.push(local_store, "origin")

    def test_load_requires_initialized_store(self, tmp_path: Path) -> None:
        store = PromptStore(tmp_path / "nope")
        with pytest.raises(RuntimeError):
            remote.load_remotes(store)


class TestDirectoryRemote:
    def test_push_creates_remote_prompts(self, local_store: PromptStore, tmp_path: Path) -> None:
        _seed(local_store, "greet", n=2, tags=["prod"])
        remote_root = tmp_path / "shared"
        remote.add_remote(local_store, "origin", str(remote_root))

        results = remote.push(local_store, "origin")

        assert [r.status for r in results] == ["new"]
        assert results[0].added_versions == 2
        dest = PromptStore(remote_root)
        assert dest.list_prompts() == ["greet"]
        assert dest.get_version("greet").content == "Hello v2 from greet"
        assert PromptRegistry(dest).get_tags("greet") == ["prod"]

    def test_push_is_idempotent(self, local_store: PromptStore, tmp_path: Path) -> None:
        _seed(local_store, "greet", n=2)
        remote.add_remote(local_store, "origin", str(tmp_path / "shared"))
        remote.push(local_store, "origin")
        results = remote.push(local_store, "origin")

        assert results[0].status == "up to date"
        assert results[0].added_versions == 0
        dest = PromptStore(tmp_path / "shared")
        assert len(dest.list_versions("greet")) == 2

    def test_push_only_new_versions(self, local_store: PromptStore, tmp_path: Path) -> None:
        _seed(local_store, "greet", n=2)
        remote.add_remote(local_store, "origin", str(tmp_path / "shared"))
        remote.push(local_store, "origin")

        local_store.add("greet", "Hello v3 from greet", message="msg 3")
        results = remote.push(local_store, "origin")

        assert results[0].status == "updated"
        assert results[0].added_versions == 1
        dest = PromptStore(tmp_path / "shared")
        assert len(dest.list_versions("greet")) == 3

    def test_push_selected_prompts(self, local_store: PromptStore, tmp_path: Path) -> None:
        _seed(local_store, "greet")
        _seed(local_store, "summarize")
        remote.add_remote(local_store, "origin", str(tmp_path / "shared"))

        results = remote.push(local_store, "origin", prompts=["summarize"])

        assert [r.name for r in results] == ["summarize"]
        assert PromptStore(tmp_path / "shared").list_prompts() == ["summarize"]

    def test_push_missing_prompt_raises(self, local_store: PromptStore, tmp_path: Path) -> None:
        remote.add_remote(local_store, "origin", str(tmp_path / "shared"))
        with pytest.raises(remote.RemoteError, match="not found in source store"):
            remote.push(local_store, "origin", prompts=["ghost"])

    def test_pull_from_directory(self, local_store: PromptStore, tmp_path: Path) -> None:
        src = PromptStore(tmp_path / "shared")
        src.init()
        _seed(src, "greet", n=2, tags=["team"])
        remote.add_remote(local_store, "origin", str(tmp_path / "shared"))

        results = remote.pull(local_store, "origin")

        assert results[0].status == "new"
        assert local_store.get_version("greet").content == "Hello v2 from greet"
        assert PromptRegistry(local_store).get_tags("greet") == ["team"]

    def test_pull_merges_tags(self, local_store: PromptStore, tmp_path: Path) -> None:
        src = PromptStore(tmp_path / "shared")
        src.init()
        _seed(src, "greet", n=2, tags=["remote-tag"])
        _seed(local_store, "greet", n=1, tags=["local-tag"])
        remote.add_remote(local_store, "origin", str(tmp_path / "shared"))

        remote.pull(local_store, "origin")

        assert PromptRegistry(local_store).get_tags("greet") == ["local-tag", "remote-tag"]

    def test_pull_from_uninitialized_dir_raises(
        self, local_store: PromptStore, tmp_path: Path
    ) -> None:
        remote.add_remote(local_store, "origin", str(tmp_path / "empty"))
        with pytest.raises(remote.RemoteError, match="not a promptdiff store"):
            remote.pull(local_store, "origin")

    def test_round_trip_between_two_stores(self, local_store: PromptStore, tmp_path: Path) -> None:
        _seed(local_store, "greet", n=2)
        shared = tmp_path / "shared"
        remote.add_remote(local_store, "origin", str(shared))
        remote.push(local_store, "origin")

        other = PromptStore(tmp_path / "other")
        other.init()
        remote.add_remote(other, "origin", str(shared))
        remote.pull(other, "origin")
        other.add("greet", "A change from the other machine")
        remote.push(other, "origin")

        results = remote.pull(local_store, "origin")
        assert results[0].added_versions == 1
        assert local_store.get_version("greet").content == "A change from the other machine"


class TestGitRemote:
    @pytest.fixture
    def bare_repo(self, tmp_path: Path) -> Path:
        bare = tmp_path / "central.git"
        subprocess.run(
            ["git", "init", "--bare", "--initial-branch=main", str(bare)],
            check=True,
            capture_output=True,
        )
        return bare

    def test_push_and_pull_via_git(
        self, local_store: PromptStore, bare_repo: Path, tmp_path: Path
    ) -> None:
        _seed(local_store, "greet", n=2, tags=["prod"])
        remote.add_remote(local_store, "origin", str(bare_repo))

        results = remote.push(local_store, "origin")
        assert results[0].status == "new"

        other = PromptStore(tmp_path / "other")
        other.init()
        remote.add_remote(other, "origin", str(bare_repo))
        pulled = remote.pull(other, "origin")

        assert pulled[0].status == "new"
        assert other.get_version("greet").content == "Hello v2 from greet"
        assert PromptRegistry(other).get_tags("greet") == ["prod"]

    def test_git_push_idempotent(self, local_store: PromptStore, bare_repo: Path) -> None:
        _seed(local_store, "greet", n=2)
        remote.add_remote(local_store, "origin", str(bare_repo))
        remote.push(local_store, "origin")
        results = remote.push(local_store, "origin")
        assert results[0].status == "up to date"

    def test_git_clone_failure_raises_remote_error(self, local_store: PromptStore) -> None:
        remote.add_remote(local_store, "origin", "/nonexistent/repo.git")
        with pytest.raises(remote.RemoteError, match="git clone failed"):
            remote.push(local_store, "origin")


class TestHttpRemote:
    def _export_body(self) -> str:
        return json.dumps(
            [
                {
                    "name": "greet",
                    "tags": ["hosted"],
                    "versions": [
                        {"version": 1, "content": "Hi there", "message": "first"},
                        {"version": 2, "content": "Hi there v2", "message": "second"},
                    ],
                }
            ]
        )

    def test_pull_from_http_export(
        self, local_store: PromptStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        remote.add_remote(local_store, "origin", "https://example.com/prompts.json")
        monkeypatch.setattr(remote, "_fetch_url", lambda url, timeout=30.0: self._export_body())

        results = remote.pull(local_store, "origin")

        assert results[0].status == "new"
        assert results[0].added_versions == 2
        assert local_store.get_version("greet").content == "Hi there v2"
        assert PromptRegistry(local_store).get_tags("greet") == ["hosted"]

    def test_http_pull_is_idempotent(
        self, local_store: PromptStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        remote.add_remote(local_store, "origin", "https://example.com/prompts.json")
        monkeypatch.setattr(remote, "_fetch_url", lambda url, timeout=30.0: self._export_body())
        remote.pull(local_store, "origin")
        results = remote.pull(local_store, "origin")
        assert results[0].status == "up to date"

    def test_http_pull_filters_prompts(
        self, local_store: PromptStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        remote.add_remote(local_store, "origin", "https://example.com/prompts.json")
        monkeypatch.setattr(remote, "_fetch_url", lambda url, timeout=30.0: self._export_body())
        with pytest.raises(remote.RemoteError, match="not found on remote: ghost"):
            remote.pull(local_store, "origin", prompts=["ghost"])

    def test_http_push_rejected(self, local_store: PromptStore) -> None:
        remote.add_remote(local_store, "origin", "https://example.com/prompts.json")
        with pytest.raises(remote.RemoteError, match="pull-only"):
            remote.push(local_store, "origin")

    def test_invalid_body_raises(
        self, local_store: PromptStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        remote.add_remote(local_store, "origin", "https://example.com/prompts.json")
        monkeypatch.setattr(remote, "_fetch_url", lambda url, timeout=30.0: "<html>nope</html>")
        with pytest.raises(remote.RemoteError, match="valid promptdiff export"):
            remote.pull(local_store, "origin")

    def test_jsonl_body_supported(
        self, local_store: PromptStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        body = "\n".join(
            json.dumps(r) for r in json.loads(self._export_body())
        )
        remote.add_remote(local_store, "origin", "https://example.com/prompts.jsonl")
        monkeypatch.setattr(remote, "_fetch_url", lambda url, timeout=30.0: body)
        results = remote.pull(local_store, "origin")
        assert results[0].added_versions == 2


class TestRemoteCli:
    def test_remote_add_list_rm(self, runner: CliRunner, tmp_path: Path) -> None:
        store_dir = tmp_path / "proj"
        args = ["--store", str(store_dir)]
        assert runner.invoke(cli, [*args, "init"]).exit_code == 0

        result = runner.invoke(cli, [*args, "remote", "add", "origin", str(tmp_path / "shared")])
        assert result.exit_code == 0
        assert "dir backend" in result.output

        result = runner.invoke(cli, [*args, "remote", "list"])
        assert result.exit_code == 0
        assert "origin" in result.output

        result = runner.invoke(cli, [*args, "remote", "rm", "origin"])
        assert result.exit_code == 0

        result = runner.invoke(cli, [*args, "remote", "list"])
        assert "No remotes configured" in result.output

    def test_remote_add_duplicate_errors(self, runner: CliRunner, tmp_path: Path) -> None:
        args = ["--store", str(tmp_path / "proj")]
        runner.invoke(cli, [*args, "init"])
        runner.invoke(cli, [*args, "remote", "add", "origin", "/a"])
        result = runner.invoke(cli, [*args, "remote", "add", "origin", "/b"])
        assert result.exit_code == 1
        assert "already exists" in result.output

    def test_push_pull_cli_round_trip(self, runner: CliRunner, tmp_path: Path) -> None:
        proj_a = ["--store", str(tmp_path / "a")]
        proj_b = ["--store", str(tmp_path / "b")]
        shared = str(tmp_path / "shared")

        runner.invoke(cli, [*proj_a, "init"])
        runner.invoke(cli, [*proj_a, "add", "greet", "-m", "v1"], input="Hello world\n")
        runner.invoke(cli, [*proj_a, "remote", "add", "origin", shared])

        result = runner.invoke(cli, [*proj_a, "push"])
        assert result.exit_code == 0
        assert "Pushed 1 new version(s)" in result.output

        runner.invoke(cli, [*proj_b, "init"])
        runner.invoke(cli, [*proj_b, "remote", "add", "origin", shared])
        result = runner.invoke(cli, [*proj_b, "pull"])
        assert result.exit_code == 0
        assert "Pulled 1 new version(s)" in result.output

        result = runner.invoke(cli, [*proj_b, "show", "greet", "--raw"])
        assert "Hello world" in result.output

    def test_push_unknown_remote_errors(self, runner: CliRunner, tmp_path: Path) -> None:
        args = ["--store", str(tmp_path / "proj")]
        runner.invoke(cli, [*args, "init"])
        result = runner.invoke(cli, [*args, "push"])
        assert result.exit_code == 1
        assert "not found" in result.output

    def test_pull_empty_remote_message(self, runner: CliRunner, tmp_path: Path) -> None:
        args = ["--store", str(tmp_path / "proj")]
        shared = PromptStore(tmp_path / "shared")
        shared.init()
        runner.invoke(cli, [*args, "init"])
        runner.invoke(cli, [*args, "remote", "add", "origin", str(tmp_path / "shared")])
        result = runner.invoke(cli, [*args, "pull"])
        assert result.exit_code == 0
        assert "no prompts to pull" in result.output
