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

### 🌐 Remote Registry Backends
`promptdiff remote add / rm / list` plus `push` and `pull` sync prompts against remote registries: directory remotes (any path holding a store), git remotes (cloned, synced, committed, and pushed automatically), and pull-only HTTP remotes serving JSON exports. Sync is merge-based and idempotent, matching versions by content hash and merging tags as a union, so teams can share one source of truth without ever duplicating history.

## v0.2 (Planned)

### 🔐 Signed Prompt Releases
Tag a prompt version as a named release (e.g. `prod-2026-08`) with an integrity checksum, list and diff releases, and verify that a deployed prompt matches its release before serving traffic.

---

Have ideas? Open an issue or start a discussion!
