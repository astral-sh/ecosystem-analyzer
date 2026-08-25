"""Configuration constants for the ecosystem analyzer."""

# Projects may require a newer Python version than this baseline.
MINIMUM_PYTHON_VERSION = (3, 11)

# Prevent source-distribution builds during script preparation and ty runs.
# Binary exclusions otherwise override no-build.
# uv 0.12.5 ignores no-editable for scripts without lockfiles.
UV_NO_BUILD_ENV = {
    "UV_NO_BUILD": "1",
    "UV_NO_BINARY": "0",
    "UV_NO_EDITABLE": "1",
}
