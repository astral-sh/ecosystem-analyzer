from git import Commit, Repo


def get_latest_ty_commits(repo: Repo, num_commits: int) -> list[Commit]:
    repo.git.checkout("origin/main")

    commits = []
    for commit in repo.iter_commits():
        if commit.message.startswith("[ty] "):  # type: ignore
            commits.append(commit)
            if len(commits) >= num_commits:
                break

    commits.reverse()

    return commits
