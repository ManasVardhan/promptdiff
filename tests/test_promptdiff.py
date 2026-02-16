"""Tests for promptdiff."""

from __future__ import annotations

import pytest
from pathlib import Path

from promptdiff.store import PromptStore, VersionInfo
from promptdiff.diff import PromptDiff, DiffResult
from promptdiff.registry import PromptRegistry
from promptdiff.eval import PromptEvaluator, TestCase, exact_match_scorer, similarity_scorer
from promptdiff.changelog import ChangelogGenerator


@pytest.fixture
def store(tmp_path: Path) -> PromptStore:
    s = PromptStore(tmp_path)
    s.init()
    return s


@pytest.fixture
def registry(store: PromptStore) -> PromptRegistry:
    return PromptRegistry(store)


class TestStore:
    def test_init_creates_directory(self, tmp_path: Path) -> None:
        store = PromptStore(tmp_path)
        store.init()
        assert store.initialized
        assert (tmp_path / ".promptdiff" / "promptdiff.json").exists()

    def test_add_and_get(self, store: PromptStore) -> None:
        info = store.add("test-prompt", "Hello {name}", message="initial")
        assert info.version == 1
        assert info.message == "initial"

        retrieved = store.get_version("test-prompt", 1)
        assert retrieved.content == "Hello {name}"

    def test_add_multiple_versions(self, store: PromptStore) -> None:
        store.add("p", "version 1")
        store.add("p", "version 2")
        info = store.add("p", "version 3")
        assert info.version == 3

        versions = store.list_versions("p")
        assert len(versions) == 3

    def test_duplicate_content_returns_existing(self, store: PromptStore) -> None:
        v1 = store.add("p", "same content")
        v2 = store.add("p", "same content")
        assert v1.version == v2.version

    def test_list_prompts(self, store: PromptStore) -> None:
        store.add("alpha", "a")
        store.add("beta", "b")
        assert store.list_prompts() == ["alpha", "beta"]

    def test_delete_prompt(self, store: PromptStore) -> None:
        store.add("to-delete", "bye")
        store.delete_prompt("to-delete")
        assert "to-delete" not in store.list_prompts()

    def test_get_latest_version(self, store: PromptStore) -> None:
        store.add("p", "v1")
        store.add("p", "v2")
        latest = store.get_version("p")
        assert latest.content == "v2"

    def test_not_initialized_raises(self, tmp_path: Path) -> None:
        store = PromptStore(tmp_path / "nope")
        with pytest.raises(RuntimeError, match="Not a promptdiff"):
            store.list_prompts()


class TestDiff:
    def test_identical_texts(self) -> None:
        d = PromptDiff()
        result = d.text_diff("hello world", "hello world")
        assert result.similarity_ratio == 1.0
        assert not result.has_changes

    def test_different_texts(self) -> None:
        d = PromptDiff()
        result = d.text_diff("line one\nline two", "line one\nline three")
        assert result.has_changes
        assert result.stats["additions"] > 0

    def test_semantic_similarity(self) -> None:
        d = PromptDiff()
        score = d.semantic_similarity(
            "the cat sat on the mat",
            "the cat sat on the rug",
        )
        assert 0.5 < score < 1.0

    def test_full_diff(self) -> None:
        d = PromptDiff()
        result = d.full_diff("old text", "new text", 1, 2)
        assert result.semantic_similarity is not None
        assert result.old_version == 1
        assert result.new_version == 2

    def test_unified_diff(self) -> None:
        d = PromptDiff()
        output = d.unified_diff("a\nb\n", "a\nc\n")
        assert "-b" in output
        assert "+c" in output


class TestRegistry:
    def test_register_and_get(self, registry: PromptRegistry) -> None:
        v = registry.register("my-prompt", "content here", tags=["prod", "v1"])
        assert v == 1
        assert registry.get("my-prompt") == "content here"

    def test_tags(self, registry: PromptRegistry) -> None:
        registry.register("p", "c", tags=["gpt4", "production"])
        assert "gpt4" in registry.get_tags("p")
        assert registry.find_by_tag("production") == ["p"]

    def test_list_all(self, registry: PromptRegistry) -> None:
        registry.register("a", "1")
        registry.register("b", "2")
        all_prompts = registry.list_all()
        assert len(all_prompts) == 2


class TestEval:
    def test_exact_match(self) -> None:
        assert exact_match_scorer("hello", "hello") == 1.0
        assert exact_match_scorer("hello", "world") == 0.0

    def test_similarity_scorer(self) -> None:
        score = similarity_scorer("the quick brown fox", "the slow brown fox")
        assert 0.5 < score < 1.0

    def test_evaluator(self) -> None:
        evaluator = PromptEvaluator()
        cases = [
            TestCase("t1", {"name": "World"}, "Hello World", 1.0),
        ]
        result = evaluator.evaluate("p", 1, "Hello {name}", cases)
        assert result.mean_score > 0

    def test_compare(self) -> None:
        evaluator = PromptEvaluator()
        cases = [TestCase("t1", {}, "hello", 1.0)]
        r1 = evaluator.evaluate("p", 1, "hello", cases)
        r2 = evaluator.evaluate("p", 2, "goodbye", cases)
        comparison = evaluator.compare([r1, r2])
        assert comparison["best_version"] == 1


class TestChangelog:
    def test_generate(self, store: PromptStore) -> None:
        store.add("p", "version 1", message="Initial prompt")
        store.add("p", "version 2 updated", message="Improved clarity")
        gen = ChangelogGenerator(store)
        log = gen.generate("p")
        assert "v1" in log
        assert "v2" in log
        assert "Improved clarity" in log

    def test_generate_all(self, store: PromptStore) -> None:
        store.add("a", "content a")
        store.add("b", "content b")
        gen = ChangelogGenerator(store)
        log = gen.generate_all()
        assert "a" in log
        assert "b" in log
