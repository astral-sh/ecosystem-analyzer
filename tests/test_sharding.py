from pathlib import Path

import pytest
from click.testing import CliRunner
from git import Repo
from mypy_primer.model import Project

from ecosystem_analyzer.main import cli, shard_project_lists
from ecosystem_analyzer.ty import Ty


def _projects(*costs: tuple[str, int]) -> dict[str, Project]:
    """Build a minimal ecosystem_projects dict from (name, ty_cost) pairs."""
    return {
        name: Project(
            location=name, mypy_cmd="mypy", pyright_cmd="pyright", cost={"ty": c}
        )
        for name, c in costs
    }


def test_two_shards_partition_all_projects():
    """All shards together cover the full project list with no overlaps."""
    projects = ["alpha", "bravo", "charlie", "delta", "echo"]
    shard_0, _ = shard_project_lists(
        projects, projects, shard=0, num_shards=2, ecosystem_projects={}
    )
    shard_1, _ = shard_project_lists(
        projects, projects, shard=1, num_shards=2, ecosystem_projects={}
    )
    assert sorted(shard_0 + shard_1) == sorted(projects)
    assert not set(shard_0) & set(shard_1)


def test_three_shards_partition_all_projects():
    """Works correctly with an uneven split across three shards."""
    projects = ["a", "b", "c", "d", "e", "f", "g"]
    all_sharded: list[str] = []
    for s in range(3):
        old, _ = shard_project_lists(
            projects, projects, shard=s, num_shards=3, ecosystem_projects={}
        )
        all_sharded.extend(old)
    assert sorted(all_sharded) == sorted(projects)


def test_bin_packing_spreads_expensive_projects():
    """Expensive projects are spread across shards, not clustered."""
    ep = _projects(("heavy_a", 100), ("heavy_b", 100), ("light_c", 1), ("light_d", 1))
    projects = ["heavy_a", "heavy_b", "light_c", "light_d"]

    s0, _ = shard_project_lists(
        projects, projects, shard=0, num_shards=2, ecosystem_projects=ep
    )
    s1, _ = shard_project_lists(
        projects, projects, shard=1, num_shards=2, ecosystem_projects=ep
    )

    assert s0 == ["heavy_a", "light_c"]
    assert s1 == ["heavy_b", "light_d"]


def test_bin_packing_deterministic_assignment():
    """Verify the exact greedy assignment with known costs."""
    ep = _projects(("a", 10), ("b", 7), ("c", 5), ("d", 3), ("e", 1))
    projects = ["a", "b", "c", "d", "e"]

    # Descending by (cost, name): a(10), b(7), c(5), d(3), e(1)
    # Shard 0 gets: a(10), then d(3) → total 13
    # Shard 1 gets: b(7), then c(5), then e(1) → total 13
    s0, _ = shard_project_lists(
        projects, projects, shard=0, num_shards=2, ecosystem_projects=ep
    )
    s1, _ = shard_project_lists(
        projects, projects, shard=1, num_shards=2, ecosystem_projects=ep
    )
    assert s0 == ["a", "d"]
    assert s1 == ["b", "c", "e"]


def test_consistent_across_old_and_new():
    """A project in both lists always lands in the same shard."""
    old = ["alpha", "bravo", "charlie"]
    new = ["bravo", "charlie", "delta"]
    old_s0, new_s0 = shard_project_lists(
        old, new, shard=0, num_shards=2, ecosystem_projects={}
    )
    for project in ["bravo", "charlie"]:
        in_old_0 = project in old_s0
        in_new_0 = project in new_s0
        assert in_old_0 == in_new_0, f"{project} inconsistent across old/new"


def test_project_added_in_new():
    """A project only in the new list still gets sharded consistently."""
    old = ["a", "c"]
    new = ["a", "b", "c"]
    old_s0, new_s0 = shard_project_lists(
        old, new, shard=0, num_shards=2, ecosystem_projects={}
    )
    old_s1, new_s1 = shard_project_lists(
        old, new, shard=1, num_shards=2, ecosystem_projects={}
    )
    # All default cost 5; descending-by-cost then name: a, b, c
    # Greedy: a→shard0, b→shard1, c→shard0
    assert old_s0 == ["a", "c"]
    assert new_s0 == ["a", "c"]
    assert old_s1 == []
    assert new_s1 == ["b"]


def test_preserves_input_order():
    """Output order matches the input list order, not sorted order."""
    projects = ["delta", "alpha", "charlie", "bravo"]
    result, _ = shard_project_lists(
        projects, projects, shard=0, num_shards=2, ecosystem_projects={}
    )
    input_indices = [projects.index(p) for p in result]
    assert input_indices == sorted(input_indices)


def test_single_shard_returns_everything():
    """With num_shards=1, the sole shard gets all projects."""
    projects = ["x", "y", "z"]
    old, new = shard_project_lists(
        projects, projects, shard=0, num_shards=1, ecosystem_projects={}
    )
    assert old == projects
    assert new == projects


def test_more_shards_than_projects():
    """Shards beyond the project count are empty."""
    projects = ["a", "b"]
    all_sharded: list[str] = []
    for s in range(5):
        shard_result, _ = shard_project_lists(
            projects, projects, shard=s, num_shards=5, ecosystem_projects={}
        )
        all_sharded.extend(shard_result)
    assert sorted(all_sharded) == sorted(projects)

    for s in range(2, 5):
        shard_result, _ = shard_project_lists(
            projects, projects, shard=s, num_shards=5, ecosystem_projects={}
        )
        assert shard_result == [], f"shard {s} should be empty"


def test_unknown_projects_get_default_cost():
    """Projects not in ecosystem_projects use the default cost."""
    ep = _projects(("known", 100))
    projects = ["known", "unknown_a", "unknown_b"]

    s0, _ = shard_project_lists(
        projects, projects, shard=0, num_shards=2, ecosystem_projects=ep
    )
    s1, _ = shard_project_lists(
        projects, projects, shard=1, num_shards=2, ecosystem_projects=ep
    )

    assert "known" in s0 or "known" in s1
    assert sorted(s0 + s1) == sorted(projects)


def test_flaky_projects_cost_multiplied_by_flaky_runs():
    """Flaky projects have their cost multiplied by flaky_runs."""
    ep = _projects(("stable_big", 20), ("stable_small", 5), ("flaky", 10))
    projects = ["stable_big", "stable_small", "flaky"]

    # Without flaky multiplier: costs are 20, 5, 10.
    # Greedy: stable_big(20)→s0, flaky(10)→s1, stable_small(5)→s1
    s0, _ = shard_project_lists(
        projects, projects, shard=0, num_shards=2, ecosystem_projects=ep
    )
    s1, _ = shard_project_lists(
        projects, projects, shard=1, num_shards=2, ecosystem_projects=ep
    )
    assert s0 == ["stable_big"]
    assert s1 == ["stable_small", "flaky"]

    # With flaky_runs=3: flaky costs 30, stable_big 20, stable_small 5.
    # Greedy: flaky(30)→s0, stable_big(20)→s1, stable_small(5)→s1
    s0, _ = shard_project_lists(
        projects,
        projects,
        shard=0,
        num_shards=2,
        ecosystem_projects=ep,
        flaky_projects={"flaky"},
        flaky_runs=3,
    )
    s1, _ = shard_project_lists(
        projects,
        projects,
        shard=1,
        num_shards=2,
        ecosystem_projects=ep,
        flaky_projects={"flaky"},
        flaky_runs=3,
    )
    assert s0 == ["flaky"]
    assert s1 == ["stable_big", "stable_small"]


def _invoke_diff(tmp_path, extra_args):
    """Invoke the `diff` subcommand with minimal valid required args."""
    Repo.init(tmp_path)
    projects = tmp_path / "projects.txt"
    projects.write_text("a\nb\n")
    runner = CliRunner()
    return runner.invoke(
        cli,
        [
            "--repository",
            str(tmp_path),
            "diff",
            "--projects-old",
            str(projects),
            "--projects-new",
            str(projects),
            "--old",
            "HEAD",
            "--new",
            "HEAD",
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
            "--projects-old",
            str(projects),
            "--projects-new",
            str(projects),
            "--old",
            "abc",
            "--new",
            "def",
        ],
    )
    assert result.exit_code != 0
    assert "--repository is required" in result.output
