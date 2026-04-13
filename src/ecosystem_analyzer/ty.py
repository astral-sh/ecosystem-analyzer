import logging
import os
import shlex
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

from git import Commit, Repo

from .diagnostic import Diagnostic, DiagnosticsParser
from .flaky import classify_diagnostics, diagnostic_keys
from .installed_project import InstalledProject
from .run_output import FlakyLocation, RunOutput

logger = logging.getLogger(__name__)


def _normalize_stderr(stderr: str) -> str | None:
    stderr = stderr.strip()
    return stderr or None


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

        output = RunOutput({
            "project": project.name,
            "project_location": project.location,
            "ty_commit": self.commit_sha,
            "diagnostics": diagnostics,
            "time_s": execution_time,
            "return_code": return_code,
        })
        if stderr:
            output["stderr"] = stderr
        if panic_messages:
            output["panic_messages"] = panic_messages
        return output

    def _build_multi_run_result(
        self,
        project: InstalledProject,
        stable: list[Diagnostic],
        flaky_locations: list[FlakyLocation],
        n: int,
        times: list[float],
        return_codes: list[int],
    ) -> RunOutput:
        """Build a RunOutput from the results of multiple ty runs."""
        # Use median time
        median_time: float | None = None
        if times:
            sorted_times = sorted(times)
            mid = len(sorted_times) // 2
            median_time = sorted_times[mid]

        # Use most common return code
        rc_counts = Counter(return_codes)
        most_common_rc = rc_counts.most_common(1)[0][0] if rc_counts else None

        result = RunOutput({
            "project": project.name,
            "project_location": project.location,
            "ty_commit": self.commit_sha,
            "diagnostics": stable,
            "flaky_runs": n,
            "time_s": median_time,
            "return_code": most_common_rc,
        })

        if flaky_locations:
            result["flaky_diagnostics"] = flaky_locations

        flaky_count = sum(len(loc["variants"]) for loc in flaky_locations)
        logger.info(
            f"  '{project.name}': {len(stable)} stable diagnostics, "
            f"{flaky_count} flaky diagnostics at {len(flaky_locations)} locations"
            f" ({n} runs)"
        )

        return result

    @staticmethod
    def _run_aborted(
        output: RunOutput, project: InstalledProject, run_idx: int, total: int
    ) -> bool:
        """Log and return True if this run's exit status aborts flaky detection."""
        rc = output.get("return_code")
        if rc is None:
            logger.warning(
                f"Run {run_idx}/{total} for '{project.name}' timed out; "
                f"aborting flaky detection"
            )
            return True
        if rc not in (0, 1):
            logger.warning(
                f"Run {run_idx}/{total} for '{project.name}' failed with return "
                f"code {rc}; aborting flaky detection"
            )
            return True
        return False

    def run_on_project_multiple(self, project: InstalledProject, n: int) -> RunOutput:
        """Run ty on a project N times and classify diagnostics as stable/flaky.

        Returns a single RunOutput where `diagnostics` contains only stable
        diagnostics and `flaky_diagnostics` contains grouped flaky ones.
        """
        assert n >= 2, "Use run_on_project for single runs"
        logger.info(
            f"Running ty on project '{project.name}' {n} times for flaky detection"
        )

        all_diagnostics: list[list] = []
        times: list[float] = []
        return_codes: list[int] = []

        for i in range(n):
            logger.info(f"  Run {i + 1}/{n} for '{project.name}'")
            output = self.run_on_project(project)
            if self._run_aborted(output, project, i + 1, n):
                return output

            all_diagnostics.append(output["diagnostics"])
            if (time_s := output.get("time_s")) is not None:
                times.append(time_s)
            rc = output["return_code"]
            assert rc is not None
            return_codes.append(rc)

        stable, flaky_locations = classify_diagnostics(all_diagnostics)
        return self._build_multi_run_result(
            project, stable, flaky_locations, n, times, return_codes
        )

    def run_on_project_dynamic(
        self,
        project: InstalledProject,
        max_runs: int,
        baseline: RunOutput | None,
    ) -> RunOutput:
        """Run ty with dynamic flaky detection that can short-circuit.

        Compared to ``run_on_project_multiple`` (which always runs exactly N
        times), this method can finish early:

        1. If the first run produces identical diagnostics to *baseline*,
           all reruns are skipped — there are no changes to investigate.
        2. After each subsequent run (starting from run 2), if every
           diagnostic that *differs* from the baseline has been classified as
           flaky, the remaining runs are skipped.

        *baseline* is typically the single-run output from the old commit in
        a ``diff`` invocation.  When *baseline* is ``None`` (e.g. for a
        newly added project), the empty set is used — so optimisation 1
        fires only when the first run itself is empty, and optimisation 2
        fires when every diagnostic turns out to be flaky.

        Note: flakiness in diagnostics shared with *baseline* is only
        missed when the first run happens to match *baseline* exactly —
        Optimisation 1 skips reruns and we never get a chance to observe
        the variation.  Once reruns do happen, every diagnostic is
        classified via ``classify_diagnostics`` regardless of whether it
        is shared with *baseline*.
        """
        assert max_runs >= 2, "Use run_on_project for single runs"
        logger.info(
            f"Running ty on project '{project.name}' with dynamic flaky detection "
            f"(max {max_runs} runs)"
        )

        baseline_keys = (
            diagnostic_keys(baseline["diagnostics"]) if baseline else frozenset()
        )

        logger.info(f"  Run 1/{max_runs} for '{project.name}'")
        first_output = self.run_on_project(project)
        if self._run_aborted(first_output, project, 1, max_runs):
            return first_output

        # Optimisation 1: no changes relative to baseline → skip reruns
        if diagnostic_keys(first_output["diagnostics"]) == baseline_keys:
            logger.info(f"  '{project.name}': no changes vs baseline, skipping reruns")
            return first_output

        all_diagnostics: list[list[Diagnostic]] = [first_output["diagnostics"]]
        times: list[float] = []
        if (t := first_output.get("time_s")) is not None:
            times.append(t)
        first_rc = first_output["return_code"]
        assert first_rc is not None
        return_codes: list[int] = [first_rc]

        for i in range(1, max_runs):
            logger.info(f"  Run {i + 1}/{max_runs} for '{project.name}'")
            output = self.run_on_project(project)
            if self._run_aborted(output, project, i + 1, max_runs):
                return output

            all_diagnostics.append(output["diagnostics"])
            if (t := output.get("time_s")) is not None:
                times.append(t)
            rc = output["return_code"]
            assert rc is not None
            return_codes.append(rc)

            stable, flaky_locations = classify_diagnostics(all_diagnostics)

            # Optimisation 2: all changes vs baseline are flaky → short-circuit
            if diagnostic_keys(stable) == baseline_keys:
                logger.info(
                    f"  '{project.name}': all changes are flaky after "
                    f"{len(all_diagnostics)} runs, short-circuiting"
                )
                break

        return self._build_multi_run_result(
            project,
            stable,
            flaky_locations,
            len(all_diagnostics),
            times,
            return_codes,
        )
