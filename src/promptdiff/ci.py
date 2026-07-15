"""CI/CD reporting: summarize prompt changes since a point in time.

Built for pull request workflows: run ``promptdiff ci-report --since <ref
date>`` in CI, post the markdown output as a PR comment or step summary,
and optionally gate the build with ``--fail-below`` when a prompt changed
more than expected.
"""

from __future__ import annotations

import difflib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from promptdiff.diff import PromptDiff
from promptdiff.store import PromptStore, VersionInfo


@dataclass
class PromptChange:
    """A prompt whose latest version changed after the reference point."""

    name: str
    base_version: int | None
    head_version: int
    similarity: float | None
    additions: int
    deletions: int
    messages: list[str]

    @property
    def is_new(self) -> bool:
        """True when the prompt did not exist at the reference point."""
        return self.base_version is None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "base_version": self.base_version,
            "head_version": self.head_version,
            "similarity": self.similarity,
            "additions": self.additions,
            "deletions": self.deletions,
            "messages": self.messages,
            "is_new": self.is_new,
        }


def parse_since(value: str) -> datetime:
    """Parse an ISO 8601 date or datetime string into an aware UTC datetime.

    Plain dates (``2026-07-01``) mean midnight UTC that day. Naive
    datetimes are assumed to be UTC.

    Raises:
        ValueError: If the string is not a valid ISO date or datetime.
    """
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _version_time(version: VersionInfo) -> datetime | None:
    """Return the version timestamp as an aware UTC datetime, or None."""
    if not version.timestamp:
        return None
    try:
        parsed = datetime.fromisoformat(version.timestamp)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def collect_changes(store: PromptStore, since: datetime) -> list[PromptChange]:
    """Collect per-prompt changes made after *since*.

    For each prompt with versions newer than *since*, the change compares
    the last version at or before *since* (the base) against the latest
    version (the head). Prompts created after *since* have no base and
    are reported as new. Versions with missing or unparseable timestamps
    are treated as existing before *since*.

    Similarity is a character-level ratio (0 to 1) between base and head
    content, so small in-line edits score high even when every line was
    touched. Line additions and deletions come from the line diff.
    """
    differ = PromptDiff()
    changes: list[PromptChange] = []

    for name in store.list_prompts():
        versions = store.list_versions(name)
        if not versions:
            continue

        base: VersionInfo | None = None
        new_versions: list[VersionInfo] = []
        for version in versions:
            timestamp = _version_time(version)
            if timestamp is None or timestamp <= since:
                base = version
            else:
                new_versions.append(version)

        if not new_versions:
            continue

        head = new_versions[-1]
        if base is None:
            similarity = None
            diff = differ.text_diff("", head.content, 0, head.version)
        else:
            diff = differ.text_diff(base.content, head.content, base.version, head.version)
            similarity = difflib.SequenceMatcher(None, base.content, head.content).ratio()

        changes.append(
            PromptChange(
                name=name,
                base_version=base.version if base else None,
                head_version=head.version,
                similarity=similarity,
                additions=diff.stats.get("additions", 0),
                deletions=diff.stats.get("deletions", 0),
                messages=[v.message for v in new_versions if v.message],
            )
        )

    return changes


def failing_changes(changes: list[PromptChange], min_similarity: float) -> list[PromptChange]:
    """Return changes whose similarity fell below *min_similarity*.

    New prompts have no base to compare against and never fail the gate.
    """
    return [
        c for c in changes if c.similarity is not None and c.similarity < min_similarity
    ]


def render_markdown(changes: list[PromptChange], since: datetime) -> str:
    """Render changes as a markdown report suitable for a PR comment."""
    since_label = since.strftime("%Y-%m-%d %H:%M UTC")
    lines = [f"## Prompt changes since {since_label}", ""]

    if not changes:
        lines.append("No prompt changes detected.")
        lines.append("")
        return "\n".join(lines)

    new_count = sum(1 for c in changes if c.is_new)
    updated_count = len(changes) - new_count
    summary_parts = []
    if updated_count:
        summary_parts.append(f"{updated_count} updated")
    if new_count:
        summary_parts.append(f"{new_count} new")
    lines.append(f"**{len(changes)} prompt(s) changed** ({', '.join(summary_parts)})")
    lines.append("")
    lines.append("| Prompt | Versions | Similarity | Lines |")
    lines.append("|--------|----------|------------|-------|")

    for change in changes:
        if change.is_new:
            versions = f"new -> v{change.head_version}"
            similarity = "n/a"
        else:
            versions = f"v{change.base_version} -> v{change.head_version}"
            similarity = f"{change.similarity:.1%}"
        lines.append(
            f"| {change.name} | {versions} | {similarity} "
            f"| +{change.additions} / -{change.deletions} |"
        )

    lines.append("")
    for change in changes:
        if change.messages:
            lines.append(f"### {change.name}")
            for message in change.messages:
                lines.append(f"- {message}")
            lines.append("")

    return "\n".join(lines)


def render_json(changes: list[PromptChange], since: datetime) -> str:
    """Render changes as a JSON document for machine consumption."""
    doc = {
        "since": since.isoformat(),
        "total_changes": len(changes),
        "changes": [c.to_dict() for c in changes],
    }
    return json.dumps(doc, indent=2)
