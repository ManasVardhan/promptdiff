"""Text diff and semantic diff for prompts."""

from __future__ import annotations

import difflib
from dataclasses import dataclass, field


@dataclass
class DiffLine:
    """A single line in a diff output."""

    tag: str  # "equal", "insert", "delete", "replace"
    old_line: str | None = None
    new_line: str | None = None


@dataclass
class DiffResult:
    """Result of comparing two prompt versions."""

    old_version: int
    new_version: int
    lines: list[DiffLine] = field(default_factory=list)
    similarity_ratio: float = 0.0
    semantic_similarity: float | None = None
    stats: dict[str, int] = field(default_factory=dict)

    @property
    def has_changes(self) -> bool:
        return any(line.tag != "equal" for line in self.lines)


class PromptDiff:
    """Compute text diffs and semantic similarity between prompt versions."""

    def text_diff(
        self,
        old_text: str,
        new_text: str,
        old_version: int = 0,
        new_version: int = 0,
    ) -> DiffResult:
        """Compute a line-level text diff between two prompt strings.

        Uses ``difflib.SequenceMatcher`` to produce equal, insert, delete,
        and replace operations, then flattens replacements into separate
        delete + insert pairs for cleaner output.

        Args:
            old_text: The original prompt text.
            new_text: The updated prompt text.
            old_version: Version label for the original (used in the result).
            new_version: Version label for the update (used in the result).

        Returns:
            A ``DiffResult`` with per-line changes, similarity ratio, and stats.
        """
        old_lines = old_text.splitlines(keepends=True)
        new_lines = new_text.splitlines(keepends=True)

        matcher = difflib.SequenceMatcher(None, old_lines, new_lines)
        ratio = matcher.ratio()

        diff_lines: list[DiffLine] = []
        additions = 0
        deletions = 0
        modifications = 0

        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag == "equal":
                for line in old_lines[i1:i2]:
                    diff_lines.append(DiffLine(tag="equal", old_line=line, new_line=line))
            elif tag == "delete":
                for line in old_lines[i1:i2]:
                    diff_lines.append(DiffLine(tag="delete", old_line=line))
                    deletions += 1
            elif tag == "insert":
                for line in new_lines[j1:j2]:
                    diff_lines.append(DiffLine(tag="insert", new_line=line))
                    additions += 1
            elif tag == "replace":
                for line in old_lines[i1:i2]:
                    diff_lines.append(DiffLine(tag="delete", old_line=line))
                    deletions += 1
                for line in new_lines[j1:j2]:
                    diff_lines.append(DiffLine(tag="insert", new_line=line))
                    additions += 1
                modifications += 1

        return DiffResult(
            old_version=old_version,
            new_version=new_version,
            lines=diff_lines,
            similarity_ratio=ratio,
            stats={
                "additions": additions,
                "deletions": deletions,
                "modifications": modifications,
            },
        )

    def semantic_similarity(self, text_a: str, text_b: str) -> float:
        """Compute similarity via Jaccard word overlap (no external API needed).

        Splits both texts on whitespace, lowercases, and returns
        ``|intersection| / |union|``.  This catches surface-level wording
        changes but not deeper semantic shifts.

        For true semantic comparison, install ``promptdiff[embeddings]``
        and use :meth:`embedding_similarity`.
        """
        words_a = set(text_a.lower().split())
        words_b = set(text_b.lower().split())

        if not words_a and not words_b:
            return 1.0
        if not words_a or not words_b:
            return 0.0

        intersection = words_a & words_b
        union = words_a | words_b
        return len(intersection) / len(union)

    def embedding_similarity(
        self,
        text_a: str,
        text_b: str,
        model: str = "text-embedding-3-small",
    ) -> float:
        """Compute semantic similarity via OpenAI embedding cosine distance.

        Requires the ``embeddings`` extra: ``pip install llm-promptdiff[embeddings]``.

        Args:
            text_a: First text to compare.
            text_b: Second text to compare.
            model: OpenAI embedding model name.

        Returns:
            Cosine similarity in the range [-1, 1] (typically 0 to 1 for text).

        Raises:
            ImportError: If ``openai`` or ``numpy`` are not installed.
        """
        try:
            import numpy as np
            from openai import OpenAI
        except ImportError:
            raise ImportError(
                "Install with `pip install promptdiff[embeddings]` for embedding-based similarity"
            )

        client = OpenAI()
        resp = client.embeddings.create(input=[text_a, text_b], model=model)
        vec_a = np.array(resp.data[0].embedding)
        vec_b = np.array(resp.data[1].embedding)

        cosine = float(np.dot(vec_a, vec_b) / (np.linalg.norm(vec_a) * np.linalg.norm(vec_b)))
        return cosine

    def full_diff(
        self,
        old_text: str,
        new_text: str,
        old_version: int = 0,
        new_version: int = 0,
        use_embeddings: bool = False,
    ) -> DiffResult:
        """Compute a complete diff: line-level changes plus semantic similarity.

        Combines :meth:`text_diff` and :meth:`semantic_similarity` into a single
        ``DiffResult``.

        Args:
            old_text: The original prompt text.
            new_text: The updated prompt text.
            old_version: Version label for the original.
            new_version: Version label for the update.
            use_embeddings: Reserved for future use (embedding support).

        Returns:
            A ``DiffResult`` with lines, similarity_ratio, semantic_similarity, and stats.
        """
        result = self.text_diff(old_text, new_text, old_version, new_version)
        result.semantic_similarity = self.semantic_similarity(old_text, new_text)
        return result

    def unified_diff(self, old_text: str, new_text: str, old_label: str = "old", new_label: str = "new") -> str:
        """Return a unified diff string (the format used by ``diff -u``).

        Returns an empty string when the texts are identical.
        """
        return "".join(
            difflib.unified_diff(
                old_text.splitlines(keepends=True),
                new_text.splitlines(keepends=True),
                fromfile=old_label,
                tofile=new_label,
            )
        )
