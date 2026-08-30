"""Tests for prompt bundles (promptdiff bundle)."""

from __future__ import annotations

import io
import json
import tarfile
from pathlib import Path

import pytest
from click.testing import CliRunner

from promptdiff.bundles import (
    BUNDLE_FORMAT,
    MANIFEST_NAME,
    BundleEntry,
    BundleError,
    BundleManager,
    BundleManifest,
    bundle_checksum,
)
from promptdiff.cli import cli
from promptdiff.pins import PinManager
from promptdiff.releases import release_checksum
from promptdiff.store import PromptStore


@pytest.fixture()
def store(tmp_path: Path) -> PromptStore:
    s = PromptStore(tmp_path / "repo")
    s.init()
    s.add("support-agent", "You are a helpful support agent.\n", message="first")
    s.add("support-agent", "You are a concise, helpful support agent.\n", message="second")
    s.add("summarizer", "Summarize the following text.\n")
    return s


@pytest.fixture()
def manager(store: PromptStore) -> BundleManager:
    return BundleManager(store)


def _pin_all(store: PromptStore) -> PinManager:
    pins = PinManager(store)
    pins.add("support-agent")
    pins.add("summarizer")
    return pins


class TestBundleCreate:
    def test_create_from_lockfile(
        self, manager: BundleManager, store: PromptStore, tmp_path: Path
    ) -> None:
        _pin_all(store)
        out = tmp_path / "prompts.bundle.tar.gz"
        manifest = manager.create(out, message="release 1")
        assert out.exists()
        assert manifest.format == BUNDLE_FORMAT
        assert manifest.message == "release 1"
        assert [e.prompt for e in manifest.entries] == ["summarizer", "support-agent"]
        assert manifest.checksum == bundle_checksum(manifest.entries)

    def test_create_uses_pinned_versions_not_latest(
        self, manager: BundleManager, store: PromptStore, tmp_path: Path
    ) -> None:
        PinManager(store).add("support-agent", version=1)
        manifest = manager.create(tmp_path / "b.tar.gz")
        assert len(manifest.entries) == 1
        assert manifest.entries[0].version == 1
        assert manifest.entries[0].checksum == release_checksum(
            "You are a helpful support agent.\n"
        )

    def test_create_explicit_prompts_uses_latest(
        self, manager: BundleManager, tmp_path: Path
    ) -> None:
        manifest = manager.create(tmp_path / "b.tar.gz", prompts=["support-agent"])
        assert manifest.entries[0].version == 2

    def test_create_without_pins_raises(
        self, manager: BundleManager, tmp_path: Path
    ) -> None:
        with pytest.raises(BundleError, match="No pins found"):
            manager.create(tmp_path / "b.tar.gz")

    def test_create_unknown_prompt_raises(
        self, manager: BundleManager, tmp_path: Path
    ) -> None:
        with pytest.raises(FileNotFoundError):
            manager.create(tmp_path / "b.tar.gz", prompts=["nope"])

    def test_create_fails_when_pinned_content_modified(
        self, manager: BundleManager, store: PromptStore, tmp_path: Path
    ) -> None:
        _pin_all(store)
        (store.prompts_path / "summarizer" / "v1.txt").write_text("tampered\n")
        with pytest.raises(BundleError, match="does not match its lockfile checksum"):
            manager.create(tmp_path / "b.tar.gz")

    def test_archive_layout(
        self, manager: BundleManager, store: PromptStore, tmp_path: Path
    ) -> None:
        _pin_all(store)
        out = tmp_path / "b.tar.gz"
        manager.create(out)
        with tarfile.open(out, "r:gz") as tar:
            names = sorted(tar.getnames())
        assert names == [
            MANIFEST_NAME,
            "prompts/summarizer.txt",
            "prompts/support-agent.txt",
        ]

    def test_without_store_raises(self, tmp_path: Path) -> None:
        with pytest.raises(BundleError, match="requires a prompt store"):
            BundleManager().create(tmp_path / "b.tar.gz", prompts=["x"])


class TestBundleShowVerify:
    def _bundle(
        self, manager: BundleManager, store: PromptStore, tmp_path: Path
    ) -> Path:
        _pin_all(store)
        out = tmp_path / "b.tar.gz"
        manager.create(out, message="msg")
        return out

    def test_show_roundtrip(
        self, manager: BundleManager, store: PromptStore, tmp_path: Path
    ) -> None:
        out = self._bundle(manager, store, tmp_path)
        manifest = BundleManager().show(out)
        assert manifest.message == "msg"
        assert len(manifest.entries) == 2

    def test_verify_ok(
        self, manager: BundleManager, store: PromptStore, tmp_path: Path
    ) -> None:
        out = self._bundle(manager, store, tmp_path)
        result = BundleManager().verify(out)
        assert result.ok
        assert result.problems == []

    def test_verify_detects_tampered_content(
        self, manager: BundleManager, store: PromptStore, tmp_path: Path
    ) -> None:
        out = self._bundle(manager, store, tmp_path)
        _rewrite_member(out, "prompts/summarizer.txt", b"evil\n")
        result = BundleManager().verify(out)
        assert not result.ok
        assert any("does not match" in p for p in result.problems)

    def test_verify_detects_tampered_manifest(
        self, manager: BundleManager, store: PromptStore, tmp_path: Path
    ) -> None:
        out = self._bundle(manager, store, tmp_path)
        manifest = BundleManager().show(out)
        data = manifest.to_dict()
        data["entries"][0]["version"] = 99
        _rewrite_member(out, MANIFEST_NAME, json.dumps(data).encode())
        result = BundleManager().verify(out)
        assert not result.ok
        assert any("Bundle checksum does not match" in p for p in result.problems)

    def test_verify_detects_missing_content_file(
        self, manager: BundleManager, store: PromptStore, tmp_path: Path
    ) -> None:
        out = self._bundle(manager, store, tmp_path)
        _drop_member(out, "prompts/summarizer.txt")
        result = BundleManager().verify(out)
        assert not result.ok
        assert any("Missing content file" in p for p in result.problems)

    def test_verify_detects_extra_file(
        self, manager: BundleManager, store: PromptStore, tmp_path: Path
    ) -> None:
        out = self._bundle(manager, store, tmp_path)
        _add_member(out, "prompts/extra.txt", b"surprise\n")
        result = BundleManager().verify(out)
        assert not result.ok
        assert any("unlisted prompt file" in p for p in result.problems)

    def test_verify_rejects_unsupported_format(
        self, manager: BundleManager, store: PromptStore, tmp_path: Path
    ) -> None:
        out = self._bundle(manager, store, tmp_path)
        manifest = BundleManager().show(out)
        data = manifest.to_dict()
        data["format"] = 99
        _rewrite_member(out, MANIFEST_NAME, json.dumps(data).encode())
        result = BundleManager().verify(out)
        assert any("Unsupported bundle format" in p for p in result.problems)

    def test_read_rejects_path_traversal_member(
        self, manager: BundleManager, store: PromptStore, tmp_path: Path
    ) -> None:
        out = self._bundle(manager, store, tmp_path)
        _add_member(out, "prompts/../escape.txt", b"nope\n")
        with pytest.raises(BundleError, match="Unexpected file|Unsafe prompt"):
            BundleManager().verify(out)

    def test_read_rejects_non_bundle_file(self, tmp_path: Path) -> None:
        bad = tmp_path / "not-a-bundle.tar.gz"
        bad.write_text("hello")
        with pytest.raises(BundleError, match="not a valid bundle archive"):
            BundleManager().show(bad)

    def test_read_missing_file(self, tmp_path: Path) -> None:
        with pytest.raises(BundleError, match="Bundle not found"):
            BundleManager().show(tmp_path / "missing.tar.gz")

    def test_read_missing_manifest(self, tmp_path: Path) -> None:
        out = tmp_path / "no-manifest.tar.gz"
        with tarfile.open(out, "w:gz") as tar:
            info = tarfile.TarInfo("prompts/a.txt")
            data = b"hi\n"
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
        with pytest.raises(BundleError, match="has no manifest.json"):
            BundleManager().show(out)


class TestBundleUnpack:
    def _bundle(
        self, manager: BundleManager, store: PromptStore, tmp_path: Path
    ) -> Path:
        _pin_all(store)
        out = tmp_path / "b.tar.gz"
        manager.create(out)
        return out

    def test_unpack_writes_files(
        self, manager: BundleManager, store: PromptStore, tmp_path: Path
    ) -> None:
        out = self._bundle(manager, store, tmp_path)
        dest = tmp_path / "deploy"
        written = BundleManager().unpack(out, dest)
        assert sorted(p.name for p in written) == ["summarizer.txt", "support-agent.txt"]
        assert (dest / "support-agent.txt").read_text() == (
            "You are a concise, helpful support agent.\n"
        )

    def test_unpack_refuses_overwrite(
        self, manager: BundleManager, store: PromptStore, tmp_path: Path
    ) -> None:
        out = self._bundle(manager, store, tmp_path)
        dest = tmp_path / "deploy"
        dest.mkdir()
        (dest / "summarizer.txt").write_text("old")
        with pytest.raises(BundleError, match="Refusing to overwrite"):
            BundleManager().unpack(out, dest)
        assert (dest / "summarizer.txt").read_text() == "old"

    def test_unpack_force_overwrites(
        self, manager: BundleManager, store: PromptStore, tmp_path: Path
    ) -> None:
        out = self._bundle(manager, store, tmp_path)
        dest = tmp_path / "deploy"
        dest.mkdir()
        (dest / "summarizer.txt").write_text("old")
        BundleManager().unpack(out, dest, force=True)
        assert (dest / "summarizer.txt").read_text() == "Summarize the following text.\n"

    def test_unpack_refuses_tampered_bundle(
        self, manager: BundleManager, store: PromptStore, tmp_path: Path
    ) -> None:
        out = self._bundle(manager, store, tmp_path)
        _rewrite_member(out, "prompts/summarizer.txt", b"evil\n")
        with pytest.raises(BundleError, match="failed verification"):
            BundleManager().unpack(out, tmp_path / "deploy")


class TestBundleDataclasses:
    def test_manifest_roundtrip(self) -> None:
        entries = [BundleEntry(prompt="a", version=1, checksum="c" * 64)]
        manifest = BundleManifest(
            format=BUNDLE_FORMAT,
            created="2026-08-30T00:00:00+00:00",
            message="m",
            entries=entries,
            checksum=bundle_checksum(entries),
        )
        assert BundleManifest.from_dict(manifest.to_dict()) == manifest

    def test_malformed_manifest_raises(self) -> None:
        with pytest.raises(BundleError, match="malformed"):
            BundleManifest.from_dict({"format": 1})

    def test_bundle_checksum_order_independent(self) -> None:
        a = BundleEntry(prompt="a", version=1, checksum="c" * 64)
        b = BundleEntry(prompt="b", version=2, checksum="d" * 64)
        assert bundle_checksum([a, b]) == bundle_checksum([b, a])


class TestBundleCLI:
    def _init_with_pins(self, runner: CliRunner) -> None:
        assert runner.invoke(cli, ["init"]).exit_code == 0
        assert runner.invoke(
            cli, ["add", "support-agent"], input="You are helpful.\n"
        ).exit_code == 0
        assert runner.invoke(cli, ["pin", "add", "support-agent"]).exit_code == 0

    def test_create_show_verify_unpack(self, tmp_path: Path) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem(temp_dir=tmp_path):
            self._init_with_pins(runner)
            result = runner.invoke(
                cli, ["bundle", "create", "out.tar.gz", "-m", "ship it"]
            )
            assert result.exit_code == 0
            assert "Bundled 1 prompt(s)" in result.output

            show = runner.invoke(cli, ["bundle", "show", "out.tar.gz"])
            assert show.exit_code == 0
            assert "support-agent" in show.output
            assert "ship it" in show.output

            verify = runner.invoke(cli, ["bundle", "verify", "out.tar.gz"])
            assert verify.exit_code == 0
            assert "Bundle OK" in verify.output

            unpack = runner.invoke(cli, ["bundle", "unpack", "out.tar.gz", "deploy"])
            assert unpack.exit_code == 0
            assert Path("deploy/support-agent.txt").read_text() == "You are helpful.\n"

    def test_create_without_pins_fails(self, tmp_path: Path) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem(temp_dir=tmp_path):
            assert runner.invoke(cli, ["init"]).exit_code == 0
            result = runner.invoke(cli, ["bundle", "create", "out.tar.gz"])
            assert result.exit_code == 1
            assert "No pins found" in result.output

    def test_create_explicit_prompt(self, tmp_path: Path) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem(temp_dir=tmp_path):
            assert runner.invoke(cli, ["init"]).exit_code == 0
            assert runner.invoke(
                cli, ["add", "solo"], input="Solo prompt.\n"
            ).exit_code == 0
            result = runner.invoke(
                cli, ["bundle", "create", "out.tar.gz", "-p", "solo"]
            )
            assert result.exit_code == 0
            assert "solo v1" in result.output

    def test_verify_tampered_exits_1(self, tmp_path: Path) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem(temp_dir=tmp_path):
            self._init_with_pins(runner)
            assert runner.invoke(
                cli, ["bundle", "create", "out.tar.gz"]
            ).exit_code == 0
            _rewrite_member(
                Path("out.tar.gz"), "prompts/support-agent.txt", b"evil\n"
            )
            result = runner.invoke(cli, ["bundle", "verify", "out.tar.gz"])
            assert result.exit_code == 1
            assert "FAILED" in result.output

    def test_verify_json_output(self, tmp_path: Path) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem(temp_dir=tmp_path):
            self._init_with_pins(runner)
            assert runner.invoke(
                cli, ["bundle", "create", "out.tar.gz"]
            ).exit_code == 0
            result = runner.invoke(
                cli, ["bundle", "verify", "out.tar.gz", "--json-output"]
            )
            assert result.exit_code == 0
            data = json.loads(result.output)
            assert data["ok"] is True
            assert data["manifest"]["entries"][0]["prompt"] == "support-agent"

    def test_show_json_output(self, tmp_path: Path) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem(temp_dir=tmp_path):
            self._init_with_pins(runner)
            assert runner.invoke(
                cli, ["bundle", "create", "out.tar.gz"]
            ).exit_code == 0
            result = runner.invoke(
                cli, ["bundle", "show", "out.tar.gz", "--json-output"]
            )
            assert result.exit_code == 0
            data = json.loads(result.output)
            assert data["format"] == BUNDLE_FORMAT

    def test_unpack_refuses_overwrite(self, tmp_path: Path) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem(temp_dir=tmp_path):
            self._init_with_pins(runner)
            assert runner.invoke(
                cli, ["bundle", "create", "out.tar.gz"]
            ).exit_code == 0
            Path("deploy").mkdir()
            Path("deploy/support-agent.txt").write_text("old")
            result = runner.invoke(cli, ["bundle", "unpack", "out.tar.gz", "deploy"])
            assert result.exit_code == 1
            assert "Refusing to overwrite" in result.output
            forced = runner.invoke(
                cli, ["bundle", "unpack", "out.tar.gz", "deploy", "--force"]
            )
            assert forced.exit_code == 0
            assert Path("deploy/support-agent.txt").read_text() == "You are helpful.\n"


def _rewrite_member(archive: Path, name: str, data: bytes) -> None:
    """Replace one member's bytes inside a tar.gz archive."""
    members: dict[str, bytes] = {}
    with tarfile.open(archive, "r:gz") as tar:
        for member in tar.getmembers():
            handle = tar.extractfile(member)
            assert handle is not None
            members[member.name] = handle.read()
    members[name] = data
    _write_archive(archive, members)


def _drop_member(archive: Path, name: str) -> None:
    members: dict[str, bytes] = {}
    with tarfile.open(archive, "r:gz") as tar:
        for member in tar.getmembers():
            handle = tar.extractfile(member)
            assert handle is not None
            members[member.name] = handle.read()
    del members[name]
    _write_archive(archive, members)


def _add_member(archive: Path, name: str, data: bytes) -> None:
    _rewrite_member(archive, name, data)


def _write_archive(archive: Path, members: dict[str, bytes]) -> None:
    with tarfile.open(archive, "w:gz") as tar:
        for name, data in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
