"""CLI interface for promptdiff."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table

from promptdiff import __version__
from promptdiff.changelog import ChangelogGenerator
from promptdiff.diff import PromptDiff
from promptdiff.eval import PromptEvaluator, TestCase
from promptdiff.registry import PromptRegistry
from promptdiff.store import PromptStore

console = Console()


def _get_store() -> PromptStore:
    return PromptStore(Path.cwd())


@click.group()
@click.version_option(version=__version__, package_name="llm-promptdiff")
def cli() -> None:
    """promptdiff - Git-style version control for LLM prompts."""
    pass


@cli.command()
def init() -> None:
    """Initialize a new promptdiff repository."""
    store = _get_store()
    if store.initialized:
        console.print("[yellow]Already initialized.[/yellow]")
        return
    path = store.init()
    console.print(f"[green]Initialized promptdiff in {path}[/green]")


@cli.command()
@click.argument("name")
@click.option("-m", "--message", default="", help="Version message")
@click.option("-f", "--file", "file_path", type=click.Path(exists=True), help="Read prompt from file")
@click.option("-t", "--tag", multiple=True, help="Tags for the prompt")
def add(name: str, message: str, file_path: str | None, tag: tuple[str, ...]) -> None:
    """Add a new prompt version."""
    store = _get_store()

    if file_path:
        content = Path(file_path).read_text()
    else:
        if sys.stdin.isatty():
            console.print("Enter prompt content (Ctrl+D to finish):")
        content = sys.stdin.read()

    if not content.strip():
        console.print("[red]Empty prompt content.[/red]")
        raise SystemExit(1)

    info = store.add(name, content, message=message)

    if tag:
        registry = PromptRegistry(store)
        # Merge with existing tags: adding a new version must not silently
        # wipe tags assigned earlier.
        registry.add_tags(name, list(tag))

    console.print(
        f"[green]Added {name} v{info.version}[/green] \\[{info.content_hash}]"
    )


@cli.command("diff")
@click.argument("name")
@click.argument("v1", type=int, required=False)
@click.argument("v2", type=int, required=False)
def diff_cmd(name: str, v1: int | None, v2: int | None) -> None:
    """Show diff between two prompt versions.

    With both versions omitted, compares the previous version to the
    latest. With only V1 given, compares V1 to the latest.
    """
    store = _get_store()
    differ = PromptDiff()

    try:
        latest = store.get_version(name)
    except (FileNotFoundError, ValueError, RuntimeError) as exc:
        console.print(f"[red]{exc}[/red]")
        raise SystemExit(1)

    if v1 is None:
        if latest.version < 2:
            console.print(
                f"[yellow]'{name}' has only one version; nothing to diff.[/yellow]"
            )
            return
        v1 = latest.version - 1
    if v2 is None:
        v2 = latest.version

    try:
        old = store.get_version(name, v1)
        new = store.get_version(name, v2)
    except (FileNotFoundError, ValueError) as exc:
        console.print(f"[red]{exc}[/red]")
        raise SystemExit(1)

    result = differ.full_diff(old.content, new.content, v1, v2)

    console.print(f"\n[bold]Diff: {name} v{v1} -> v{v2}[/bold]\n")

    for line in result.lines:
        if line.tag == "equal":
            console.print(f"  {(line.old_line or '').rstrip()}")
        elif line.tag == "delete":
            console.print(f"[red]- {(line.old_line or '').rstrip()}[/red]")
        elif line.tag == "insert":
            console.print(f"[green]+ {(line.new_line or '').rstrip()}[/green]")

    console.print()
    console.print(f"[bold]Text similarity:[/bold]  {result.similarity_ratio:.1%}")
    if result.semantic_similarity is not None:
        console.print(
            f"[bold]Semantic similarity:[/bold] {result.semantic_similarity:.1%}"
        )
    console.print(
        f"[bold]Changes:[/bold] [green]+{result.stats['additions']}[/green] "
        f"[red]-{result.stats['deletions']}[/red]"
    )


@cli.command()
@click.argument("name")
def log(name: str) -> None:
    """Show version history for a prompt."""
    store = _get_store()
    versions = store.list_versions(name)

    if not versions:
        console.print(f"[yellow]No versions found for '{name}'[/yellow]")
        return

    table = Table(title=f"Prompt: {name}")
    table.add_column("Version", style="cyan", justify="right")
    table.add_column("Hash", style="dim")
    table.add_column("Date", style="green")
    table.add_column("Message")

    for v in reversed(versions):
        table.add_row(
            f"v{v.version}",
            v.content_hash,
            v.timestamp[:10] if v.timestamp else "",
            v.message or "-",
        )

    console.print(table)


@cli.command("show")
@click.argument("name")
@click.option("-v", "--version", "version", type=int, default=None, help="Version to show (default: latest)")
@click.option("--raw", is_flag=True, help="Print content only, no header. Useful for piping.")
def show_cmd(name: str, version: int | None, raw: bool) -> None:
    """Print the content of a prompt version.

    Shows the latest version unless -v is given. Use --raw to pipe the
    prompt text into other tools without any decoration.
    """
    store = _get_store()
    try:
        info = store.get_version(name, version)
    except (FileNotFoundError, ValueError) as exc:
        console.print(f"[red]{exc}[/red]")
        raise SystemExit(1)

    if raw:
        click.echo(info.content, nl=False)
        return

    console.print(f"[bold cyan]{name} v{info.version}[/bold cyan] \\[{info.content_hash}]")
    if info.message:
        console.print(f"[dim]{info.message}[/dim]")
    if info.timestamp:
        console.print(f"[dim]{info.timestamp}[/dim]")
    console.print()
    click.echo(info.content)


@cli.command("rollback")
@click.argument("name")
@click.argument("version", type=int)
@click.option("-m", "--message", default="", help="Message for the new version")
def rollback_cmd(name: str, version: int, message: str) -> None:
    """Restore an old version as a new latest version.

    Creates a new version whose content matches VERSION, keeping full
    history intact, just like git revert.
    """
    store = _get_store()
    try:
        old = store.get_version(name, version)
        latest = store.get_version(name)
    except (FileNotFoundError, ValueError) as exc:
        console.print(f"[red]{exc}[/red]")
        raise SystemExit(1)

    if latest.content == old.content:
        console.print(
            f"[yellow]v{latest.version} already matches v{version}. Nothing to do.[/yellow]"
        )
        return

    info = store.add(name, old.content, message=message or f"Rollback to v{version}")
    console.print(
        f"[green]Rolled back {name} to v{version} as new v{info.version}[/green] \\[{info.content_hash}]"
    )


@cli.command("rm")
@click.argument("name")
@click.option("-y", "--yes", is_flag=True, help="Skip confirmation prompt.")
def rm_cmd(name: str, yes: bool) -> None:
    """Delete a prompt and all its versions permanently."""
    store = _get_store()
    if not yes:
        click.confirm(
            f"Delete '{name}' and all its versions? This cannot be undone",
            abort=True,
        )
    try:
        store.delete_prompt(name)
    except FileNotFoundError as exc:
        console.print(f"[red]{exc}[/red]")
        raise SystemExit(1)
    console.print(f"[green]Deleted prompt '{name}'.[/green]")


@cli.command("list")
def list_cmd() -> None:
    """List all tracked prompts."""
    store = _get_store()
    prompts = store.list_prompts()

    if not prompts:
        console.print("[yellow]No prompts tracked yet.[/yellow]")
        return

    table = Table(title="Tracked Prompts")
    table.add_column("Name", style="cyan")
    table.add_column("Versions", justify="right")
    table.add_column("Latest", style="green")

    registry = PromptRegistry(store)

    for info in registry.list_all():
        table.add_row(info["name"], str(info["total_versions"]), f"v{info['latest_version']}")

    console.print(table)


@cli.command()
@click.argument("name")
@click.option("-n", "--last", type=int, default=None, help="Only last N versions")
def changelog(name: str, last: int | None) -> None:
    """Generate changelog for a prompt."""
    store = _get_store()
    gen = ChangelogGenerator(store)
    output = gen.generate(name, last_n=last)
    console.print(output)


@cli.command("eval")
@click.argument("name")
@click.argument("version", type=int)
def eval_cmd(name: str, version: int) -> None:
    """Evaluate a prompt version (demo: self-test mode).

    NOTE: This is a demo command that evaluates the prompt against itself,
    so it will always score ~100%. For real evaluation, use the Python API
    with custom test cases and an LLM runner. See the README for details.
    """
    store = _get_store()
    v = store.get_version(name, version)

    # Demo: simple format-based evaluation
    evaluator = PromptEvaluator()
    test_cases = [
        TestCase(
            name="basic_format",
            input_vars={},
            expected=v.content,
            weight=1.0,
        ),
    ]

    result = evaluator.evaluate(name, version, v.content, test_cases)
    console.print(f"\n[bold]Eval: {name} v{version}[/bold]")
    console.print(f"Mean score: {result.mean_score:.1%}")
    console.print(f"Passed: {'[green]Yes[/green]' if result.passed else '[red]No[/red]'}")


@cli.command("export")
@click.argument("name", required=False)
@click.option("-o", "--output", "output_path", type=click.Path(), help="Output file path")
@click.option("--format", "fmt", type=click.Choice(["json", "jsonl"]), default="json", help="Output format")
def export_cmd(name: str | None, output_path: str | None, fmt: str) -> None:
    """Export prompt versions to JSON or JSONL for backup and sharing.

    If NAME is given, exports only that prompt. Otherwise exports all prompts.
    """
    store = _get_store()

    prompts_to_export = [name] if name else store.list_prompts()

    if not prompts_to_export:
        console.print("[yellow]No prompts to export.[/yellow]")
        return

    registry = PromptRegistry(store)

    export_data: list[dict] = []
    for pname in prompts_to_export:
        try:
            versions = store.list_versions(pname)
        except FileNotFoundError:
            console.print(f"[red]Prompt '{pname}' not found.[/red]")
            raise SystemExit(1)

        tags = registry.get_tags(pname)
        prompt_record = {
            "name": pname,
            "tags": tags,
            "versions": [
                {
                    "version": v.version,
                    "content": v.content,
                    "message": v.message,
                    "timestamp": v.timestamp,
                    "content_hash": v.content_hash,
                    "metadata": v.metadata,
                }
                for v in versions
            ],
        }
        export_data.append(prompt_record)

    if fmt == "json":
        output = json.dumps(export_data, indent=2)
    else:
        output = "\n".join(json.dumps(record) for record in export_data)

    if output_path:
        Path(output_path).write_text(output)
        console.print(f"[green]Exported {len(export_data)} prompt(s) to {output_path}[/green]")
    else:
        click.echo(output)


@cli.command("search")
@click.argument("query")
@click.option("--tag", "-t", "tag_filter", default=None, help="Filter by tag")
@click.option("--content", "-c", "search_content", is_flag=True, help="Search within prompt content")
def search_cmd(query: str, tag_filter: str | None, search_content: bool) -> None:
    """Search prompts by name, tag, or content.

    By default, searches prompt names. Use --content to also search
    within prompt text. Use --tag to filter by tag.
    """
    store = _get_store()
    registry = PromptRegistry(store)

    prompts = store.list_prompts()
    if not prompts:
        console.print("[yellow]No prompts tracked yet.[/yellow]")
        return

    results: list[dict] = []
    query_lower = query.lower()

    for pname in prompts:
        tags = registry.get_tags(pname)

        # Tag filter: skip if tag doesn't match
        if tag_filter and tag_filter not in tags:
            continue

        matched = False
        match_reason = ""

        # Name match
        if query_lower in pname.lower():
            matched = True
            match_reason = "name"

        # Tag match
        if not matched and any(query_lower in t.lower() for t in tags):
            matched = True
            match_reason = "tag"

        # Content match (only if flag set)
        if not matched and search_content:
            try:
                versions = store.list_versions(pname)
                for v in versions:
                    if query_lower in v.content.lower():
                        matched = True
                        match_reason = f"content (v{v.version})"
                        break
            except FileNotFoundError:
                pass

        if matched:
            meta = store._read_meta(pname)
            results.append({
                "name": pname,
                "match": match_reason,
                "versions": len(meta.get("versions", [])),
                "tags": tags,
            })

    if not results:
        console.print(f"[yellow]No prompts matching '{query}'.[/yellow]")
        return

    table = Table(title=f"Search: '{query}'")
    table.add_column("Name", style="cyan")
    table.add_column("Match", style="green")
    table.add_column("Versions", justify="right")
    table.add_column("Tags", style="dim")

    for r in results:
        table.add_row(
            r["name"],
            r["match"],
            str(r["versions"]),
            ", ".join(r["tags"]) if r["tags"] else "-",
        )

    console.print(table)


@cli.command("import")
@click.argument("file_path", type=click.Path(exists=True))
@click.option("--merge", is_flag=True, help="Merge with existing prompts instead of skipping")
def import_cmd(file_path: str, merge: bool) -> None:
    """Import prompts from a JSON export file.

    Reads a file created by 'promptdiff export' and adds the prompts
    to the current store. Existing prompts are skipped unless --merge is set.
    """
    store = _get_store()
    registry = PromptRegistry(store)

    raw = Path(file_path).read_text()

    # Support both JSON array and JSONL
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            data = [data]
    except json.JSONDecodeError:
        data = []
        for line in raw.strip().splitlines():
            line = line.strip()
            if line:
                data.append(json.loads(line))

    imported = 0
    skipped = 0

    for record in data:
        pname = record["name"]
        existing = store.list_prompts()
        if pname in existing and not merge:
            console.print(f"[yellow]Skipping '{pname}' (already exists, use --merge to add versions)[/yellow]")
            skipped += 1
            continue

        for v in record.get("versions", []):
            store.add(
                pname,
                v["content"],
                message=v.get("message", ""),
                metadata=v.get("metadata"),
            )
            imported += 1

        if record.get("tags"):
            registry.set_tags(pname, record["tags"])

    console.print(f"[green]Imported {imported} version(s), skipped {skipped} prompt(s).[/green]")


@cli.command("track")
@click.argument("name")
@click.argument("file_path", type=click.Path(exists=True))
def track_cmd(name: str, file_path: str) -> None:
    """Link a prompt NAME to a source FILE and snapshot it.

    Once tracked, 'promptdiff sync' (and the git pre-commit hook) will
    snapshot the file automatically whenever its content changes.
    """
    from promptdiff.tracking import FileTracker

    store = _get_store()
    tracker = FileTracker(store)
    try:
        result = tracker.track(name, file_path)
    except (RuntimeError, FileNotFoundError) as exc:
        console.print(f"[red]{exc}[/red]")
        raise SystemExit(1)

    if result.status == "added":
        console.print(f"[green]Tracking '{name}' -> {result.path} (snapshotted as v{result.version})[/green]")
    else:
        console.print(f"[green]Tracking '{name}' -> {result.path} (already at v{result.version})[/green]")


@cli.command("untrack")
@click.argument("name")
def untrack_cmd(name: str) -> None:
    """Stop tracking a prompt's source file. Stored versions are kept."""
    from promptdiff.tracking import FileTracker

    store = _get_store()
    tracker = FileTracker(store)
    try:
        tracker.untrack(name)
    except (RuntimeError, KeyError) as exc:
        msg = exc.args[0] if exc.args else str(exc)
        console.print(f"[red]{msg}[/red]")
        raise SystemExit(1)
    console.print(f"[green]Stopped tracking '{name}'.[/green]")


@cli.command("tracked")
def tracked_cmd() -> None:
    """List tracked files and their sync status."""
    from promptdiff.tracking import FileTracker

    store = _get_store()
    tracker = FileTracker(store)
    try:
        tracked = tracker.list_tracked()
    except RuntimeError as exc:
        console.print(f"[red]{exc}[/red]")
        raise SystemExit(1)

    if not tracked:
        console.print("[yellow]No tracked files. Use 'promptdiff track NAME FILE' to add one.[/yellow]")
        return

    table = Table(title="Tracked Files")
    table.add_column("Name", style="cyan")
    table.add_column("File", style="dim")
    table.add_column("Status")

    styles = {"in sync": "green", "modified": "yellow", "missing": "red", "new": "cyan"}
    for name, path in sorted(tracked.items()):
        status = tracker.status(name, path)
        table.add_row(name, path, f"[{styles[status]}]{status}[/{styles[status]}]")

    console.print(table)


@cli.command("sync")
@click.option("-m", "--message", default="", help="Message for new versions")
@click.option("--quiet", is_flag=True, help="Only print when versions are added. Hook-friendly.")
def sync_cmd(message: str, quiet: bool) -> None:
    """Snapshot all tracked files whose content changed.

    Never fails on missing files, so it is safe to run from a git
    pre-commit hook.
    """
    from promptdiff.tracking import FileTracker

    store = _get_store()
    tracker = FileTracker(store)
    try:
        results = tracker.sync(message=message)
    except RuntimeError as exc:
        console.print(f"[red]{exc}[/red]")
        raise SystemExit(1)

    if not results:
        if not quiet:
            console.print("[yellow]No tracked files. Use 'promptdiff track NAME FILE' to add one.[/yellow]")
        return

    added = 0
    for r in results:
        if r.status == "added":
            added += 1
            console.print(f"[green]Synced '{r.name}' -> v{r.version}[/green] ({r.path})")
        elif r.status == "missing":
            console.print(f"[red]Missing file for '{r.name}': {r.path}[/red]")
        elif not quiet:
            console.print(f"[dim]'{r.name}' unchanged (v{r.version})[/dim]")

    if not quiet or added:
        console.print(f"Synced {added} of {len(results)} tracked prompt(s).")


@cli.group("hook")
def hook_group() -> None:
    """Manage the git pre-commit hook that auto-syncs tracked prompts."""


@hook_group.command("install")
@click.option("--force", is_flag=True, help="Overwrite an existing foreign pre-commit hook.")
def hook_install_cmd(force: bool) -> None:
    """Install a pre-commit hook that runs 'promptdiff sync --quiet'."""
    from promptdiff import hooks

    try:
        path = hooks.install_hook(Path.cwd(), force=force)
    except (FileNotFoundError, FileExistsError) as exc:
        console.print(f"[red]{exc}[/red]")
        raise SystemExit(1)
    console.print(f"[green]Installed pre-commit hook at {path}[/green]")


@hook_group.command("uninstall")
def hook_uninstall_cmd() -> None:
    """Remove the promptdiff pre-commit hook."""
    from promptdiff import hooks

    try:
        path = hooks.uninstall_hook(Path.cwd())
    except (FileNotFoundError, RuntimeError) as exc:
        console.print(f"[red]{exc}[/red]")
        raise SystemExit(1)
    console.print(f"[green]Removed pre-commit hook at {path}[/green]")


@hook_group.command("status")
def hook_status_cmd() -> None:
    """Show whether the promptdiff pre-commit hook is installed."""
    from promptdiff import hooks

    if hooks.is_installed(Path.cwd()):
        console.print("[green]promptdiff pre-commit hook is installed.[/green]")
    else:
        console.print("[yellow]No promptdiff pre-commit hook installed. Run 'promptdiff hook install'.[/yellow]")


@cli.group("tag")
def tag_group() -> None:
    """View and manage prompt tags."""


def _get_registry_and_check(name: str) -> tuple["PromptRegistry", list[str]]:
    """Return (registry, tags) for *name*, exiting with an error if missing."""
    registry = PromptRegistry(_get_store())
    try:
        tags = registry.get_tags(name)
    except (FileNotFoundError, RuntimeError) as exc:
        console.print(f"[red]{exc}[/red]")
        raise SystemExit(1)
    return registry, tags


@tag_group.command("add")
@click.argument("name")
@click.argument("tags", nargs=-1, required=True)
def tag_add_cmd(name: str, tags: tuple[str, ...]) -> None:
    """Add one or more TAGS to a prompt."""
    registry, _ = _get_registry_and_check(name)
    registry.add_tags(name, list(tags))
    current = ", ".join(registry.get_tags(name))
    console.print(f"[green]Tags for '{name}':[/green] {current}")


@tag_group.command("rm")
@click.argument("name")
@click.argument("tags", nargs=-1, required=True)
def tag_rm_cmd(name: str, tags: tuple[str, ...]) -> None:
    """Remove one or more TAGS from a prompt."""
    registry, _ = _get_registry_and_check(name)
    removed = registry.remove_tags(name, list(tags))
    if not removed:
        console.print(f"[yellow]No matching tags on '{name}'.[/yellow]")
        return
    current = ", ".join(registry.get_tags(name)) or "(none)"
    console.print(
        f"[green]Removed {', '.join(removed)}.[/green] Tags for '{name}': {current}"
    )


@tag_group.command("list")
@click.argument("name", required=False)
def tag_list_cmd(name: str | None) -> None:
    """List tags for a prompt, or all tags across the store."""
    store = _get_store()
    registry = PromptRegistry(store)

    if name is not None:
        _, tags = _get_registry_and_check(name)
        if not tags:
            console.print(f"[yellow]'{name}' has no tags.[/yellow]")
            return
        for t in tags:
            click.echo(t)
        return

    try:
        prompts = store.list_prompts()
    except RuntimeError as exc:
        console.print(f"[red]{exc}[/red]")
        raise SystemExit(1)

    counts: dict[str, int] = {}
    for prompt_name in prompts:
        for t in registry.get_tags(prompt_name):
            counts[t] = counts.get(t, 0) + 1

    if not counts:
        console.print("[yellow]No tags in this store yet.[/yellow]")
        return

    table = Table(title="Tags")
    table.add_column("Tag", style="cyan")
    table.add_column("Prompts", justify="right")
    for t in sorted(counts):
        table.add_row(t, str(counts[t]))
    console.print(table)


@cli.command("ci-report")
@click.option(
    "--since",
    "since_str",
    required=True,
    help="ISO date or datetime (e.g. 2026-07-01 or 2026-07-01T12:00:00). "
    "Report versions created after this point.",
)
@click.option(
    "--format",
    "fmt",
    type=click.Choice(["markdown", "json"]),
    default="markdown",
    help="Output format.",
)
@click.option("--output", "-o", type=click.Path(), help="Write report to a file instead of stdout.")
@click.option(
    "--fail-below",
    type=click.FloatRange(0.0, 1.0),
    default=None,
    help="Exit 1 if any changed prompt's similarity to its base version falls below this value.",
)
def ci_report_cmd(since_str: str, fmt: str, output: str | None, fail_below: float | None) -> None:
    """Summarize prompt changes since a date, for PR comments and CI gates.

    Designed for CI: post the markdown output as a pull request comment
    or GitHub step summary, and use --fail-below to block merges when a
    prompt changed more than expected.
    """
    from promptdiff.ci import collect_changes, failing_changes, parse_since, render_json, render_markdown

    try:
        since = parse_since(since_str)
    except ValueError:
        console.print(f"[red]Invalid --since value: '{since_str}'. Use an ISO date like 2026-07-01.[/red]")
        raise SystemExit(1)

    store = _get_store()
    try:
        changes = collect_changes(store, since)
    except RuntimeError as exc:
        console.print(f"[red]{exc}[/red]")
        raise SystemExit(1)

    report = render_json(changes, since) if fmt == "json" else render_markdown(changes, since)

    if output:
        Path(output).write_text(report)
        console.print(f"[green]Report written to {output}[/green]")
    else:
        click.echo(report)

    if fail_below is not None:
        failures = failing_changes(changes, fail_below)
        if failures:
            for change in failures:
                console.print(
                    f"[red]FAIL: '{change.name}' similarity {change.similarity:.1%} "
                    f"is below threshold {fail_below:.1%} "
                    f"(v{change.base_version} -> v{change.head_version})[/red]",
                    highlight=False,
                )
            raise SystemExit(1)


if __name__ == "__main__":
    cli()
