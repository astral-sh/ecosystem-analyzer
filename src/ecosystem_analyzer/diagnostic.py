import re
from pathlib import Path
from typing import NotRequired, TypedDict


class Diagnostic(TypedDict):
    level: str
    lint_name: str

    path: str
    line: int
    column: int

    message: str

    github_ref: NotRequired[str]


class DiagnosticsParser:
    def __init__(
        self,
        repo_location: str | None = None,
        repo_commit: str | None = None,
        repo_working_dir: Path | None = None,
    ) -> None:
        self.repo_location = repo_location
        self.repo_commit = repo_commit
        self.repo_working_dir = repo_working_dir

    def _parse_diagnostic_message(self, line: str) -> Diagnostic | None:
        pattern = (
            r"^(?P<level>error|warning)\[(?P<lint_name>.+?)\] "
            r"(?P<path>.+?):(?P<line>\d+):(?P<column>\d+): "
            r"(?P<message>.+)$"
        )

        if match := re.match(pattern, line):
            path = str(match.group("path"))
            line = str(match.group("line"))

            diagnostic: Diagnostic = {
                "level": str(match.group("level")),
                "lint_name": str(match.group("lint_name")),
                "path": path,
                "line": int(line),
                "column": int(match.group("column")),
                "message": str(match.group("message")),
            }

            # Only include github_ref if we have valid repo location and commit
            if self.repo_location and self.repo_commit:
                github_ref = (
                    f"{self.repo_location}/blob/{self.repo_commit}/{path}#L{line}"
                )
                diagnostic["github_ref"] = github_ref

            return diagnostic
        return None

    def parse(self, content: str) -> list[Diagnostic]:
        messages = []
        for line in content.splitlines():
            line = line.strip()
            if not line:
                continue

            if message := self._parse_diagnostic_message(line):
                messages.append(message)
        return messages
