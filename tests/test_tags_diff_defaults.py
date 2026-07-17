"""Tests for the tag CLI group, tag merge on add, and diff version defaults."""

from pathlib import Path

import pytest
from click.testing import CliRunner

from promptdiff.cli import cli
from promptdiff.registry import PromptRegistry
from promptdiff.store import PromptStore


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def workspace(runner, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(cli, ["init"])
    assert result.exit_code == 0
    return tmp_path


def _add(runner, name, content, *args):
    result = runner.invoke(cli, ["add", name, *args], input=content)
    assert result.exit_code == 0, result.output
    return result


class TestRegistryRemoveTags:
    def test_remove_existing_tags(self, workspace):
        store = PromptStore(workspace)
        registry = PromptRegistry(store)
        store.add("p", "content")
        registry.set_tags("p", ["a", "b", "c"])
        removed = registry.remove_tags("p", ["a", "c"])
        assert removed == ["a", "c"]
        assert registry.get_tags("p") == ["b"]

    def test_remove_unknown_tags_ignored(self, workspace):
        store = PromptStore(workspace)
        registry = PromptRegistry(store)
        store.add("p", "content")
        registry.set_tags("p", ["a"])
        removed = registry.remove_tags("p", ["zzz"])
        assert removed == []
        assert registry.get_tags("p") == ["a"]

    def test_remove_from_missing_prompt_raises(self, workspace):
        registry = PromptRegistry(PromptStore(workspace))
        with pytest.raises(FileNotFoundError):
            registry.remove_tags("ghost", ["a"])


class TestAddTagMerge:
    def test_add_new_version_merges_tags(self, runner, workspace):
        _add(runner, "p", "v1 content", "-t", "prod")
        _add(runner, "p", "v2 content", "-t", "reviewed")
        registry = PromptRegistry(PromptStore(workspace))
        assert registry.get_tags("p") == ["prod", "reviewed"]

    def test_add_duplicate_tag_not_repeated(self, runner, workspace):
        _add(runner, "p", "v1 content", "-t", "prod")
        _add(runner, "p", "v2 content", "-t", "prod")
        registry = PromptRegistry(PromptStore(workspace))
        assert registry.get_tags("p") == ["prod"]


class TestContentHashDisplay:
    def test_add_output_always_shows_hash(self, runner, workspace):
        # Regression test: rich parsed [hash] as markup when the hash
        # started with a letter and silently swallowed it.
        for i in range(12):
            content = f"prompt body number {i}\n"
            result = runner.invoke(cli, ["add", f"p{i}"], input=content)
            assert result.exit_code == 0
            info = PromptStore(workspace).get_version(f"p{i}")
            assert info.content_hash in result.output

    def test_show_output_shows_hash(self, runner, workspace):
        _add(runner, "p", "some content\n")
        info = PromptStore(workspace).get_version("p")
        result = runner.invoke(cli, ["show", "p"])
        assert result.exit_code == 0
        assert info.content_hash in result.output


class TestTagCli:
    def test_tag_add(self, runner, workspace):
        _add(runner, "p", "content")
        result = runner.invoke(cli, ["tag", "add", "p", "prod", "chatbot"])
        assert result.exit_code == 0
        assert "chatbot" in result.output
        registry = PromptRegistry(PromptStore(workspace))
        assert registry.get_tags("p") == ["chatbot", "prod"]

    def test_tag_add_missing_prompt(self, runner, workspace):
        result = runner.invoke(cli, ["tag", "add", "ghost", "prod"])
        assert result.exit_code == 1
        assert "not found" in result.output

    def test_tag_add_requires_tags(self, runner, workspace):
        _add(runner, "p", "content")
        result = runner.invoke(cli, ["tag", "add", "p"])
        assert result.exit_code != 0

    def test_tag_rm(self, runner, workspace):
        _add(runner, "p", "content", "-t", "prod", "-t", "beta")
        result = runner.invoke(cli, ["tag", "rm", "p", "beta"])
        assert result.exit_code == 0
        assert "Removed beta" in result.output
        registry = PromptRegistry(PromptStore(workspace))
        assert registry.get_tags("p") == ["prod"]

    def test_tag_rm_no_match(self, runner, workspace):
        _add(runner, "p", "content", "-t", "prod")
        result = runner.invoke(cli, ["tag", "rm", "p", "nope"])
        assert result.exit_code == 0
        assert "No matching tags" in result.output

    def test_tag_rm_last_tag_shows_none(self, runner, workspace):
        _add(runner, "p", "content", "-t", "prod")
        result = runner.invoke(cli, ["tag", "rm", "p", "prod"])
        assert result.exit_code == 0
        assert "(none)" in result.output

    def test_tag_list_for_prompt(self, runner, workspace):
        _add(runner, "p", "content", "-t", "prod", "-t", "beta")
        result = runner.invoke(cli, ["tag", "list", "p"])
        assert result.exit_code == 0
        assert "beta" in result.output
        assert "prod" in result.output

    def test_tag_list_for_untagged_prompt(self, runner, workspace):
        _add(runner, "p", "content")
        result = runner.invoke(cli, ["tag", "list", "p"])
        assert result.exit_code == 0
        assert "no tags" in result.output

    def test_tag_list_all_counts(self, runner, workspace):
        _add(runner, "a", "content a", "-t", "prod")
        _add(runner, "b", "content b", "-t", "prod", "-t", "beta")
        result = runner.invoke(cli, ["tag", "list"])
        assert result.exit_code == 0
        assert "prod" in result.output
        assert "beta" in result.output
        assert "2" in result.output

    def test_tag_list_all_empty(self, runner, workspace):
        result = runner.invoke(cli, ["tag", "list"])
        assert result.exit_code == 0
        assert "No tags" in result.output

    def test_tag_list_uninitialized(self, runner, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(cli, ["tag", "list"])
        assert result.exit_code == 1
        assert "Not a promptdiff repository" in result.output


class TestDiffDefaults:
    def test_diff_no_versions_uses_prev_and_latest(self, runner, workspace):
        _add(runner, "p", "hello world\n")
        _add(runner, "p", "hello there\n")
        _add(runner, "p", "goodbye world\n")
        result = runner.invoke(cli, ["diff", "p"])
        assert result.exit_code == 0
        assert "v2 -> v3" in result.output

    def test_diff_one_version_compares_to_latest(self, runner, workspace):
        _add(runner, "p", "hello world\n")
        _add(runner, "p", "hello there\n")
        _add(runner, "p", "goodbye world\n")
        result = runner.invoke(cli, ["diff", "p", "1"])
        assert result.exit_code == 0
        assert "v1 -> v3" in result.output

    def test_diff_explicit_versions_still_work(self, runner, workspace):
        _add(runner, "p", "hello world\n")
        _add(runner, "p", "hello there\n")
        result = runner.invoke(cli, ["diff", "p", "1", "2"])
        assert result.exit_code == 0
        assert "v1 -> v2" in result.output

    def test_diff_single_version_friendly_message(self, runner, workspace):
        _add(runner, "p", "hello world\n")
        result = runner.invoke(cli, ["diff", "p"])
        assert result.exit_code == 0
        assert "only one version" in result.output

    def test_diff_missing_prompt_clean_error(self, runner, workspace):
        result = runner.invoke(cli, ["diff", "ghost"])
        assert result.exit_code == 1
        assert "not found" in result.output

    def test_diff_missing_version_clean_error(self, runner, workspace):
        _add(runner, "p", "hello world\n")
        _add(runner, "p", "hello there\n")
        result = runner.invoke(cli, ["diff", "p", "9"])
        assert result.exit_code == 1
        assert "not found" in result.output

    def test_diff_uninitialized_store(self, runner, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        Path(tmp_path, "keep").touch()
        result = runner.invoke(cli, ["diff", "p"])
        assert result.exit_code == 1
