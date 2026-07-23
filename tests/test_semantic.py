"""Tests for the semantic similarity module and the semantic CLI command."""

from __future__ import annotations

import json
import sys
import types

import pytest
from click.testing import CliRunner

from promptdiff.cli import cli
from promptdiff.semantic import (
    SemanticComparison,
    classify_similarity,
    compare_semantic,
    cosine_similarity,
    extract_features,
    local_similarity,
    openai_similarity,
)


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def workspace(runner, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(cli, ["init"])
    assert result.exit_code == 0
    return tmp_path


def _add(runner, name, content):
    result = runner.invoke(cli, ["add", name], input=content)
    assert result.exit_code == 0, result.output
    return result


class TestExtractFeatures:
    def test_word_unigrams(self):
        features = extract_features("hello world")
        assert features["w:hello"] == 1
        assert features["w:world"] == 1

    def test_word_bigrams(self):
        features = extract_features("summarize the document")
        assert features["b:summarize the"] == 1
        assert features["b:the document"] == 1

    def test_char_trigrams(self):
        features = extract_features("cat")
        assert features["c:cat"] == 1

    def test_lowercase_normalization(self):
        assert extract_features("Hello WORLD") == extract_features("hello world")

    def test_punctuation_ignored(self):
        assert extract_features("hello, world!") == extract_features("hello world")

    def test_empty_text(self):
        assert extract_features("") == {}

    def test_whitespace_only(self):
        assert extract_features("   \n\t  ") == {}

    def test_repeated_words_counted(self):
        features = extract_features("very very good")
        assert features["w:very"] == 2


class TestCosineSimilarity:
    def test_identical_vectors(self):
        vec = {"a": 1.0, "b": 2.0}
        assert cosine_similarity(vec, vec) == pytest.approx(1.0)

    def test_orthogonal_vectors(self):
        assert cosine_similarity({"a": 1.0}, {"b": 1.0}) == 0.0

    def test_empty_vector(self):
        assert cosine_similarity({}, {"a": 1.0}) == 0.0
        assert cosine_similarity({"a": 1.0}, {}) == 0.0
        assert cosine_similarity({}, {}) == 0.0

    def test_zero_norm(self):
        assert cosine_similarity({"a": 0.0}, {"a": 1.0}) == 0.0

    def test_symmetric(self):
        vec_a = {"a": 1.0, "b": 3.0}
        vec_b = {"b": 2.0, "c": 1.0}
        assert cosine_similarity(vec_a, vec_b) == pytest.approx(cosine_similarity(vec_b, vec_a))


class TestLocalSimilarity:
    def test_identical_texts(self):
        text = "You are a helpful assistant. Answer concisely."
        assert local_similarity(text, text) == pytest.approx(1.0)

    def test_both_empty(self):
        assert local_similarity("", "") == 1.0

    def test_one_empty(self):
        assert local_similarity("hello", "") == 0.0
        assert local_similarity("", "hello") == 0.0

    def test_completely_different(self):
        sim = local_similarity(
            "alpha bravo charlie delta echo",
            "zzz qqq xxx jjj vvv",
        )
        assert sim < 0.1

    def test_symmetric(self):
        a = "Summarize the following document in three bullet points."
        b = "Summarize the document below using exactly three bullets."
        assert local_similarity(a, b) == pytest.approx(local_similarity(b, a))

    def test_range_bounds(self):
        a = "You are a pirate. Answer in pirate speak."
        b = "You are a lawyer. Answer in formal legalese."
        sim = local_similarity(a, b)
        assert 0.0 <= sim <= 1.0

    def test_paraphrase_scores_higher_than_unrelated(self):
        base = "Summarize the following document in three bullet points."
        paraphrase = "Summarize the document below in three bullet points."
        unrelated = "Translate this sentence into French immediately."
        assert local_similarity(base, paraphrase) > local_similarity(base, unrelated)

    def test_word_order_matters(self):
        # Bigrams should make reordered text score below identical text.
        a = "first do this then do that"
        b = "then do that first do this"
        assert local_similarity(a, b) < 1.0

    def test_formatting_only_change_scores_high(self):
        a = "You are a helpful assistant.\nAnswer concisely."
        b = "You are a helpful assistant. Answer concisely."
        assert local_similarity(a, b) > 0.95


class TestClassifySimilarity:
    def test_equivalent(self):
        assert classify_similarity(1.0) == "equivalent"
        assert classify_similarity(0.95) == "equivalent"

    def test_minor_change(self):
        assert classify_similarity(0.94) == "minor change"
        assert classify_similarity(0.80) == "minor change"

    def test_moderate_change(self):
        assert classify_similarity(0.79) == "moderate change"
        assert classify_similarity(0.55) == "moderate change"

    def test_major_change(self):
        assert classify_similarity(0.54) == "major change"
        assert classify_similarity(0.0) == "major change"


class TestCompareSemantic:
    def test_local_backend(self):
        result = compare_semantic("hello world", "hello world")
        assert isinstance(result, SemanticComparison)
        assert result.backend == "local"
        assert result.model is None
        assert result.similarity == pytest.approx(1.0)
        assert result.verdict == "equivalent"

    def test_unknown_backend_raises(self):
        with pytest.raises(ValueError, match="Unknown semantic backend"):
            compare_semantic("a", "b", backend="quantum")

    def test_openai_backend_missing_dependency(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "openai", None)
        with pytest.raises(ImportError, match="embeddings extra"):
            compare_semantic("a", "b", backend="openai")


def _install_fake_openai(monkeypatch, embeddings):
    """Inject a fake openai module returning the given embedding vectors."""

    class FakeData:
        def __init__(self, embedding):
            self.embedding = embedding

    class FakeResponse:
        def __init__(self):
            self.data = [FakeData(vec) for vec in embeddings]

    class FakeEmbeddings:
        def create(self, input, model):
            assert len(input) == 2
            return FakeResponse()

    class FakeOpenAI:
        def __init__(self, *args, **kwargs):
            self.embeddings = FakeEmbeddings()

    fake = types.ModuleType("openai")
    fake.OpenAI = FakeOpenAI
    monkeypatch.setitem(sys.modules, "openai", fake)


class TestOpenAISimilarity:
    def test_identical_embeddings(self, monkeypatch):
        _install_fake_openai(monkeypatch, [[1.0, 2.0, 3.0], [1.0, 2.0, 3.0]])
        assert openai_similarity("a", "b") == pytest.approx(1.0)

    def test_orthogonal_embeddings(self, monkeypatch):
        _install_fake_openai(monkeypatch, [[1.0, 0.0], [0.0, 1.0]])
        assert openai_similarity("a", "b") == pytest.approx(0.0)

    def test_zero_norm_embedding(self, monkeypatch):
        _install_fake_openai(monkeypatch, [[0.0, 0.0], [1.0, 1.0]])
        assert openai_similarity("a", "b") == 0.0

    def test_compare_semantic_openai_backend(self, monkeypatch):
        _install_fake_openai(monkeypatch, [[1.0, 2.0], [1.0, 2.0]])
        result = compare_semantic("a", "b", backend="openai", model="custom-model")
        assert result.backend == "openai"
        assert result.model == "custom-model"
        assert result.verdict == "equivalent"


class TestSemanticCLI:
    def test_defaults_to_last_two_versions(self, runner, workspace):
        _add(runner, "p", "You are a helpful assistant.\n")
        _add(runner, "p", "You are a helpful assistant. Be concise.\n")
        result = runner.invoke(cli, ["semantic", "p"])
        assert result.exit_code == 0, result.output
        assert "v1 -> v2" in result.output
        assert "Similarity:" in result.output
        assert "Verdict:" in result.output

    def test_explicit_versions(self, runner, workspace):
        _add(runner, "p", "version one content\n")
        _add(runner, "p", "version two content\n")
        _add(runner, "p", "version three content\n")
        result = runner.invoke(cli, ["semantic", "p", "1", "3"])
        assert result.exit_code == 0, result.output
        assert "v1 -> v3" in result.output

    def test_single_version_given_compares_to_latest(self, runner, workspace):
        _add(runner, "p", "one\n")
        _add(runner, "p", "two\n")
        _add(runner, "p", "three\n")
        result = runner.invoke(cli, ["semantic", "p", "1"])
        assert result.exit_code == 0, result.output
        assert "v1 -> v3" in result.output

    def test_only_one_version_notice(self, runner, workspace):
        _add(runner, "p", "only version\n")
        result = runner.invoke(cli, ["semantic", "p"])
        assert result.exit_code == 0
        assert "only one version" in result.output

    def test_missing_prompt_errors(self, runner, workspace):
        result = runner.invoke(cli, ["semantic", "ghost"])
        assert result.exit_code == 1

    def test_missing_version_errors(self, runner, workspace):
        _add(runner, "p", "one\n")
        _add(runner, "p", "two\n")
        result = runner.invoke(cli, ["semantic", "p", "1", "99"])
        assert result.exit_code == 1

    def test_identical_versions_equivalent(self, runner, workspace):
        _add(runner, "p", "same content here\n")
        _add(runner, "p", "different content\n")
        result = runner.invoke(cli, ["semantic", "p", "1", "1"])
        assert result.exit_code == 0
        assert "100.0%" in result.output
        assert "equivalent" in result.output

    def test_json_output_parses(self, runner, workspace):
        _add(runner, "p", "You are a helpful assistant with a long descriptive setup. " * 4 + "\n")
        _add(
            runner,
            "p",
            "You are a helpful assistant with a long descriptive setup, mostly. " * 4 + "\n",
        )
        result = runner.invoke(cli, ["semantic", "p", "--json-output"])
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert payload["name"] == "p"
        assert payload["old_version"] == 1
        assert payload["new_version"] == 2
        assert payload["backend"] == "local"
        assert payload["model"] is None
        assert 0.0 <= payload["similarity"] <= 1.0
        assert payload["passed"] is True

    def test_fail_below_passes(self, runner, workspace):
        _add(runner, "p", "same text\n")
        _add(runner, "p", "same text plus a word\n")
        result = runner.invoke(cli, ["semantic", "p", "--fail-below", "0.1"])
        assert result.exit_code == 0, result.output

    def test_fail_below_fails(self, runner, workspace):
        _add(runner, "p", "alpha bravo charlie\n")
        _add(runner, "p", "zzz qqq xxx\n")
        result = runner.invoke(cli, ["semantic", "p", "--fail-below", "0.9"])
        assert result.exit_code == 1
        assert "FAIL" in result.output

    def test_fail_below_json_reports_failure(self, runner, workspace):
        _add(runner, "p", "alpha bravo charlie\n")
        _add(runner, "p", "zzz qqq xxx\n")
        result = runner.invoke(cli, ["semantic", "p", "--fail-below", "0.9", "--json-output"])
        assert result.exit_code == 1
        json_part = result.output[: result.output.rindex("}") + 1]
        payload = json.loads(json_part)
        assert payload["passed"] is False
        assert payload["threshold"] == 0.9

    def test_invalid_backend_rejected(self, runner, workspace):
        _add(runner, "p", "one\n")
        _add(runner, "p", "two\n")
        result = runner.invoke(cli, ["semantic", "p", "--backend", "quantum"])
        assert result.exit_code == 2

    def test_openai_backend_without_dependency(self, runner, workspace, monkeypatch):
        _add(runner, "p", "one\n")
        _add(runner, "p", "two\n")
        monkeypatch.setitem(sys.modules, "openai", None)
        result = runner.invoke(cli, ["semantic", "p", "--backend", "openai"])
        assert result.exit_code == 1
        assert "embeddings extra" in result.output
        # Regression: rich swallowed the [embeddings] bracket text as
        # markup, printing a wrong install command without the extra.
        assert "llm-promptdiff[embeddings]" in result.output

    def test_openai_backend_api_error(self, runner, workspace, monkeypatch):
        _add(runner, "p", "one\n")
        _add(runner, "p", "two\n")

        class BrokenOpenAI:
            def __init__(self, *args, **kwargs):
                raise RuntimeError("no API key configured")

        fake = types.ModuleType("openai")
        fake.OpenAI = BrokenOpenAI
        monkeypatch.setitem(sys.modules, "openai", fake)
        result = runner.invoke(cli, ["semantic", "p", "--backend", "openai"])
        assert result.exit_code == 1
        assert "Semantic comparison failed" in result.output

    def test_openai_backend_via_cli(self, runner, workspace, monkeypatch):
        _add(runner, "p", "one\n")
        _add(runner, "p", "two\n")
        _install_fake_openai(monkeypatch, [[1.0, 2.0], [1.0, 2.0]])
        result = runner.invoke(cli, ["semantic", "p", "--backend", "openai", "--json-output"])
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert payload["backend"] == "openai"
        assert payload["model"] == "text-embedding-3-small"
        assert payload["similarity"] == pytest.approx(1.0)

    def test_public_api_exports(self):
        import promptdiff

        assert promptdiff.compare_semantic is compare_semantic
        assert promptdiff.local_similarity is local_similarity
        assert promptdiff.SemanticComparison is SemanticComparison
