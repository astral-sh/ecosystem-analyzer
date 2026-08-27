"""Configuration defaults and cache paths for the ecosystem analyzer."""

import os
from pathlib import Path

# Projects may require a newer Python version than this baseline.
MINIMUM_PYTHON_VERSION = (3, 11)

# Prevent source-distribution builds during script preparation and ty runs.
# Binary exclusions otherwise override no-build.
UV_NO_BUILD_ENV = {
    "UV_NO_BUILD": "1",
    "UV_NO_BINARY": "0",
}


def get_cache_dir() -> Path:
    """Get the XDG cache directory for ecosystem-analyzer."""
    cache_home = os.environ.get("XDG_CACHE_HOME")
    if cache_home:
        cache_dir = Path(cache_home) / "ecosystem-analyzer"
    else:
        cache_dir = Path.home() / ".cache" / "ecosystem-analyzer"

    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir
