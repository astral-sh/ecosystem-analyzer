"""Shared subprocess helpers."""

import subprocess
from pathlib import Path


def run(*args: str, cwd: Path) -> str:
    """Return a command's stdout, preserving stderr in exception tracebacks.

    Use surrogate escapes to preserve non-UTF-8 paths and messages.
    """
    try:
        return subprocess.run(
            list(args),
            cwd=cwd,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="surrogateescape",
        ).stdout
    except subprocess.CalledProcessError as error:
        if error.stderr:
            error.add_note(error.stderr.rstrip())
        raise
