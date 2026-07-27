
<p align="center"><strong>Git-style diff and version control for LLM prompts</strong></p>

<p align="center">
  <a href="https://github.com/ManasVardhan/promptdiff/actions"><img src="https://img.shields.io/github/actions/workflow/status/ManasVardhan/promptdiff/ci.yml?branch=main&style=flat-square" alt="CI"></a>
  <a href="https://pypi.org/project/llm-promptdiff/"><img src="https://img.shields.io/pypi/v/llm-promptdiff?style=flat-square&color=blue" alt="PyPI"></a>
  <a href="https://pypi.org/project/llm-promptdiff/"><img src="https://img.shields.io/pypi/pyversions/llm-promptdiff?style=flat-square" alt="Python"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-green?style=flat-square" alt="License"></a>
</p>

---

## The Problem

**Prompts are code. Treat them like it.**

You iterate on prompts dozens of times. You tweak a system message, change a few words, restructure instructions. But you have no history, no way to compare versions, and no idea if the new version is actually better.

`promptdiff` fixes that. Track every version, see exactly what changed (both textually and semantically), evaluate regressions, and maintain a changelog. All from the command line.

## Features

- 📦 **Version Control** - Store and track every prompt version with messages and metadata
- 🔀 **Smart Diffs** - Line-level text diffs with additions, deletions, and similarity scores
- 🧠 **Semantic Similarity** - Offline lexical-semantic scoring with change verdicts, OpenAI embeddings optional, CI gate via --fail-below
- 🏷️ **Tags & Registry** - Organize prompts with tags, find them by name or label
- 📂 **Shared Registry** - Point every project at one store with `--store` or `PROMPTDIFF_STORE`
- 📊 **Evaluation** - Run prompt versions against test cases and score results
- 📋 **Changelog** - Auto-generate version history with diff stats
- 💻 **CLI First** - Beautiful terminal output powered by Rich

## Quick Start

```bash
pip install llm-promptdiff
```

### Initialize and start tracking

> **New here?** Start with the [Getting Started Guide](GETTING_STARTED.md).

```bash
# Initialize a promptdiff repo
promptdiff init

# Add your first prompt version
echo "Summarize this text: {text}" | promptdiff add summarizer -m "Initial version"

# Iterate on it
echo "You are an expert summarizer. Summarize the text below in 2 sentences.

Text: {text}

Summary:" | promptdiff add summarizer -m "Added role and structure"

# See what changed (defaults to previous vs latest)
promptdiff diff summarizer

# Or pick versions explicitly
promptdiff diff summarizer 1 2
```

### Terminal Output

```
Diff: summarizer v1 -> v2

- Summarize this text: {text}
+ You are an expert summarizer. Summarize the text below in 2 sentences.
+
+ Text: {text}
+
+ Summary:

Text similarity:       0.0%
Semantic similarity:   18.8%
Changes: +5 -1
```

```bash
# View version history
promptdiff log summarizer

# List all tracked prompts
promptdiff list

# Generate a changelog
promptdiff changelog summarizer

# Tag prompts and manage tags over time
promptdiff tag add summarizer prod reviewed
promptdiff tag rm summarizer reviewed
promptdiff tag list              # all tags with prompt counts
promptdiff tag list summarizer   # tags for one prompt
```

## Python API

```python
from promptdiff import PromptStore, PromptDiff, PromptRegistry

# Initialize
store = PromptStore(".")
store.init()

# Track versions
store.add("my-prompt", "Hello {name}", message="v1")
store.add("my-prompt", "Hi there, {name}!", message="More friendly")

# Compare
differ = PromptDiff()
v1 = store.get_version("my-prompt", 1)
v2 = store.get_version("my-prompt", 2)
result = differ.full_diff(v1.content, v2.content, 1, 2)

print(f"Similarity: {result.similarity_ratio:.1%}")
print(f"Semantic:   {result.semantic_similarity:.1%}")
```

## Version Control

Every prompt gets its own directory with numbered versions and metadata:

```
.promptdiff/
  prompts/
    summarizer/
      meta.json      # name, tags, version history
      v1.txt         # version 1 content
      v2.txt         # version 2 content
      v3.txt         # version 3 content
```

Each version stores a content hash, timestamp, message, and arbitrary metadata. Duplicate content is detected and skipped automatically.

Retrieve and restore versions from the CLI:

```bash
# Print the latest version (or -v 2 for a specific one)
promptdiff show summarizer

# Pipe raw prompt text into another tool
promptdiff show summarizer --raw | pbcopy

# Bad deploy? Restore v2 as a new version, history stays intact
promptdiff rollback summarizer 2 -m "revert tone experiment"

# Remove a prompt entirely (asks for confirmation without -y)
promptdiff rm old-prompt -y
```

## Shared Registry Across Projects

By default promptdiff uses the `.promptdiff/` store in the current directory. To share one prompt registry across every project, point commands at a common store with the global `--store` flag or the `PROMPTDIFF_STORE` environment variable (the flag wins when both are set):

```bash
# One-time setup of a central registry
promptdiff --store ~/prompt-registry init

# Use it from any project directory
promptdiff --store ~/prompt-registry add summarizer -t prod -f prompts/summarizer.txt

# Or set it once per shell / CI job and drop the flag
export PROMPTDIFF_STORE=~/prompt-registry
promptdiff list --tag prod
promptdiff show summarizer --raw
```

`promptdiff list` shows each prompt's tags, and `--tag` filters the listing, so a central registry stays navigable as it grows. Combined with `export` / `import` for backups, this completes the prompt registry workflow: store, version, and retrieve prompts by name and tag across projects.

## File Tracking and Git Hooks

Prompts usually live in source files. Link them once and promptdiff snapshots them automatically whenever they change:

```bash
# Link a prompt name to its source file (snapshots it immediately)
promptdiff track summarizer prompts/summarizer.txt

# See what is tracked and whether files drifted from the stored versions
promptdiff tracked

# Snapshot every tracked file whose content changed
promptdiff sync -m "tuned tone"

# Stop tracking (stored versions are kept)
promptdiff untrack summarizer
```

Install the git pre-commit hook and never lose a prompt version again. Every commit runs `promptdiff sync --quiet` first, so prompt edits are versioned alongside your code:

```bash
promptdiff hook install     # refuses to clobber a foreign hook unless --force
promptdiff hook status
promptdiff hook uninstall   # only removes hooks promptdiff created
```

Missing files are reported but never block a commit.

## CI Reports and PR Gates

Summarize prompt changes since a date and post the result to a pull request. Designed for CI pipelines:

```bash
# Markdown report of everything that changed since July 1
promptdiff ci-report --since 2026-07-01

# JSON for machine consumption
promptdiff ci-report --since 2026-07-01T12:00:00 --format json

# Write to a file (drop into $GITHUB_STEP_SUMMARY or a PR comment)
promptdiff ci-report --since 2026-07-01 -o report.md

# Gate the build: exit 1 if any prompt drifted below 40% similarity
promptdiff ci-report --since 2026-07-01 --fail-below 0.4
```

The report compares each prompt's last version at the reference point against its latest version: a summary table (versions, character-level similarity, line changes) plus the version messages written along the way. New prompts are listed but never fail the similarity gate.

A ready-to-copy GitHub Actions workflow that runs on every PR and publishes the report to the step summary lives in [`examples/github-actions-prompt-report.yml`](examples/github-actions-prompt-report.yml).

## Similarity Scoring

Beyond line-level text diffs, `promptdiff` computes similarity between versions:

- **Built-in**: Jaccard word-overlap similarity (zero dependencies)
- **Optional**: OpenAI embedding cosine similarity for true semantic comparison (`pip install llm-promptdiff[embeddings]`)

The built-in scorer measures word overlap, which is useful for detecting surface-level changes. For actual semantic similarity (detecting meaning changes), use the optional embeddings integration.

## Semantic Similarity

The `semantic` command scores how much a prompt's meaning changed between versions and buckets the score into a verdict (`equivalent`, `minor change`, `moderate change`, `major change`):

```bash
# Compare previous vs latest with the offline backend (default)
promptdiff semantic summarizer

# Compare explicit versions
promptdiff semantic summarizer 1 3

# True embedding similarity via OpenAI (needs the embeddings extra + OPENAI_API_KEY)
promptdiff semantic summarizer --backend openai --model text-embedding-3-small

# CI gate: exit 1 if the prompt drifted below 80% similarity
promptdiff semantic summarizer --fail-below 0.8

# Machine-readable output for scripts
promptdiff semantic summarizer --json-output
```

Two backends:

- **local** (default, offline, deterministic): cosine similarity over word unigrams, word bigrams, and character trigrams with sublinear TF weighting. Much stronger than plain word overlap: bigrams capture phrasing, character trigrams tolerate small spelling and inflection changes.
- **openai**: cosine similarity between real embeddings, for detecting deeper meaning changes. Requires `pip install 'llm-promptdiff[embeddings]'`.

The same scoring is available from Python:

```python
from promptdiff import compare_semantic

result = compare_semantic(old_text, new_text)          # local backend
print(result.similarity, result.verdict)

result = compare_semantic(old_text, new_text, backend="openai")
```

## Evaluation

Run prompt versions against test cases to catch regressions:

```python
from promptdiff.eval import PromptEvaluator, TestCase

evaluator = PromptEvaluator(
    runner=my_llm_runner,       # your function: (template, vars) -> output
    scorer=my_custom_scorer,    # your function: (output, expected) -> float
)

cases = [
    TestCase("short_text", {"text": "AI is cool."}, "AI is interesting."),
    TestCase("long_text", {"text": long_article}, expected_summary),
]

result = evaluator.evaluate("summarizer", 3, prompt_content, cases)
print(f"Score: {result.mean_score:.1%}")
```

Built-in scorers: `exact_match_scorer`, `contains_scorer`, `similarity_scorer`.

## Changelog

Auto-generate changelogs from your version history:

```bash
promptdiff changelog summarizer
```

```markdown
## v3 (2025-01-15)
**Added constraint to focus on facts**
- Text similarity: 92.3%
- Semantic similarity: 87.1%
- Changes: +2 -0

## v2 (2025-01-14)
**Improved with role and clearer instructions**
- Text similarity: 32.5%
- Semantic similarity: 54.2%
- Changes: +4 -1
```

## CI Integration

Add prompt regression checks to your CI pipeline:

```yaml
# .github/workflows/prompt-check.yml
- name: Check prompt quality
  run: |
    pip install llm-promptdiff
    promptdiff eval summarizer 3
```

Or use the Python API in your test suite:

```python
def test_prompt_similarity():
    """Ensure new version isn't too different from production."""
    store = PromptStore(".")
    differ = PromptDiff()
    v_prod = store.get_version("summarizer", 2)
    v_new = store.get_version("summarizer", 3)
    result = differ.full_diff(v_prod.content, v_new.content)
    assert result.similarity_ratio > 0.7, "Prompt changed too much!"
```

## CLI Reference

| Command | Description |
|---|---|
| `promptdiff init` | Initialize a new promptdiff repository |
| `promptdiff add <name> -m "msg"` | Add a new prompt version |
| `promptdiff show <name> [-v N] [--raw]` | Print a prompt version's content |
| `promptdiff diff <name> [v1] [v2]` | Show diff between versions (defaults to previous vs latest) |
| `promptdiff semantic <name> [v1] [v2] [--backend openai] [--fail-below X]` | Score semantic similarity with a change verdict |
| `promptdiff log <name>` | Show version history |
| `promptdiff rollback <name> <version>` | Restore an old version as a new latest |
| `promptdiff list [--tag T]` | List all tracked prompts, optionally filtered by tag |
| `promptdiff --store <dir> <command>` | Run any command against a shared store (or set `PROMPTDIFF_STORE`) |
| `promptdiff rm <name> [-y]` | Delete a prompt and all its versions |
| `promptdiff search <query> [--tag] [--content]` | Search prompts |
| `promptdiff tag add\|rm\|list [name] [tags...]` | View and manage prompt tags |
| `promptdiff changelog <name>` | Generate changelog |
| `promptdiff eval <name> <version>` | Evaluate a prompt version |
| `promptdiff export [name] [-o file]` | Export prompts to JSON or JSONL |
| `promptdiff import <file> [--merge]` | Import prompts from a backup |
| `promptdiff track <name> <file>` | Link a prompt to a source file and snapshot it |
| `promptdiff untrack <name>` | Stop tracking a file (versions are kept) |
| `promptdiff tracked` | List tracked files with sync status |
| `promptdiff sync [-m "msg"] [--quiet]` | Snapshot all tracked files that changed |
| `promptdiff hook install\|status\|uninstall` | Manage the git pre-commit auto-sync hook |
| `promptdiff ci-report --since <date> [--fail-below X]` | Markdown/JSON change report and CI similarity gate |

## License

MIT License. Copyright (c) 2025 Manas Vardhan.
