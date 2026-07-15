"""Tests for the CI reporting module and ci-report CLI command."""

import json
from datetime import datetime, timezone

import pytest
from click.testing import CliRunner

from promptdiff.ci import (
    PromptChange,
    collect_changes,
    failing_changes,
    parse_since,
    render_json,
    render_markdown,
)
from promptdiff.cli import cli
from promptdiff.store import PromptStore

CUTOFF = datetime(2026, 7, 1, tzinfo=timezone.utc)
BEFORE = "2026-06-15T10:00:00+00:00"
AFTER = "2026-07-10T10:00:00+00:00"


def _make_store(tmp_path) -> PromptStore:
    store = PromptStore(tmp_path)
    store.init()
    return store


def _add_version(store, name, content, message="", timestamp=""):
    info = store.add(name, content, message=message)
    if timestamp:
        meta = store._read_meta(name)
        for v_data in meta["versions"]:
            if v_data["version"] == info.version:
                v_data["timestamp"] = timestamp
        store._write_meta(name, meta)
    return info


class TestParseSince:
    def test_plain_date(self):
        result = parse_since("2026-07-01")
        assert result == CUTOFF

    def test_naive_datetime_assumed_utc(self):
        result = parse_since("2026-07-01T12:30:00")
        assert result.tzinfo is not None
        assert result.hour == 12

    def test_aware_datetime_preserved(self):
        result = parse_since("2026-07-01T00:00:00+02:00")
        assert result.utcoffset().total_seconds() == 7200

    def test_invalid_raises(self):
        with pytest.raises(ValueError):
            parse_since("not-a-date")


class TestCollectChanges:
    def test_unchanged_prompt_not_reported(self, tmp_path):
        store = _make_store(tmp_path)
        _add_version(store, "stable", "hello", timestamp=BEFORE)
        assert collect_changes(store, CUTOFF) == []

    def test_updated_prompt_reported(self, tmp_path):
        store = _make_store(tmp_path)
        _add_version(store, "greet", "hello world", timestamp=BEFORE)
        _add_version(store, "greet", "hello brave new world", "expanded", timestamp=AFTER)
        changes = collect_changes(store, CUTOFF)
        assert len(changes) == 1
        change = changes[0]
        assert change.name == "greet"
        assert change.base_version == 1
        assert change.head_version == 2
        assert not change.is_new
        assert 0 < change.similarity < 1
        assert change.messages == ["expanded"]

    def test_new_prompt_reported_as_new(self, tmp_path):
        store = _make_store(tmp_path)
        _add_version(store, "fresh", "brand new prompt", timestamp=AFTER)
        changes = collect_changes(store, CUTOFF)
        assert len(changes) == 1
        assert changes[0].is_new
        assert changes[0].base_version is None
        assert changes[0].similarity is None
        assert changes[0].additions > 0

    def test_multiple_new_versions_compare_base_to_head(self, tmp_path):
        store = _make_store(tmp_path)
        _add_version(store, "multi", "v1 content", timestamp=BEFORE)
        _add_version(store, "multi", "v2 content", "second", timestamp=AFTER)
        _add_version(store, "multi", "v3 content", "third", timestamp="2026-07-11T10:00:00+00:00")
        changes = collect_changes(store, CUTOFF)
        assert len(changes) == 1
        assert changes[0].base_version == 1
        assert changes[0].head_version == 3
        assert changes[0].messages == ["second", "third"]

    def test_version_without_message_skipped_in_messages(self, tmp_path):
        store = _make_store(tmp_path)
        _add_version(store, "quiet", "one", timestamp=BEFORE)
        _add_version(store, "quiet", "two", timestamp=AFTER)
        changes = collect_changes(store, CUTOFF)
        assert changes[0].messages == []

    def test_unparseable_timestamp_treated_as_old(self, tmp_path):
        store = _make_store(tmp_path)
        _add_version(store, "odd", "content", timestamp="not-a-timestamp")
        assert collect_changes(store, CUTOFF) == []

    def test_multiple_prompts_sorted_by_name(self, tmp_path):
        store = _make_store(tmp_path)
        _add_version(store, "zeta", "z content", timestamp=AFTER)
        _add_version(store, "alpha", "a content", timestamp=AFTER)
        changes = collect_changes(store, CUTOFF)
        assert [c.name for c in changes] == ["alpha", "zeta"]

    def test_uninitialized_store_raises(self, tmp_path):
        store = PromptStore(tmp_path)
        with pytest.raises(RuntimeError):
            collect_changes(store, CUTOFF)


class TestFailingChanges:
    def _change(self, similarity):
        return PromptChange(
            name="p",
            base_version=1,
            head_version=2,
            similarity=similarity,
            additions=1,
            deletions=1,
            messages=[],
        )

    def test_below_threshold_fails(self):
        assert failing_changes([self._change(0.3)], 0.5) != []

    def test_at_threshold_passes(self):
        assert failing_changes([self._change(0.5)], 0.5) == []

    def test_new_prompts_never_fail(self):
        new = PromptChange(
            name="n",
            base_version=None,
            head_version=1,
            similarity=None,
            additions=5,
            deletions=0,
            messages=[],
        )
        assert failing_changes([new], 0.9) == []


class TestRenderMarkdown:
    def test_no_changes(self):
        report = render_markdown([], CUTOFF)
        assert "No prompt changes detected." in report

    def test_table_and_messages(self, tmp_path):
        store = _make_store(tmp_path)
        _add_version(store, "greet", "hello world", timestamp=BEFORE)
        _add_version(store, "greet", "goodbye world", "flipped tone", timestamp=AFTER)
        _add_version(store, "fresh", "new one", timestamp=AFTER)
        report = render_markdown(collect_changes(store, CUTOFF), CUTOFF)
        assert "2 prompt(s) changed" in report
        assert "1 updated" in report
        assert "1 new" in report
        assert "| greet | v1 -> v2 |" in report
        assert "| fresh | new -> v1 | n/a" in report
        assert "- flipped tone" in report

    def test_since_label_in_header(self):
        report = render_markdown([], CUTOFF)
        assert "2026-07-01 00:00 UTC" in report


class TestRenderJson:
    def test_json_document(self, tmp_path):
        store = _make_store(tmp_path)
        _add_version(store, "greet", "hello", timestamp=BEFORE)
        _add_version(store, "greet", "hi there", timestamp=AFTER)
        doc = json.loads(render_json(collect_changes(store, CUTOFF), CUTOFF))
        assert doc["total_changes"] == 1
        assert doc["since"].startswith("2026-07-01")
        entry = doc["changes"][0]
        assert entry["name"] == "greet"
        assert entry["is_new"] is False
        assert entry["base_version"] == 1


class TestCliCiReport:
    def _setup(self, runner):
        runner.invoke(cli, ["init"])
        store = PromptStore(".")
        _add_version(store, "greet", "hello world", timestamp=BEFORE)
        _add_version(store, "greet", "totally different text now", "rewrite", timestamp=AFTER)
        return store

    def test_markdown_report(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            self._setup(runner)
            result = runner.invoke(cli, ["ci-report", "--since", "2026-07-01"])
            assert result.exit_code == 0
            assert "Prompt changes since" in result.output
            assert "greet" in result.output

    def test_json_report(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            self._setup(runner)
            result = runner.invoke(
                cli, ["ci-report", "--since", "2026-07-01", "--format", "json"]
            )
            assert result.exit_code == 0
            doc = json.loads(result.output)
            assert doc["total_changes"] == 1

    def test_output_file(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            self._setup(runner)
            result = runner.invoke(
                cli, ["ci-report", "--since", "2026-07-01", "-o", "report.md"]
            )
            assert result.exit_code == 0
            from pathlib import Path

            assert "Prompt changes since" in Path("report.md").read_text()

    def test_fail_below_triggers_exit_1(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            self._setup(runner)
            result = runner.invoke(
                cli, ["ci-report", "--since", "2026-07-01", "--fail-below", "0.99"]
            )
            assert result.exit_code == 1
            assert "FAIL" in result.output

    def test_fail_below_passes_when_similar_enough(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            self._setup(runner)
            result = runner.invoke(
                cli, ["ci-report", "--since", "2026-07-01", "--fail-below", "0.01"]
            )
            assert result.exit_code == 0

    def test_invalid_since(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            runner.invoke(cli, ["init"])
            result = runner.invoke(cli, ["ci-report", "--since", "bogus"])
            assert result.exit_code == 1
            assert "Invalid --since" in result.output

    def test_uninitialized_store(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            result = runner.invoke(cli, ["ci-report", "--since", "2026-07-01"])
            assert result.exit_code == 1
            assert "Not a promptdiff repository" in result.output

    def test_no_changes_message(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            runner.invoke(cli, ["init"])
            result = runner.invoke(cli, ["ci-report", "--since", "2026-07-01"])
            assert result.exit_code == 0
            assert "No prompt changes detected." in result.output
