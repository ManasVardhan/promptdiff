"""Link prompt names to source files so they can be auto-snapshotted.

The tracking map lives in ``.promptdiff/tracked.json`` and maps a prompt
name to a file path (stored relative to the store root when possible).
``sync_tracked`` snapshots every tracked file whose content differs from
the latest stored version, which is what the git pre-commit hook runs.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from promptdiff.store import PromptStore

TRACKED_FILE = "tracked.json"


@dataclass
class SyncResult:
    """Outcome of syncing a single tracked prompt."""

    name: str
    path: str
    status: str  # "added", "unchanged", "missing"
    version: int | None = None


class FileTracker:
    """Manages the prompt-name to source-file mapping for a store."""

    def __init__(self, store: PromptStore) -> None:
        self.store = store
        self._tracked_path = store.store_path / TRACKED_FILE

    def _read(self) -> dict[str, str]:
        if not self._tracked_path.exists():
            return {}
        data = json.loads(self._tracked_path.read_text())
        tracked = data.get("tracked", {})
        return {str(k): str(v) for k, v in tracked.items()}

    def _write(self, tracked: dict[str, str]) -> None:
        self._tracked_path.write_text(json.dumps({"tracked": tracked}, indent=2))

    def _resolve(self, path_str: str) -> Path:
        """Resolve a stored path (relative paths are relative to the store root)."""
        path = Path(path_str)
        if not path.is_absolute():
            path = self.store.root / path
        return path

    def track(self, name: str, file_path: str | Path) -> SyncResult:
        """Link *name* to *file_path* and snapshot its current content.

        The path is stored relative to the store root when the file lives
        inside it, so the mapping stays valid across machines.

        Raises:
            RuntimeError: If the store is not initialized.
            FileNotFoundError: If *file_path* does not exist.
        """
        self.store._ensure_init()
        path = Path(file_path).resolve()
        if not path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")

        try:
            stored = str(path.relative_to(self.store.root))
        except ValueError:
            stored = str(path)

        tracked = self._read()
        tracked[name] = stored
        self._write(tracked)

        return self._sync_one(name, stored, message=f"Snapshot of {stored}")

    def untrack(self, name: str) -> None:
        """Remove *name* from the tracking map. Stored versions are kept.

        Raises:
            KeyError: If *name* is not tracked.
        """
        self.store._ensure_init()
        tracked = self._read()
        if name not in tracked:
            raise KeyError(f"Prompt '{name}' is not tracked")
        del tracked[name]
        self._write(tracked)

    def list_tracked(self) -> dict[str, str]:
        """Return the tracking map of prompt name to stored path."""
        self.store._ensure_init()
        return self._read()

    def status(self, name: str, path_str: str) -> str:
        """Return sync status for one entry: "in sync", "modified", "missing", or "new"."""
        path = self._resolve(path_str)
        if not path.exists():
            return "missing"
        content = path.read_text()
        try:
            latest = self.store.get_version(name)
        except (FileNotFoundError, RuntimeError):
            return "new"
        return "in sync" if latest.content == content else "modified"

    def _sync_one(self, name: str, path_str: str, message: str = "") -> SyncResult:
        path = self._resolve(path_str)
        if not path.exists():
            return SyncResult(name=name, path=path_str, status="missing")

        content = path.read_text()
        try:
            latest = self.store.get_version(name)
            if latest.content == content:
                return SyncResult(
                    name=name, path=path_str, status="unchanged", version=latest.version
                )
        except FileNotFoundError:
            pass

        info = self.store.add(name, content, message=message or f"Auto-sync from {path_str}")
        return SyncResult(name=name, path=path_str, status="added", version=info.version)

    def sync(self, message: str = "") -> list[SyncResult]:
        """Snapshot every tracked file whose content changed.

        Returns one ``SyncResult`` per tracked prompt. Missing files are
        reported, not raised, so a pre-commit hook never blocks a commit.

        Raises:
            RuntimeError: If the store is not initialized.
        """
        self.store._ensure_init()
        tracked = self._read()
        return [self._sync_one(name, path, message=message) for name, path in sorted(tracked.items())]
