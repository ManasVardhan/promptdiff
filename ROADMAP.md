# Roadmap: promptdiff

## Shipped

### 🧠 Semantic Similarity
`promptdiff semantic` scores how much a prompt's meaning changed between versions, with an offline lexical-semantic backend (word unigrams + bigrams + character trigrams, cosine similarity) and an optional OpenAI embeddings backend. Scores are bucketed into verdicts (equivalent, minor, moderate, major change) and `--fail-below` turns the command into a CI gate.

### 🪝 Git Hooks Integration
`promptdiff track` links prompt names to source files, `promptdiff sync` snapshots changed files, and `promptdiff hook install` adds a pre-commit hook that runs the sync automatically, ensuring prompt versions are never lost.

### 🤖 CI/CD Reports
`promptdiff ci-report --since <date>` generates markdown or JSON summaries of prompt changes for PR comments and step summaries, with `--fail-below` as a similarity gate for CI. Ships with a ready-to-copy GitHub Actions workflow in `examples/`.

### 📂 Prompt Registry
Local prompt registry to store, version, and retrieve prompt templates by name and tag across projects. A single shared store can be selected from any directory with the global `--store` flag or the `PROMPTDIFF_STORE` environment variable, `promptdiff list` shows tags and filters with `--tag`, and `export` / `import` move prompts between registries.

## v0.2 (Planned)

### 🌐 Remote Registry Backends
Push and pull prompts against a remote registry (git or HTTP backed) so teams can share one source of truth.

---

Have ideas? Open an issue or start a discussion!
