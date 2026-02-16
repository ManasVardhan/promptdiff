"""Auto-generate changelogs from prompt version history."""

from __future__ import annotations


from promptdiff.diff import PromptDiff
from promptdiff.store import PromptStore


class ChangelogGenerator:
    """Generate changelogs from prompt version history."""

    def __init__(self, store: PromptStore) -> None:
        self.store = store
        self.differ = PromptDiff()

    def generate(self, name: str, last_n: int | None = None) -> str:
        """Generate a markdown changelog for a prompt."""
        versions = self.store.list_versions(name)
        if last_n is not None:
            versions = versions[-last_n:]

        lines = [f"# Changelog: {name}", ""]

        for i in range(len(versions) - 1, -1, -1):
            v = versions[i]
            ts = v.timestamp[:10] if v.timestamp else "unknown"
            msg = v.message or "No description"
            lines.append(f"## v{v.version} ({ts})")
            lines.append("")
            lines.append(f"**{msg}**")
            lines.append("")

            if i > 0:
                prev = versions[i - 1]
                diff = self.differ.full_diff(
                    prev.content, v.content, prev.version, v.version
                )
                lines.append(
                    f"- Text similarity: {diff.similarity_ratio:.1%}"
                )
                if diff.semantic_similarity is not None:
                    lines.append(
                        f"- Semantic similarity: {diff.semantic_similarity:.1%}"
                    )
                lines.append(
                    f"- Changes: +{diff.stats['additions']} -{diff.stats['deletions']}"
                )
            else:
                lines.append("- Initial version")

            lines.append("")

        return "\n".join(lines)

    def generate_all(self) -> str:
        """Generate a combined changelog for all prompts."""
        prompts = self.store.list_prompts()
        if not prompts:
            return "# Changelog\n\nNo prompts tracked yet.\n"

        sections = ["# Prompt Changelog", ""]
        for name in prompts:
            sections.append(self.generate(name))
            sections.append("---")
            sections.append("")

        return "\n".join(sections)
