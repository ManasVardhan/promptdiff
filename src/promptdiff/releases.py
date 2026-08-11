"""Named prompt releases with integrity checksums.

A release pins a prompt version under a stable name (for example
prod-2026-08) together with the full SHA-256 checksum of its content.
Releases can be listed, diffed, and verified, so a deployment can prove
that the prompt it is about to serve is exactly the one that was
released.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from promptdiff.store import PromptStore

RELEASES_FILE = "releases.json"


class ReleaseError(Exception):
    """Raised for invalid release operations (duplicates, unknown names)."""


def release_checksum(content: str) -> str:
    """Return the full SHA-256 hex digest of a prompt's content."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


@dataclass
class Release:
    """A named, checksummed pointer to one version of one prompt."""

    name: str
    prompt: str
    version: int
    checksum: str
    created: str = ""
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "prompt": self.prompt,
            "version": self.version,
            "checksum": self.checksum,
            "created": self.created,
            "message": self.message,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Release:
        return cls(
            name=data["name"],
            prompt=data["prompt"],
            version=data["version"],
            checksum=data["checksum"],
            created=data.get("created", ""),
            message=data.get("message", ""),
        )


@dataclass
class VerifyResult:
    """Outcome of verifying a release against store content and, optionally, deployed content."""

    release: Release
    store_ok: bool
    deployed_ok: bool | None = None
    problems: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.store_ok and self.deployed_ok is not False

    def to_dict(self) -> dict[str, Any]:
        return {
            "release": self.release.to_dict(),
            "store_ok": self.store_ok,
            "deployed_ok": self.deployed_ok,
            "ok": self.ok,
            "problems": self.problems,
        }


class ReleaseManager:
    """Create, list, verify, and delete releases stored in .promptdiff/releases.json."""

    def __init__(self, store: PromptStore) -> None:
        self.store = store

    @property
    def _path(self):  # noqa: ANN202
        return self.store.store_path / RELEASES_FILE

    def _load(self) -> dict[str, dict[str, Any]]:
        self.store._ensure_init()
        if not self._path.exists():
            return {}
        data = json.loads(self._path.read_text())
        return data.get("releases", {})

    def _save(self, releases: dict[str, dict[str, Any]]) -> None:
        self._path.write_text(json.dumps({"releases": releases}, indent=2))

    def create(
        self,
        name: str,
        prompt: str,
        version: int | None = None,
        message: str = "",
        force: bool = False,
    ) -> Release:
        """Create a release pinning *prompt* at *version* (latest if None).

        Raises:
            ReleaseError: If the release name already exists and force is False.
            FileNotFoundError: If the prompt or version does not exist.
        """
        if not name or "/" in name:
            raise ReleaseError(f"Invalid release name: {name!r}")
        releases = self._load()
        if name in releases and not force:
            existing = Release.from_dict(releases[name])
            raise ReleaseError(
                f"Release '{name}' already exists "
                f"({existing.prompt} v{existing.version}). Use force to replace it."
            )
        info = self.store.get_version(prompt, version)
        release = Release(
            name=name,
            prompt=prompt,
            version=info.version,
            checksum=release_checksum(info.content),
            created=datetime.now(timezone.utc).isoformat(),
            message=message,
        )
        releases[name] = release.to_dict()
        self._save(releases)
        return release

    def get(self, name: str) -> Release:
        """Return the release with the given name.

        Raises:
            ReleaseError: If no such release exists.
        """
        releases = self._load()
        if name not in releases:
            raise ReleaseError(f"Release '{name}' not found")
        return Release.from_dict(releases[name])

    def list_releases(self) -> list[Release]:
        """Return all releases sorted by creation time."""
        releases = [Release.from_dict(d) for d in self._load().values()]
        return sorted(releases, key=lambda r: (r.created, r.name))

    def delete(self, name: str) -> None:
        """Delete a release by name.

        Raises:
            ReleaseError: If no such release exists.
        """
        releases = self._load()
        if name not in releases:
            raise ReleaseError(f"Release '{name}' not found")
        del releases[name]
        self._save(releases)

    def content(self, name: str) -> str:
        """Return the stored prompt content a release points at."""
        release = self.get(name)
        return self.store.get_version(release.prompt, release.version).content

    def verify(self, name: str, deployed_content: str | None = None) -> VerifyResult:
        """Verify a release's integrity.

        Always checks that the version content in the store still matches the
        release checksum. If *deployed_content* is given (for example the
        prompt text a service is about to use), it is checked against the
        release checksum too.
        """
        release = self.get(name)
        result = VerifyResult(release=release, store_ok=True)

        try:
            stored = self.store.get_version(release.prompt, release.version)
        except (FileNotFoundError, ValueError) as exc:
            result.store_ok = False
            result.problems.append(f"Stored version missing: {exc}")
        else:
            actual = release_checksum(stored.content)
            if actual != release.checksum:
                result.store_ok = False
                result.problems.append(
                    f"Store content for {release.prompt} v{release.version} does not "
                    f"match the release checksum (expected {release.checksum[:12]}, "
                    f"got {actual[:12]}). The stored version was modified after "
                    f"the release was created."
                )

        if deployed_content is not None:
            deployed = release_checksum(deployed_content)
            if deployed == release.checksum:
                result.deployed_ok = True
            else:
                result.deployed_ok = False
                result.problems.append(
                    f"Deployed content does not match release '{name}' "
                    f"(expected {release.checksum[:12]}, got {deployed[:12]})."
                )

        return result
