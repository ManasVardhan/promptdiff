# Roadmap: promptdiff

## Shipped

### 🧠 Semantic Similarity
`promptdiff semantic` scores how much a prompt's meaning changed between versions, with an offline lexical-semantic backend (word unigrams + bigrams + character trigrams, cosine similarity) and an optional OpenAI embeddings backend. Scores are bucketed into verdicts (equivalent, minor, moderate, major change) and `--fail-below` turns the command into a CI gate.

### 🪝 Git Hooks Integration
`promptdiff track` links prompt names to source files, `promptdiff sync` snapshots changed files, and `promptdiff hook install` adds a pre-commit hook that runs the sync automatically, ensuring prompt versions are never lost.

### 🤖 CI/CD Reports
`promptdiff ci-report --since <date>` generates markdown or JSON summaries of prompt changes for PR comments and step summaries, with `--fail-below` as a similarity gate for CI. Ships with a ready-to-copy GitHub Actions workflow in `examples/`.

## v0.2 (Planned)

### 📂 Prompt Registry
Local or remote prompt registry to store, version, and retrieve prompt templates by name and tag across projects.

---

Have ideas? Open an issue or start a discussion!
