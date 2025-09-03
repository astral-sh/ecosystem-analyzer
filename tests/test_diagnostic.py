from ecosystem_analyzer.diagnostic import DiagnosticsParser


class TestDiagnosticsParser:
    def test_parse_error_diagnostic(self):
        """Test parsing a basic error diagnostic message."""
        parser = DiagnosticsParser()
        content = "error[invalid-assignment] try.py:3:1: Object of type `Literal[1]` is not assignable to `str`"

        diagnostics = parser.parse(content)

        assert len(diagnostics) == 1
        diagnostic = diagnostics[0]
        assert diagnostic["level"] == "error"
        assert diagnostic["lint_name"] == "invalid-assignment"
        assert diagnostic["path"] == "try.py"
        assert diagnostic["line"] == 3
        assert diagnostic["column"] == 1
        assert (
            diagnostic["message"]
            == "Object of type `Literal[1]` is not assignable to `str`"
        )
        assert "github_ref" not in diagnostic

    def test_parse_warning_diagnostic(self):
        """Test parsing a warning diagnostic message."""
        parser = DiagnosticsParser()
        content = "warning[unused-variable] main.py:10:5: Variable `x` is not used"

        diagnostics = parser.parse(content)

        assert len(diagnostics) == 1
        diagnostic = diagnostics[0]
        assert diagnostic["level"] == "warning"
        assert diagnostic["lint_name"] == "unused-variable"
        assert diagnostic["path"] == "main.py"
        assert diagnostic["line"] == 10
        assert diagnostic["column"] == 5
        assert diagnostic["message"] == "Variable `x` is not used"

    def test_parse_with_github_ref(self):
        """Test parsing with GitHub reference generation."""
        parser = DiagnosticsParser(
            repo_location="https://github.com/user/repo", repo_commit="abc123"
        )
        content = "error[type-error] src/module.py:25:10: Type mismatch"

        diagnostics = parser.parse(content)

        assert len(diagnostics) == 1
        diagnostic = diagnostics[0]
        assert "github_ref" in diagnostic
        assert (
            diagnostic["github_ref"]
            == "https://github.com/user/repo/blob/abc123/src/module.py#L25"
        )

    def test_parse_multiple_diagnostics(self):
        """Test parsing multiple diagnostic messages."""
        parser = DiagnosticsParser()
        content = """error[invalid-assignment] try.py:3:1: Object of type `Literal[1]` is not assignable to `str`
warning[unused-import] utils.py:1:1: Import `os` is not used
error[missing-return] calc.py:15:20: Function must return a value"""

        diagnostics = parser.parse(content)

        assert len(diagnostics) == 3

        # First diagnostic
        assert diagnostics[0]["level"] == "error"
        assert diagnostics[0]["lint_name"] == "invalid-assignment"
        assert diagnostics[0]["path"] == "try.py"

        # Second diagnostic
        assert diagnostics[1]["level"] == "warning"
        assert diagnostics[1]["lint_name"] == "unused-import"
        assert diagnostics[1]["path"] == "utils.py"

        # Third diagnostic
        assert diagnostics[2]["level"] == "error"
        assert diagnostics[2]["lint_name"] == "missing-return"
        assert diagnostics[2]["path"] == "calc.py"

    def test_parse_invalid_format(self):
        """Test parsing content that doesn't match the expected format."""
        parser = DiagnosticsParser()
        content = """This is not a diagnostic message
Some other random text
info: This is just info, not an error or warning"""

        diagnostics = parser.parse(content)

        assert len(diagnostics) == 0

    def test_parse_new_format_error_diagnostic(self):
        """Test parsing a basic error diagnostic message in new format."""
        parser = DiagnosticsParser()
        content = "try.py:3:1: error[invalid-assignment] Object of type `Literal[1]` is not assignable to `str`"

        diagnostics = parser.parse(content)

        assert len(diagnostics) == 1
        diagnostic = diagnostics[0]
        assert diagnostic["level"] == "error"
        assert diagnostic["lint_name"] == "invalid-assignment"
        assert diagnostic["path"] == "try.py"
        assert diagnostic["line"] == 3
        assert diagnostic["column"] == 1
        assert (
            diagnostic["message"]
            == "Object of type `Literal[1]` is not assignable to `str`"
        )
        assert "github_ref" not in diagnostic

    def test_parse_new_format_warning_diagnostic(self):
        """Test parsing a warning diagnostic message in new format."""
        parser = DiagnosticsParser()
        content = "main.py:10:5: warning[unused-variable] Variable `x` is not used"

        diagnostics = parser.parse(content)

        assert len(diagnostics) == 1
        diagnostic = diagnostics[0]
        assert diagnostic["level"] == "warning"
        assert diagnostic["lint_name"] == "unused-variable"
        assert diagnostic["path"] == "main.py"
        assert diagnostic["line"] == 10
        assert diagnostic["column"] == 5
        assert diagnostic["message"] == "Variable `x` is not used"

    def test_parse_new_format_with_github_ref(self):
        """Test parsing new format with GitHub reference generation."""
        parser = DiagnosticsParser(
            repo_location="https://github.com/user/repo", repo_commit="abc123"
        )
        content = "src/module.py:25:10: error[type-error] Type mismatch"

        diagnostics = parser.parse(content)

        assert len(diagnostics) == 1
        diagnostic = diagnostics[0]
        assert "github_ref" in diagnostic
        assert (
            diagnostic["github_ref"]
            == "https://github.com/user/repo/blob/abc123/src/module.py#L25"
        )
