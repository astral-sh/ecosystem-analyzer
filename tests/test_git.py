"""Test Git operations against isolated local repositories."""

import json
import os
import subprocess
import traceback
from pathlib import Path
from unittest.mock import call, patch

import pytest
from click.testing import CliRunner
from mypy_primer.model import Project

from ecosystem_analyzer.git import (
    _ty_repo_cache_path,
    get_latest_ty_commits,
    resolve_ty_repo,
)
from ecosystem_analyzer.installed_project import (
    InstalledProject,
    _get_project_cache_path,
)
from ecosystem_analyzer.main import cli
from ecosystem_analyzer.process import run
from ecosystem_analyzer.ty import Ty


def _git(repo: Path, *args: str) -> str:
    """Run Git in a test repository and strip surrounding output whitespace."""
    return run("git", *args, cwd=repo).strip()


def _commit(repo: Path, message: str) -> str:
    """Create a commit, even with no staged changes, and return its full SHA."""
    _git(repo, "commit", "--allow-empty", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Create a repository isolated from the user's Git configuration and caches.

    Its path contains a space to exercise argument handling. Only local file
    transports are allowed, so cloning cannot contact a network remote.
    """
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_AUTHOR_NAME", "Test")
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "test@example.com")
    monkeypatch.setenv("GIT_COMMITTER_NAME", "Test")
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", "test@example.com")
    monkeypatch.setenv("GIT_ALLOW_PROTOCOL", "file")
    path = tmp_path / "source repo"
    _git(tmp_path, "init", "--initial-branch=main", str(path))
    return path


@pytest.fixture
def signed_repo(repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Sign commits with a temporary SSH key and enable signature display.

    Set log.showSignature globally so cloned repositories also request the
    verification output that can interfere with parsing Git's stdout.
    """
    key = tmp_path / "signing_key"
    run("ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key), cwd=tmp_path)
    signers = tmp_path / "allowed_signers"
    signers.write_text(f"test@example.com {key.with_suffix('.pub').read_text()}")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "gitconfig"))
    _git(repo, "config", "--global", "log.showSignature", "true")
    _git(repo, "config", "--global", "gpg.ssh.allowedSignersFile", str(signers))
    _git(repo, "config", "gpg.format", "ssh")
    _git(repo, "config", "user.signingKey", str(key))
    _git(repo, "config", "commit.gpgSign", "true")
    return repo


def _install(
    repo: Path, *, exclude_newer: str | None = None, paths: list[str] | None = None
) -> InstalledProject:
    """Prepare a real project checkout without installing Python dependencies."""
    project = Project(
        location=repo.as_uri(), mypy_cmd=None, pyright_cmd=None, paths=paths
    )
    with patch.object(InstalledProject, "_install_dependencies"):
        return InstalledProject(project, exclude_newer=exclude_newer)


def test_resolve_linked_worktree(repo: Path, tmp_path: Path) -> None:
    """Accept a linked worktree and read its checked-out commit.

    A worktree created with `git worktree add --detach` has a .git file pointing
    to shared metadata. Resolution must return that worktree's path, not the
    main repository's path.
    """
    sha = _commit(repo, "Initial commit")
    worktree = tmp_path / "linked worktree"
    _git(repo, "worktree", "add", "--detach", str(worktree), sha)

    assert resolve_ty_repo(worktree) == worktree
    assert Ty(repository=worktree).commit_sha == sha


@pytest.mark.parametrize("suffix", [" ", "\n"], ids=["space", "newline"])
def test_resolve_repository_preserves_trailing_whitespace(
    repo: Path, tmp_path: Path, suffix: str
) -> None:
    r"""Preserve whitespace that is part of the repository directory's name.

    For example, "source repo " and "source repo\n" must not resolve to the
    sibling repository named "source repo", which contains a different commit.
    """
    _commit(repo, "Other repository")
    intended = repo.with_name(repo.name + suffix)
    _git(tmp_path, "init", "--initial-branch=main", str(intended))
    sha = _commit(intended, "Intended repository")

    resolved = resolve_ty_repo(intended)

    assert resolved == intended
    assert Ty(repository=resolved).commit_sha == sha


def test_resolve_invalid_repository(tmp_path: Path) -> None:
    """Reject a directory outside any repository and retain Git's error message.

    The failure must keep Git's exit code 128 and include its stderr in the
    formatted traceback, so a caller can see why repository discovery failed.
    """
    with pytest.raises(subprocess.CalledProcessError) as error:
        resolve_ty_repo(tmp_path)

    assert error.value.returncode == 128
    assert error.value.stderr.strip() in "".join(
        traceback.format_exception(error.value)
    )


def test_resolve_rejects_repository_subdirectory(repo: Path) -> None:
    """Reject a subdirectory instead of resolving it to an enclosing repository.

    For example, `--repository parent/child` must not return `parent` simply
    because Git searches upwards: a later checkout would modify the wrong repo.
    """
    subdirectory = repo / "child"
    subdirectory.mkdir()

    with pytest.raises(ValueError, match="not a Git working tree root"):
        resolve_ty_repo(subdirectory)


def test_failed_checkout_includes_git_error(repo: Path) -> None:
    """Keep Git's explanation when checking out a missing ty revision fails.

    `compile_for_commit("missing-revision")` must raise with Git's exit code 1
    and expose its stderr in the formatted traceback.
    """
    _commit(repo, "Initial commit")

    with pytest.raises(subprocess.CalledProcessError) as error:
        Ty(repository=repo).compile_for_commit("missing-revision")

    assert error.value.returncode == 1
    assert error.value.stderr.strip() in "".join(
        traceback.format_exception(error.value)
    )


@pytest.mark.parametrize("cache_type", ["project", "ty"])
def test_invalid_cache_does_not_modify_parent_repository(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cache_type: str
) -> None:
    """Reject cache directories that would make Git operate on a parent repo.

    Put an empty cache directory inside another checkout, for both project and
    ty caches. The enclosing checkout's HEAD, uncommitted file edit, and origin
    URL must remain unchanged when cache validation fails.
    """
    (repo / "tracked.py").write_text("remote = 1\n")
    _git(repo, "add", "tracked.py")
    _commit(repo, "Remote commit")
    parent = tmp_path / "parent"
    _git(tmp_path, "clone", str(repo), str(parent))
    local_commit = _commit(parent, "Local commit")
    tracked_file = parent / "tracked.py"
    tracked_file.write_text("local = 2\n")
    monkeypatch.setenv("XDG_CACHE_HOME", str(parent / "cache"))

    bare = tmp_path / "bare"
    if cache_type == "project":
        project = Project(location=repo.as_uri(), mypy_cmd=None, pyright_cmd=None)
        cache_path = _get_project_cache_path(project)
    else:
        _git(tmp_path, "clone", "--bare", str(repo), str(bare))
        cache_path = _ty_repo_cache_path(bare)
    cache_path.mkdir()

    if cache_type == "project":
        with pytest.raises(ValueError, match="not a Git working tree root"):
            _install(repo)
    else:
        with pytest.raises(ValueError, match="not a Git working tree root"):
            resolve_ty_repo(bare)

    assert _git(parent, "rev-parse", "HEAD") == local_commit
    assert tracked_file.read_text() == "local = 2\n"
    assert _git(parent, "remote", "get-url", "origin") == str(repo)


@pytest.mark.parametrize("origin_state", ["unchanged", "missing", "wrong_url"])
def test_resolve_bare_repository_refreshes_cache(
    repo: Path, tmp_path: Path, origin_state: str
) -> None:
    """Clone a bare ty repository into a working tree and refresh the same cache.

    After adding an upstream commit, resolution must fetch it into origin/main.
    Cover an already-correct origin as well as a missing remote or one pointing
    elsewhere; each case must end with origin pointing to the bare repository.
    """
    first = _commit(repo, "Initial commit")
    bare = tmp_path / "bare repo"
    _git(tmp_path, "clone", "--bare", str(repo), str(bare))

    cached = resolve_ty_repo(bare)
    assert cached != bare
    assert _git(cached, "rev-parse", "--is-bare-repository") == "false"
    assert _git(cached, "rev-parse", "HEAD") == first

    if origin_state == "missing":
        _git(cached, "remote", "remove", "origin")
    elif origin_state == "wrong_url":
        _git(cached, "remote", "set-url", "origin", str(tmp_path / "missing"))

    second = _commit(repo, "New commit")
    _git(bare, "fetch", str(repo), "main:main")

    assert resolve_ty_repo(bare) == cached
    assert _git(cached, "remote", "get-url", "origin") == str(bare)
    assert _git(cached, "rev-parse", "origin/main") == second


@pytest.mark.parametrize("num_commits", [1, 2, 10])
def test_latest_ty_commits_filter_and_order(repo: Path, num_commits: int) -> None:
    """Select recent ty commits from origin/main and return them oldest first.

    Requesting two of three matches returns the second and third in that order;
    requesting more than exist returns all three. Ignore a [ruff] commit whose
    body mentions [ty], and a newer local commit outside origin/main. Return
    only each message's first line, preserving non-ASCII text such as an em dash.
    """
    first = _commit(repo, "[ty] First")
    _commit(repo, "[ruff] Unrelated\n\n[ty] Only mentioned in the body")
    second = _commit(repo, "[ty] Second — details\non the next line\n\nBody")
    third = _commit(repo, "[ty] Third")
    _git(repo, "update-ref", "refs/remotes/origin/main", third)
    _commit(repo, "[ty] Local commit outside origin/main")

    expected = [
        (first, "[ty] First"),
        (second, "[ty] Second — details"),
        (third, "[ty] Third"),
    ]
    assert get_latest_ty_commits(repo, num_commits) == expected[-num_commits:]
    assert _git(repo, "rev-parse", "HEAD") == third


def test_latest_ty_commits_without_matches(repo: Path) -> None:
    """Return an empty list when origin/main contains no ty commits.

    For example, a history containing only "[ruff] Unrelated" produces [].
    """
    sha = _commit(repo, "[ruff] Unrelated")
    _git(repo, "update-ref", "refs/remotes/origin/main", sha)
    assert get_latest_ty_commits(repo, 10) == []


def test_latest_ty_commits_ignore_signatures(signed_repo: Path) -> None:
    """Read signed commit messages without parsing signature-verification output.

    With log.showSignature enabled, the result must still contain the commit's
    full SHA and "[ty] Signed commit", without extra signature text or fields.
    """
    sha = _commit(signed_repo, "[ty] Signed commit")
    _git(signed_repo, "update-ref", "refs/remotes/origin/main", sha)

    assert get_latest_ty_commits(signed_repo, 1) == [(sha, "[ty] Signed commit")]


def test_git_output_encoding(repo: Path) -> None:
    """Read UTF-8 history even when Git is configured to emit Latin-1 messages.

    "[ty] Café" must survive both checkout's stderr and the history log output.
    A separate `run("git", "log", ...)` call must preserve Git's original
    Latin-1 bytes through surrogate escapes when Git's encoding is not overridden.
    """
    sha = _commit(repo, "[ty] Café")
    _git(repo, "update-ref", "refs/remotes/origin/main", sha)
    _git(repo, "config", "i18n.logOutputEncoding", "ISO-8859-1")

    assert get_latest_ty_commits(repo, 1) == [(sha, "[ty] Café")]
    output = run("git", "log", "-1", "--format=%s", cwd=repo)
    assert output.encode("utf-8", errors="surrogateescape") == b"[ty] Caf\xe9\n"


def test_history_uses_commit_shas_and_messages(repo: Path, tmp_path: Path) -> None:
    """Pass full SHAs to analysis and write abbreviated SHAs and subjects to JSON.

    For two ty commits, the history CLI must call the mocked Manager oldest
    first, using full hashes. Its JSON uses the first seven SHA characters and
    the first message line, omitting a commit body such as "Body".
    """
    first = _commit(repo, "[ty] First\n\nBody")
    second = _commit(repo, "[ty] Second")
    _git(repo, "update-ref", "refs/remotes/origin/main", second)
    output = tmp_path / "history.json"

    with (
        patch("ecosystem_analyzer.main.Manager") as manager_class,
        patch("ecosystem_analyzer.main.get_ecosystem_projects", return_value={}),
    ):
        manager = manager_class.return_value
        manager.run_for_commit.return_value = [{"diagnostics": []}]
        result = CliRunner().invoke(
            cli, ["--repository", str(repo), "history", "--output", str(output)]
        )

    assert result.exit_code == 0, result.output
    assert manager.run_for_commit.call_args_list == [call(first), call(second)]
    assert json.loads(output.read_text()) == {
        "statistics": [
            {
                "commit": first[:7],
                "commit_message": "[ty] First",
                "total_diagnostics": 0,
            },
            {
                "commit": second[:7],
                "commit_message": "[ty] Second",
                "total_diagnostics": 0,
            },
        ]
    }


def test_project_clone_and_update(repo: Path) -> None:
    """Create a shallow project checkout and refresh that cache on reuse.

    The first installation reports the main branch and its initial SHA. After
    an upstream commit, another installation must reuse the same directory and
    report the new SHA.
    """
    first = _commit(repo, "Initial commit")
    project = _install(repo)
    assert project.current_commit == first
    assert project.default_branch == "main"
    assert (
        _git(project.root_directory, "rev-parse", "--is-shallow-repository") == "true"
    )

    second = _commit(repo, "New commit")
    updated = _install(repo)
    assert updated.root_directory == project.root_directory
    assert updated.current_commit == second


def test_failed_submodule_clone_aborts_project_preparation(
    repo: Path, tmp_path: Path
) -> None:
    """Abort preparation when a required submodule cannot be cloned.

    The parent can be a valid checkout while module/module.py is missing. Both
    the initial clone and a retry using that partial cache must raise; neither
    attempt may proceed to installing Python dependencies.
    """
    child = tmp_path / "child"
    _git(tmp_path, "init", "--initial-branch=main", str(child))
    (child / "module.py").write_text("value: int = 'type error'\n")
    _git(child, "add", "module.py")
    _commit(child, "Submodule commit")
    _git(repo, "submodule", "add", child.as_uri(), "module")
    _git(
        repo,
        "config",
        "--file",
        ".gitmodules",
        "submodule.module.url",
        (tmp_path / "missing").as_uri(),
    )
    _git(repo, "add", ".gitmodules")
    _commit(repo, "Unavailable submodule")
    project = Project(location=repo.as_uri(), mypy_cmd=None, pyright_cmd=None)
    cache_path = _get_project_cache_path(project)

    with patch.object(InstalledProject, "_install_dependencies") as install:
        with pytest.raises(subprocess.CalledProcessError):
            InstalledProject(project)

        assert (cache_path / ".git").is_dir()
        assert not (cache_path / "module" / "module.py").exists()

        # Retrying must not treat the partial checkout as a usable cache.
        with pytest.raises(subprocess.CalledProcessError):
            InstalledProject(project)

    install.assert_not_called()


def test_project_clone_and_update_recursive_submodules(
    repo: Path, tmp_path: Path
) -> None:
    """Populate and refresh submodules nested two levels deep.

    For a parent -> child -> leaf layout, cloning must include the leaf's file.
    After each parent records its child's new commit, refreshing the cache must
    update the leaf's module.py from "first = 1" to "second = 2".
    """
    leaf = tmp_path / "leaf"
    _git(tmp_path, "init", "--initial-branch=main", str(leaf))
    (leaf / "module.py").write_text("first = 1\n")
    _git(leaf, "add", "module.py")
    _commit(leaf, "Initial leaf")

    child = tmp_path / "child"
    _git(tmp_path, "init", "--initial-branch=main", str(child))
    _git(child, "submodule", "add", leaf.as_uri(), "nested leaf")
    _commit(child, "Initial child")
    _git(repo, "submodule", "add", child.as_uri(), "nested child")
    _commit(repo, "Initial parent")

    project = _install(repo)
    cached_module = (
        project.root_directory / "nested child" / "nested leaf" / "module.py"
    )
    assert cached_module.read_text() == "first = 1\n"

    (leaf / "module.py").write_text("second = 2\n")
    _git(leaf, "add", "module.py")
    new_leaf = _commit(leaf, "Update leaf")
    _git(child / "nested leaf", "fetch", "origin")
    _git(child / "nested leaf", "checkout", new_leaf)
    _git(child, "add", "nested leaf")
    new_child = _commit(child, "Update child")
    _git(repo / "nested child", "fetch", "origin")
    _git(repo / "nested child", "checkout", new_child)
    _git(repo, "add", "nested child")
    _commit(repo, "Update parent")

    project._clone_or_update()
    assert cached_module.read_text() == "second = 2\n"


@pytest.mark.parametrize("update_strategy", ["merge", "rebase"])
def test_project_submodule_update_checks_out_recorded_commit(
    repo: Path, tmp_path: Path, update_strategy: str
) -> None:
    """Use the recorded submodule commit, ignoring merge/rebase update strategies.

    Replace the child's main branch with unrelated history and have the parent
    record the new SHA. Even with update=merge or update=rebase in .gitmodules,
    refreshing the cache must check out that SHA without merging or replaying
    the old commit.
    """
    child = tmp_path / "child"
    _git(tmp_path, "init", "--initial-branch=main", str(child))
    (child / "module.py").write_text("value = 1\n")
    _git(child, "add", "module.py")
    _commit(child, "Initial child")
    _git(repo, "submodule", "add", child.as_uri(), "module")
    _git(
        repo,
        "config",
        "--file",
        ".gitmodules",
        "submodule.module.update",
        update_strategy,
    )
    _git(repo, "add", ".gitmodules")
    _commit(repo, "Initial parent")
    project = _install(repo)

    # The recorded commit can move to a different history after a force-push.
    _git(child, "checkout", "--orphan", "replacement")
    (child / "module.py").write_text("value = 2\n")
    _git(child, "add", "module.py")
    new_child = _commit(child, "Replacement child")
    _git(child, "branch", "-M", "main")
    _git(repo / "module", "fetch", "origin")
    _git(repo / "module", "checkout", new_child)
    _git(repo, "add", "module")
    _commit(repo, "Update parent")

    project._clone_or_update()

    assert _git(project.root_directory / "module", "rev-parse", "HEAD") == new_child


@pytest.mark.parametrize(
    ("cutoff", "use_first"),
    [
        ("2026-04-08T00:00:00Z", True),
        ("2026-04-09T22:00:00Z", False),
        ("2026-04-11T00:00:00Z", False),
        ("2026-03-01T00:00:00Z", False),
    ],
)
def test_project_timestamp_pinning(
    repo: Path, monkeypatch: pytest.MonkeyPatch, cutoff: str, use_first: bool
) -> None:
    """Pin using timezone-aware committer dates, not the earlier author dates.

    Both commits were authored in March but committed in April. An April 8
    cutoff selects the first; a cutoff exactly at the second commit's timestamp
    (April 9 at 22:00 UTC) selects the second. Keep HEAD when it already meets
    the cutoff, or when neither commit is old enough.
    """
    monkeypatch.setenv("GIT_AUTHOR_DATE", "2026-03-01T00:00:00Z")
    monkeypatch.setenv("GIT_COMMITTER_DATE", "2026-04-01T00:00:00Z")
    first = _commit(repo, "Before cutoff")
    monkeypatch.setenv("GIT_COMMITTER_DATE", "2026-04-10T00:00:00+02:00")
    second = _commit(repo, "After cutoff")

    project = _install(repo, exclude_newer=cutoff)
    assert project.current_commit == (first if use_first else second)


def test_project_timestamp_pinning_with_head_file(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Read HEAD's committer date even when a tracked file is also named HEAD.

    With an April 1 commit and an April 2 cutoff, preparation must keep the
    current commit. Git must interpret HEAD as the revision when reading its
    date, without rejecting the name as ambiguous with the tracked file.
    """
    (repo / "HEAD").write_text("A tracked file named HEAD.\n")
    _git(repo, "add", "HEAD")
    monkeypatch.setenv("GIT_COMMITTER_DATE", "2026-04-01T00:00:00Z")
    sha = _commit(repo, "Add file named HEAD")

    project = _install(repo, exclude_newer="2026-04-02T00:00:00Z")

    assert project.current_commit == sha


def test_project_timestamp_pinning_ignores_signatures(
    signed_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Parse committer dates without Git's signature-verification output.

    Given signed commits on April 1 and April 10, an April 8 cutoff must select
    the April 1 commit even when global log.showSignature is enabled.
    """
    monkeypatch.setenv("GIT_COMMITTER_DATE", "2026-04-01T00:00:00Z")
    first = _commit(signed_repo, "Before cutoff")
    monkeypatch.setenv("GIT_COMMITTER_DATE", "2026-04-10T00:00:00Z")
    _commit(signed_repo, "After cutoff")

    project = _install(signed_repo, exclude_newer="2026-04-08T00:00:00Z")

    assert project.current_commit == first


def test_project_timestamp_pinning_failed_fetch(
    repo: Path, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Warn and keep HEAD if fetching older history for timestamp pinning fails.

    Start with a valid checkout, request a cutoff before HEAD, and point origin
    at a missing repository. Failure to deepen the clone must leave the current
    commit in place and log the fallback, without raising.
    """
    sha = _commit(repo, "Initial commit")
    project = _install(repo)
    project._exclude_newer = "2000-01-01T00:00:00Z"
    _git(
        project.root_directory, "remote", "set-url", "origin", str(tmp_path / "missing")
    )

    project._pin_to_timestamp()

    assert project.current_commit == sha
    assert "failed to deepen clone, using HEAD as-is" in caplog.text


@pytest.mark.parametrize("paths", [None, ["scripts"]])
def test_script_discovery(
    repo: Path, monkeypatch: pytest.MonkeyPatch, paths: list[str] | None
) -> None:
    r"""Discover tracked inline Python scripts without changing their filenames.

    Names include " leading space.py", "a\nnewline.py", and "café.pyi". Select
    only .py/.pyi files containing the "# /// script" marker, ignore untracked
    files, and honor an optional restriction to the scripts directory. Check
    that both dependency-sync calls receive each selected script's exact path.
    """
    script_names = ["a space.py", "a\nnewline.py", "café.pyi"]
    scripts = repo / "scripts"
    scripts.mkdir()
    for name in [*script_names, "notes.md"]:
        (scripts / name).write_text("# /// script\n")
    (scripts / "ordinary.py").write_text("x = 1\n")
    (repo / " leading space.py").write_text("# /// script\n")
    _git(repo, "add", ".")
    _commit(repo, "Add scripts")
    project = _install(repo, paths=paths)
    (project.root_directory / "scripts" / "untracked.py").write_text("# /// script\n")
    monkeypatch.setenv("UV", "true")

    with patch(
        "ecosystem_analyzer.installed_project.subprocess.run", wraps=subprocess.run
    ) as run:
        project._install_script_dependencies()

    sync_commands = [c.args[0] for c in run.call_args_list if c.args[0][0] == "true"]
    expected = {str(project.root_directory / "scripts" / name) for name in script_names}
    if paths is None:
        expected.add(str(project.root_directory / " leading space.py"))
    assert len(sync_commands) == 2 * len(expected)
    assert {cmd[cmd.index("--script") + 1] for cmd in sync_commands} == expected


def test_script_discovery_without_matches(repo: Path) -> None:
    """Skip dependency synchronization when Git finds no inline scripts.

    Git grep's exit code 1 is a normal no-match result: only the grep command
    should run, without starting any dependency-sync subprocesses.
    """
    _commit(repo, "No scripts")
    project = _install(repo)

    with patch(
        "ecosystem_analyzer.installed_project.subprocess.run", wraps=subprocess.run
    ) as run:
        project._install_script_dependencies()

    assert run.call_count == 1


def test_script_discovery_reports_git_errors(repo: Path, tmp_path: Path) -> None:
    """Propagate Git discovery failures instead of treating them as no matches.

    Running git grep outside a repository must raise with exit code 128, unlike
    the expected exit code 1 when a valid repository has no matching scripts.
    """
    _commit(repo, "Initial commit")
    project = _install(repo)
    project._cache_path = tmp_path

    with pytest.raises(subprocess.CalledProcessError) as error:
        project._install_script_dependencies()

    assert error.value.returncode == 128
