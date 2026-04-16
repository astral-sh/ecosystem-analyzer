# ecosystem analyzer

ecosystem-analyzer is a tool for analyzing Python projects with `ty` (a Python type checker). It downloads Python projects based on setup instructions in `mypy_primer`'s project list, runs `ty` on them, and collects diagnostics. The tool supports single project analysis, ecosystem-wide analysis, diagnostic diff comparison between commits, and historical analysis.


## Project Structure & Module Organization

The Python code lives in `src/ecosystem_analyzer/`. `main.py` exposes the Click CLI, `manager.py` coordinates project runs, and modules such as `diagnostic.py`, `diff.py`, and `ty.py` hold parsing, comparison, and tool-integration logic. HTML templates for generated reports live in `src/ecosystem_analyzer/templates/`. Tests live in `tests/``.

## Build, Test, and Development Commands

Use `uv` for all local work.

- `uv run ecosystem-analyzer --help`: list CLI commands.
- `uv run pytest`: run the full test suite.
- `uvx prek run -a`: run linting.
- `uv run ty check`: run type checking against the repository.
- `uv run ecosystem-analyzer --repository ~/ty run --project-name <project> --commit <ty-commit> --output project-diagnostics.json`: analyze one project against a `ty` checkout.
- `uv run ecosystem-analyzer generate-report project-diagnostics.json --output report.html`: render an HTML report from saved diagnostics.

## `ty` CI Integration

`ty` uses `ecosystem-analyzer` from the `ruff` repository's CI workflow at [`.github/workflows/ty-ecosystem-analyzer.yaml`](https://github.com/astral-sh/ruff/blob/main/.github/workflows/ty-ecosystem-analyzer.yaml).

The CI job checks out `ruff` (which includes `ty`'s implementation), copies `.github/ty-ecosystem.toml` into `~/.config/ty/ty.toml`, creates two local branches (`new_commit` at the PR head and `old_commit` at the merge base against the PR base branch), and snapshots the primer project lists from each revision. It installs `ecosystem-analyzer` with `uv tool install` from a pinned Git commit, then runs:

- `ecosystem-analyzer --repository ruff --flaky-runs 10 diff ...`: compare diagnostics between `old_commit` and `new_commit` using the old/new/flaky project lists. The `--projects-old` and `--projects-new` options are optional; when omitted, each side defaults to every project known to `mypy_primer`.
- `ecosystem-analyzer generate-diff ...`: produce the HTML diagnostics diff report.
- `ecosystem-analyzer generate-diff-statistics ...`: produce the Markdown summary that is posted back to the PR.
- `ecosystem-analyzer generate-timing-diff ...`: produce the HTML timing comparison report.

CI uploads `dist/diff.html`, `dist/timing.html`, the full `dist/` report directory, and `comment.md` as artifacts. `astral-sh-bot` consumes those artifacts, so artifact names are part of the contract.
