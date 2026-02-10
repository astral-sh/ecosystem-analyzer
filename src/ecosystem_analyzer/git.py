import hashlib
import logging
from pathlib import Path

from git import Commit, Repo

from .installed_project import _get_cache_dir


def _ty_repo_cache_path(repo_path: Path) -> Path:
    cache_dir = _get_cache_dir()
    repo_path = repo_path.resolve()
    location_hash = hashlib.sha256(repo_path.as_posix().encode()).hexdigest()[:12]
    return cache_dir / f"ty_{location_hash}"


def _update_cached_repo(repo: Repo) -> None:
    logging.debug("Updating cached ty repository")
    repo.remote().fetch()


def resolve_ty_repo(repo_path: str | Path) -> Repo:
    """Return a Repo with a working tree, cloning bare repos into cache."""
    resolved_path = Path(repo_path).expanduser().resolve()
    repo = Repo(resolved_path)

    if not repo.bare:
        return repo

    cache_path = _ty_repo_cache_path(resolved_path)

    if cache_path.exists():
        logging.info(f"Using cached ty repository at {cache_path}")
        cached_repo = Repo(cache_path)
    else:
        logging.info(f"Cloning bare ty repository from {resolved_path} to {cache_path}")
        cached_repo = Repo.clone_from(resolved_path.as_posix(), cache_path)

    try:
        origin = cached_repo.remote()
    except ValueError:
        origin = cached_repo.create_remote("origin", resolved_path.as_posix())
    else:
        origin.set_url(resolved_path.as_posix())

    _update_cached_repo(cached_repo)
    return cached_repo


def get_latest_ty_commits(repo: Repo, num_commits: int) -> list[Commit]:
    repo.git.checkout("origin/main")

    commits = []
    for commit in repo.iter_commits():
        assert isinstance(commit.message, str)

        if commit.message.startswith("[ty] "):
            commits.append(commit)
            if len(commits) >= num_commits:
                break

    commits.reverse()

    return commits
