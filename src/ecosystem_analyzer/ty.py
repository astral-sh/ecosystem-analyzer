import logging
import os
import shlex
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

from git import Commit, Repo

from .diagnostic import DiagnosticsParser
from .flaky import classify_diagnostics
from .installed_project import InstalledProject
from .run_output import RunOutput


class Ty:
    def __init__(self, repository: Repo, target_dir: Path | None, profile: str = "dev") -> None:
        self.repository: Repo = repository
        self.working_dir: Path = Path(self.repository.working_dir)
        self.cargo_target_dir: Path = target_dir or self.working_dir / "target"
        self.profile: str = profile

    def compile_for_commit(self, commit: str | Commit):
        # Checkout the commit
        logging.debug(f"Checking out ty commit '{commit}'")
        self.repository.git.checkout(commit)

        # Compile ty
        env = os.environ.copy()
        env["CARGO_TARGET_DIR"] = self.cargo_target_dir.as_posix()

        logging.info(f"Compiling ty ({self.profile})")
        cargo_cmd = ["cargo", "build", "--package", "ty", "--profile", self.profile]
        logging.debug(
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

    def run_on_project(self, project: InstalledProject) -> RunOutput:
        logging.info(f"Running ty on project '{project.name}'")

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
        logging.debug(f"Executing: {' '.join(cmd)}")
        start_time = time.time()
        try:
            result = subprocess.run(
                cmd,
                cwd=project.root_directory,
                check=False,
                capture_output=True,
                text=True,
                timeout=30 if self.profile == "release" else 180,
            )

            execution_time = time.time() - start_time
            return_code = result.returncode

            if result.returncode not in (0, 1):
                logging.error(
                    f"ty failed with error code {result.returncode} for project '{project.name}' ... panic?"
                )
                if result.stderr:
                    print("ty stderr output:", file=sys.stderr)
                    print(result.stderr, file=sys.stderr)
                # Don't trust execution time for abnormal exits
                execution_time = None

            parser = DiagnosticsParser(
                repo_location=project.location,
                repo_commit=project.current_commit,
                repo_working_dir=project.root_directory,
            )

            diagnostics = parser.parse(result.stdout)
        except subprocess.TimeoutExpired:
            diagnostics = []
            execution_time = None
            return_code = None

        return RunOutput(
            {
                "project": project.name,
                "project_location": project.location,
                "ty_commit": self.repository.head.commit.hexsha,
                "diagnostics": diagnostics,
                "time_s": execution_time,
                "return_code": return_code,
            }
        )

    @staticmethod
    def _diagnostic_keys(diagnostics: list) -> frozenset:
        """Return the set of unique diagnostic keys from a run."""
        return frozenset(
            (d["path"], d["line"], d["column"], d["level"], d["lint_name"], d["message"])
            for d in diagnostics
        )

    def run_on_project_multiple(self, project: InstalledProject, n: int) -> RunOutput:
        """Run ty on a project up to N times and classify diagnostics as stable/flaky.

        Uses an adaptive strategy: stops early if an additional run doesn't
        change the diagnostic classification (no new variants discovered,
        no previously-stable diagnostic becomes flaky).

        Returns a single RunOutput where `diagnostics` contains only stable
        diagnostics and `flaky_diagnostics` contains grouped flaky ones.
        """
        assert n >= 2, "Use run_on_project for single runs"
        logging.info(f"Running ty on project '{project.name}' up to {n} times for flaky detection")

        all_diagnostics: list[list] = []
        times: list[float] = []
        return_codes: list[int | None] = []

        # Track the set of all diagnostic keys seen so far, and
        # the set of keys that appeared in every run so far.
        all_seen: set = set()
        stable_so_far: set | None = None

        actual_runs = 0
        for i in range(n):
            logging.info(f"  Run {i + 1}/{n} for '{project.name}'")
            output = self.run_on_project(project)

            # If any run fails abnormally, bail out and return the failure
            if output.get("return_code") is not None and output["return_code"] not in (0, 1):
                logging.warning(
                    f"Run {i + 1}/{n} for '{project.name}' failed with return code "
                    f"{output['return_code']}; aborting flaky detection"
                )
                return output
            if output.get("return_code") is None:
                # Timeout
                logging.warning(
                    f"Run {i + 1}/{n} for '{project.name}' timed out; aborting flaky detection"
                )
                return output

            all_diagnostics.append(output["diagnostics"])
            if output.get("time_s") is not None:
                times.append(output["time_s"])
            return_codes.append(output.get("return_code"))
            actual_runs += 1

            # Check if this run changed the classification
            run_keys = self._diagnostic_keys(output["diagnostics"])

            if i == 0:
                all_seen = set(run_keys)
                stable_so_far = set(run_keys)
            else:
                new_variants = run_keys - all_seen
                newly_flaky = stable_so_far - run_keys

                # Require at least 3 runs before stopping early, to reduce
                # the chance of missing a rare variant that only appears in
                # ~1/3 of runs.
                if actual_runs >= 3 and not new_variants and not newly_flaky:
                    logging.info(
                        f"  '{project.name}': run {i + 1} unchanged, "
                        f"stopping early after {actual_runs} runs"
                    )
                    break

                all_seen |= run_keys
                stable_so_far &= run_keys

        stable, flaky_locations = classify_diagnostics(all_diagnostics)

        # Use median time
        median_time: float | None = None
        if times:
            sorted_times = sorted(times)
            mid = len(sorted_times) // 2
            median_time = sorted_times[mid]

        # Use most common return code
        rc_counts = Counter(rc for rc in return_codes if rc is not None)
        most_common_rc = rc_counts.most_common(1)[0][0] if rc_counts else None

        result = RunOutput(
            {
                "project": project.name,
                "project_location": project.location,
                "ty_commit": self.repository.head.commit.hexsha,
                "diagnostics": stable,
                "flaky_runs": actual_runs,
                "time_s": median_time,
                "return_code": most_common_rc,
            }
        )

        if flaky_locations:
            result["flaky_diagnostics"] = flaky_locations

        flaky_count = sum(len(loc["variants"]) for loc in flaky_locations)
        logging.info(
            f"  '{project.name}': {len(stable)} stable diagnostics, "
            f"{flaky_count} flaky diagnostics at {len(flaky_locations)} locations"
            f" ({actual_runs}/{n} runs)"
        )

        return result
