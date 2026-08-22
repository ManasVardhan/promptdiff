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

### 🔐 Signed Prompt Releases
Tag a prompt version as a named release (e.g. `prod-2026-08`) with an integrity checksum, list and diff releases, and verify that a deployed prompt matches its release before serving traffic. Shipped as the `release` command group (`create`, `list`, `show`, `verify`, `diff`, `rm`) backed by `ReleaseManager`: full SHA-256 checksums stored in `.promptdiff/releases.json`, `verify --file/--stdin` as a deploy gate that exits 1 on mismatch and also detects store tampering, `--json-output` for scripting, and releases as pure pointers that never touch version history.

### 📊 Release Audit Trail
Every `verify` outcome is recorded with a timestamp in an append-only audit log (`.promptdiff/release_audit.jsonl`), and `promptdiff release history` shows the trail with per-release filtering, `--limit`, and `--json-output`. Teams can show when a deployed prompt was last proven to match its release. `ReleaseManager.history()` exposes the same data in Python, and `verify(..., record=False)` opts out of logging.

### ⏱️ Prompt Version Pinning in CI
`promptdiff pin add` locks a prompt at a version with its full SHA-256 checksum in a committable `promptdiff.lock`, and `promptdiff pin check` fails CI (exit 1) when any pinned prompt drifted to a newer version, was edited in place, or disappeared, closing the loop between releases and pull requests. `pin list`, `pin rm`, and `pin update` manage the lockfile, `--json-output` supports scripting, `--lockfile` selects an alternate path, and `PinManager` exposes the same operations in Python.

## v0.4 (Planned)

### 📦 Prompt Bundles
A `promptdiff bundle` command that packs a set of pinned prompts into a single signed archive for deployment, and unpacks or verifies it on the serving side, so services can ship exactly the reviewed prompt set as one artifact.

---

Have ideas? Open an issue or start a discussion!
