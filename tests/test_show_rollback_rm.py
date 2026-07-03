"""Tests for the show, rollback, and rm CLI commands."""

from __future__ import annotations

from click.testing import CliRunner

from promptdiff.cli import cli
from promptdiff.store import PromptStore


def make_store_with_prompt(tmp_path, versions=("first version", "second version")):
    store = PromptStore(tmp_path)
    store.init()
    for i, content in enumerate(versions, start=1):
        store.add("greeting", content, message=f"msg {i}")
    return store


class TestShow:
    def test_show_latest(self, tmp_path, monkeypatch):
        make_store_with_prompt(tmp_path)
        monkeypatch.chdir(tmp_path)
        result = CliRunner().invoke(cli, ["show", "greeting"])
        assert result.exit_code == 0
        assert "greeting v2" in result.output
        assert "second version" in result.output

    def test_show_specific_version(self, tmp_path, monkeypatch):
        make_store_with_prompt(tmp_path)
        monkeypatch.chdir(tmp_path)
        result = CliRunner().invoke(cli, ["show", "greeting", "-v", "1"])
        assert result.exit_code == 0
        assert "greeting v1" in result.output
        assert "first version" in result.output

    def test_show_raw_prints_content_only(self, tmp_path, monkeypatch):
        make_store_with_prompt(tmp_path)
        monkeypatch.chdir(tmp_path)
        result = CliRunner().invoke(cli, ["show", "greeting", "--raw"])
        assert result.exit_code == 0
        assert result.output == "second version"

    def test_show_includes_message_and_timestamp(self, tmp_path, monkeypatch):
        make_store_with_prompt(tmp_path)
        monkeypatch.chdir(tmp_path)
        result = CliRunner().invoke(cli, ["show", "greeting"])
        assert "msg 2" in result.output

    def test_show_missing_prompt(self, tmp_path, monkeypatch):
        store = PromptStore(tmp_path)
        store.init()
        monkeypatch.chdir(tmp_path)
        result = CliRunner().invoke(cli, ["show", "nope"])
        assert result.exit_code == 1
        assert "not found" in result.output

    def test_show_missing_version(self, tmp_path, monkeypatch):
        make_store_with_prompt(tmp_path)
        monkeypatch.chdir(tmp_path)
        result = CliRunner().invoke(cli, ["show", "greeting", "-v", "99"])
        assert result.exit_code == 1
        assert "not found" in result.output


class TestRollback:
    def test_rollback_creates_new_version(self, tmp_path, monkeypatch):
        store = make_store_with_prompt(tmp_path)
        monkeypatch.chdir(tmp_path)
        result = CliRunner().invoke(cli, ["rollback", "greeting", "1"])
        assert result.exit_code == 0
        assert "new v3" in result.output
        latest = store.get_version("greeting")
        assert latest.version == 3
        assert latest.content == "first version"
        assert latest.message == "Rollback to v1"

    def test_rollback_custom_message(self, tmp_path, monkeypatch):
        store = make_store_with_prompt(tmp_path)
        monkeypatch.chdir(tmp_path)
        result = CliRunner().invoke(
            cli, ["rollback", "greeting", "1", "-m", "undo experiment"]
        )
        assert result.exit_code == 0
        assert store.get_version("greeting").message == "undo experiment"

    def test_rollback_noop_when_identical(self, tmp_path, monkeypatch):
        store = make_store_with_prompt(tmp_path)
        monkeypatch.chdir(tmp_path)
        result = CliRunner().invoke(cli, ["rollback", "greeting", "2"])
        assert result.exit_code == 0
        assert "Nothing to do" in result.output
        assert store.get_version("greeting").version == 2

    def test_rollback_missing_version(self, tmp_path, monkeypatch):
        make_store_with_prompt(tmp_path)
        monkeypatch.chdir(tmp_path)
        result = CliRunner().invoke(cli, ["rollback", "greeting", "99"])
        assert result.exit_code == 1
        assert "not found" in result.output

    def test_rollback_missing_prompt(self, tmp_path, monkeypatch):
        store = PromptStore(tmp_path)
        store.init()
        monkeypatch.chdir(tmp_path)
        result = CliRunner().invoke(cli, ["rollback", "nope", "1"])
        assert result.exit_code == 1

    def test_rollback_preserves_history(self, tmp_path, monkeypatch):
        store = make_store_with_prompt(tmp_path)
        monkeypatch.chdir(tmp_path)
        CliRunner().invoke(cli, ["rollback", "greeting", "1"])
        versions = store.list_versions("greeting")
        assert [v.version for v in versions] == [1, 2, 3]
        assert versions[1].content == "second version"


class TestRm:
    def test_rm_with_yes_flag(self, tmp_path, monkeypatch):
        store = make_store_with_prompt(tmp_path)
        monkeypatch.chdir(tmp_path)
        result = CliRunner().invoke(cli, ["rm", "greeting", "-y"])
        assert result.exit_code == 0
        assert "Deleted" in result.output
        assert store.list_prompts() == []

    def test_rm_confirmation_accepted(self, tmp_path, monkeypatch):
        store = make_store_with_prompt(tmp_path)
        monkeypatch.chdir(tmp_path)
        result = CliRunner().invoke(cli, ["rm", "greeting"], input="y\n")
        assert result.exit_code == 0
        assert store.list_prompts() == []

    def test_rm_confirmation_declined(self, tmp_path, monkeypatch):
        store = make_store_with_prompt(tmp_path)
        monkeypatch.chdir(tmp_path)
        result = CliRunner().invoke(cli, ["rm", "greeting"], input="n\n")
        assert result.exit_code != 0
        assert store.list_prompts() == ["greeting"]

    def test_rm_missing_prompt(self, tmp_path, monkeypatch):
        store = PromptStore(tmp_path)
        store.init()
        monkeypatch.chdir(tmp_path)
        result = CliRunner().invoke(cli, ["rm", "nope", "-y"])
        assert result.exit_code == 1
        assert "not found" in result.output

    def test_rm_other_prompts_untouched(self, tmp_path, monkeypatch):
        store = make_store_with_prompt(tmp_path)
        store.add("other", "keep me")
        monkeypatch.chdir(tmp_path)
        result = CliRunner().invoke(cli, ["rm", "greeting", "-y"])
        assert result.exit_code == 0
        assert store.list_prompts() == ["other"]
        assert store.get_version("other").content == "keep me"


class TestShowRollbackIntegration:
    def test_show_after_rollback(self, tmp_path, monkeypatch):
        make_store_with_prompt(tmp_path)
        monkeypatch.chdir(tmp_path)
        runner = CliRunner()
        runner.invoke(cli, ["rollback", "greeting", "1"])
        result = runner.invoke(cli, ["show", "greeting", "--raw"])
        assert result.output == "first version"

    def test_help_lists_new_commands(self):
        result = CliRunner().invoke(cli, ["--help"])
        assert "show" in result.output
        assert "rollback" in result.output
        assert "rm" in result.output
