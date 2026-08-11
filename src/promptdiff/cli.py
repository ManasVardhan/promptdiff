"""CLI interface for promptdiff."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from promptdiff import __version__
from promptdiff.changelog import ChangelogGenerator
from promptdiff.diff import PromptDiff
from promptdiff.eval import PromptEvaluator, TestCase
from promptdiff.registry import PromptRegistry
from promptdiff.store import PromptStore

console = Console()

_store_root: Path | None = None


def _get_store() -> PromptStore:
    return PromptStore(_store_root or Path.cwd())


@click.group()
@click.version_option(version=__version__, package_name="llm-promptdiff")
@click.option(
    "-S",
    "--store",
    "store_path",
    type=click.Path(file_okay=False),
    envvar="PROMPTDIFF_STORE",
    default=None,
    help=(
        "Directory containing the .promptdiff store. Defaults to the current "
        "directory. Can also be set via the PROMPTDIFF_STORE environment "
        "variable to share one prompt registry across projects."
    ),
)
def cli(store_path: str | None) -> None:
    """promptdiff - Git-style version control for LLM prompts."""
    global _store_root
    _store_root = Path(store_path).expanduser() if store_path else None


def main() -> None:
    """Console entry point: render store errors cleanly instead of tracebacks."""
    try:
        cli()
    except RuntimeError as exc:
        console.print(f"[red]{exc}[/red]")
        raise SystemExit(1)


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


@cli.command("semantic")
@click.argument("name")
@click.argument("v1", type=int, required=False)
@click.argument("v2", type=int, required=False)
@click.option(
    "--backend",
    type=click.Choice(["local", "openai"]),
    default="local",
    help="Similarity backend: offline lexical-semantic (local) or OpenAI embeddings.",
)
@click.option(
    "--model",
    default="text-embedding-3-small",
    help="Embedding model name (openai backend only).",
)
@click.option(
    "--fail-below",
    type=click.FloatRange(0.0, 1.0),
    default=None,
    help="Exit 1 if similarity falls below this value. Useful as a CI gate.",
)
@click.option("--json-output", is_flag=True, help="Output machine-readable JSON.")
def semantic_cmd(
    name: str,
    v1: int | None,
    v2: int | None,
    backend: str,
    model: str,
    fail_below: float | None,
    json_output: bool,
) -> None:
    """Score semantic similarity between two prompt versions.

    With both versions omitted, compares the previous version to the
    latest. With only V1 given, compares V1 to the latest. The default
    backend runs fully offline; use --backend openai for true embedding
    similarity (requires the embeddings extra and OPENAI_API_KEY).
    """
    from promptdiff.semantic import compare_semantic

    store = _get_store()

    try:
        latest = store.get_version(name)
    except (FileNotFoundError, ValueError, RuntimeError) as exc:
        console.print(f"[red]{exc}[/red]")
        raise SystemExit(1)

    if v1 is None:
        if latest.version < 2:
            console.print(
                f"[yellow]'{name}' has only one version; nothing to compare.[/yellow]"
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

    try:
        comparison = compare_semantic(old.content, new.content, backend=backend, model=model)
    except ImportError as exc:
        # escape() keeps rich from eating the literal [embeddings] extra
        # in the install hint (bracket text parses as markup otherwise).
        console.print(f"[red]{escape(str(exc))}[/red]")
        raise SystemExit(1)
    except Exception as exc:  # API/auth/network errors from the openai backend
        console.print(f"[red]Semantic comparison failed: {escape(str(exc))}[/red]")
        raise SystemExit(1)

    below = fail_below is not None and comparison.similarity < fail_below

    if json_output:
        payload = {
            "name": name,
            "old_version": v1,
            "new_version": v2,
            "backend": comparison.backend,
            "model": comparison.model,
            "similarity": comparison.similarity,
            "verdict": comparison.verdict,
            "threshold": fail_below,
            "passed": not below,
        }
        click.echo(json.dumps(payload, indent=2))
    else:
        console.print(f"\n[bold]Semantic: {name} v{v1} -> v{v2}[/bold]\n")
        backend_label = comparison.backend + (f" ({comparison.model})" if comparison.model else "")
        console.print(f"[bold]Backend:[/bold]    {backend_label}")
        console.print(f"[bold]Similarity:[/bold] {comparison.similarity:.1%}")
        verdict_styles = {
            "equivalent": "green",
            "minor change": "green",
            "moderate change": "yellow",
            "major change": "red",
        }
        style = verdict_styles.get(comparison.verdict, "white")
        console.print(f"[bold]Verdict:[/bold]    [{style}]{comparison.verdict}[/{style}]")

    if below:
        assert fail_below is not None
        console.print(
            f"[red]FAIL: similarity {comparison.similarity:.1%} is below "
            f"threshold {fail_below:.1%}[/red]",
            highlight=False,
        )
        raise SystemExit(1)


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
@click.option("-t", "--tag", default=None, help="Only show prompts with this tag")
def list_cmd(tag: str | None) -> None:
    """List all tracked prompts, optionally filtered by tag."""
    store = _get_store()
    prompts = store.list_prompts()

    if not prompts:
        console.print("[yellow]No prompts tracked yet.[/yellow]")
        return

    registry = PromptRegistry(store)
    infos = registry.list_all()
    if tag is not None:
        infos = [info for info in infos if tag in info["tags"]]
        if not infos:
            console.print(f"[yellow]No prompts with tag '{tag}'.[/yellow]")
            return

    title = "Tracked Prompts" if tag is None else f"Tracked Prompts (tag: {tag})"
    table = Table(title=title)
    table.add_column("Name", style="cyan")
    table.add_column("Versions", justify="right")
    table.add_column("Latest", style="green")
    table.add_column("Tags", style="dim")

    for info in infos:
        table.add_row(
            info["name"],
            str(info["total_versions"]),
            f"v{info['latest_version']}",
            ", ".join(info["tags"]) if info["tags"] else "-",
        )

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


@cli.group("remote")
def remote_group() -> None:
    """Manage remote registries (directory, git, or HTTP backed)."""


@remote_group.command("add")
@click.argument("name")
@click.argument("url")
def remote_add_cmd(name: str, url: str) -> None:
    """Register a remote registry under NAME.

    URL may be a directory path (another promptdiff store root), a git
    URL ending in .git, or an http(s) URL serving a JSON export.
    """
    from promptdiff import remote

    store = _get_store()
    try:
        remote.add_remote(store, name, url)
    except remote.RemoteError as exc:
        console.print(f"[red]{exc}[/red]")
        raise SystemExit(1)
    backend = remote.detect_backend(url)
    console.print(f"[green]Added remote '{name}' -> {url} ({backend} backend)[/green]")


@remote_group.command("rm")
@click.argument("name")
def remote_rm_cmd(name: str) -> None:
    """Remove a remote registry by NAME."""
    from promptdiff import remote

    store = _get_store()
    try:
        url = remote.remove_remote(store, name)
    except remote.RemoteError as exc:
        console.print(f"[red]{exc}[/red]")
        raise SystemExit(1)
    console.print(f"[green]Removed remote '{name}' ({url}).[/green]")


@remote_group.command("list")
def remote_list_cmd() -> None:
    """List configured remotes."""
    from promptdiff import remote

    store = _get_store()
    remotes = remote.load_remotes(store)
    if not remotes:
        console.print(
            "[yellow]No remotes configured. Use 'promptdiff remote add NAME URL'.[/yellow]"
        )
        return

    table = Table(title="Remotes")
    table.add_column("Name", style="cyan")
    table.add_column("URL", style="dim")
    table.add_column("Backend", style="green")
    for name in sorted(remotes):
        table.add_row(name, remotes[name], remote.detect_backend(remotes[name]))
    console.print(table)


def _print_sync_results(results: list, direction: str) -> None:
    """Render SyncResult rows and a summary line for push/pull."""
    styles = {"new": "cyan", "updated": "green", "up to date": "dim"}
    added_total = 0
    for r in results:
        style = styles.get(r.status, "white")
        suffix = f" (+{r.added_versions} version(s))" if r.added_versions else ""
        console.print(f"[{style}]{r.status}[/{style}]  {r.name}{suffix}")
        added_total += r.added_versions
    console.print(
        f"{direction} {added_total} new version(s) across {len(results)} prompt(s)."
    )


@cli.command("push")
@click.argument("remote_name", default="origin")
@click.option("-p", "--prompt", "prompts", multiple=True, help="Only push these prompts.")
def push_cmd(remote_name: str, prompts: tuple[str, ...]) -> None:
    """Push local prompts to a remote registry (default remote: origin).

    Sync is merge-based and idempotent: versions are matched by content
    hash, so pushing twice never duplicates history.
    """
    from promptdiff import remote

    store = _get_store()
    try:
        results = remote.push(store, remote_name, list(prompts) or None)
    except remote.RemoteError as exc:
        console.print(f"[red]{exc}[/red]")
        raise SystemExit(1)

    if not results:
        console.print("[yellow]No prompts to push.[/yellow]")
        return
    _print_sync_results(results, "Pushed")


@cli.command("pull")
@click.argument("remote_name", default="origin")
@click.option("-p", "--prompt", "prompts", multiple=True, help="Only pull these prompts.")
def pull_cmd(remote_name: str, prompts: tuple[str, ...]) -> None:
    """Pull prompts from a remote registry (default remote: origin).

    Works with directory, git, and HTTP (JSON export) remotes. Existing
    local versions are never overwritten, new versions are appended.
    """
    from promptdiff import remote

    store = _get_store()
    try:
        results = remote.pull(store, remote_name, list(prompts) or None)
    except remote.RemoteError as exc:
        console.print(f"[red]{exc}[/red]")
        raise SystemExit(1)

    if not results:
        console.print("[yellow]Remote has no prompts to pull.[/yellow]")
        return
    _print_sync_results(results, "Pulled")


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


@cli.group()
def release() -> None:
    """Manage named, checksummed prompt releases."""


@release.command("create")
@click.argument("release_name")
@click.argument("prompt_name")
@click.option("-v", "--version", "version", type=int, default=None,
              help="Prompt version to release (default: latest).")
@click.option("-m", "--message", default="", help="Release message.")
@click.option("--force", is_flag=True, help="Replace the release if it already exists.")
def release_create(
    release_name: str, prompt_name: str, version: int | None, message: str, force: bool
) -> None:
    """Pin a prompt version as a named release with an integrity checksum.

    Example: promptdiff release create prod-2026-08 support-agent -v 3
    """
    from promptdiff.releases import ReleaseError, ReleaseManager

    manager = ReleaseManager(_get_store())
    try:
        rel = manager.create(
            release_name, prompt_name, version=version, message=message, force=force
        )
    except (ReleaseError, FileNotFoundError, ValueError, RuntimeError) as exc:
        console.print(f"[red]{exc}[/red]")
        raise SystemExit(1)
    console.print(
        f"[green]Released '{rel.name}': {rel.prompt} v{rel.version}[/green] "
        f"\\[sha256:{rel.checksum[:12]}]"
    )


@release.command("list")
@click.option("--json-output", is_flag=True, help="Output machine-readable JSON.")
def release_list(json_output: bool) -> None:
    """List all releases."""
    from promptdiff.releases import ReleaseManager

    manager = ReleaseManager(_get_store())
    try:
        releases = manager.list_releases()
    except RuntimeError as exc:
        console.print(f"[red]{exc}[/red]")
        raise SystemExit(1)

    if json_output:
        click.echo(json.dumps([r.to_dict() for r in releases], indent=2))
        return

    if not releases:
        console.print("[yellow]No releases yet. "
                      "Create one with: promptdiff release create NAME PROMPT[/yellow]")
        return

    table = Table(title="Releases")
    table.add_column("Release", style="cyan")
    table.add_column("Prompt", style="yellow")
    table.add_column("Version", justify="right")
    table.add_column("Checksum")
    table.add_column("Created")
    table.add_column("Message")
    for rel in releases:
        created = rel.created[:19].replace("T", " ") if rel.created else ""
        table.add_row(
            rel.name, rel.prompt, f"v{rel.version}",
            rel.checksum[:12], created, rel.message,
        )
    console.print(table)


@release.command("show")
@click.argument("release_name")
@click.option("--raw", is_flag=True, help="Print content only, no header. Useful for piping.")
def release_show(release_name: str, raw: bool) -> None:
    """Show a release and the prompt content it points at."""
    from promptdiff.releases import ReleaseError, ReleaseManager

    manager = ReleaseManager(_get_store())
    try:
        rel = manager.get(release_name)
        content = manager.content(release_name)
    except (ReleaseError, FileNotFoundError, ValueError, RuntimeError) as exc:
        console.print(f"[red]{exc}[/red]")
        raise SystemExit(1)

    if raw:
        click.echo(content, nl=False)
        return

    console.print(f"[bold cyan]{rel.name}[/bold cyan]: {rel.prompt} v{rel.version}")
    console.print(f"  Checksum: sha256:{rel.checksum}")
    if rel.created:
        console.print(f"  Created:  {rel.created}")
    if rel.message:
        console.print(f"  Message:  {rel.message}")
    console.print()
    console.print(escape(content))


@release.command("verify")
@click.argument("release_name")
@click.option("-f", "--file", "file_path", type=click.Path(exists=True),
              help="Deployed prompt file to check against the release checksum.")
@click.option("--stdin", "use_stdin", is_flag=True,
              help="Read the deployed prompt content from stdin instead of a file.")
@click.option("--json-output", is_flag=True, help="Output machine-readable JSON.")
def release_verify(
    release_name: str, file_path: str | None, use_stdin: bool, json_output: bool
) -> None:
    """Verify a release's integrity. Exits 1 on any mismatch.

    Always checks that the stored prompt version still matches the release
    checksum. With --file or --stdin, also checks that the deployed content
    matches, so CI or a deploy script can gate on it:

        promptdiff release verify prod-2026-08 --file deployed_prompt.txt
    """
    from promptdiff.releases import ReleaseError, ReleaseManager

    if file_path and use_stdin:
        console.print("[red]Use either --file or --stdin, not both.[/red]")
        raise SystemExit(1)

    deployed: str | None = None
    if file_path:
        deployed = Path(file_path).read_text()
    elif use_stdin:
        deployed = sys.stdin.read()

    manager = ReleaseManager(_get_store())
    try:
        result = manager.verify(release_name, deployed_content=deployed)
    except (ReleaseError, RuntimeError) as exc:
        console.print(f"[red]{exc}[/red]")
        raise SystemExit(1)

    if json_output:
        click.echo(json.dumps(result.to_dict(), indent=2))
        if not result.ok:
            raise SystemExit(1)
        return

    rel = result.release
    console.print(f"[bold]Verify '{rel.name}'[/bold] ({rel.prompt} v{rel.version})")
    store_msg = "[green]OK[/green]" if result.store_ok else "[red]MISMATCH[/red]"
    console.print(f"  Store integrity:   {store_msg}")
    if result.deployed_ok is not None:
        deployed_msg = "[green]OK[/green]" if result.deployed_ok else "[red]MISMATCH[/red]"
        console.print(f"  Deployed content:  {deployed_msg}")
    for problem in result.problems:
        console.print(f"  [red]{problem}[/red]")
    if not result.ok:
        raise SystemExit(1)


@release.command("diff")
@click.argument("release_a")
@click.argument("release_b")
def release_diff(release_a: str, release_b: str) -> None:
    """Show the diff between the contents of two releases."""
    from promptdiff.releases import ReleaseError, ReleaseManager

    manager = ReleaseManager(_get_store())
    try:
        rel_a = manager.get(release_a)
        rel_b = manager.get(release_b)
        content_a = manager.content(release_a)
        content_b = manager.content(release_b)
    except (ReleaseError, FileNotFoundError, ValueError, RuntimeError) as exc:
        console.print(f"[red]{exc}[/red]")
        raise SystemExit(1)

    differ = PromptDiff()
    result = differ.full_diff(content_a, content_b, rel_a.version, rel_b.version)

    console.print(
        f"\n[bold]Diff: {release_a} ({rel_a.prompt} v{rel_a.version}) -> "
        f"{release_b} ({rel_b.prompt} v{rel_b.version})[/bold]\n"
    )
    for line in result.lines:
        if line.tag == "equal":
            console.print(f"  {escape((line.old_line or '').rstrip())}")
        elif line.tag == "delete":
            console.print(f"[red]- {escape((line.old_line or '').rstrip())}[/red]")
        elif line.tag == "insert":
            console.print(f"[green]+ {escape((line.new_line or '').rstrip())}[/green]")
    console.print()
    console.print(f"[bold]Text similarity:[/bold]  {result.similarity_ratio:.1%}")
    console.print(
        f"[bold]Changes:[/bold] [green]+{result.stats['additions']}[/green] "
        f"[red]-{result.stats['deletions']}[/red]"
    )


@release.command("rm")
@click.argument("release_name")
@click.option("-y", "--yes", is_flag=True, help="Skip confirmation prompt.")
def release_rm(release_name: str, yes: bool) -> None:
    """Delete a release. The underlying prompt version is untouched."""
    from promptdiff.releases import ReleaseError, ReleaseManager

    manager = ReleaseManager(_get_store())
    try:
        rel = manager.get(release_name)
    except (ReleaseError, RuntimeError) as exc:
        console.print(f"[red]{exc}[/red]")
        raise SystemExit(1)

    if not yes and not click.confirm(
        f"Delete release '{release_name}' ({rel.prompt} v{rel.version})?"
    ):
        console.print("[yellow]Aborted.[/yellow]")
        return

    manager.delete(release_name)
    console.print(f"[green]Deleted release '{release_name}'.[/green]")


if __name__ == "__main__":
    cli()
