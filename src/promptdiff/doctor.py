"""Store-wide integrity sweep: the engine behind `promptdiff doctor`.

The doctor walks every corner of a promptdiff store and reports problems
in one pass: corrupted or inconsistent prompt metadata, version files
that exist on disk but are not registered (or vice versa), tracked
source files that vanished, pins pointing at missing or edited versions,
releases whose checksums no longer match the store, and directory
remotes whose paths are gone.

Issues are classified by severity ("error" or "warning") and a subset is
safely repairable: `run_doctor(store, fix=True)` recovers orphaned
version files into prompt metadata, repairs a stale latest_version
pointer, and removes empty orphaned prompt directories. Repairs never
delete prompt content and never rewrite checksums, so tampering is
always surfaced, never papered over.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from promptdiff.pins import (
    STATUS_DRIFTED,
    STATUS_MISSING,
    STATUS_MODIFIED,
    PinError,
    PinManager,
)
from promptdiff.releases import ReleaseManager, release_checksum
from promptdiff.remote import BACKEND_DIR, detect_backend, load_remotes
from promptdiff.store import PromptStore, _content_hash
from promptdiff.tracking import FileTracker

SEVERITY_ERROR = "error"
SEVERITY_WARNING = "warning"


@dataclass
class Issue:
    """One problem found during the integrity sweep.

    Attributes:
        category: Stable machine-readable identifier for the problem type.
        severity: "error" (integrity broken) or "warning" (needs attention).
        subject: The prompt, pin, release, remote, or path concerned.
        detail: Human-readable description of the problem.
        fixable: True if `--fix` can repair this issue safely.
        fixed: True if this run repaired the issue.
    """

    category: str
    severity: str
    subject: str
    detail: str
    fixable: bool = False
    fixed: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "category": self.category,
            "severity": self.severity,
            "subject": self.subject,
            "detail": self.detail,
            "fixable": self.fixable,
            "fixed": self.fixed,
        }


@dataclass
class DoctorReport:
    """Aggregated outcome of a doctor run."""

    issues: list[Issue] = field(default_factory=list)
    prompts_checked: int = 0
    pins_checked: int = 0
    releases_checked: int = 0
    remotes_checked: int = 0
    tracked_checked: int = 0

    @property
    def ok(self) -> bool:
        """True when no unrepaired issues remain."""
        return not self.outstanding

    @property
    def outstanding(self) -> list[Issue]:
        """Issues that were found and not fixed during this run."""
        return [i for i in self.issues if not i.fixed]

    @property
    def fixed(self) -> list[Issue]:
        """Issues that were repaired during this run."""
        return [i for i in self.issues if i.fixed]

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "checked": {
                "prompts": self.prompts_checked,
                "pins": self.pins_checked,
                "releases": self.releases_checked,
                "remotes": self.remotes_checked,
                "tracked": self.tracked_checked,
            },
            "issues": [i.to_dict() for i in self.issues],
            "fixed_count": len(self.fixed),
            "outstanding_count": len(self.outstanding),
        }


def _check_prompt(store: PromptStore, name: str, fix: bool, report: DoctorReport) -> None:
    """Check one prompt directory: metadata, version files, checksums."""
    prompt_dir = store.prompts_path / name
    meta_path = prompt_dir / "meta.json"
    try:
        meta = json.loads(meta_path.read_text())
    except json.JSONDecodeError as exc:
        report.issues.append(
            Issue(
                category="corrupt_meta",
                severity=SEVERITY_ERROR,
                subject=name,
                detail=f"meta.json is not valid JSON: {exc}",
            )
        )
        return

    versions = meta.get("versions", [])
    known = {v["version"] for v in versions if isinstance(v.get("version"), int)}
    meta_dirty = False

    # Version files on disk that meta.json does not know about.
    on_disk: set[int] = set()
    for path in sorted(prompt_dir.glob("v*.txt")):
        stem = path.stem[1:]
        if not stem.isdigit():
            continue
        number = int(stem)
        on_disk.add(number)
        if number in known:
            continue
        issue = Issue(
            category="orphan_version_file",
            severity=SEVERITY_ERROR,
            subject=name,
            detail=(
                f"{path.name} exists on disk but is not registered in "
                f"meta.json"
            ),
            fixable=True,
        )
        if fix:
            content = path.read_text()
            versions.append(
                {
                    "version": number,
                    "message": "Recovered by promptdiff doctor",
                    "timestamp": "",
                    "content_hash": _content_hash(content),
                    "metadata": {},
                }
            )
            known.add(number)
            meta_dirty = True
            issue.fixed = True
        report.issues.append(issue)

    # Meta entries whose content file is gone, or whose content was edited.
    for v_data in versions:
        number = v_data.get("version")
        if not isinstance(number, int):
            continue
        v_path = prompt_dir / f"v{number}.txt"
        if not v_path.exists():
            report.issues.append(
                Issue(
                    category="missing_version_file",
                    severity=SEVERITY_ERROR,
                    subject=name,
                    detail=(
                        f"meta.json lists v{number} but v{number}.txt is "
                        f"missing from the store"
                    ),
                )
            )
            continue
        recorded = v_data.get("content_hash", "")
        if recorded and _content_hash(v_path.read_text()) != recorded:
            report.issues.append(
                Issue(
                    category="version_hash_mismatch",
                    severity=SEVERITY_ERROR,
                    subject=name,
                    detail=(
                        f"v{number}.txt content does not match the hash "
                        f"recorded in meta.json (the stored version was "
                        f"edited in place)"
                    ),
                )
            )

    # Stale latest_version pointer.
    if versions:
        versions.sort(key=lambda v: v.get("version", 0))
        expected_latest = max(known) if known else None
        if expected_latest is not None and meta.get("latest_version") != expected_latest:
            issue = Issue(
                category="latest_mismatch",
                severity=SEVERITY_ERROR,
                subject=name,
                detail=(
                    f"latest_version is {meta.get('latest_version')} but the "
                    f"highest registered version is {expected_latest}"
                ),
                fixable=True,
            )
            if fix:
                meta["latest_version"] = expected_latest
                meta_dirty = True
                issue.fixed = True
            report.issues.append(issue)

    if meta_dirty:
        meta["versions"] = versions
        meta_path.write_text(json.dumps(meta, indent=2))


def _check_orphan_dirs(store: PromptStore, fix: bool, report: DoctorReport) -> None:
    """Flag prompt directories that have no meta.json."""
    if not store.prompts_path.exists():
        return
    for path in sorted(store.prompts_path.iterdir()):
        if not path.is_dir() or (path / "meta.json").exists():
            continue
        empty = not any(path.iterdir())
        issue = Issue(
            category="orphan_dir",
            severity=SEVERITY_ERROR,
            subject=path.name,
            detail=(
                "prompt directory has no meta.json"
                + ("" if empty else " and still contains files")
            ),
            fixable=empty,
        )
        if fix and empty:
            path.rmdir()
            issue.fixed = True
        report.issues.append(issue)


def _check_tracked(store: PromptStore, report: DoctorReport) -> None:
    """Flag tracked source files that vanished or never made it into the store."""
    tracker = FileTracker(store)
    tracked = tracker.list_tracked()
    report.tracked_checked = len(tracked)
    prompts = set(store.list_prompts())
    for name, path_str in sorted(tracked.items()):
        if not tracker._resolve(path_str).exists():
            report.issues.append(
                Issue(
                    category="tracked_file_missing",
                    severity=SEVERITY_WARNING,
                    subject=name,
                    detail=(
                        f"tracked source file '{path_str}' no longer exists "
                        f"(untrack it with: promptdiff untrack {name})"
                    ),
                )
            )
        if name not in prompts:
            report.issues.append(
                Issue(
                    category="tracked_prompt_missing",
                    severity=SEVERITY_WARNING,
                    subject=name,
                    detail=(
                        f"'{name}' is tracked but has no versions in the "
                        f"store (run: promptdiff sync)"
                    ),
                )
            )


def _check_pins(store: PromptStore, lockfile: str | None, report: DoctorReport) -> None:
    """Check the lockfile, if any, for missing, modified, or drifted pins."""
    manager = PinManager(store, lock_path=lockfile)
    if not manager.lock_path.exists():
        return
    try:
        results = manager.check()
    except PinError as exc:
        # An empty lockfile is fine for the doctor; a corrupt one is not.
        if "No pins found" in str(exc):
            return
        report.issues.append(
            Issue(
                category="corrupt_lockfile",
                severity=SEVERITY_ERROR,
                subject=str(manager.lock_path),
                detail=str(exc),
            )
        )
        return
    report.pins_checked = len(results)
    severity = {
        STATUS_MISSING: SEVERITY_ERROR,
        STATUS_MODIFIED: SEVERITY_ERROR,
        STATUS_DRIFTED: SEVERITY_WARNING,
    }
    for result in results:
        if result.ok:
            continue
        detail = result.problems[0] if result.problems else result.status
        report.issues.append(
            Issue(
                category=f"pin_{result.status}",
                severity=severity.get(result.status, SEVERITY_ERROR),
                subject=result.pin.prompt,
                detail=detail,
            )
        )


def _check_releases(store: PromptStore, report: DoctorReport) -> None:
    """Verify every release checksum against current store content."""
    manager = ReleaseManager(store)
    releases = manager.list_releases()
    report.releases_checked = len(releases)
    for release in releases:
        try:
            info = store.get_version(release.prompt, release.version)
        except (FileNotFoundError, ValueError) as exc:
            report.issues.append(
                Issue(
                    category="release_missing",
                    severity=SEVERITY_ERROR,
                    subject=release.name,
                    detail=f"release points at a missing version: {exc}",
                )
            )
            continue
        if release_checksum(info.content) != release.checksum:
            report.issues.append(
                Issue(
                    category="release_mismatch",
                    severity=SEVERITY_ERROR,
                    subject=release.name,
                    detail=(
                        f"{release.prompt} v{release.version} no longer "
                        f"matches the release checksum (the stored version "
                        f"was modified after the release was created)"
                    ),
                )
            )


def _check_remotes(store: PromptStore, report: DoctorReport) -> None:
    """Flag directory remotes whose path no longer exists."""
    remotes = load_remotes(store)
    report.remotes_checked = len(remotes)
    for name, url in sorted(remotes.items()):
        if detect_backend(url) != BACKEND_DIR:
            continue
        candidate = Path(url).expanduser()
        if not candidate.is_absolute():
            candidate = store.root / candidate
        if not candidate.exists():
            report.issues.append(
                Issue(
                    category="stale_remote",
                    severity=SEVERITY_WARNING,
                    subject=name,
                    detail=(
                        f"directory remote '{name}' points at '{url}', which "
                        f"does not exist (remove it with: promptdiff remote "
                        f"rm {name})"
                    ),
                )
            )


def run_doctor(
    store: PromptStore,
    fix: bool = False,
    lockfile: str | None = None,
) -> DoctorReport:
    """Run the full integrity sweep and return a :class:`DoctorReport`.

    Args:
        store: The prompt store to inspect (must be initialized).
        fix: Apply safe repairs: recover orphaned version files into
            meta.json, correct stale latest_version pointers, and remove
            empty orphaned prompt directories. Repairs never delete
            content and never rewrite recorded checksums.
        lockfile: Alternate promptdiff.lock path (default: store root).

    Raises:
        RuntimeError: If the store is not initialized.
    """
    store._ensure_init()
    report = DoctorReport()

    prompts = store.list_prompts()
    report.prompts_checked = len(prompts)
    for name in prompts:
        _check_prompt(store, name, fix, report)
    _check_orphan_dirs(store, fix, report)
    _check_tracked(store, report)
    _check_pins(store, lockfile, report)
    _check_releases(store, report)
    _check_remotes(store, report)
    return report
