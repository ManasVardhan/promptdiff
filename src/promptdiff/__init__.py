"""promptdiff - Git-style diff and version control for LLM prompts."""

__version__ = "0.1.1"

from promptdiff.store import PromptStore
from promptdiff.diff import PromptDiff
from promptdiff.registry import PromptRegistry
from promptdiff.changelog import ChangelogGenerator
from promptdiff.eval import PromptTestCase
from promptdiff.tracking import FileTracker, SyncResult

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
    "__version__",
]
