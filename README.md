<p align="center">
  <img src="assets/hero.svg" alt="promptdiff" width="800">
</p>

<p align="center">
  <h1 align="center">🔀 promptdiff</h1>
  <p align="center"><strong>Git-style diff and version control for LLM prompts</strong></p>
</p>

<p align="center">
  <a href="https://github.com/yourusername/promptdiff/actions"><img src="https://img.shields.io/github/actions/workflow/status/yourusername/promptdiff/ci.yml?branch=main&style=flat-square" alt="CI"></a>
  <a href="https://pypi.org/project/promptdiff/"><img src="https://img.shields.io/pypi/v/promptdiff?style=flat-square&color=blue" alt="PyPI"></a>
  <a href="https://pypi.org/project/promptdiff/"><img src="https://img.shields.io/pypi/pyversions/promptdiff?style=flat-square" alt="Python"></a>
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
- 🧠 **Semantic Similarity** - Word-overlap similarity built in, OpenAI embeddings optional
- 🏷️ **Tags & Registry** - Organize prompts with tags, find them by name or label
- 📊 **Evaluation** - Run prompt versions against test cases and score results
- 📋 **Changelog** - Auto-generate version history with diff stats
- 💻 **CLI First** - Beautiful terminal output powered by Rich

## Quick Start

```bash
pip install promptdiff
```

### Initialize and start tracking

```bash
# Initialize a promptdiff repo
promptdiff init

# Add your first prompt version
echo "Summarize this text: {text}" | promptdiff add summarizer -m "Initial version"

# Iterate on it
echo "You are an expert summarizer. Summarize the text below in 2 sentences.

Text: {text}

Summary:" | promptdiff add summarizer -m "Added role and structure"

# See what changed
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

Text similarity:     32.5%
Semantic similarity:  54.2%
Changes: +4 -1
```

```bash
# View version history
promptdiff log summarizer

# List all tracked prompts
promptdiff list

# Generate a changelog
promptdiff changelog summarizer
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

## Semantic Diff

Beyond line-level text diffs, `promptdiff` computes semantic similarity between versions:

- **Built-in**: Jaccard word overlap (zero dependencies)
- **Optional**: OpenAI embedding cosine similarity (`pip install promptdiff[embeddings]`)

This tells you whether a rewrite actually changed the *meaning* or just the wording.

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
    pip install promptdiff
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
| `promptdiff diff <name> <v1> <v2>` | Show diff between versions |
| `promptdiff log <name>` | Show version history |
| `promptdiff list` | List all tracked prompts |
| `promptdiff changelog <name>` | Generate changelog |
| `promptdiff eval <name> <version>` | Evaluate a prompt version |

## License

MIT License. Copyright (c) 2025 Manas Vardhan.
