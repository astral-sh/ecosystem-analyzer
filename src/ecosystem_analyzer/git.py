"""Resolve ty repositories and discover commits for ecosystem analysis."""

import hashlib
import logging
from pathlib import Path

from .installed_project import _get_cache_dir
from .process import run

logger = logging.getLogger(__name__)


def _ty_repo_cache_path(repo_path: Path) -> Path:
    cache_dir = _get_cache_dir()
    repo_path = repo_path.resolve()
    location_hash = hashlib.sha256(repo_path.as_posix().encode()).hexdigest()[:12]
    return cache_dir / f"ty_{location_hash}"


def resolve_ty_repo(repo_path: str | Path) -> Path:
    """Return the working tree path, cloning bare repositories into cache."""
    resolved_path = Path(repo_path).expanduser().resolve()
    result = run("git", "rev-parse", "--is-bare-repository", cwd=resolved_path)
    if result.strip() == "false":
        result = run("git", "rev-parse", "--show-toplevel", cwd=resolved_path)
        return Path(result.strip())

    cache_path = _ty_repo_cache_path(resolved_path)

    if cache_path.exists():
        logger.info(f"Using cached ty repository at {cache_path}")
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
    result = run("git", "log", "-z", "--format=%H%x00%B", cwd=repo)

    commits: list[tuple[str, str]] = []
    fields = result.removesuffix("\0").split("\0")
    for sha, message in zip(fields[::2], fields[1::2], strict=True):
        if message.startswith("[ty] "):
            commits.append((sha, message.splitlines()[0]))
            if len(commits) >= num_commits:
                break

    commits.reverse()

    return commits
