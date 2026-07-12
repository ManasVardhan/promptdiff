# Roadmap: promptdiff

## Shipped

### 🪝 Git Hooks Integration
`promptdiff track` links prompt names to source files, `promptdiff sync` snapshots changed files, and `promptdiff hook install` adds a pre-commit hook that runs the sync automatically, ensuring prompt versions are never lost.

## v0.2 (Planned)

### 🤖 CI/CD GitHub Action
A GitHub Action that runs `promptdiff` on pull requests, posting a summary of prompt changes as a PR comment for easy review.

### 🧠 Semantic Similarity with Embeddings
Go beyond text diffs. Use embeddings to score semantic similarity between prompt versions and flag meaningful behavioral changes.

### 📂 Prompt Registry
Local or remote prompt registry to store, version, and retrieve prompt templates by name and tag across projects.

---

Have ideas? Open an issue or start a discussion!
