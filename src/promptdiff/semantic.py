"""Semantic similarity scoring between prompt versions.

Two backends are available:

- ``local``: a dependency-free lexical-semantic vectorizer that combines
  word unigrams, word bigrams, and character trigrams with sublinear
  term-frequency weighting, compared by cosine similarity. It runs
  offline, is fully deterministic, and is a much stronger signal than
  plain Jaccard word overlap because bigrams capture phrasing and
  character trigrams tolerate small spelling or inflection changes.
- ``openai``: true embedding-based similarity using the OpenAI
  embeddings API. Requires ``pip install llm-promptdiff[embeddings]``
  and an ``OPENAI_API_KEY``.

Both backends produce a :class:`SemanticComparison` whose ``verdict``
buckets the score into human-readable change severity, so CI and humans
can flag meaningful behavioral changes at a glance.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass

_WORD_RE = re.compile(r"[a-z0-9]+")

# Verdict thresholds, from most to least similar. Heuristic buckets
# chosen so that pure formatting edits land in "equivalent" and full
# rewrites land in "major change".
VERDICT_THRESHOLDS: tuple[tuple[float, str], ...] = (
    (0.95, "equivalent"),
    (0.80, "minor change"),
    (0.55, "moderate change"),
    (0.0, "major change"),
)


@dataclass(frozen=True)
class SemanticComparison:
    """Result of a semantic comparison between two prompt texts."""

    similarity: float
    backend: str
    verdict: str
    model: str | None = None


def classify_similarity(similarity: float) -> str:
    """Bucket a similarity score into a human-readable verdict.

    Scores at or above 0.95 are "equivalent", 0.80 "minor change",
    0.55 "moderate change", and anything below is a "major change".
    """
    for threshold, verdict in VERDICT_THRESHOLDS:
        if similarity >= threshold:
            return verdict
    return VERDICT_THRESHOLDS[-1][1]


def _words(text: str) -> list[str]:
    return _WORD_RE.findall(text.lower())


def extract_features(text: str) -> Counter[str]:
    """Extract weighted lexical features from *text*.

    Features are word unigrams (``w:``), word bigrams (``b:``), and
    character trigrams (``c:``) computed over the normalized word
    stream. The mix makes the local backend sensitive to phrasing and
    word order, not just vocabulary.
    """
    words = _words(text)
    features: Counter[str] = Counter()

    for word in words:
        features[f"w:{word}"] += 1

    for first, second in zip(words, words[1:]):
        features[f"b:{first} {second}"] += 1

    joined = " ".join(words)
    for i in range(len(joined) - 2):
        features[f"c:{joined[i : i + 3]}"] += 1

    return features


def _weighted(features: Counter[str]) -> dict[str, float]:
    """Apply sublinear term-frequency weighting: 1 + log(count)."""
    return {feat: 1.0 + math.log(count) for feat, count in features.items()}


def cosine_similarity(vec_a: dict[str, float], vec_b: dict[str, float]) -> float:
    """Cosine similarity between two sparse vectors (dict form).

    Returns 0.0 when either vector is empty or has zero norm.
    """
    if not vec_a or not vec_b:
        return 0.0

    if len(vec_b) < len(vec_a):
        vec_a, vec_b = vec_b, vec_a

    dot = sum(value * vec_b.get(feat, 0.0) for feat, value in vec_a.items())
    norm_a = math.sqrt(sum(value * value for value in vec_a.values()))
    norm_b = math.sqrt(sum(value * value for value in vec_b.values()))

    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


def local_similarity(text_a: str, text_b: str) -> float:
    """Dependency-free semantic-ish similarity in the range [0, 1].

    Both texts empty (after normalization) scores 1.0, exactly one
    empty scores 0.0.
    """
    features_a = extract_features(text_a)
    features_b = extract_features(text_b)

    if not features_a and not features_b:
        return 1.0
    if not features_a or not features_b:
        return 0.0

    return cosine_similarity(_weighted(features_a), _weighted(features_b))


def openai_similarity(
    text_a: str,
    text_b: str,
    model: str = "text-embedding-3-small",
) -> float:
    """Embedding cosine similarity via the OpenAI API.

    Raises:
        ImportError: If the ``embeddings`` extra is not installed.
    """
    try:
        from openai import OpenAI
    except ImportError:
        raise ImportError(
            "The 'openai' backend requires the embeddings extra: "
            "pip install 'llm-promptdiff[embeddings]'"
        )

    client = OpenAI()
    resp = client.embeddings.create(input=[text_a, text_b], model=model)
    vec_a = resp.data[0].embedding
    vec_b = resp.data[1].embedding

    dot = sum(a * b for a, b in zip(vec_a, vec_b))
    norm_a = math.sqrt(sum(a * a for a in vec_a))
    norm_b = math.sqrt(sum(b * b for b in vec_b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


def compare_semantic(
    text_a: str,
    text_b: str,
    backend: str = "local",
    model: str = "text-embedding-3-small",
) -> SemanticComparison:
    """Compare two texts semantically and classify the change.

    Args:
        text_a: Original prompt text.
        text_b: Updated prompt text.
        backend: ``"local"`` (offline, default) or ``"openai"``.
        model: Embedding model name, used by the ``openai`` backend.

    Returns:
        A :class:`SemanticComparison` with similarity, backend, verdict,
        and the model name (``None`` for the local backend).

    Raises:
        ValueError: On an unknown backend name.
        ImportError: If the openai backend is requested without the
            ``embeddings`` extra installed.
    """
    if backend == "local":
        similarity = local_similarity(text_a, text_b)
        used_model = None
    elif backend == "openai":
        similarity = openai_similarity(text_a, text_b, model=model)
        used_model = model
    else:
        raise ValueError(f"Unknown semantic backend: '{backend}'. Use 'local' or 'openai'.")

    return SemanticComparison(
        similarity=similarity,
        backend=backend,
        verdict=classify_similarity(similarity),
        model=used_model,
    )
