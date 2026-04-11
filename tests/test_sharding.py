from pathlib import Path

import pytest
from click.testing import CliRunner
from git import Repo

from ecosystem_analyzer.main import cli, shard_project_lists
from ecosystem_analyzer.ty import Ty


def test_two_shards_partition_all_projects():
    """All shards together cover the full project list with no overlaps."""
    projects = ["alpha", "bravo", "charlie", "delta", "echo"]
    shard_0, _ = shard_project_lists(projects, projects, shard=0, num_shards=2)
    shard_1, _ = shard_project_lists(projects, projects, shard=1, num_shards=2)
    assert sorted(shard_0 + shard_1) == sorted(projects)
    assert not set(shard_0) & set(shard_1)


def test_three_shards_partition_all_projects():
    """Works correctly with an uneven split across three shards."""
    projects = ["a", "b", "c", "d", "e", "f", "g"]
    all_sharded: list[str] = []
    for s in range(3):
        old, _ = shard_project_lists(projects, projects, shard=s, num_shards=3)
        all_sharded.extend(old)
    assert sorted(all_sharded) == sorted(projects)


def test_round_robin_assignment():
    """Projects are assigned round-robin by index in sorted order."""
    projects = ["a", "b", "c", "d", "e", "f"]
    # Sorted: a(0), b(1), c(2), d(3), e(4), f(5)
    # num_shards=3: shard 0 gets indices 0,3 → a,d
    #               shard 1 gets indices 1,4 → b,e
    #               shard 2 gets indices 2,5 → c,f
    s0, _ = shard_project_lists(projects, projects, shard=0, num_shards=3)
    s1, _ = shard_project_lists(projects, projects, shard=1, num_shards=3)
    s2, _ = shard_project_lists(projects, projects, shard=2, num_shards=3)
    assert s0 == ["a", "d"]
    assert s1 == ["b", "e"]
    assert s2 == ["c", "f"]


def test_consistent_across_old_and_new():
    """A project in both lists always lands in the same shard."""
    old = ["alpha", "bravo", "charlie"]
    new = ["bravo", "charlie", "delta"]
    old_s0, new_s0 = shard_project_lists(old, new, shard=0, num_shards=2)
    old_s1, new_s1 = shard_project_lists(old, new, shard=1, num_shards=2)
    # bravo and charlie appear in both lists; verify they land in the
    # same shard on each side
    for project in ["bravo", "charlie"]:
        in_old_0 = project in old_s0
        in_new_0 = project in new_s0
        assert in_old_0 == in_new_0, f"{project} inconsistent across old/new"


def test_project_added_in_new():
    """A project only in the new list still gets sharded consistently."""
    old = ["a", "c"]
    new = ["a", "b", "c"]
    # Union sorted: a(0), b(1), c(2); shard 0 with num_shards=2 → indices 0,2 → a,c
    old_s0, new_s0 = shard_project_lists(old, new, shard=0, num_shards=2)
    assert old_s0 == ["a", "c"]
    assert new_s0 == ["a", "c"]
    old_s1, new_s1 = shard_project_lists(old, new, shard=1, num_shards=2)
    assert old_s1 == []
    assert new_s1 == ["b"]


def test_preserves_input_order():
    """Output order matches the input list order, not sorted order."""
    projects = ["delta", "alpha", "charlie", "bravo"]
    result, _ = shard_project_lists(projects, projects, shard=0, num_shards=2)
    # Sorted union: alpha(0), bravo(1), charlie(2), delta(3)
    # Shard 0 → indices 0,2 → {alpha, charlie}
    # Input order of those: alpha then charlie (positions 1 and 2 in input)
    assert result == ["alpha", "charlie"]


def test_single_shard_returns_everything():
    """With num_shards=1, the sole shard gets all projects."""
    projects = ["x", "y", "z"]
    old, new = shard_project_lists(projects, projects, shard=0, num_shards=1)
    assert old == projects
    assert new == projects


def test_more_shards_than_projects():
    """Shards beyond the project count are empty."""
    projects = ["a", "b"]
    s0, _ = shard_project_lists(projects, projects, shard=0, num_shards=5)
    s1, _ = shard_project_lists(projects, projects, shard=1, num_shards=5)
    s2, _ = shard_project_lists(projects, projects, shard=2, num_shards=5)
    assert s0 == ["a"]
    assert s1 == ["b"]
    assert s2 == []


def _invoke_diff(tmp_path, extra_args):
    """Invoke the `diff` subcommand with minimal valid required args."""
    Repo.init(tmp_path)
    projects = tmp_path / "projects.txt"
    projects.write_text("a\nb\n")
    runner = CliRunner()
    return runner.invoke(
        cli,
        [
            "--repository", str(tmp_path),
            "diff",
            "--projects-old", str(projects),
            "--projects-new", str(projects),
            "--old", "HEAD",
            "--new", "HEAD",
            *extra_args,
        ],
    )


def test_shard_without_num_shards_is_error(tmp_path):
    """Providing --shard alone is an error."""
    result = _invoke_diff(tmp_path, ["--shard", "0"])
    assert result.exit_code != 0
    assert "--shard and --num-shards must be used together" in result.output


def test_num_shards_without_shard_is_error(tmp_path):
    """Providing --num-shards alone is an error."""
    result = _invoke_diff(tmp_path, ["--num-shards", "2"])
    assert result.exit_code != 0
    assert "--shard and --num-shards must be used together" in result.output


# -- Prebuilt binary tests --


def test_use_prebuilt_sets_executable_and_commit():
    """use_prebuilt sets the executable path and overrides the commit SHA."""
    ty = Ty()
    binary = Path("/tmp/ty-prebuilt")
    ty.use_prebuilt(binary, "abc123")
    assert ty.executable == binary.resolve()
    assert ty.commit_sha == "abc123"


def test_use_prebuilt_overrides_previous_commit():
    """Calling use_prebuilt twice updates the commit SHA."""
    ty = Ty()
    ty.use_prebuilt(Path("/tmp/ty-old"), "aaa")
    ty.use_prebuilt(Path("/tmp/ty-new"), "bbb")
    assert ty.commit_sha == "bbb"


def test_commit_sha_without_repo_or_override_raises():
    """Accessing commit_sha with no repo and no override is an error."""
    ty = Ty()
    with pytest.raises(RuntimeError, match="No commit SHA available"):
        _ = ty.commit_sha


def test_commit_sha_falls_back_to_repo(tmp_path):
    """Without an override, commit_sha reads from the repository HEAD."""
    repo = Repo.init(tmp_path)
    (tmp_path / "f.txt").write_text("x")
    repo.index.add(["f.txt"])
    repo.index.commit("init")
    ty = Ty(repository=repo)
    assert ty.commit_sha == repo.head.commit.hexsha


def test_diff_without_repository_requires_prebuilt_binaries(tmp_path):
    """Omitting --repository is an error unless both --ty-binary-* are given."""
    projects = tmp_path / "projects.txt"
    projects.write_text("a\nb\n")
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "diff",
            "--projects-old", str(projects),
            "--projects-new", str(projects),
            "--old", "abc",
            "--new", "def",
        ],
    )
    assert result.exit_code != 0
    assert "--repository is required" in result.output
