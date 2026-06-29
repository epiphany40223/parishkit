from __future__ import annotations

import argparse
import subprocess
import sys
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

SCRIPT = Path(__file__).parents[1] / "tools" / "prepare-release.py"


def load_release_module():
    """Import the standalone prepare-release.py script as a module.

    The release tool lives outside the importable package, so load it by
    file path and register it in sys.modules for the tests to exercise.
    """
    spec = spec_from_file_location("prepare_release", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = module_from_spec(spec)
    sys.modules[module.__name__] = module
    spec.loader.exec_module(module)
    return module


def release_args(**overrides):
    """Build an argparse Namespace of release options with safe defaults.

    Tests pass only the fields they care about as overrides; everything
    else defaults to a no-op so build_release_plan can run without flags.
    """
    values = {
        "bump": "auto",
        "version": None,
        "write_version": False,
        "write_notes": None,
        "notes_file": None,
        "build_artifacts": False,
        "tag": False,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def test_determine_bump_prefers_highest_semver_impact():
    """Verify determine_bump picks the largest impact across a commit set.

    A mix of docs/fix/feat yields a minor bump, a `feat!` breaking marker
    yields major, and a `BREAKING CHANGE:` body trailer also yields major.

    The setup stays local to this test so fixtures remain easy to understand
    and change.
    """
    release = load_release_module()

    assert (
        release.determine_bump(
            [
                release.Commit("docs: update readme"),
                release.Commit("fix: handle missing field"),
                release.Commit("feat: add importer"),
            ]
        )
        == "minor"
    )
    assert (
        release.determine_bump(
            [
                release.Commit("feat!: replace command names"),
                release.Commit("fix: handle missing field"),
            ]
        )
        == "major"
    )
    assert (
        release.determine_bump(
            [
                release.Commit(
                    "feat: add importer",
                    "BREAKING CHANGE: configuration keys changed",
                )
            ]
        )
        == "major"
    )


def test_next_version_from_bump():
    """Verify next_version_from applies none/patch/minor/major correctly."""
    release = load_release_module()

    assert release.next_version_from("1.2.3", "none") == "1.2.3"
    assert release.next_version_from("1.2.3", "patch") == "1.2.4"
    assert release.next_version_from("1.2.3", "minor") == "1.3.0"
    assert release.next_version_from("1.2.3", "major") == "2.0.0"


def test_tag_release_uses_current_project_version():
    """Verify select_release_version returns the current pyproject version.

    When use_current_version is set, the already-bumped project version is
    tagged as-is rather than recomputing a bump from the previous tag.
    """
    release = load_release_module()

    assert (
        release.select_release_version(
            current_version="1.3.0",
            previous_tag="v1.2.3",
            bump="none",
            explicit_version=None,
            use_current_version=True,
        )
        == "1.3.0"
    )


def test_format_release_notes_groups_commits():
    """Verify format_release_notes groups commits under typed headings.

    Features, fixes, and remaining commits land under their own sections,
    with a title and a "changes since <previous tag>" line.
    """
    release = load_release_module()
    plan = release.ReleasePlan(
        previous_tag="v1.2.3",
        current_version="1.2.3",
        release_version="1.3.0",
        bump="minor",
        commits=[
            release.Commit("feat: add importer"),
            release.Commit("fix: handle missing field"),
            release.Commit("docs: update readme"),
        ],
    )

    notes = release.format_release_notes(plan)

    assert "# ParishKit 1.3.0" in notes
    assert "Changes since `v1.2.3`." in notes
    assert "## Features" in notes
    assert "- feat: add importer" in notes
    assert "## Fixes" in notes
    assert "- fix: handle missing field" in notes
    assert "## Other Changes" in notes
    assert "- docs: update readme" in notes


def test_latest_release_uses_reachable_annotated_tags(monkeypatch):
    """Verify the latest release tag skips lightweight, non-annotated tags.

    Among reachable tags, v2.0.0 is lightweight (cat-file reports "commit")
    and v1.3.0 is excluded, so the newest annotated tag left is v1.2.3.
    """
    release = load_release_module()

    def fake_git(args):
        """Stub run_git: list merged tags and report each tag's object type.

        v2.0.0 is reported as a plain commit (lightweight tag); all others
        report as annotated "tag" objects. Unexpected calls fail loudly.
        """
        if args == ["tag", "--merged", "HEAD", "--list", "v*"]:
            return "v1.2.3\nv1.3.0\nv2.0.0"
        if args[:2] == ["cat-file", "-t"]:
            if args[-1] == "v2.0.0":
                return "commit"
            return "tag"
        raise AssertionError(args)

    monkeypatch.setattr(release, "run_git", fake_git)

    assert release.latest_annotated_release_tag(exclude_tag="v1.3.0") == "v1.2.3"


def test_workflow_release_notes_path_excludes_current_tag(monkeypatch):
    """Verify the release plan diffs from the prior tag, not the current one.

    With the requested version already tagged, build_release_plan must pick
    v1.2.3 as the previous tag and log commits in the v1.2.3..HEAD range.
    """
    release = load_release_module()
    calls = []

    def fake_git(args):
        """Stub run_git: record every call and answer the queries used here.

        Returns the merged tag list, an annotated type for any cat-file
        lookup, and a single commit for the v1.2.3..HEAD log range.
        """
        calls.append(args)
        if args == ["tag", "--merged", "HEAD", "--list", "v*"]:
            return "v1.2.3\nv1.3.0"
        if args[:2] == ["cat-file", "-t"]:
            return "tag"
        if args == ["log", "--format=%s%x1f%b%x1e", "v1.2.3..HEAD"]:
            return "fix: repair release notes\x1f\x1e"
        raise AssertionError(args)

    monkeypatch.setattr(release, "run_git", fake_git)
    monkeypatch.setattr(release, "read_project_version", lambda: "1.3.0")

    plan = release.build_release_plan(release_args(version="1.3.0"))

    assert plan.previous_tag == "v1.2.3"
    assert plan.release_version == "1.3.0"
    assert ["log", "--format=%s%x1f%b%x1e", "v1.2.3..HEAD"] in calls


def test_auto_bump_requires_explicit_bump_for_unclassified_commits(monkeypatch):
    """Auto bump fails when post-release commits have no semver classification."""
    release = load_release_module()

    def fake_git(args):
        """Stub git so a docs-only post-release history is visible."""
        if args == ["tag", "--merged", "HEAD", "--list", "v*"]:
            return "v1.2.3"
        if args[:2] == ["cat-file", "-t"]:
            return "tag"
        if args == ["log", "--format=%s%x1f%b%x1e", "v1.2.3..HEAD"]:
            return "Update deployment notes\x1f\x1e"
        raise AssertionError(args)

    monkeypatch.setattr(release, "run_git", fake_git)
    monkeypatch.setattr(release, "read_project_version", lambda: "1.2.3")

    try:
        release.build_release_plan(release_args())
    except RuntimeError as exc:
        assert "--bump" in str(exc)
    else:
        raise AssertionError("expected explicit bump/version requirement")


def test_tag_prerequisites_reject_dirty_worktree(monkeypatch):
    """Verify tag validation refuses to tag when the worktree is dirty."""
    release = load_release_module()
    plan = release.ReleasePlan(
        previous_tag="v1.2.3",
        current_version="1.3.0",
        release_version="1.3.0",
        bump="minor",
        commits=[],
    )

    monkeypatch.setattr(release, "worktree_is_clean", lambda: False)

    try:
        release.validate_tag_prerequisites(plan)
    except RuntimeError as exc:
        assert "uncommitted changes" in str(exc)
    else:
        raise AssertionError("expected dirty worktree rejection")


def test_tag_prerequisites_verify_head_version(monkeypatch):
    """Verify tag validation rejects a HEAD version that differs from the plan.

    A clean worktree still fails when HEAD's pyproject.toml version does not
    match the version about to be tagged.
    """
    release = load_release_module()
    plan = release.ReleasePlan(
        previous_tag="v1.2.3",
        current_version="1.3.0",
        release_version="1.3.0",
        bump="minor",
        commits=[],
    )

    monkeypatch.setattr(release, "worktree_is_clean", lambda: True)
    monkeypatch.setattr(release, "version_at_head", lambda: "1.2.3")

    try:
        release.validate_tag_prerequisites(plan)
    except RuntimeError as exc:
        assert "HEAD:pyproject.toml" in str(exc)
    else:
        raise AssertionError("expected HEAD version rejection")


def test_tag_requires_notes_file():
    """Verify --tag without --notes-file exits as an argparse usage error."""
    release = load_release_module()

    try:
        release.parse_args(["--tag"])
    except SystemExit as exc:
        assert exc.code == 2
    else:
        raise AssertionError("expected --tag without --notes-file to fail")


def test_create_annotated_tag_uses_notes_file(monkeypatch, tmp_path):
    """Verify main(--tag) creates the annotated tag from the notes file.

    The full tagging path runs end to end against stubbed git, asserting the
    tag is created with `git tag -a vX -F -` and the notes file piped as input.

    The setup stays local to this test so fixtures remain easy to understand
    and change.
    """
    release = load_release_module()
    notes_file = tmp_path / "RELEASE_NOTES.md"
    notes_file.write_text("release notes\n", encoding="utf-8")
    tag_calls = []

    def fake_git(args):
        """Stub run_git answering every query the tagging path makes.

        Covers tag listing, annotated-type lookups, the commit log range, a
        clean status, and the HEAD pyproject version. Other calls fail loudly.
        """
        if args == ["tag", "--merged", "HEAD", "--list", "v*"]:
            return "v1.2.3"
        if args[:2] == ["cat-file", "-t"]:
            return "tag"
        if args == ["log", "--format=%s%x1f%b%x1e", "v1.2.3..HEAD"]:
            return "feat: add release flow\x1f\x1e"
        if args == ["status", "--porcelain"]:
            return ""
        if args == ["show", "HEAD:pyproject.toml"]:
            return 'version = "1.3.0"\n'
        raise AssertionError(args)

    def fake_run(command, **kwargs):
        """Stub subprocess.run: record the tag command and report success."""
        tag_calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(release, "run_git", fake_git)
    monkeypatch.setattr(release, "read_project_version", lambda: "1.3.0")
    monkeypatch.setattr(release.subprocess, "run", fake_run)

    assert release.main(["--tag", "--notes-file", str(notes_file)]) == 0

    assert tag_calls == [
        (
            ["git", "tag", "-a", "v1.3.0", "-F", "-"],
            {
                "cwd": release.ROOT,
                "check": True,
                "text": True,
                "input": "release notes\n",
            },
        )
    ]


def test_build_artifacts_removes_existing_dist(monkeypatch, tmp_path):
    """Verify build_artifacts clears a stale dist dir before building.

    A pre-existing dist/ with leftover files must be removed so the build
    starts clean, and the build subprocess must still be invoked.
    """
    release = load_release_module()
    dist = tmp_path / "dist"
    stale = dist / "old.whl"
    dist.mkdir()
    stale.write_text("old", encoding="utf-8")
    run_calls = []

    def fake_run(command, **kwargs):
        """Stub subprocess.run: record build commands and report success."""
        run_calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(release, "DIST", dist)
    monkeypatch.setattr(release.subprocess, "run", fake_run)

    release.build_artifacts()

    assert not dist.exists()
    assert run_calls


def test_update_pyproject_version(tmp_path):
    """Verify update_pyproject_version rewrites the version line in place."""
    release = load_release_module()
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        """
[project]
name = "parishkit"
version = "1.2.3"
""".lstrip(),
        encoding="utf-8",
    )

    release.update_pyproject_version("1.2.4", pyproject)

    assert 'version = "1.2.4"' in pyproject.read_text(encoding="utf-8")
