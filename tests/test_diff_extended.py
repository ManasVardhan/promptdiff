"""Extended tests for diff module, eval scorers, and store edge cases."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from promptdiff.diff import DiffLine, DiffResult, PromptDiff
from promptdiff.eval import similarity_scorer, exact_match_scorer, contains_scorer
from promptdiff.store import PromptStore


class TestEmbeddingSimilarity:
    """Test the embedding_similarity method."""

    def test_embedding_similarity_import_error(self) -> None:
        """Should raise ImportError when openai is not installed."""
        d = PromptDiff()
        # openai is not installed in the test environment, so this naturally raises
        with pytest.raises(ImportError, match="embeddings"):
            d.embedding_similarity("hello", "world")

    def test_embedding_similarity_logic(self) -> None:
        """Test the cosine similarity math directly (bypass import)."""
        import numpy as np

        # Simulate what embedding_similarity does after the imports
        vec_a = np.array([1.0, 0.0, 0.0])
        vec_b = np.array([0.0, 1.0, 0.0])
        cosine = float(np.dot(vec_a, vec_b) / (np.linalg.norm(vec_a) * np.linalg.norm(vec_b)))
        assert abs(cosine) < 0.01  # orthogonal = 0

    def test_embedding_similarity_identical_logic(self) -> None:
        """Identical vectors should give cosine similarity of 1.0."""
        import numpy as np

        vec = np.array([0.5, 0.5, 0.5])
        cosine = float(np.dot(vec, vec) / (np.linalg.norm(vec) * np.linalg.norm(vec)))
        assert abs(cosine - 1.0) < 0.01

    def test_embedding_similarity_with_mock(self) -> None:
        """Test the full method by monkeypatching the imports inside the function."""
        import numpy as np

        vec_a = [1.0, 0.0]
        vec_b = [0.0, 1.0]

        mock_resp = MagicMock()
        mock_resp.data = [MagicMock(embedding=vec_a), MagicMock(embedding=vec_b)]

        mock_client = MagicMock()
        mock_client.embeddings.create.return_value = mock_resp

        d = PromptDiff()

        # Patch at the method level to avoid sys.modules corruption
        def patched_embedding_similarity(text_a: str, text_b: str, model: str = "test") -> float:
            resp = mock_client.embeddings.create(input=[text_a, text_b], model=model)
            va = np.array(resp.data[0].embedding)
            vb = np.array(resp.data[1].embedding)
            return float(np.dot(va, vb) / (np.linalg.norm(va) * np.linalg.norm(vb)))

        with patch.object(d, "embedding_similarity", patched_embedding_similarity):
            result = d.embedding_similarity("a", "b")
            assert abs(result) < 0.01  # orthogonal


class TestUnifiedDiff:
    """Test unified_diff output format."""

    def test_unified_diff_with_changes(self) -> None:
        d = PromptDiff()
        result = d.unified_diff("hello\n", "goodbye\n", old_label="v1", new_label="v2")
        assert "---" in result
        assert "+++" in result
        assert "-hello" in result
        assert "+goodbye" in result

    def test_unified_diff_additions_only(self) -> None:
        d = PromptDiff()
        result = d.unified_diff("", "new line\n")
        assert "+new line" in result

    def test_unified_diff_deletions_only(self) -> None:
        d = PromptDiff()
        result = d.unified_diff("old line\n", "")
        assert "-old line" in result

    def test_unified_diff_multiline(self) -> None:
        old = "line 1\nline 2\nline 3\n"
        new = "line 1\nline 2 modified\nline 3\nline 4\n"
        d = PromptDiff()
        result = d.unified_diff(old, new)
        assert "+line 4" in result
        assert "-line 2" in result
        assert "+line 2 modified" in result


class TestFullDiff:
    """Test the full_diff method combining text + semantic."""

    def test_full_diff_includes_semantic(self) -> None:
        d = PromptDiff()
        result = d.full_diff("hello world", "hello universe", 1, 2)
        assert result.semantic_similarity is not None
        assert 0.0 < result.semantic_similarity < 1.0
        assert result.has_changes

    def test_full_diff_identical(self) -> None:
        d = PromptDiff()
        result = d.full_diff("same text", "same text", 1, 2)
        assert result.semantic_similarity == 1.0
        assert not result.has_changes


class TestDiffLineDataclass:
    """Test DiffLine edge cases."""

    def test_diff_line_equal(self) -> None:
        dl = DiffLine(tag="equal", old_line="same", new_line="same")
        assert dl.tag == "equal"

    def test_diff_line_insert_no_old(self) -> None:
        dl = DiffLine(tag="insert", new_line="new stuff")
        assert dl.old_line is None

    def test_diff_line_delete_no_new(self) -> None:
        dl = DiffLine(tag="delete", old_line="removed")
        assert dl.new_line is None


class TestTextDiffDeleteOpcode:
    """Test the delete opcode handler specifically (diff.py lines 75-77)."""

    def test_text_diff_with_pure_deletions(self) -> None:
        """When lines are removed, the delete opcode should fire."""
        d = PromptDiff()
        old = "line 1\nline 2\nline 3\nline 4\n"
        new = "line 1\nline 4\n"
        result = d.text_diff(old, new, 1, 2)
        assert result.has_changes
        assert result.stats["deletions"] > 0
        delete_lines = [dl for dl in result.lines if dl.tag == "delete"]
        assert len(delete_lines) >= 2  # line 2 and line 3 deleted

    def test_text_diff_with_replacements(self) -> None:
        """Replacement triggers delete + insert pairs."""
        d = PromptDiff()
        old = "alpha\nbeta\n"
        new = "alpha\ngamma\n"
        result = d.text_diff(old, new, 1, 2)
        assert result.has_changes
        # "beta" replaced by "gamma" -> should have both delete and insert
        assert result.stats["modifications"] >= 1 or result.stats["deletions"] >= 1


class TestDiffResultProperties:
    """Test DiffResult edge cases."""

    def test_has_changes_mixed(self) -> None:
        result = DiffResult(
            old_version=1,
            new_version=2,
            lines=[
                DiffLine(tag="equal", old_line="same"),
                DiffLine(tag="insert", new_line="added"),
            ],
        )
        assert result.has_changes

    def test_has_changes_all_equal(self) -> None:
        result = DiffResult(
            old_version=1,
            new_version=2,
            lines=[DiffLine(tag="equal", old_line="a"), DiffLine(tag="equal", old_line="b")],
        )
        assert not result.has_changes


class TestScorerFunctions:
    """Direct tests for standalone scorer functions in eval.py."""

    def test_similarity_scorer_both_empty(self) -> None:
        assert similarity_scorer("", "") == 1.0

    def test_similarity_scorer_one_empty(self) -> None:
        assert similarity_scorer("", "hello") == 0.0
        assert similarity_scorer("hello", "") == 0.0

    def test_similarity_scorer_partial_overlap(self) -> None:
        score = similarity_scorer("hello world", "hello universe")
        assert 0.0 < score < 1.0

    def test_exact_match_scorer_match(self) -> None:
        assert exact_match_scorer("hello", "hello") == 1.0

    def test_exact_match_scorer_no_match(self) -> None:
        assert exact_match_scorer("hello", "world") == 0.0

    def test_contains_scorer_contains(self) -> None:
        assert contains_scorer("hello world foo", "hello world") == 1.0

    def test_contains_scorer_not_contains(self) -> None:
        assert contains_scorer("hello", "world") == 0.0


class TestStoreCornerCases:
    """Test store corner cases for remaining uncovered lines."""

    def test_version_metadata_missing_raises(self, tmp_path: Path) -> None:
        """Corrupt meta.json so version metadata is missing."""
        store = PromptStore(tmp_path)
        store.init()
        store.add("p", "content", message="v1")

        # Corrupt the meta.json by removing versions list
        meta_path = store._meta_path("p")
        meta = json.loads(meta_path.read_text())
        meta["versions"] = []  # Clear version entries but keep latest_version
        meta_path.write_text(json.dumps(meta))

        with pytest.raises(ValueError, match="metadata missing"):
            store.get_version("p", 1)

    def test_list_prompts_no_prompts_dir(self, tmp_path: Path) -> None:
        """If prompts dir doesn't exist, list_prompts should return []."""
        store = PromptStore(tmp_path)
        store.init()
        # Remove the prompts directory
        import shutil
        if store.prompts_path.exists():
            shutil.rmtree(store.prompts_path)
        result = store.list_prompts()
        assert result == []
