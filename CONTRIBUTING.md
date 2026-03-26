# Contributing to promptdiff

Thanks for wanting to contribute! Here's how to get started.

## Setup

```bash
git clone https://github.com/ManasVardhan/promptdiff.git
cd promptdiff
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## Running Tests

```bash
pytest -v
pytest --cov=promptdiff --cov-report=term-missing
```

## Code Style

We use [ruff](https://docs.astral.sh/ruff/) for linting and formatting:

```bash
ruff check src/ tests/
ruff format src/ tests/
```

## Pull Request Guidelines

1. Fork the repo and create a feature branch
2. Write tests for any new functionality
3. Make sure all tests pass (`pytest -v`)
4. Run `ruff check` and fix any issues
5. Keep commits focused and descriptive
6. Open a PR against `main`

## What to Work On

Check the [Issues](https://github.com/ManasVardhan/promptdiff/issues) page. Issues labeled `good-first-issue` are great starting points.

Some areas that could use help:

- **Prompt templates** (Jinja2, Mustache support)
- **More scoring functions** (BLEU, ROUGE, custom metrics)
- **Import/export** (LangChain hub, PromptLayer, etc.)
- **Git integration** (track prompts alongside code changes)
- **Web UI** (browser-based diff viewer)
- **Batch evaluation** (run test suites across multiple versions)

## Architecture

```
src/promptdiff/
    __init__.py      # Public API exports
    cli.py           # Click CLI commands
    diff.py          # Text diff + semantic similarity
    eval.py          # Prompt evaluation framework
    registry.py      # Tag-based prompt registry
    store.py         # File-based version store
    changelog.py     # Auto-generated changelogs
```

## Questions?

Open an issue or reach out to [@vardhan_manas](https://twitter.com/vardhan_manas).
