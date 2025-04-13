import subprocess
import tempfile
from pathlib import Path

from git import Repo


class InstalledProject:
    def __init__(self, project) -> None:
        self.project = project
        self.temp_dir = tempfile.TemporaryDirectory()

    def _clone(self) -> None:
        try:
            print(f"Cloning {self.project.location} into {self.temp_dir.name}")
            Repo.clone_from(url=self.project.location, to_path=self.temp_dir.name)
        except Exception as e:
            print(f"Error cloning repository: {e}")
            return

    def _install_dependencies(self) -> None:
        if self.project.deps:
            print(f"Installing dependencies: {', '.join(self.project.deps)}")
            subprocess.run(["uv", "venv"], check=True, cwd=self.temp_dir.name)
            subprocess.run(
                ["uv", "pip", "install", "--link-mode=copy", *self.project.deps],
                check=True,
                cwd=self.temp_dir.name,
                capture_output=False,
            )

    def install(self) -> None:
        self._clone()
        self._install_dependencies()

    def count_diagnostics(self, red_knot: Path) -> int:
        extra_args = self.project.knot_paths if self.project.knot_paths else []
        result = subprocess.run(
            [
                red_knot.as_posix(),
                "check",
                "--output-format=concise",
                "--python",
                ".venv",
                *extra_args,
            ],
            cwd=self.temp_dir.name,
            check=False,
            capture_output=True,
            text=True,
        )

        # Append result.stdout to log file
        with open("log.txt", "a") as log_file:
            log_file.write(result.stdout)

        return len(result.stdout.splitlines()) 
