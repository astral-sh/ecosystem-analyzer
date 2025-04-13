"""
Configuration constants for the ecosystem analyzer.
"""

# Path to the Ruff repository
RUFF_REPO_PATH = "/home/shark/ruff3"

# Project pattern for filtering projects
PROJECT_PATTERN = r"/(mypy_primer|black|pyp|git-revise|zipp|arrow|isort|itsdangerous|rich|packaging|pybind11|pyinstrument|typeshed-stats|scrapy|werkzeug|bidict|async-utils)$"

# Log file name
LOG_FILE = "log.txt"

# Commit blacklist
COMMIT_BLACKLIST = ["907b6ed7b57d58dd6a26488e1393106dba78cb2d"]

# Number of commits to analyze
NUM_COMMITS = 1
