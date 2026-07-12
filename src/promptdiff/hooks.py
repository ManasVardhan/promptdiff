"""Git pre-commit hook management.

Installs a pre-commit hook that runs ``promptdiff sync --quiet`` so tracked
prompt files are snapshotted automatically before every commit. Hooks written
by promptdiff carry a marker line so foreign hooks are never overwritten
without ``force=True``.
"""

from __future__ import annotations

from pathlib import Path

HOOK_MARKER = "# installed-by: promptdiff"

HOOK_SCRIPT = f"""#!/bin/sh
{HOOK_MARKER}
# Snapshots tracked prompt files before each commit.
# Remove with: promptdiff hook uninstall
if command -v promptdiff >/dev/null 2>&1; then
    promptdiff sync --quiet
    git add .promptdiff 2>/dev/null || true
fi
"""


def find_git_dir(root: str | Path) -> Path:
    """Return the ``.git`` directory at or above *root*.

    Raises:
        FileNotFoundError: If no ``.git`` directory is found.
    """
    current = Path(root).resolve()
    for candidate in [current, *current.parents]:
        git_dir = candidate / ".git"
        if git_dir.is_dir():
            return git_dir
    raise FileNotFoundError(f"No git repository found at or above {root}")


def _hook_path(root: str | Path) -> Path:
    return find_git_dir(root) / "hooks" / "pre-commit"


def is_installed(root: str | Path) -> bool:
    """Return True if a promptdiff-managed pre-commit hook is installed."""
    try:
        hook = _hook_path(root)
    except FileNotFoundError:
        return False
    return hook.exists() and HOOK_MARKER in hook.read_text()


def install_hook(root: str | Path, force: bool = False) -> Path:
    """Install the pre-commit hook for the repository containing *root*.

    Args:
        root: Any path inside the git repository.
        force: Overwrite an existing hook that promptdiff did not create.

    Returns:
        Path to the installed hook.

    Raises:
        FileNotFoundError: If no git repository is found.
        FileExistsError: If a foreign pre-commit hook exists and *force* is False.
    """
    hook = _hook_path(root)
    if hook.exists() and HOOK_MARKER not in hook.read_text() and not force:
        raise FileExistsError(
            f"A pre-commit hook already exists at {hook} and was not installed by "
            "promptdiff. Re-run with --force to overwrite it."
        )
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_text(HOOK_SCRIPT)
    hook.chmod(hook.stat().st_mode | 0o111)
    return hook


def uninstall_hook(root: str | Path) -> Path:
    """Remove the promptdiff pre-commit hook.

    Returns:
        Path of the removed hook.

    Raises:
        FileNotFoundError: If no git repository or no pre-commit hook exists.
        RuntimeError: If the existing hook was not installed by promptdiff.
    """
    hook = _hook_path(root)
    if not hook.exists():
        raise FileNotFoundError(f"No pre-commit hook found at {hook}")
    if HOOK_MARKER not in hook.read_text():
        raise RuntimeError(
            f"The pre-commit hook at {hook} was not installed by promptdiff. "
            "Refusing to remove it."
        )
    hook.unlink()
    return hook
