"""promptdiff - Git-style diff and version control for LLM prompts."""

__version__ = "0.2.0"

from promptdiff.store import PromptStore
from promptdiff.diff import PromptDiff
from promptdiff.registry import PromptRegistry
from promptdiff.changelog import ChangelogGenerator
from promptdiff.eval import PromptTestCase
from promptdiff.tracking import FileTracker, SyncResult
from promptdiff.ci import PromptChange, collect_changes, render_markdown
from promptdiff.releases import (
    AuditEntry,
    Release,
    ReleaseError,
    ReleaseManager,
    VerifyResult,
    release_checksum,
)
from promptdiff.semantic import SemanticComparison, compare_semantic, local_similarity

# Backward-compatible alias
TestCase = PromptTestCase

__all__ = [
    "PromptStore",
    "PromptDiff",
    "PromptRegistry",
    "ChangelogGenerator",
    "PromptTestCase",
    "TestCase",
    "FileTracker",
    "SyncResult",
    "PromptChange",
    "AuditEntry",
    "Release",
    "ReleaseError",
    "ReleaseManager",
    "VerifyResult",
    "release_checksum",
    "collect_changes",
    "render_markdown",
    "SemanticComparison",
    "compare_semantic",
    "local_similarity",
    "__version__",
]
