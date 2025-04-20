import logging

from git import Commit, Repo

from .config import NUM_COMMITS


def get_latest_red_knot_commits(repo: Repo) -> list[Commit]:
    repo.git.checkout("origin/main")

    commits = []
    for commit in repo.iter_commits():
        if commit.message.startswith("[red-knot] "):  # type: ignore
            commits.append(commit)
            if len(commits) >= NUM_COMMITS:
                break

    commits.reverse()

    return commits


def run_for_history(repository: Repo, projects: list[InstalledProject]) -> list[dict]:
    statistics = []

    last_commits = get_latest_red_knot_commits(repository)

    for commit in last_commits:
        message = commit.message.splitlines()[0]
        logging.info(f"Analyzing commit: {message}")

        executable = self._compile_for_commit(commit)

        total_diagnostics = 0
        project_stats = []

        for project in self.projects:
            diagnostics = project.count_diagnostics(executable)
            total_diagnostics += diagnostics
            project_stats.append(
                {
                    "location": project.project.location,
                    "diagnostics_count": diagnostics,
                }
            )

        logging.info(f"Total diagnostics: {total_diagnostics}")

        statistics.append(
            {
                "commit_message": message,
                "total_diagnostics": total_diagnostics,
                "projects": project_stats,
            }
        )

    return statistics
