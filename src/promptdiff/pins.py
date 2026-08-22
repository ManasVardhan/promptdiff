"""Prompt version pinning for CI.

A lockfile (promptdiff.lock) records prompt name to version and checksum
pairs. `promptdiff pin check` re-hashes the pinned versions and compares
the store's latest versions against the lockfile, failing CI when any
tracked prompt drifted from its pin, so prompt changes always show up in
pull requests instead of sneaking into production.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from promptdiff.releases import release_checksum
from promptdiff.store import PromptStore

LOCK_FILE = "promptdiff.lock"

STATUS_OK = "ok"
STATUS_DRIFTED = "drifted"
STATUS_MODIFIED = "modified"
STATUS_MISSING = "missing"


class PinError(Exception):
    """Raised for invalid pin operations (unknown prompts, missing pins)."""


@dataclass
class Pin:
    """A locked prompt version with its content checksum."""

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
    def from_dict(cls, data: dict[str, Any]) -> Pin:
        return cls(
            prompt=data["prompt"],
            version=data["version"],
            checksum=data["checksum"],
        )


@dataclass
class PinCheckResult:
    """Outcome of checking one pin against the store."""

    pin: Pin
    status: str
    latest_version: int | None = None
    problems: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.status == STATUS_OK

    def to_dict(self) -> dict[str, Any]:
        return {
            "pin": self.pin.to_dict(),
            "status": self.status,
            "latest_version": self.latest_version,
            "ok": self.ok,
            "problems": self.problems,
        }


class PinManager:
    """Manage the promptdiff.lock lockfile next to the .promptdiff store.

    The lockfile lives in the store root (not inside .promptdiff/) so it
    can be committed to git and reviewed in pull requests.
    """

    def __init__(self, store: PromptStore, lock_path: str | Path | None = None) -> None:
        self.store = store
        self.lock_path = Path(lock_path) if lock_path else store.root / LOCK_FILE

    def _load(self) -> dict[str, dict[str, Any]]:
        if not self.lock_path.exists():
            return {}
        try:
            data = json.loads(self.lock_path.read_text())
        except json.JSONDecodeError as exc:
            raise PinError(f"Lockfile {self.lock_path} is not valid JSON: {exc}") from exc
        pins = data.get("pins", {})
        if not isinstance(pins, dict):
            raise PinError(f"Lockfile {self.lock_path} has an invalid 'pins' section")
        return pins

    def _save(self, pins: dict[str, dict[str, Any]]) -> None:
        payload = {"pins": {name: pins[name] for name in sorted(pins)}}
        self.lock_path.write_text(json.dumps(payload, indent=2) + "\n")

    def add(self, prompt: str, version: int | None = None) -> Pin:
        """Pin *prompt* at *version* (latest if None) and write the lockfile.

        Re-pinning an already pinned prompt simply overwrites its entry.

        Raises:
            FileNotFoundError: If the prompt or version does not exist.
        """
        info = self.store.get_version(prompt, version)
        pin = Pin(
            prompt=prompt,
            version=info.version,
            checksum=release_checksum(info.content),
        )
        pins = self._load()
        pins[prompt] = pin.to_dict()
        self._save(pins)
        return pin

    def remove(self, prompt: str) -> None:
        """Remove a prompt's pin from the lockfile.

        Raises:
            PinError: If the prompt is not pinned.
        """
        pins = self._load()
        if prompt not in pins:
            raise PinError(f"Prompt '{prompt}' is not pinned")
        del pins[prompt]
        self._save(pins)

    def list_pins(self) -> list[Pin]:
        """Return all pins sorted by prompt name."""
        pins = self._load()
        return [Pin.from_dict(pins[name]) for name in sorted(pins)]

    def get(self, prompt: str) -> Pin:
        """Return the pin for *prompt*.

        Raises:
            PinError: If the prompt is not pinned.
        """
        pins = self._load()
        if prompt not in pins:
            raise PinError(f"Prompt '{prompt}' is not pinned")
        return Pin.from_dict(pins[prompt])

    def check(self, prompt: str | None = None) -> list[PinCheckResult]:
        """Check pins against the store, returning one result per pin.

        Statuses:
            ok: The latest version equals the pin and its content matches
                the pinned checksum.
            drifted: New versions were added after the pin, so the latest
                version no longer equals the pinned one.
            modified: The pinned version's stored content no longer hashes
                to the pinned checksum (the store was edited in place).
            missing: The prompt or pinned version no longer exists.

        Args:
            prompt: Only check this pinned prompt (all pins if None).

        Raises:
            PinError: If the lockfile has no pins, or *prompt* is not pinned.
        """
        pins = self.list_pins()
        if not pins:
            raise PinError(
                f"No pins found in {self.lock_path}. Add one with: promptdiff pin add PROMPT"
            )
        if prompt is not None:
            pins = [p for p in pins if p.prompt == prompt]
            if not pins:
                raise PinError(f"Prompt '{prompt}' is not pinned")

        results: list[PinCheckResult] = []
        for pin in pins:
            results.append(self._check_one(pin))
        return results

    def _check_one(self, pin: Pin) -> PinCheckResult:
        result = PinCheckResult(pin=pin, status=STATUS_OK)
        try:
            pinned = self.store.get_version(pin.prompt, pin.version)
        except (FileNotFoundError, ValueError) as exc:
            result.status = STATUS_MISSING
            result.problems.append(f"Pinned version missing: {exc}")
            return result

        actual = release_checksum(pinned.content)
        if actual != pin.checksum:
            result.status = STATUS_MODIFIED
            result.problems.append(
                f"Content of {pin.prompt} v{pin.version} does not match the "
                f"pinned checksum (expected {pin.checksum[:12]}, got "
                f"{actual[:12]}). The stored version was modified after "
                f"it was pinned."
            )

        try:
            latest = self.store.get_version(pin.prompt)
        except (FileNotFoundError, ValueError):
            latest = None
        if latest is not None:
            result.latest_version = latest.version
            if latest.version != pin.version and result.status == STATUS_OK:
                result.status = STATUS_DRIFTED
                result.problems.append(
                    f"{pin.prompt} drifted from its pin: pinned v{pin.version}, "
                    f"latest is v{latest.version}. Re-pin with: "
                    f"promptdiff pin add {pin.prompt}"
                )
        return result

    def update(self, prompt: str | None = None) -> list[Pin]:
        """Re-pin prompts at their latest versions.

        Args:
            prompt: Only update this pinned prompt (all pins if None).

        Raises:
            PinError: If the lockfile has no pins, or *prompt* is not pinned.
        """
        pins = self.list_pins()
        if not pins:
            raise PinError(
                f"No pins found in {self.lock_path}. Add one with: promptdiff pin add PROMPT"
            )
        if prompt is not None:
            pins = [p for p in pins if p.prompt == prompt]
            if not pins:
                raise PinError(f"Prompt '{prompt}' is not pinned")
        updated: list[Pin] = []
        for pin in pins:
            updated.append(self.add(pin.prompt))
        return updated
