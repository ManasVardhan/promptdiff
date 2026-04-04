# AGENTS.md - promptdiff

## Overview
- Git-style diff and version control for LLM prompts. Track every version, see exactly what changed (textually and semantically), evaluate regressions with test cases, and maintain changelogs. All from the command line.
- For developers iterating on LLM prompts who need version history, comparison tooling, and regression detection.
- Core value: file-based versioning with deduplication, line-level + semantic diffs, evaluation framework with pluggable scorers, and Rich terminal output.

## Architecture

```
+--------------------+     +-------------------+     +-------------------+
|   CLI / Python API |     |   PromptStore     |     |  .promptdiff/     |
|                    | --> | (file-based VCS)  | --> |   prompts/        |
|  add, diff, log,   |     | add, get_version, |     |     my-prompt/    |
|  list, eval, etc.  |     | list_versions     |     |       meta.json   |
+--------------------+     +-------------------+     |       v1.txt      |
        |                          |                  |       v2.txt      |
        v                          v                  +-------------------+
+--------------------+     +-------------------+
|   PromptDiff       |     |  PromptRegistry   |
| text_diff(),       |     | tags, find_by_tag |
| semantic_similarity|     | list_all          |
| full_diff()        |     +-------------------+
+--------------------+
        |
        v
+--------------------+     +-------------------+
| ChangelogGenerator |     | PromptEvaluator   |
| generate()         |     | evaluate()        |
| generate_all()     |     | compare()         |
+--------------------+     +-------------------+
```

**Data flow:**
1. `promptdiff init` creates `.promptdiff/` directory with metadata
2. `promptdiff add <name>` stores prompt content as `vN.txt` with metadata in `meta.json`
3. Duplicate content is detected and skipped automatically (SHA-256 hash)
4. `promptdiff diff` computes line-level changes via `difflib.SequenceMatcher` and Jaccard word-overlap similarity
5. `PromptEvaluator` runs prompts through a pluggable runner + scorer against test cases

## Directory Structure

```
promptdiff/
  .github/workflows/ci.yml        -- CI: lint + test + coverage on Python 3.10-3.12
  src/promptdiff/
    __init__.py                    -- Public API re-exports, __version__ = "0.1.1"
    __main__.py                    -- python -m promptdiff entry
    store.py                       -- PromptStore: file-based version storage, VersionInfo
    diff.py                        -- PromptDiff: text diffs, Jaccard similarity, embedding similarity
    registry.py                    -- PromptRegistry: tags, metadata, find/list
    eval.py                        -- PromptEvaluator, TestCase, built-in scorers
    changelog.py                   -- ChangelogGenerator: Markdown changelog from version history
    cli.py                         -- Click CLI: init, add, diff, log, list, changelog, eval, export, search, import
  examples/
    workflow.py                    -- Example workflow script
  tests/                           -- 154 tests across 7 test files
    test_promptdiff.py             -- Core integration tests
    test_cli.py                    -- CLI command tests
    test_cli_extended.py           -- Extended CLI coverage
    test_diff_extended.py          -- Extended diff tests
    test_edge_cases.py             -- Edge case coverage
    test_nightly_apr01.py          -- Nightly regression tests
    test_nightly_apr03.py          -- Nightly regression tests
  pyproject.toml                   -- Hatchling build, metadata
  README.md                        -- Full docs with examples
  ROADMAP.md                       -- v0.2 plans
  CONTRIBUTING.md                  -- Contribution guidelines
  GETTING_STARTED.md               -- Quick start guide
  LICENSE                          -- MIT
```

## Core Concepts

- **PromptStore**: File-based version store. Each prompt gets a directory under `.promptdiff/prompts/<name>/` with `meta.json` (metadata, version list, tags) and `vN.txt` files. Duplicate content is detected by SHA-256 hash.
- **VersionInfo**: Metadata for a single version: version number, content, message, timestamp, content_hash, metadata dict.
- **PromptDiff**: Diff engine. `text_diff()` uses `difflib.SequenceMatcher` for line-level diffs. `semantic_similarity()` uses Jaccard word overlap. `embedding_similarity()` uses OpenAI embeddings (optional). `full_diff()` combines both.
- **DiffResult**: Contains lines (list of DiffLine), similarity_ratio, semantic_similarity, stats (additions, deletions, modifications).
- **DiffLine**: Single line in a diff. Tag is one of: equal, insert, delete, replace.
- **PromptRegistry**: High-level layer on top of PromptStore for tags and metadata management. `find_by_tag()`, `list_all()`, `set_tags()`, `get_tags()`.
- **PromptEvaluator**: Runs prompt templates through a pluggable runner function against TestCase objects. Scores with pluggable scorer. Returns EvalResult with mean/weighted scores.
- **TestCase** (alias `PromptTestCase`): name, input_vars dict, expected output, weight.
- **Built-in scorers**: `exact_match_scorer`, `contains_scorer`, `similarity_scorer` (Jaccard).
- **ChangelogGenerator**: Generates Markdown changelogs from version history with diff stats.

## API Reference

### PromptStore
```python
class PromptStore:
    def __init__(self, root: str | Path = ".")
    def init(self) -> Path
    @property initialized -> bool
    def add(self, name, content, message="", metadata=None) -> VersionInfo
    def get_version(self, name, version=None) -> VersionInfo  # None = latest
    def list_versions(self, name) -> list[VersionInfo]
    def list_prompts(self) -> list[str]
    def delete_prompt(self, name) -> None
```

### PromptDiff
```python
class PromptDiff:
    def text_diff(self, old_text, new_text, old_version=0, new_version=0) -> DiffResult
    def semantic_similarity(self, text_a, text_b) -> float  # Jaccard
    def embedding_similarity(self, text_a, text_b, model="text-embedding-3-small") -> float
    def full_diff(self, old_text, new_text, old_version=0, new_version=0) -> DiffResult
    def unified_diff(self, old_text, new_text, old_label="old", new_label="new") -> str
```

### PromptEvaluator
```python
class PromptEvaluator:
    def __init__(self, runner=None, scorer=None)
    def evaluate(self, prompt_name, version, content, test_cases) -> EvalResult
    def compare(self, results: list[EvalResult]) -> dict  # versions + best_version
```

### PromptRegistry
```python
class PromptRegistry:
    def register(self, name, content, message="", tags=None, metadata=None) -> int
    def get(self, name, version=None) -> str
    def set_tags(self, name, tags) -> None
    def get_tags(self, name) -> list[str]
    def add_tags(self, name, tags) -> None
    def find_by_tag(self, tag) -> list[str]
    def list_all(self) -> list[dict]
```

### ChangelogGenerator
```python
class ChangelogGenerator:
    def generate(self, name, last_n=None) -> str  # Markdown
    def generate_all(self) -> str
```

## CLI Commands

```bash
# Initialize a promptdiff repository
promptdiff init

# Add a new prompt version (from stdin or file)
echo "Summarize: {text}" | promptdiff add summarizer -m "Initial version"
promptdiff add summarizer -m "v2" -f prompt.txt -t production -t summarization

# Show diff between versions
promptdiff diff summarizer 1 2

# Show version history
promptdiff log summarizer

# List all tracked prompts
promptdiff list

# Generate changelog
promptdiff changelog summarizer
promptdiff changelog summarizer -n 5  # last 5 versions only

# Evaluate a prompt version (demo mode)
promptdiff eval summarizer 3

# Export prompts to JSON/JSONL
promptdiff export summarizer -o backup.json
promptdiff export --format jsonl -o all.jsonl

# Import prompts from JSON
promptdiff import backup.json
promptdiff import backup.json --merge

# Search prompts by name, tag, or content
promptdiff search "summar"
promptdiff search "summar" --tag production
promptdiff search "keyword" --content

# Version
promptdiff --version
```

## Configuration

- **Store location**: `.promptdiff/` directory in the working directory
- **Store structure**: `prompts/<name>/meta.json` + `vN.txt` files
- **No env vars** for core functionality
- **Embeddings** (optional): Set `OPENAI_API_KEY` env var, install `pip install llm-promptdiff[embeddings]`

## Testing

```bash
pip install -e ".[dev]"
pytest --cov=promptdiff -v
```

- **154 tests** across 7 test files
- All tests use temporary directories (no persistent state)
- Located in `tests/`

## Dependencies

- **click>=8.0**: CLI framework
- **rich>=13.0**: Terminal rendering
- **numpy>=1.24**: Array operations (used minimally)
- **openai>=1.0** (optional, `[embeddings]` extra): Embedding-based semantic similarity
- **Python >=3.10**

## CI/CD

- **GitHub Actions** (`.github/workflows/ci.yml`)
- Matrix: Python 3.10, 3.11, 3.12
- Steps: install, ruff lint, pytest with coverage, codecov upload on 3.12
- Triggers: push/PR to main

## Current Status

- **Version**: 0.1.1
- **Published on PyPI**: yes (`pip install llm-promptdiff`)
- **What works**: Full version control (init, add, get, list, delete), text diffs with similarity scoring, Jaccard semantic similarity, prompt evaluation with pluggable runners/scorers, changelog generation, export/import (JSON/JSONL), search, tag management
- **Known limitations**: Embedding similarity requires OpenAI API key and extra install. Eval CLI command is demo-only (self-test mode). No git hooks integration yet.
- **Roadmap (v0.2)**: Git hooks integration, CI/CD GitHub Action, semantic similarity with embeddings, prompt registry

## Development Guide

```bash
git clone https://github.com/manasvardhan/promptdiff.git
cd promptdiff
pip install -e ".[dev]"
pytest
```

- **Build system**: Hatchling
- **Source layout**: `src/promptdiff/`
- **Adding a new CLI command**: Add `@cli.command()` in `cli.py`
- **Adding a new scorer**: Implement the `Scorer` protocol (callable with `(output, expected) -> float`)
- **Adding a new similarity method**: Add to `PromptDiff` class in `diff.py`
- **Code style**: Ruff, line length 100, target Python 3.10

## Git Conventions

- **Branch**: main
- **Commits**: Imperative style ("Add feature X", "Fix bug Y")
- Never use em dashes in commit messages or docs

## Context

- **Author**: Manas Vardhan (ManasVardhan on GitHub)
- **Part of**: A suite of AI agent tooling
- **Related repos**: llm-cost-guardian (cost tracking), agent-sentry (crash reporting), agent-replay (trace debugging), llm-shelter (safety guardrails), mcp-forge (MCP server scaffolding), bench-my-llm (benchmarking)
- **PyPI package**: `llm-promptdiff`
- **Import as**: `promptdiff`
