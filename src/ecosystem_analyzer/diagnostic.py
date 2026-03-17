import re
from pathlib import Path
from typing import NotRequired, TypedDict


OLD_DIAGNOSTIC_PATTERN = re.compile(
    r"^(?P<level>error|warning)\[(?P<lint_name>.+?)\] "
    r"(?P<path>.+?):(?P<line>\d+):(?P<column>\d+): "
    r"(?P<message>.+)$"
)
NEW_DIAGNOSTIC_PATTERN = re.compile(
    r"^(?P<path>.+?):(?P<line>\d+):(?P<column>\d+): "
    r"(?P<level>error|warning)\[(?P<lint_name>.+?)\] "
    r"(?P<message>.+)$"
)
PANIC_PATTERN = re.compile(r"^error\[panic\]: (?P<message>.+)$")


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
        if (match := OLD_DIAGNOSTIC_PATTERN.match(line)) or (
            match := NEW_DIAGNOSTIC_PATTERN.match(line)
        ):
            path = str(match.group("path"))
            line_num = str(match.group("line"))

            diagnostic: Diagnostic = {
                "level": str(match.group("level")),
                "lint_name": str(match.group("lint_name")),
                "path": path,
                "line": int(line_num),
                "column": int(match.group("column")),
                "message": str(match.group("message")),
            }

            # Only include github_ref if we have valid repo location and commit
            if self.repo_location and self.repo_commit:
                github_ref = (
                    f"{self.repo_location}/blob/{self.repo_commit}/{path}#L{line_num}"
                )
                diagnostic["github_ref"] = github_ref

            return diagnostic

        return None

    def _is_regular_diagnostic_start(self, line: str) -> bool:
        return bool(
            OLD_DIAGNOSTIC_PATTERN.match(line) or NEW_DIAGNOSTIC_PATTERN.match(line)
        )

    def parse_panic_messages(self, content: str) -> list[str]:
        panic_messages = []
        lines = content.splitlines()
        index = 0

        while index < len(lines):
            line = lines[index].strip()
            if not (match := PANIC_PATTERN.match(line)):
                index += 1
                continue

            panic_parts = [match.group("message")]
            index += 1

            while index < len(lines):
                raw_line = lines[index].rstrip()
                stripped = raw_line.strip()

                if not stripped:
                    break
                if PANIC_PATTERN.match(stripped) or self._is_regular_diagnostic_start(
                    stripped
                ):
                    break
                if raw_line.startswith(("info:", " ", "\t")):
                    panic_parts.append(stripped if raw_line.startswith("info:") else raw_line)
                    index += 1
                    continue
                break

            panic_messages.append("\n".join(panic_parts))

        return panic_messages

    def parse(self, content: str) -> list[Diagnostic]:
        messages = []
        for raw_line in content.splitlines():
            line = raw_line.strip()
            if not line:
                continue

            if message := self._parse_diagnostic_message(line):
                messages.append(message)
        return messages
