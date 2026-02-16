#!/usr/bin/env python3
"""Example: prompt versioning workflow with promptdiff."""

from pathlib import Path
import tempfile

from promptdiff import PromptStore, PromptDiff, PromptRegistry, ChangelogGenerator
from promptdiff.eval import PromptEvaluator, TestCase


def main() -> None:
    # Work in a temp directory
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)

        # 1. Initialize store
        store = PromptStore(root)
        store.init()
        print("Initialized promptdiff store\n")

        # 2. Register prompt versions
        registry = PromptRegistry(store)

        registry.register(
            "summarizer",
            "Summarize the following text in 2 sentences:\n\n{text}",
            message="Initial summarizer prompt",
            tags=["production", "gpt4"],
        )
        print("Added summarizer v1")

        registry.register(
            "summarizer",
            "You are an expert summarizer. Given the text below, provide a concise "
            "2-sentence summary that captures the key points.\n\nText: {text}\n\nSummary:",
            message="Improved with role and clearer instructions",
        )
        print("Added summarizer v2")

        registry.register(
            "summarizer",
            "You are an expert summarizer. Given the text below, provide a concise "
            "2-sentence summary that captures the key points. Focus on facts and "
            "avoid opinions.\n\nText: {text}\n\nSummary:",
            message="Added constraint to focus on facts",
        )
        print("Added summarizer v3\n")

        # 3. Show diff between versions
        differ = PromptDiff()
        v1 = store.get_version("summarizer", 1)
        v3 = store.get_version("summarizer", 3)
        result = differ.full_diff(v1.content, v3.content, 1, 3)

        print("Diff v1 -> v3:")
        print(f"  Text similarity:     {result.similarity_ratio:.1%}")
        print(f"  Semantic similarity:  {result.semantic_similarity:.1%}")
        print(f"  Additions: {result.stats['additions']}, Deletions: {result.stats['deletions']}\n")

        # 4. Evaluate prompt
        evaluator = PromptEvaluator()
        test_cases = [
            TestCase(
                name="basic",
                input_vars={"text": "The weather is sunny today."},
                expected="The weather is sunny today.",
            ),
        ]
        eval_result = evaluator.evaluate("summarizer", 3, v3.content, test_cases)
        print(f"Eval v3: mean score = {eval_result.mean_score:.1%}\n")

        # 5. Generate changelog
        changelog = ChangelogGenerator(store)
        print(changelog.generate("summarizer"))


if __name__ == "__main__":
    main()
