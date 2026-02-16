"""Evaluate prompt versions against test cases."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Protocol


class Scorer(Protocol):
    """Protocol for scoring a prompt output against expected output."""

    def __call__(self, output: str, expected: str) -> float: ...


@dataclass
class TestCase:
    """A single test case for prompt evaluation."""

    name: str
    input_vars: dict[str, str]
    expected: str
    weight: float = 1.0


@dataclass
class EvalResult:
    """Result of evaluating a prompt version."""

    prompt_name: str
    version: int
    scores: list[float] = field(default_factory=list)
    test_names: list[str] = field(default_factory=list)
    details: list[dict[str, Any]] = field(default_factory=list)

    @property
    def mean_score(self) -> float:
        if not self.scores:
            return 0.0
        return sum(self.scores) / len(self.scores)

    @property
    def weighted_score(self) -> float:
        if not self.details:
            return 0.0
        total_weight = sum(d.get("weight", 1.0) for d in self.details)
        if total_weight == 0:
            return 0.0
        weighted = sum(d["score"] * d.get("weight", 1.0) for d in self.details)
        return weighted / total_weight

    @property
    def passed(self) -> bool:
        return all(s >= 0.5 for s in self.scores)


def exact_match_scorer(output: str, expected: str) -> float:
    """Score 1.0 if output matches expected exactly, else 0.0."""
    return 1.0 if output.strip() == expected.strip() else 0.0


def contains_scorer(output: str, expected: str) -> float:
    """Score 1.0 if expected is contained in output, else 0.0."""
    return 1.0 if expected.strip() in output.strip() else 0.0


def similarity_scorer(output: str, expected: str) -> float:
    """Score based on word overlap (Jaccard similarity)."""
    words_out = set(output.lower().split())
    words_exp = set(expected.lower().split())
    if not words_out and not words_exp:
        return 1.0
    if not words_out or not words_exp:
        return 0.0
    return len(words_out & words_exp) / len(words_out | words_exp)


class PromptEvaluator:
    """Evaluate prompts against test cases.

    The `runner` function takes a prompt template string and input variables,
    then returns the output string. This allows plugging in any LLM backend.
    """

    def __init__(
        self,
        runner: Callable[[str, dict[str, str]], str] | None = None,
        scorer: Scorer | None = None,
    ) -> None:
        self.runner = runner or self._default_runner
        self.scorer = scorer or similarity_scorer

    @staticmethod
    def _default_runner(template: str, variables: dict[str, str]) -> str:
        """Default runner: simple string formatting (no LLM call)."""
        try:
            return template.format(**variables)
        except KeyError:
            return template

    def evaluate(
        self,
        prompt_name: str,
        version: int,
        content: str,
        test_cases: list[TestCase],
    ) -> EvalResult:
        """Run prompt content against test cases and score results."""
        result = EvalResult(prompt_name=prompt_name, version=version)

        for tc in test_cases:
            output = self.runner(content, tc.input_vars)
            score = self.scorer(output, tc.expected)

            result.scores.append(score)
            result.test_names.append(tc.name)
            result.details.append({
                "test": tc.name,
                "input": tc.input_vars,
                "expected": tc.expected,
                "output": output,
                "score": score,
                "weight": tc.weight,
            })

        return result

    def compare(
        self,
        results: list[EvalResult],
    ) -> dict[str, Any]:
        """Compare evaluation results across versions."""
        return {
            "versions": [
                {
                    "version": r.version,
                    "mean_score": r.mean_score,
                    "weighted_score": r.weighted_score,
                    "passed": r.passed,
                }
                for r in results
            ],
            "best_version": max(results, key=lambda r: r.weighted_score).version
            if results
            else None,
        }
