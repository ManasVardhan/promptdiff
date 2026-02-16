"""File-based prompt version store. Each prompt gets a directory with numbered versions."""

from __future__ import annotations

import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

STORE_DIR = ".promptdiff"
PROMPTS_DIR = "prompts"
META_FILE = "promptdiff.json"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:12]


class VersionInfo:
    """Metadata for a single prompt version."""

    def __init__(
        self,
        version: int,
        content: str,
        message: str = "",
        timestamp: str = "",
        content_hash: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.version = version
        self.content = content
        self.message = message
        self.timestamp = timestamp or _now_iso()
        self.content_hash = content_hash or _content_hash(content)
        self.metadata = metadata or {}

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "message": self.message,
            "timestamp": self.timestamp,
            "content_hash": self.content_hash,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any], content: str = "") -> VersionInfo:
        return cls(
            version=data["version"],
            content=content,
            message=data.get("message", ""),
            timestamp=data.get("timestamp", ""),
            content_hash=data.get("content_hash", ""),
            metadata=data.get("metadata", {}),
        )


class PromptStore:
    """File-based prompt version store.

    Layout:
        .promptdiff/
            promptdiff.json          # global metadata
            prompts/
                my-prompt/
                    meta.json        # prompt metadata + version list
                    v1.txt           # version 1 content
                    v2.txt           # version 2 content
    """

    def __init__(self, root: str | Path = ".") -> None:
        self.root = Path(root).resolve()
        self.store_path = self.root / STORE_DIR
        self.prompts_path = self.store_path / PROMPTS_DIR

    @property
    def initialized(self) -> bool:
        return (self.store_path / META_FILE).exists()

    def init(self) -> Path:
        """Initialize a new promptdiff store."""
        self.store_path.mkdir(parents=True, exist_ok=True)
        self.prompts_path.mkdir(exist_ok=True)
        meta_path = self.store_path / META_FILE
        if not meta_path.exists():
            meta_path.write_text(
                json.dumps({"created": _now_iso(), "version": "0.1.0"}, indent=2)
            )
        return self.store_path

    def _prompt_dir(self, name: str) -> Path:
        return self.prompts_path / name

    def _meta_path(self, name: str) -> Path:
        return self._prompt_dir(name) / "meta.json"

    def _version_path(self, name: str, version: int) -> Path:
        return self._prompt_dir(name) / f"v{version}.txt"

    def _read_meta(self, name: str) -> dict[str, Any]:
        meta_path = self._meta_path(name)
        if not meta_path.exists():
            raise FileNotFoundError(f"Prompt '{name}' not found")
        return json.loads(meta_path.read_text())

    def _write_meta(self, name: str, meta: dict[str, Any]) -> None:
        self._meta_path(name).write_text(json.dumps(meta, indent=2))

    def add(
        self,
        name: str,
        content: str,
        message: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> VersionInfo:
        """Add a new version of a prompt. Creates the prompt if it doesn't exist."""
        self._ensure_init()
        prompt_dir = self._prompt_dir(name)
        prompt_dir.mkdir(parents=True, exist_ok=True)

        meta_path = self._meta_path(name)
        if meta_path.exists():
            meta = json.loads(meta_path.read_text())
            # Check for duplicate content
            latest_v = meta["latest_version"]
            latest_content = self._version_path(name, latest_v).read_text()
            if latest_content == content:
                return self.get_version(name, latest_v)
            next_version = latest_v + 1
        else:
            meta = {"name": name, "created": _now_iso(), "tags": [], "versions": []}
            next_version = 1

        info = VersionInfo(
            version=next_version,
            content=content,
            message=message,
            metadata=metadata,
        )

        # Write content
        self._version_path(name, next_version).write_text(content)

        # Update metadata
        meta["latest_version"] = next_version
        meta["versions"].append(info.to_dict())
        self._write_meta(name, meta)

        return info

    def get_version(self, name: str, version: int | None = None) -> VersionInfo:
        """Get a specific version (or latest) of a prompt."""
        self._ensure_init()
        meta = self._read_meta(name)

        if version is None:
            version = meta["latest_version"]

        version_path = self._version_path(name, version)
        if not version_path.exists():
            raise FileNotFoundError(f"Version {version} of '{name}' not found")

        content = version_path.read_text()
        version_data = next((v for v in meta["versions"] if v["version"] == version), None)
        if version_data is None:
            raise ValueError(f"Version {version} metadata missing for '{name}'")

        return VersionInfo.from_dict(version_data, content=content)

    def list_versions(self, name: str) -> list[VersionInfo]:
        """List all versions of a prompt."""
        self._ensure_init()
        meta = self._read_meta(name)
        results = []
        for v_data in meta["versions"]:
            content = self._version_path(name, v_data["version"]).read_text()
            results.append(VersionInfo.from_dict(v_data, content=content))
        return results

    def list_prompts(self) -> list[str]:
        """List all prompt names in the store."""
        self._ensure_init()
        if not self.prompts_path.exists():
            return []
        return sorted(
            d.name for d in self.prompts_path.iterdir() if d.is_dir() and (d / "meta.json").exists()
        )

    def delete_prompt(self, name: str) -> None:
        """Delete a prompt and all its versions."""
        self._ensure_init()
        prompt_dir = self._prompt_dir(name)
        if not prompt_dir.exists():
            raise FileNotFoundError(f"Prompt '{name}' not found")
        shutil.rmtree(prompt_dir)

    def _ensure_init(self) -> None:
        if not self.initialized:
            raise RuntimeError(
                "Not a promptdiff repository. Run 'promptdiff init' first."
            )
