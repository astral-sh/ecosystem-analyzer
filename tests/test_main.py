import json

from click.testing import CliRunner

from ecosystem_analyzer.main import cli


def test_parse_diagnostics_requires_return_code(tmp_path):
    output = tmp_path / "diagnostics.json"

    result = CliRunner().invoke(
        cli,
        ["parse-diagnostics", "--output", str(output)],
        input="All checks passed!\n",
    )

    assert result.exit_code == 2
    assert "Missing option '--return-code'" in result.output
    assert not output.exists()


def test_parse_diagnostics_preserves_exit_status_and_panic(tmp_path):
    output = tmp_path / "diagnostics.json"
    panic = """error[panic]: internal error
info: Version: 0.0.1
"""

    result = CliRunner().invoke(
        cli,
        [
            "parse-diagnostics",
            "--output",
            str(output),
            "--return-code",
            "101",
        ],
        input=panic,
    )

    assert result.exit_code == 0, result.output
    [run_output] = json.loads(output.read_text())["outputs"]
    assert run_output["diagnostics"] == []
    assert run_output["exit_statuses"] == [
        {
            "return_code": 101,
            "count": 1,
            "panic_messages": [
                {"message": "internal error\ninfo: Version: 0.0.1", "count": 1}
            ],
        }
    ]


def test_parse_diagnostics_preserves_empty_output_exit_status(tmp_path):
    output = tmp_path / "diagnostics.json"

    result = CliRunner().invoke(
        cli,
        [
            "parse-diagnostics",
            "--output",
            str(output),
            "--return-code",
            "101",
        ],
        input="",
    )

    assert result.exit_code == 0, result.output
    [run_output] = json.loads(output.read_text())["outputs"]
    assert run_output["diagnostics"] == []
    assert run_output["exit_statuses"] == [{"return_code": 101, "count": 1}]


def test_statistics_skip_missing_timings_from_parsed_diagnostics(tmp_path):
    runner = CliRunner()
    old_output = tmp_path / "old.json"
    new_output = tmp_path / "new.json"
    statistics = tmp_path / "statistics.md"

    result = runner.invoke(
        cli,
        [
            "parse-diagnostics",
            "--output",
            str(old_output),
            "--return-code",
            "0",
        ],
        input="All checks passed!\n",
    )
    assert result.exit_code == 0, result.output

    result = runner.invoke(
        cli,
        [
            "parse-diagnostics",
            "--output",
            str(new_output),
            "--return-code",
            "1",
        ],
        input="example.py:1:1: error[invalid-assignment] Bad assignment\n",
    )
    assert result.exit_code == 0, result.output

    result = runner.invoke(
        cli,
        [
            "generate-diff-statistics",
            str(old_output),
            str(new_output),
            "--output",
            str(statistics),
        ],
    )

    assert result.exit_code == 0, result.output
    markdown = statistics.read_text()
    assert "| `invalid-assignment` | 1 | 0 | 0 |" in markdown
    assert "| **Total** | **1** | **0** | **0** |" in markdown
