"""Configuration constants for the ecosystem analyzer."""

# Projects may require a newer Python version than this baseline.
MINIMUM_PYTHON_VERSION = (3, 11)

# Prevent source-distribution builds during script preparation and ty runs.
# Binary exclusions otherwise override no-build.
UV_NO_BUILD_ENV = {
    "UV_NO_BUILD": "1",
    "UV_NO_BINARY": "0",
}
