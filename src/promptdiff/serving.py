"""Runtime helpers for serving verified prompt bundles.

Applications that consume bundles built with ``promptdiff bundle create``
can load them into memory without touching the CLI:

- :func:`load_bundle` reads a bundle archive once, verifies every checksum,
  and returns an immutable :class:`LoadedBundle` for lookups.
- :class:`BundleServer` keeps a bundle loaded for the lifetime of a process
  and hot-reloads it when the file changes on disk. A reload that fails
  verification never replaces the last good bundle, so a bad deploy cannot
  take working prompts down.

Example::

    from promptdiff import BundleServer

    server = BundleServer("prompts.bundle.tar.gz", check_interval=5.0)
    system_prompt = server.get("system")   # rechecks the file every 5s
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Callable, Iterator, Mapping

from promptdiff.bundles import (
    BundleError,
    BundleManager,
    BundleManifest,
    verify_contents,
)

__all__ = ["LoadedBundle", "BundleServer", "load_bundle"]


def _fingerprint(path: Path) -> tuple[int, int]:
    """Return (mtime_ns, size) used to detect bundle file changes."""
    stat = path.stat()
    return (stat.st_mtime_ns, stat.st_size)


@dataclass(frozen=True)
class LoadedBundle:
    """A verified bundle held in memory.

    Attributes:
        path: The archive the bundle was loaded from.
        manifest: The bundle manifest (entries, checksums, message).
        prompts: Read-only mapping of prompt name to content.
        loaded_at: Unix timestamp of when the bundle was loaded.
        fingerprint: (mtime_ns, size) of the archive at load time.
    """

    path: Path
    manifest: BundleManifest
    prompts: Mapping[str, str]
    loaded_at: float = field(compare=False)
    fingerprint: tuple[int, int] = field(compare=False)

    def get(self, name: str) -> str:
        """Return the content of prompt *name*.

        Raises:
            BundleError: If the bundle has no prompt with that name.
        """
        try:
            return self.prompts[name]
        except KeyError:
            available = ", ".join(sorted(self.prompts)) or "none"
            raise BundleError(
                f"Bundle {self.path.name} has no prompt '{name}' "
                f"(available: {available})"
            ) from None

    def names(self) -> list[str]:
        """Return the bundled prompt names, sorted."""
        return sorted(self.prompts)

    def __contains__(self, name: object) -> bool:
        return name in self.prompts

    def __len__(self) -> int:
        return len(self.prompts)

    def __iter__(self) -> Iterator[str]:
        return iter(sorted(self.prompts))


def load_bundle(bundle_path: str | Path) -> LoadedBundle:
    """Read a bundle archive, verify it, and return it as a :class:`LoadedBundle`.

    The archive is read exactly once and every checksum in the manifest is
    verified against the loaded contents, so a bundle that was tampered with
    (or truncated by a partial deploy) is rejected before any prompt is served.

    Raises:
        BundleError: If the archive is missing, malformed, or fails
            checksum verification.
    """
    path = Path(bundle_path)
    if not path.exists():
        raise BundleError(f"Bundle not found: {path}")
    fingerprint = _fingerprint(path)
    manifest, contents = BundleManager._read_archive(path)
    problems = verify_contents(manifest, contents)
    if problems:
        raise BundleError(
            f"Bundle {path} failed verification:\n  " + "\n  ".join(problems)
        )
    return LoadedBundle(
        path=path,
        manifest=manifest,
        prompts=MappingProxyType(dict(contents)),
        loaded_at=time.time(),
        fingerprint=fingerprint,
    )


class BundleServer:
    """Serve prompts from a bundle with verification and hot reload.

    The bundle is loaded (and fully verified) at construction time, so a
    service fails fast at startup on a bad artifact. Afterwards every
    accessor rechecks the file at most once per *check_interval* seconds
    and reloads when the file changed on disk.

    A hot reload that fails (unreadable file, checksum mismatch) never
    replaces the last good bundle: the server keeps serving the previous
    contents, stores the failure in :attr:`last_error`, and invokes
    *on_error* if given. A successful reload clears :attr:`last_error`
    and invokes *on_reload* with the fresh :class:`LoadedBundle`.

    Args:
        bundle_path: Path to the bundle archive.
        check_interval: Minimum seconds between file change checks.
            0 checks on every access.
        on_reload: Called with the new LoadedBundle after a successful reload.
        on_error: Called with the BundleError when a hot reload fails.
    """

    def __init__(
        self,
        bundle_path: str | Path,
        check_interval: float = 1.0,
        on_reload: Callable[[LoadedBundle], None] | None = None,
        on_error: Callable[[BundleError], None] | None = None,
    ) -> None:
        self._path = Path(bundle_path)
        self.check_interval = max(0.0, float(check_interval))
        self._on_reload = on_reload
        self._on_error = on_error
        self._lock = threading.Lock()
        self._bundle = load_bundle(self._path)
        self._last_check = time.monotonic()
        self.last_error: BundleError | None = None

    @property
    def path(self) -> Path:
        """The bundle archive path being served."""
        return self._path

    @property
    def bundle(self) -> LoadedBundle:
        """The currently loaded bundle, after a throttled change check."""
        self._maybe_reload()
        with self._lock:
            return self._bundle

    @property
    def manifest(self) -> BundleManifest:
        """The manifest of the currently loaded bundle."""
        return self.bundle.manifest

    def get(self, name: str) -> str:
        """Return the content of prompt *name* from the current bundle.

        Raises:
            BundleError: If the bundle has no prompt with that name.
        """
        return self.bundle.get(name)

    def names(self) -> list[str]:
        """Return the prompt names in the current bundle, sorted."""
        return self.bundle.names()

    def __contains__(self, name: object) -> bool:
        return name in self.bundle

    def reload(self) -> LoadedBundle:
        """Force a reload regardless of file changes.

        Raises:
            BundleError: If loading or verification fails. The last good
                bundle is kept and continues to be served.
        """
        fresh = load_bundle(self._path)
        with self._lock:
            self._bundle = fresh
            self._last_check = time.monotonic()
            self.last_error = None
        if self._on_reload is not None:
            self._on_reload(fresh)
        return fresh

    def reload_if_changed(self) -> bool:
        """Reload now if the file changed on disk, ignoring the throttle.

        Returns:
            True if a new bundle was loaded. False if the file is
            unchanged, or if the changed file failed verification (in
            which case :attr:`last_error` holds the failure and the last
            good bundle stays in place).
        """
        try:
            current = _fingerprint(self._path)
        except OSError as exc:
            error = BundleError(f"Cannot stat bundle {self._path}: {exc}")
            with self._lock:
                self._last_check = time.monotonic()
                self.last_error = error
            if self._on_error is not None:
                self._on_error(error)
            return False

        with self._lock:
            self._last_check = time.monotonic()
            unchanged = current == self._bundle.fingerprint
        if unchanged:
            return False

        try:
            self.reload()
        except BundleError as exc:
            with self._lock:
                self.last_error = exc
            if self._on_error is not None:
                self._on_error(exc)
            return False
        return True

    def _maybe_reload(self) -> None:
        with self._lock:
            due = time.monotonic() - self._last_check >= self.check_interval
        if due:
            self.reload_if_changed()
