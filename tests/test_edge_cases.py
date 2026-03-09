"""Edge case and robustness tests for promptdiff."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from promptdiff.store import PromptStore, VersionInfo
from promptdiff.diff import PromptDiff, DiffResult
from promptdiff.registry import PromptRegistry
from promptdiff.eval import (
    PromptEvaluator,
    PromptTestCase,
    TestCase,
    exact_match_scorer,
    contains_scorer,
    similarity_scorer,
    EvalResult,
)
from promptdiff.changelog import ChangelogGenerator


@pytest.fixture
def store(tmp_path: Path) -> PromptStore:
    s = PromptStore(tmp_path)
    s.init()
    return s


class TestStoreEdgeCases:
    def test_get_nonexistent_prompt_raises(self, store: PromptStore) -> None:
        with pytest.raises(FileNotFoundError, match="not found"):
            store.get_version("nonexistent")

    def test_get_nonexistent_version_raises(self, store: PromptStore) -> None:
        store.add("p", "content")
        with pytest.raises(FileNotFoundError, match="Version 99"):
            store.get_version("p", 99)

    def test_delete_nonexistent_raises(self, store: PromptStore) -> None:
        with pytest.raises(FileNotFoundError, match="not found"):
            store.delete_prompt("ghost")

    def test_add_empty_string(self, store: PromptStore) -> None:
        # Empty string is technically valid at the store level
        info = store.add("empty", "")
        assert info.version == 1
        assert info.content == ""

    def test_multiline_content(self, store: PromptStore) -> None:
        content = "Line 1\nLine 2\nLine 3\n\nLine 5"
        store.add("multi", content)
        retrieved = store.get_version("multi", 1)
        assert retrieved.content == content

    def test_special_characters_in_content(self, store: PromptStore) -> None:
        content = "Use {var} and {{escaped}} and 'quotes' and \"double\""
        store.add("special", content)
        assert store.get_version("special", 1).content == content

    def test_unicode_content(self, store: PromptStore) -> None:
        content = "Hello! Summarize in Japanese. Output: summarize."
        store.add("unicode", content)
        assert store.get_version("unicode", 1).content == content

    def test_content_hash_consistency(self, store: PromptStore) -> None:
        info1 = store.add("p1", "exact content")
        info2 = store.add("p2", "exact content")
        assert info1.content_hash == info2.content_hash

    def test_init_idempotent(self, tmp_path: Path) -> None:
        store = PromptStore(tmp_path)
        store.init()
        store.init()  # Should not error
        assert store.initialized

    def test_list_prompts_empty(self, store: PromptStore) -> None:
        assert store.list_prompts() == []

    def test_version_info_to_dict(self) -> None:
        info = VersionInfo(version=1, content="test", message="first", metadata={"key": "val"})
        d = info.to_dict()
        assert d["version"] == 1
        assert d["message"] == "first"
        assert d["metadata"] == {"key": "val"}

    def test_version_info_from_dict(self) -> None:
        d = {"version": 2, "message": "second", "timestamp": "2026-01-01T00:00:00", "content_hash": "abc123"}
        info = VersionInfo.from_dict(d, content="hello")
        assert info.version == 2
        assert info.content == "hello"


class TestDiffEdgeCases:
    def test_empty_strings(self) -> None:
        d = PromptDiff()
        result = d.text_diff("", "")
        assert result.similarity_ratio == 1.0
        assert not result.has_changes

    def test_one_empty_one_full(self) -> None:
        d = PromptDiff()
        result = d.text_diff("", "hello\nworld")
        assert result.has_changes
        assert result.stats["additions"] == 2

    def test_identical_multiline(self) -> None:
        d = PromptDiff()
        text = "line 1\nline 2\nline 3"
        result = d.text_diff(text, text)
        assert not result.has_changes
        assert result.similarity_ratio == 1.0

    def test_semantic_similarity_empty(self) -> None:
        d = PromptDiff()
        assert d.semantic_similarity("", "") == 1.0

    def test_semantic_similarity_one_empty(self) -> None:
        d = PromptDiff()
        assert d.semantic_similarity("", "hello") == 0.0
        assert d.semantic_similarity("hello", "") == 0.0

    def test_semantic_similarity_identical(self) -> None:
        d = PromptDiff()
        assert d.semantic_similarity("hello world", "hello world") == 1.0

    def test_unified_diff_no_changes(self) -> None:
        d = PromptDiff()
        result = d.unified_diff("same\n", "same\n")
        assert result == ""

    def test_diff_result_properties(self) -> None:
        result = DiffResult(old_version=1, new_version=2)
        assert not result.has_changes
        assert result.similarity_ratio == 0.0


class TestRegistryEdgeCases:
    def test_add_tags_accumulates(self, store: PromptStore) -> None:
        registry = PromptRegistry(store)
        registry.register("p", "content", tags=["a"])
        registry.add_tags("p", ["b", "c"])
        tags = registry.get_tags("p")
        assert "a" in tags
        assert "b" in tags
        assert "c" in tags

    def test_find_by_tag_empty(self, store: PromptStore) -> None:
        registry = PromptRegistry(store)
        assert registry.find_by_tag("nonexistent") == []

    def test_list_all_empty(self, store: PromptStore) -> None:
        registry = PromptRegistry(store)
        assert registry.list_all() == []

    def test_set_tags_deduplicates(self, store: PromptStore) -> None:
        registry = PromptRegistry(store)
        registry.register("p", "content")
        registry.set_tags("p", ["a", "b", "a", "b"])
        tags = registry.get_tags("p")
        assert tags == ["a", "b"]


class TestEvalEdgeCases:
    def test_testcase_backward_compat(self) -> None:
        """TestCase alias should work like PromptTestCase."""
        tc = TestCase("test", {}, "expected")
        ptc = PromptTestCase("test", {}, "expected")
        assert tc.name == ptc.name

    def test_contains_scorer(self) -> None:
        assert contains_scorer("hello world foo bar", "hello world") == 1.0
        assert contains_scorer("hello", "world") == 0.0

    def test_exact_match_with_whitespace(self) -> None:
        assert exact_match_scorer("  hello  ", "hello") == 1.0

    def test_evaluator_empty_cases(self) -> None:
        evaluator = PromptEvaluator()
        result = evaluator.evaluate("p", 1, "content", [])
        assert result.mean_score == 0.0
        assert result.passed

    def test_eval_result_weighted_score(self) -> None:
        result = EvalResult(prompt_name="p", version=1)
        assert result.weighted_score == 0.0

    def test_eval_result_no_details_weighted(self) -> None:
        result = EvalResult(prompt_name="p", version=1, details=[])
        assert result.weighted_score == 0.0

    def test_compare_empty(self) -> None:
        evaluator = PromptEvaluator()
        result = evaluator.compare([])
        assert result["best_version"] is None

    def test_default_runner_with_missing_key(self) -> None:
        evaluator = PromptEvaluator()
        cases = [PromptTestCase("t1", {"missing": "val"}, "expected")]
        # Template has {name} but variables has {missing} - should not crash
        result = evaluator.evaluate("p", 1, "Hello {name}", cases)
        assert len(result.scores) == 1


class TestChangelogEdgeCases:
    def test_single_version_changelog(self, store: PromptStore) -> None:
        store.add("p", "first version", message="Initial")
        gen = ChangelogGenerator(store)
        log = gen.generate("p")
        assert "v1" in log
        assert "Initial" in log

    def test_changelog_last_n(self, store: PromptStore) -> None:
        store.add("p", "v1", message="first")
        store.add("p", "v2", message="second")
        store.add("p", "v3", message="third")
        gen = ChangelogGenerator(store)
        log = gen.generate("p", last_n=2)
        # Should only include last 2 versions
        assert "third" in log
        assert "second" in log

    def test_generate_all_empty(self, store: PromptStore) -> None:
        gen = ChangelogGenerator(store)
        log = gen.generate_all()
        assert "No prompts" in log
