"""Tests for bundle serving helpers (promptdiff.serving)."""

from __future__ import annotations

import os
import tarfile
import threading
from pathlib import Path

import pytest

from promptdiff.bundles import BundleError, BundleManager
from promptdiff.serving import BundleServer, LoadedBundle, load_bundle
from promptdiff.store import PromptStore


@pytest.fixture()
def store(tmp_path: Path) -> PromptStore:
    s = PromptStore(tmp_path / "repo")
    s.init()
    s.add("support-agent", "You are a helpful support agent.\n", message="first")
    s.add("summarizer", "Summarize the following text.\n")
    return s


@pytest.fixture()
def manager(store: PromptStore) -> BundleManager:
    return BundleManager(store)


@pytest.fixture()
def bundle_path(manager: BundleManager, tmp_path: Path) -> Path:
    out = tmp_path / "prompts.bundle.tar.gz"
    manager.create(out, prompts=["support-agent", "summarizer"], message="v1")
    return out


def _rebuild(
    manager: BundleManager, path: Path, prompts: list[str], message: str
) -> None:
    """Recreate the bundle at *path* and force a different mtime."""
    before = path.stat().st_mtime_ns
    manager.create(path, prompts=prompts, message=message)
    os.utime(path, ns=(before + 2_000_000_000, before + 2_000_000_000))


def _tamper(path: Path) -> None:
    """Corrupt one prompt inside the archive, keeping it a valid tar.gz."""
    extract_dir = path.parent / "tampered"
    with tarfile.open(path, "r:gz") as tar:
        tar.extractall(extract_dir, filter="data")
    target = extract_dir / "prompts" / "support-agent.txt"
    target.write_text("EVIL PROMPT\n")
    with tarfile.open(path, "w:gz") as tar:
        for member in sorted(extract_dir.rglob("*")):
            if member.is_file():
                tar.add(member, arcname=str(member.relative_to(extract_dir)))
    stat = path.stat()
    os.utime(path, ns=(stat.st_mtime_ns + 2_000_000_000, stat.st_mtime_ns))


class TestLoadBundle:
    def test_loads_verified_bundle(self, bundle_path: Path) -> None:
        bundle = load_bundle(bundle_path)
        assert isinstance(bundle, LoadedBundle)
        assert bundle.get("support-agent") == "You are a helpful support agent.\n"
        assert bundle.get("summarizer") == "Summarize the following text.\n"
        assert bundle.manifest.message == "v1"
        assert bundle.names() == ["summarizer", "support-agent"]
        assert "summarizer" in bundle
        assert "nope" not in bundle
        assert len(bundle) == 2
        assert list(bundle) == ["summarizer", "support-agent"]

    def test_prompts_mapping_is_read_only(self, bundle_path: Path) -> None:
        bundle = load_bundle(bundle_path)
        with pytest.raises(TypeError):
            bundle.prompts["support-agent"] = "changed"  # type: ignore[index]

    def test_missing_prompt_raises_with_available_names(
        self, bundle_path: Path
    ) -> None:
        bundle = load_bundle(bundle_path)
        with pytest.raises(BundleError, match="no prompt 'nope'"):
            bundle.get("nope")
        with pytest.raises(BundleError, match="summarizer, support-agent"):
            bundle.get("nope")

    def test_missing_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(BundleError, match="Bundle not found"):
            load_bundle(tmp_path / "missing.tar.gz")

    def test_tampered_bundle_raises(self, bundle_path: Path) -> None:
        _tamper(bundle_path)
        with pytest.raises(BundleError, match="failed verification"):
            load_bundle(bundle_path)

    def test_not_an_archive_raises(self, tmp_path: Path) -> None:
        bad = tmp_path / "bad.tar.gz"
        bad.write_bytes(b"definitely not a tarball")
        with pytest.raises(BundleError):
            load_bundle(bad)

    def test_fingerprint_tracks_file(self, bundle_path: Path) -> None:
        bundle = load_bundle(bundle_path)
        stat = bundle_path.stat()
        assert bundle.fingerprint == (stat.st_mtime_ns, stat.st_size)


class TestBundleServer:
    def test_serves_prompts(self, bundle_path: Path) -> None:
        server = BundleServer(bundle_path)
        assert server.get("summarizer") == "Summarize the following text.\n"
        assert server.names() == ["summarizer", "support-agent"]
        assert "support-agent" in server
        assert server.manifest.message == "v1"
        assert server.path == bundle_path
        assert server.last_error is None

    def test_startup_fails_fast_on_bad_bundle(self, bundle_path: Path) -> None:
        _tamper(bundle_path)
        with pytest.raises(BundleError, match="failed verification"):
            BundleServer(bundle_path)

    def test_hot_reload_on_change(
        self, manager: BundleManager, store: PromptStore, bundle_path: Path
    ) -> None:
        server = BundleServer(bundle_path, check_interval=0.0)
        assert server.get("support-agent") == "You are a helpful support agent.\n"
        store.add("support-agent", "You are a terse support agent.\n")
        _rebuild(manager, bundle_path, ["support-agent", "summarizer"], "v2")
        assert server.get("support-agent") == "You are a terse support agent.\n"
        assert server.manifest.message == "v2"

    def test_check_interval_throttles_reload(
        self, manager: BundleManager, store: PromptStore, bundle_path: Path
    ) -> None:
        server = BundleServer(bundle_path, check_interval=3600.0)
        store.add("support-agent", "You are a terse support agent.\n")
        _rebuild(manager, bundle_path, ["support-agent", "summarizer"], "v2")
        assert server.get("support-agent") == "You are a helpful support agent.\n"
        assert server.reload_if_changed() is True
        assert server.get("support-agent") == "You are a terse support agent.\n"

    def test_reload_if_changed_false_when_unchanged(self, bundle_path: Path) -> None:
        server = BundleServer(bundle_path)
        assert server.reload_if_changed() is False

    def test_failed_reload_keeps_last_good_bundle(
        self, bundle_path: Path
    ) -> None:
        errors: list[BundleError] = []
        server = BundleServer(
            bundle_path, check_interval=0.0, on_error=errors.append
        )
        _tamper(bundle_path)
        assert server.reload_if_changed() is False
        assert server.get("support-agent") == "You are a helpful support agent.\n"
        assert server.last_error is not None
        assert "failed verification" in str(server.last_error)
        # Each access retries the changed file, so at least one error fired.
        assert errors
        assert all("failed verification" in str(e) for e in errors)

    def test_successful_reload_clears_last_error(
        self, manager: BundleManager, bundle_path: Path
    ) -> None:
        server = BundleServer(bundle_path, check_interval=0.0)
        _tamper(bundle_path)
        server.reload_if_changed()
        assert server.last_error is not None
        _rebuild(manager, bundle_path, ["support-agent", "summarizer"], "v3")
        assert server.reload_if_changed() is True
        assert server.last_error is None
        assert server.manifest.message == "v3"

    def test_forced_reload_raises_and_keeps_last_good(
        self, bundle_path: Path
    ) -> None:
        server = BundleServer(bundle_path)
        _tamper(bundle_path)
        with pytest.raises(BundleError, match="failed verification"):
            server.reload()
        assert server.get("support-agent") == "You are a helpful support agent.\n"

    def test_deleted_file_keeps_serving_and_records_error(
        self, bundle_path: Path
    ) -> None:
        server = BundleServer(bundle_path, check_interval=0.0)
        bundle_path.unlink()
        assert server.reload_if_changed() is False
        assert server.get("summarizer") == "Summarize the following text.\n"
        assert "Cannot stat bundle" in str(server.last_error)

    def test_on_reload_callback(
        self, manager: BundleManager, store: PromptStore, bundle_path: Path
    ) -> None:
        reloads: list[LoadedBundle] = []
        server = BundleServer(
            bundle_path, check_interval=0.0, on_reload=reloads.append
        )
        store.add("summarizer", "Summarize briefly.\n")
        _rebuild(manager, bundle_path, ["support-agent", "summarizer"], "v2")
        assert server.reload_if_changed() is True
        assert len(reloads) == 1
        assert reloads[0].get("summarizer") == "Summarize briefly.\n"

    def test_negative_check_interval_clamped(self, bundle_path: Path) -> None:
        server = BundleServer(bundle_path, check_interval=-5)
        assert server.check_interval == 0.0

    def test_concurrent_reads_during_reload(
        self, manager: BundleManager, store: PromptStore, bundle_path: Path
    ) -> None:
        server = BundleServer(bundle_path, check_interval=0.0)
        store.add("support-agent", "You are a terse support agent.\n")
        _rebuild(manager, bundle_path, ["support-agent", "summarizer"], "v2")
        results: list[str] = []
        errors: list[Exception] = []

        def reader() -> None:
            try:
                for _ in range(20):
                    results.append(server.get("support-agent"))
            except Exception as exc:  # pragma: no cover - failure path
                errors.append(exc)

        threads = [threading.Thread(target=reader) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert not errors
        valid = {
            "You are a helpful support agent.\n",
            "You are a terse support agent.\n",
        }
        assert set(results) <= valid
