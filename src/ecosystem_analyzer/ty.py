import logging
import os
import shlex
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

from git import Commit, Repo

from .diagnostic import Diagnostic, DiagnosticsParser, index_panic_messages
from .flaky import classify_diagnostics
from .installed_project import InstalledProject
from .run_output import ExitStatus, OutputVariant, RunOutput

logger = logging.getLogger(__name__)


def _normalize_stderr(stderr: str) -> str | None:
    stderr = stderr.strip()
    return stderr or None


def _aggregate_panic_messages(statuses: list[ExitStatus]) -> list[OutputVariant]:
    """Aggregate normalized panic identities while preserving multiplicity."""
    indexed_by_run = []
    for status in statuses:
        messages = [
            variant["message"]
            for variant in status.get("panic_messages", [])
            for _ in range(variant["count"])
        ]
        indexed_by_run.append(index_panic_messages(messages))

    variants = []
    all_keys = set().union(*(indexed.keys() for indexed in indexed_by_run))
    for key in all_keys:
        messages_by_run = [indexed.get(key, []) for indexed in indexed_by_run]
        max_multiplicity = max(map(len, messages_by_run))
        for index in range(max_multiplicity):
            present_messages = [
                messages[index] for messages in messages_by_run if len(messages) > index
            ]
            variants.append(
                OutputVariant(message=present_messages[0], count=len(present_messages))
            )

    return sorted(variants, key=lambda variant: variant["message"])


def _aggregate_stderr(statuses: list[ExitStatus]) -> list[OutputVariant]:
    """Aggregate exact stderr variants across runs with one count per run."""
    counts: Counter[str] = Counter()
    for status in statuses:
        counts.update({variant["message"] for variant in status.get("stderr", [])})
    return [
        OutputVariant(message=message, count=count)
        for message, count in sorted(counts.items())
    ]


class Ty:
    def __init__(
        self,
        repository: Repo | None = None,
        target_dir: Path | None = None,
        profile: str = "dev",
    ) -> None:
        self.repository: Repo | None = repository
        if repository is not None:
            self.working_dir: Path = Path(repository.working_dir)
            self.cargo_target_dir: Path = target_dir or self.working_dir / "target"
        self.profile: str = profile
        self._commit_override: str | None = None

    def compile_for_commit(self, commit: str | Commit):
        if self.repository is None:
            raise RuntimeError("Cannot compile for commit without a repository")

        # Checkout the commit
        logger.debug(f"Checking out ty commit '{commit}'")
        self.repository.git.checkout(commit)

        # Compile ty
        env = os.environ.copy()
        env["CARGO_TARGET_DIR"] = self.cargo_target_dir.as_posix()

        logger.info(f"Compiling ty ({self.profile})")
        cargo_cmd = ["cargo", "build", "--package", "ty", "--profile", self.profile]
        logger.debug(
            f"Executing: {' '.join(cargo_cmd)} (CARGO_TARGET_DIR={self.cargo_target_dir})"
        )
        subprocess.run(
            cargo_cmd,
            cwd=self.working_dir,
            capture_output=True,
            check=True,
            env=env,
        )

        # Cargo uses "dev" as the profile name, but outputs to "debug" directory
        # For other profiles, the directory name matches the profile name
        target_dir = "debug" if self.profile == "dev" else self.profile
        self.executable = self.cargo_target_dir / target_dir / "ty"
        self._commit_override = None

    def use_prebuilt(self, binary_path: Path, commit_sha: str) -> None:
        """Use a pre-built ty binary instead of building from source."""
        self.executable = binary_path.resolve()
        self._commit_override = commit_sha

    @property
    def commit_sha(self) -> str:
        if self._commit_override is not None:
            return self._commit_override
        if self.repository is None:
            raise RuntimeError(
                "No commit SHA available: no repository and no prebuilt override set"
            )
        return self.repository.head.commit.hexsha

    def run_on_project(self, project: InstalledProject) -> RunOutput:
        logger.info(f"Running ty on project '{project.name}'")

        # Standard flags to add to all ty check commands
        standard_flags = [
            "--output-format=concise",
            "--python",
            str(project.venv_path),
        ]

        if project.ty_cmd:
            # Use custom ty command from project configuration
            cmd_parts = shlex.split(project.ty_cmd)

            # Replace placeholders: {ty} with executable, {paths} with project paths
            cmd = []
            for part in cmd_parts:
                if part == "{ty}":
                    cmd.append(self.executable.as_posix())
                elif part == "{paths}":
                    cmd.extend(project.paths)
                else:
                    cmd.append(part)

            cmd.extend(standard_flags)
        else:
            cmd = [
                self.executable.as_posix(),
                "check",
                *standard_flags,
                *project.paths,
            ]
        logger.debug(f"Executing: {' '.join(cmd)}")
        start_time = time.time()
        try:
            result = subprocess.run(
                cmd,
                cwd=project.root_directory,
                check=False,
                capture_output=True,
                text=True,
                timeout=30 if self.profile in {"profiling", "release"} else 180,
            )

            execution_time = time.time() - start_time
            return_code = result.returncode
            stderr = _normalize_stderr(result.stderr)

            if result.returncode not in (0, 1):
                logger.error(
                    f"ty failed with error code {result.returncode} for project '{project.name}' ... panic?"
                )
                if stderr:
                    print("ty stderr output:", file=sys.stderr)
                    print(stderr, file=sys.stderr)
                # Don't trust execution time for abnormal exits
                execution_time = None

            parser = DiagnosticsParser(
                repo_location=project.location,
                repo_commit=project.current_commit,
                repo_working_dir=project.root_directory,
            )

            panic_messages = parser.parse_panic_messages(result.stdout)
            diagnostics = parser.parse(result.stdout)
        except subprocess.TimeoutExpired:
            diagnostics = []
            execution_time = None
            return_code = None
            stderr = None
            panic_messages = []

        exit_status = ExitStatus(return_code=return_code, count=1)
        if panic_messages:
            exit_status["panic_messages"] = [
                OutputVariant(message=message, count=1) for message in panic_messages
            ]
        if stderr:
            exit_status["stderr"] = [OutputVariant(message=stderr, count=1)]

        return RunOutput({
            "project": project.name,
            "project_location": project.location,
            "ty_commit": self.commit_sha,
            "diagnostics": diagnostics,
            "exit_statuses": [exit_status],
            "median_time_s": execution_time,
        })

    def run_on_project_multiple(self, project: InstalledProject, n: int) -> RunOutput:
        """Run ty on a project N times and classify diagnostics and exit statuses.

        Returns a single RunOutput where `diagnostics` contains only stable
        diagnostics, while flaky diagnostics and exit statuses retain their
        frequencies across the runs.
        """
        assert n >= 2, "Use run_on_project for single runs"
        logger.info(
            f"Running ty on project '{project.name}' {n} times for flaky detection"
        )

        all_diagnostics: list[list[Diagnostic]] = []
        times: list[float] = []
        statuses_by_return_code: dict[int | None, list[ExitStatus]] = {}

        for i in range(n):
            logger.info(f"  Run {i + 1}/{n} for '{project.name}'")
            output = self.run_on_project(project)

            all_diagnostics.append(output["diagnostics"])
            [exit_status] = output["exit_statuses"]
            return_code = exit_status["return_code"]
            statuses_by_return_code.setdefault(return_code, []).append(exit_status)

            if (
                return_code in (0, 1)
                and (time_s := output.get("median_time_s")) is not None
            ):
                times.append(time_s)

        stable, flaky_locations = classify_diagnostics(all_diagnostics)

        # Use median time
        median_time: float | None = None
        if times:
            sorted_times = sorted(times)
            mid = len(sorted_times) // 2
            median_time = sorted_times[mid]

        exit_statuses = []
        for return_code, statuses in sorted(
            statuses_by_return_code.items(),
            key=lambda item: (item[0] is None, item[0] or 0),
        ):
            exit_status = ExitStatus(return_code=return_code, count=len(statuses))
            if panic_messages := _aggregate_panic_messages(statuses):
                exit_status["panic_messages"] = panic_messages
            if stderr := _aggregate_stderr(statuses):
                exit_status["stderr"] = stderr
            exit_statuses.append(exit_status)

        result = RunOutput({
            "project": project.name,
            "project_location": project.location,
            "ty_commit": self.commit_sha,
            "diagnostics": stable,
            "flaky_runs": n,
            "exit_statuses": exit_statuses,
            "median_time_s": median_time,
        })

        if flaky_locations:
            result["flaky_diagnostics"] = flaky_locations

        flaky_count = sum(len(loc["variants"]) for loc in flaky_locations)
        logger.info(
            f"  '{project.name}': {len(stable)} stable diagnostics, "
            f"{flaky_count} flaky diagnostics at {len(flaky_locations)} locations"
            f", {len(exit_statuses)} exit status{'es' if len(exit_statuses) != 1 else ''}"
            f" ({n} runs)"
        )

        return result
