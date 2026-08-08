"""Remote registry backends: share prompts across machines and teams.

Supports three kinds of remotes, detected from the URL:

- Directory remotes: a path to another promptdiff store root (for example
  a mounted network share or a second checkout). Push and pull.
- Git remotes: any URL ending in ``.git`` or using ``git@``/``ssh://``/
  ``git://``. The repository holds a promptdiff store at its root. Push
  clones, syncs, commits, and pushes. Pull clones and syncs down.
- HTTP remotes: an ``http(s)://`` URL serving a JSON export (as produced
  by ``promptdiff export``). Pull only.

Sync is merge-based and idempotent: versions are matched by content hash,
so pushing or pulling twice never duplicates history, and tags are merged
as a union of both sides.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from promptdiff.registry import PromptRegistry
from promptdiff.store import PromptStore, _content_hash

REMOTES_FILE = "remotes.json"

BACKEND_DIR = "dir"
BACKEND_GIT = "git"
BACKEND_HTTP = "http"


class RemoteError(Exception):
    """Raised when a remote operation fails (bad remote, git or network error)."""


@dataclass
class SyncResult:
    """Outcome of syncing a single prompt.

    Attributes:
        name: Prompt name.
        added_versions: How many new versions were written to the destination.
        status: One of ``"new"``, ``"updated"``, or ``"up to date"``.
    """

    name: str
    added_versions: int
    status: str


def detect_backend(url: str) -> str:
    """Classify a remote URL as ``dir``, ``git``, or ``http``."""
    lowered = url.lower()
    if lowered.startswith(("git@", "ssh://", "git://")):
        return BACKEND_GIT
    if lowered.startswith(("http://", "https://")):
        return BACKEND_GIT if lowered.endswith(".git") else BACKEND_HTTP
    if lowered.endswith(".git"):
        return BACKEND_GIT
    return BACKEND_DIR


def _remotes_path(store: PromptStore) -> Path:
    return store.store_path / REMOTES_FILE


def load_remotes(store: PromptStore) -> dict[str, str]:
    """Return the name -> URL mapping of configured remotes."""
    store._ensure_init()
    path = _remotes_path(store)
    if not path.exists():
        return {}
    return json.loads(path.read_text())


def _save_remotes(store: PromptStore, remotes: dict[str, str]) -> None:
    _remotes_path(store).write_text(json.dumps(remotes, indent=2, sort_keys=True))


def add_remote(store: PromptStore, name: str, url: str) -> None:
    """Register a remote under *name*. Overwriting an existing name is an error."""
    remotes = load_remotes(store)
    if name in remotes:
        raise RemoteError(
            f"Remote '{name}' already exists ({remotes[name]}). Remove it first."
        )
    remotes[name] = url
    _save_remotes(store, remotes)


def remove_remote(store: PromptStore, name: str) -> str:
    """Delete a remote by name and return its URL."""
    remotes = load_remotes(store)
    if name not in remotes:
        raise RemoteError(f"Remote '{name}' not found.")
    url = remotes.pop(name)
    _save_remotes(store, remotes)
    return url


def _resolve_remote(store: PromptStore, name: str) -> str:
    remotes = load_remotes(store)
    if name not in remotes:
        known = ", ".join(sorted(remotes)) or "none configured"
        raise RemoteError(f"Remote '{name}' not found. Known remotes: {known}.")
    return remotes[name]


def _collect_records(
    store: PromptStore, prompts: list[str] | None = None
) -> list[dict[str, Any]]:
    """Build export-format records for *prompts* (all prompts when None)."""
    registry = PromptRegistry(store)
    names = prompts if prompts else store.list_prompts()
    records: list[dict[str, Any]] = []
    for name in names:
        try:
            versions = store.list_versions(name)
        except FileNotFoundError:
            raise RemoteError(f"Prompt '{name}' not found in source store.")
        records.append(
            {
                "name": name,
                "tags": registry.get_tags(name),
                "versions": [
                    {
                        "version": v.version,
                        "content": v.content,
                        "message": v.message,
                        "timestamp": v.timestamp,
                        "content_hash": v.content_hash,
                        "metadata": v.metadata,
                    }
                    for v in versions
                ],
            }
        )
    return records


def _apply_records(
    records: list[dict[str, Any]], dest: PromptStore
) -> list[SyncResult]:
    """Merge export-format records into *dest*, deduplicating by content hash."""
    if not dest.initialized:
        dest.init()
    registry = PromptRegistry(dest)
    existing_prompts = set(dest.list_prompts())
    results: list[SyncResult] = []

    for record in records:
        name = record["name"]
        is_new = name not in existing_prompts
        seen_hashes: set[str] = set()
        if not is_new:
            seen_hashes = {v.content_hash for v in dest.list_versions(name)}

        added = 0
        for v in record.get("versions", []):
            content = v["content"]
            digest = v.get("content_hash") or _content_hash(content)
            if digest in seen_hashes:
                continue
            dest.add(name, content, message=v.get("message", ""), metadata=v.get("metadata"))
            seen_hashes.add(digest)
            added += 1

        tags = record.get("tags") or []
        if tags and (not is_new or added):
            merged = tags if is_new else sorted(set(registry.get_tags(name)) | set(tags))
            registry.set_tags(name, sorted(set(merged)))

        if is_new and added:
            status = "new"
        elif added:
            status = "updated"
        else:
            status = "up to date"
        results.append(SyncResult(name=name, added_versions=added, status=status))

    return results


def _run_git(args: list[str], cwd: Path | None = None) -> str:
    """Run a git command, returning stdout or raising RemoteError with stderr."""
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        raise RemoteError("git executable not found. Install git to use git remotes.")
    if proc.returncode != 0:
        detail = proc.stderr.strip() or proc.stdout.strip() or "unknown git error"
        raise RemoteError(f"git {args[0]} failed: {detail}")
    return proc.stdout


def _fetch_url(url: str, timeout: float = 30.0) -> str:
    """Fetch the body of an HTTP(S) URL as text."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310
            return resp.read().decode("utf-8")
    except Exception as exc:
        raise RemoteError(f"Failed to fetch '{url}': {exc}")


def push(
    store: PromptStore, remote_name: str, prompts: list[str] | None = None
) -> list[SyncResult]:
    """Push local prompts to a remote registry.

    Directory remotes are synced in place. Git remotes are cloned to a
    temporary directory, synced, committed, and pushed. HTTP remotes are
    pull only and raise :class:`RemoteError`.
    """
    url = _resolve_remote(store, remote_name)
    backend = detect_backend(url)
    records = _collect_records(store, prompts)

    if backend == BACKEND_HTTP:
        raise RemoteError(
            "HTTP remotes are pull-only. Use a directory or git remote to push."
        )

    if backend == BACKEND_DIR:
        dest = PromptStore(Path(url).expanduser())
        return _apply_records(records, dest)

    # Git backend
    tmp = Path(tempfile.mkdtemp(prefix="promptdiff-remote-"))
    try:
        _run_git(["clone", "--depth", "1", url, str(tmp / "repo")])
        repo = tmp / "repo"
        dest = PromptStore(repo)
        results = _apply_records(records, dest)
        added_total = sum(r.added_versions for r in results)
        status = _run_git(["status", "--porcelain"], cwd=repo)
        if status.strip():
            _run_git(["add", "-A"], cwd=repo)
            _run_git(
                [
                    "-c",
                    "user.name=promptdiff",
                    "-c",
                    "user.email=promptdiff@localhost",
                    "commit",
                    "-m",
                    f"promptdiff push: {added_total} new version(s)",
                ],
                cwd=repo,
            )
            _run_git(["push", "origin", "HEAD"], cwd=repo)
        return results
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def pull(
    store: PromptStore, remote_name: str, prompts: list[str] | None = None
) -> list[SyncResult]:
    """Pull prompts from a remote registry into the local store."""
    url = _resolve_remote(store, remote_name)
    backend = detect_backend(url)

    if backend == BACKEND_DIR:
        src = PromptStore(Path(url).expanduser())
        if not src.initialized:
            raise RemoteError(f"'{url}' is not a promptdiff store.")
        records = _collect_records(src, prompts)
        return _apply_records(records, store)

    if backend == BACKEND_GIT:
        tmp = Path(tempfile.mkdtemp(prefix="promptdiff-remote-"))
        try:
            _run_git(["clone", "--depth", "1", url, str(tmp / "repo")])
            src = PromptStore(tmp / "repo")
            if not src.initialized:
                raise RemoteError(
                    f"Git remote '{url}' does not contain a promptdiff store at its root."
                )
            records = _collect_records(src, prompts)
            return _apply_records(records, store)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    # HTTP backend: expects a JSON export document
    body = _fetch_url(url)
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        # Fall back to JSONL
        data = []
        for line in body.strip().splitlines():
            line = line.strip()
            if line:
                try:
                    data.append(json.loads(line))
                except json.JSONDecodeError:
                    raise RemoteError(
                        f"Remote '{url}' did not return a valid promptdiff export."
                    )
    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list) or any(not isinstance(r, dict) or "name" not in r for r in data):
        raise RemoteError(f"Remote '{url}' did not return a valid promptdiff export.")
    if prompts:
        wanted = set(prompts)
        data = [r for r in data if r["name"] in wanted]
        missing = wanted - {r["name"] for r in data}
        if missing:
            raise RemoteError(
                f"Prompt(s) not found on remote: {', '.join(sorted(missing))}."
            )
    return _apply_records(data, store)
