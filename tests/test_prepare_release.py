from __future__ import annotations

import argparse
import subprocess
import sys
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

SCRIPT = Path(__file__).parents[1] / "tools" / "prepare-release.py"


def load_release_module():
    spec = spec_from_file_location("prepare_release", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = module_from_spec(spec)
    sys.modules[module.__name__] = module
    spec.loader.exec_module(module)
    return module


def release_args(**overrides):
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
    release = load_release_module()

    assert release.next_version_from("1.2.3", "none") == "1.2.3"
    assert release.next_version_from("1.2.3", "patch") == "1.2.4"
    assert release.next_version_from("1.2.3", "minor") == "1.3.0"
    assert release.next_version_from("1.2.3", "major") == "2.0.0"


def test_tag_release_uses_current_project_version():
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
    release = load_release_module()

    def fake_git(args):
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
    release = load_release_module()
    calls = []

    def fake_git(args):
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


def test_tag_prerequisites_reject_dirty_worktree(monkeypatch):
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
    release = load_release_module()

    try:
        release.parse_args(["--tag"])
    except SystemExit as exc:
        assert exc.code == 2
    else:
        raise AssertionError("expected --tag without --notes-file to fail")


def test_create_annotated_tag_uses_notes_file(monkeypatch, tmp_path):
    release = load_release_module()
    notes_file = tmp_path / "RELEASE_NOTES.md"
    notes_file.write_text("release notes\n", encoding="utf-8")
    tag_calls = []

    def fake_git(args):
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
    release = load_release_module()
    dist = tmp_path / "dist"
    stale = dist / "old.whl"
    dist.mkdir()
    stale.write_text("old", encoding="utf-8")
    run_calls = []

    def fake_run(command, **kwargs):
        run_calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(release, "DIST", dist)
    monkeypatch.setattr(release.subprocess, "run", fake_run)

    release.build_artifacts()

    assert not dist.exists()
    assert run_calls


def test_update_pyproject_version(tmp_path):
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
