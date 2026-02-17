"""CLI interface for promptdiff."""

from __future__ import annotations

import sys
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table

from promptdiff.changelog import ChangelogGenerator
from promptdiff.diff import PromptDiff
from promptdiff.eval import PromptEvaluator, TestCase
from promptdiff.store import PromptStore

console = Console()


def _get_store() -> PromptStore:
    return PromptStore(Path.cwd())


@click.group()
@click.version_option(package_name="promptdiff")
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
        from promptdiff.registry import PromptRegistry
        registry = PromptRegistry(store)
        registry.set_tags(name, list(tag))

    console.print(
        f"[green]Added {name} v{info.version}[/green] [{info.content_hash}]"
    )


@cli.command("diff")
@click.argument("name")
@click.argument("v1", type=int)
@click.argument("v2", type=int)
def diff_cmd(name: str, v1: int, v2: int) -> None:
    """Show diff between two prompt versions."""
    store = _get_store()
    differ = PromptDiff()

    old = store.get_version(name, v1)
    new = store.get_version(name, v2)

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

    from promptdiff.registry import PromptRegistry
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


if __name__ == "__main__":
    cli()
