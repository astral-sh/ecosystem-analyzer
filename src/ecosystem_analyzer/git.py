"""Resolve ty repositories and discover commits for ecosystem analysis."""

import hashlib
import logging
from pathlib import Path

from .config import get_cache_dir
from .process import run

logger = logging.getLogger(__name__)


def _ty_repo_cache_path(repo_path: Path) -> Path:
    cache_dir = get_cache_dir()
    repo_path = repo_path.resolve()
    location_hash = hashlib.sha256(repo_path.as_posix().encode()).hexdigest()[:12]
    return cache_dir / f"ty_{location_hash}"


def validate_worktree(repo_path: Path) -> None:
    """Require a working tree rooted at repo_path, not an enclosing repository."""
    root = Path(
        run("git", "rev-parse", "--show-toplevel", cwd=repo_path).removesuffix("\n")
    )
    if root.resolve() != repo_path.resolve():
        raise ValueError(f"{repo_path} is not a Git working tree root (found {root})")


def resolve_ty_repo(repo_path: str | Path) -> Path:
    """Return the working tree path, cloning bare repositories into cache."""
    resolved_path = Path(repo_path).expanduser().resolve()
    result = run("git", "rev-parse", "--is-bare-repository", cwd=resolved_path)
    if result.strip() == "false":
        validate_worktree(resolved_path)
        return resolved_path

    cache_path = _ty_repo_cache_path(resolved_path)

    if cache_path.exists():
        logger.info(f"Using cached ty repository at {cache_path}")
        validate_worktree(cache_path)
    else:
        logger.info(f"Cloning bare ty repository from {resolved_path} to {cache_path}")
        run("git", "clone", "--", str(resolved_path), str(cache_path), cwd=Path.cwd())

    remotes = run("git", "remote", cwd=cache_path).splitlines()
    run(
        "git",
        "remote",
        "set-url" if "origin" in remotes else "add",
        "origin",
        str(resolved_path),
        cwd=cache_path,
    )
    logger.debug("Updating cached ty repository")
    run("git", "fetch", "origin", cwd=cache_path)
    return cache_path


def get_latest_ty_commits(repo: Path, num_commits: int) -> list[tuple[str, str]]:
    """Return the latest ty commit SHAs and first message lines, oldest first."""

    run("git", "checkout", "origin/main", cwd=repo)
    result = run(
        "git",
        "log",
        "--no-show-signature",
        "--encoding=UTF-8",
        "-z",
        "--format=%H%x00%B",
        cwd=repo,
    )

    commits: list[tuple[str, str]] = []
    fields = result.removesuffix("\0").split("\0")
    for sha, message in zip(fields[::2], fields[1::2], strict=True):
        if message.startswith("[ty] "):
            commits.append((sha, message.splitlines()[0]))
            if len(commits) >= num_commits:
                break

    commits.reverse()

    return commits
