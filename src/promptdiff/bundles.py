"""Signed prompt bundles for deployment.

A bundle packs a set of prompt versions into a single tar.gz artifact
containing the prompt contents plus a manifest with per-prompt SHA-256
checksums and a bundle-level checksum over the whole set. The serving
side can verify the artifact and unpack exactly the reviewed prompt set,
so deployments ship one file instead of chasing individual versions.

Archive layout:
    manifest.json            # format, created, message, entries, bundle checksum
    prompts/<name>.txt       # one file per bundled prompt
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import tarfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from promptdiff.pins import PinManager
from promptdiff.releases import release_checksum
from promptdiff.store import PromptStore

BUNDLE_FORMAT = 1
MANIFEST_NAME = "manifest.json"
PROMPTS_PREFIX = "prompts/"

_SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class BundleError(Exception):
    """Raised for invalid bundle operations (bad archives, checksum mismatches)."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def bundle_checksum(entries: list[BundleEntry]) -> str:
    """Return the SHA-256 hex digest over the sorted (prompt, version, checksum) set."""
    canonical = json.dumps(
        [
            {"prompt": e.prompt, "version": e.version, "checksum": e.checksum}
            for e in sorted(entries, key=lambda e: e.prompt)
        ],
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass
class BundleEntry:
    """One bundled prompt: its name, version, and content checksum."""

    prompt: str
    version: int
    checksum: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "prompt": self.prompt,
            "version": self.version,
            "checksum": self.checksum,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BundleEntry:
        return cls(
            prompt=data["prompt"],
            version=data["version"],
            checksum=data["checksum"],
        )


@dataclass
class BundleManifest:
    """Metadata stored inside a bundle archive."""

    format: int
    created: str
    message: str
    entries: list[BundleEntry]
    checksum: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": self.format,
            "created": self.created,
            "message": self.message,
            "entries": [e.to_dict() for e in self.entries],
            "checksum": self.checksum,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BundleManifest:
        try:
            entries = [BundleEntry.from_dict(e) for e in data["entries"]]
            return cls(
                format=data["format"],
                created=data.get("created", ""),
                message=data.get("message", ""),
                entries=entries,
                checksum=data["checksum"],
            )
        except (KeyError, TypeError) as exc:
            raise BundleError(f"Bundle manifest is malformed: {exc}") from exc


@dataclass
class BundleVerifyResult:
    """Outcome of verifying a bundle archive."""

    manifest: BundleManifest
    problems: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems

    def to_dict(self) -> dict[str, Any]:
        return {
            "manifest": self.manifest.to_dict(),
            "ok": self.ok,
            "problems": self.problems,
        }


def verify_contents(manifest: BundleManifest, contents: dict[str, str]) -> list[str]:
    """Return a list of problems for *contents* checked against *manifest*.

    An empty list means every manifest entry has a matching content file
    whose SHA-256 equals the recorded checksum, no extra prompt files are
    present, and the bundle-level checksum matches the entry set.
    """
    problems: list[str] = []

    if manifest.format != BUNDLE_FORMAT:
        problems.append(
            f"Unsupported bundle format {manifest.format} "
            f"(this promptdiff supports format {BUNDLE_FORMAT})"
        )

    expected = bundle_checksum(manifest.entries)
    if expected != manifest.checksum:
        problems.append(
            "Bundle checksum does not match its entries: the manifest "
            "was modified after the bundle was created"
        )

    seen = set()
    for entry in manifest.entries:
        seen.add(entry.prompt)
        if entry.prompt not in contents:
            problems.append(
                f"Missing content file for {entry.prompt} v{entry.version}"
            )
            continue
        actual = release_checksum(contents[entry.prompt])
        if actual != entry.checksum:
            problems.append(
                f"Content of {entry.prompt} v{entry.version} does not match "
                f"its checksum (expected {entry.checksum[:12]}, got {actual[:12]})"
            )
    for extra in sorted(set(contents) - seen):
        problems.append(f"Bundle contains unlisted prompt file: {extra}")
    return problems


def _validate_member_name(name: str) -> str:
    """Return the prompt name for a prompts/ member, rejecting unsafe paths."""
    if not name.startswith(PROMPTS_PREFIX) or not name.endswith(".txt"):
        raise BundleError(f"Unexpected file in bundle: {name}")
    prompt = name[len(PROMPTS_PREFIX) : -len(".txt")]
    if not _SAFE_NAME.match(prompt):
        raise BundleError(f"Unsafe prompt file name in bundle: {name}")
    return prompt


class BundleManager:
    """Create, inspect, verify, and unpack prompt bundles."""

    def __init__(self, store: PromptStore | None = None) -> None:
        self.store = store

    def _require_store(self) -> PromptStore:
        if self.store is None:
            raise BundleError("This operation requires a prompt store")
        return self.store

    def create(
        self,
        output: str | Path,
        prompts: list[str] | None = None,
        lockfile: str | Path | None = None,
        message: str = "",
    ) -> BundleManifest:
        """Pack prompts into a bundle archive at *output*.

        By default the bundle contains exactly the pinned prompt versions
        from the lockfile (promptdiff.lock), so what ships is what CI
        reviewed. Passing *prompts* instead bundles those prompts at
        their latest versions.

        Raises:
            BundleError: If there is nothing to bundle or a name is unsafe.
            FileNotFoundError: If a prompt or version does not exist.
        """
        store = self._require_store()
        entries: list[BundleEntry] = []
        contents: dict[str, str] = {}

        if prompts:
            for name in prompts:
                info = store.get_version(name)
                entries.append(
                    BundleEntry(
                        prompt=name,
                        version=info.version,
                        checksum=release_checksum(info.content),
                    )
                )
                contents[name] = info.content
        else:
            pins = PinManager(store, lock_path=lockfile).list_pins()
            if not pins:
                raise BundleError(
                    "No pins found to bundle. Pin prompts first with "
                    "'promptdiff pin add PROMPT' or pass prompt names explicitly."
                )
            for pin in pins:
                info = store.get_version(pin.prompt, pin.version)
                actual = release_checksum(info.content)
                if actual != pin.checksum:
                    raise BundleError(
                        f"Pinned content of {pin.prompt} v{pin.version} does not "
                        f"match its lockfile checksum (expected {pin.checksum[:12]}, "
                        f"got {actual[:12]}). Run 'promptdiff pin check' first."
                    )
                entries.append(
                    BundleEntry(
                        prompt=pin.prompt, version=pin.version, checksum=pin.checksum
                    )
                )
                contents[pin.prompt] = info.content

        for entry in entries:
            if not _SAFE_NAME.match(entry.prompt):
                raise BundleError(
                    f"Prompt name '{entry.prompt}' cannot be bundled safely"
                )

        manifest = BundleManifest(
            format=BUNDLE_FORMAT,
            created=_now_iso(),
            message=message,
            entries=sorted(entries, key=lambda e: e.prompt),
            checksum=bundle_checksum(entries),
        )

        output_path = Path(output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with tarfile.open(output_path, "w:gz") as tar:
            self._add_bytes(
                tar,
                MANIFEST_NAME,
                (json.dumps(manifest.to_dict(), indent=2) + "\n").encode("utf-8"),
            )
            for entry in manifest.entries:
                self._add_bytes(
                    tar,
                    f"{PROMPTS_PREFIX}{entry.prompt}.txt",
                    contents[entry.prompt].encode("utf-8"),
                )
        return manifest

    @staticmethod
    def _add_bytes(tar: tarfile.TarFile, name: str, data: bytes) -> None:
        info = tarfile.TarInfo(name=name)
        info.size = len(data)
        info.mtime = 0
        tar.addfile(info, io.BytesIO(data))

    @staticmethod
    def _read_archive(bundle_path: str | Path) -> tuple[BundleManifest, dict[str, str]]:
        """Return (manifest, {prompt: content}) from a bundle archive.

        Raises:
            BundleError: If the archive is missing, unreadable, or malformed.
        """
        path = Path(bundle_path)
        if not path.exists():
            raise BundleError(f"Bundle not found: {path}")
        try:
            with tarfile.open(path, "r:gz") as tar:
                manifest_data: bytes | None = None
                contents: dict[str, str] = {}
                for member in tar.getmembers():
                    if not member.isfile():
                        raise BundleError(
                            f"Unexpected non-file entry in bundle: {member.name}"
                        )
                    handle = tar.extractfile(member)
                    if handle is None:
                        raise BundleError(f"Cannot read bundle member: {member.name}")
                    data = handle.read()
                    if member.name == MANIFEST_NAME:
                        manifest_data = data
                    else:
                        prompt = _validate_member_name(member.name)
                        contents[prompt] = data.decode("utf-8")
        except tarfile.TarError as exc:
            raise BundleError(f"{path} is not a valid bundle archive: {exc}") from exc

        if manifest_data is None:
            raise BundleError(f"{path} has no {MANIFEST_NAME}")
        try:
            manifest = BundleManifest.from_dict(json.loads(manifest_data))
        except json.JSONDecodeError as exc:
            raise BundleError(f"Bundle manifest is not valid JSON: {exc}") from exc
        return manifest, contents

    def show(self, bundle_path: str | Path) -> BundleManifest:
        """Return the manifest of a bundle without verifying contents."""
        manifest, _ = self._read_archive(bundle_path)
        return manifest

    def verify(self, bundle_path: str | Path) -> BundleVerifyResult:
        """Verify a bundle's contents against its manifest checksums.

        Checks that every manifest entry has a matching content file whose
        SHA-256 equals the recorded checksum, that no extra prompt files
        are present, and that the bundle-level checksum matches the entry
        set. Returns a result whose ``ok`` is False on any mismatch.
        """
        manifest, contents = self._read_archive(bundle_path)
        return BundleVerifyResult(
            manifest=manifest, problems=verify_contents(manifest, contents)
        )

    def unpack(
        self,
        bundle_path: str | Path,
        dest: str | Path,
        force: bool = False,
    ) -> list[Path]:
        """Verify a bundle and write its prompts to *dest* as <name>.txt files.

        Refuses to unpack a bundle that fails verification, and refuses to
        overwrite existing files unless *force* is True.

        Returns:
            The list of written file paths.

        Raises:
            BundleError: If verification fails or a destination file exists.
        """
        result = self.verify(bundle_path)
        if not result.ok:
            raise BundleError(
                "Refusing to unpack a bundle that failed verification:\n  "
                + "\n  ".join(result.problems)
            )
        _, contents = self._read_archive(bundle_path)

        dest_dir = Path(dest)
        dest_dir.mkdir(parents=True, exist_ok=True)
        if not force:
            existing = [
                entry.prompt
                for entry in result.manifest.entries
                if (dest_dir / f"{entry.prompt}.txt").exists()
            ]
            if existing:
                raise BundleError(
                    f"Refusing to overwrite existing files in {dest_dir}: "
                    + ", ".join(sorted(existing))
                    + ". Use force to overwrite."
                )

        written: list[Path] = []
        for entry in result.manifest.entries:
            target = dest_dir / f"{entry.prompt}.txt"
            target.write_text(contents[entry.prompt])
            written.append(target)
        return written
