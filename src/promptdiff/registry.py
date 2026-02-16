"""Prompt registry for managing named prompts with metadata and tags."""

from __future__ import annotations

from typing import Any

from promptdiff.store import PromptStore


class PromptRegistry:
    """High-level registry for managing prompts with tags and metadata."""

    def __init__(self, store: PromptStore) -> None:
        self.store = store

    def register(
        self,
        name: str,
        content: str,
        message: str = "",
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> int:
        """Register a new prompt version. Returns the version number."""
        info = self.store.add(name, content, message=message, metadata=metadata)
        if tags:
            self.set_tags(name, tags)
        return info.version

    def get(self, name: str, version: int | None = None) -> str:
        """Get prompt content by name and optional version."""
        return self.store.get_version(name, version).content

    def set_tags(self, name: str, tags: list[str]) -> None:
        """Set tags for a prompt."""
        meta = self.store._read_meta(name)
        meta["tags"] = sorted(set(tags))
        self.store._write_meta(name, meta)

    def get_tags(self, name: str) -> list[str]:
        """Get tags for a prompt."""
        meta = self.store._read_meta(name)
        return meta.get("tags", [])

    def add_tags(self, name: str, tags: list[str]) -> None:
        """Add tags to existing tags."""
        existing = self.get_tags(name)
        self.set_tags(name, existing + tags)

    def find_by_tag(self, tag: str) -> list[str]:
        """Find all prompts with a given tag."""
        results = []
        for name in self.store.list_prompts():
            if tag in self.get_tags(name):
                results.append(name)
        return results

    def list_all(self) -> list[dict[str, Any]]:
        """List all prompts with summary info."""
        results = []
        for name in self.store.list_prompts():
            meta = self.store._read_meta(name)
            results.append({
                "name": name,
                "latest_version": meta.get("latest_version", 0),
                "tags": meta.get("tags", []),
                "total_versions": len(meta.get("versions", [])),
            })
        return results
