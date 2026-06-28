#!/usr/bin/env python3
"""Prepare ParishKit release metadata and artifacts."""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = ROOT / "pyproject.toml"
DIST = ROOT / "dist"
TAG_RE = re.compile(
    r"^v(?P<major>0|[1-9]\d*)\.(?P<minor>0|[1-9]\d*)\.(?P<patch>0|[1-9]\d*)$"
)
BREAKING_RE = re.compile(r"^[a-zA-Z0-9_-]+(?:\([^)]+\))?!:")
BUMP_ORDER = {"none": 0, "patch": 1, "minor": 2, "major": 3}


@dataclass(frozen=True)
class Commit:
    subject: str
    body: str = ""


@dataclass(frozen=True)
class ReleasePlan:
    previous_tag: str | None
    current_version: str
    release_version: str
    bump: str
    commits: list[Commit]


def run_git(args: list[str], *, check: bool = True) -> str:
    """Run git and return its captured output."""
    completed = subprocess.run(
        ["git", *args],
        cwd=ROOT,
        check=check,
        text=True,
        capture_output=True,
    )
    return completed.stdout.strip()


def parse_version(value: str) -> tuple[int, int, int]:
    """Parse a semantic version string."""
    match = TAG_RE.match(value if value.startswith("v") else f"v{value}")
    if not match:
        raise ValueError(f"invalid semantic version: {value}")
    return (
        int(match.group("major")),
        int(match.group("minor")),
        int(match.group("patch")),
    )


def format_version(version: tuple[int, int, int]) -> str:
    """Render a (major, minor, patch) tuple as a dotted version string."""
    return ".".join(str(part) for part in version)


def read_project_version(pyproject: Path = PYPROJECT) -> str:
    """Read the current project version from pyproject.toml."""
    text = pyproject.read_text(encoding="utf-8")
    match = re.search(r'(?m)^version = "([^"]+)"$', text)
    if not match:
        raise RuntimeError(f"could not read project version from {pyproject}")
    return match.group(1)


def latest_annotated_release_tag(exclude_tag: str | None = None) -> str | None:
    """Return the highest annotated vVERSION tag reachable from HEAD.

    Only tags that match the semantic-version pattern and are true annotated
    tag objects count as releases; lightweight tags are skipped. The optional
    exclude_tag lets a re-run ignore the tag it is about to create.
    """
    output = run_git(["tag", "--merged", "HEAD", "--list", "v*"])
    tags = []
    for tag in output.splitlines():
        if tag == exclude_tag:
            continue
        if not TAG_RE.match(tag):
            continue
        # "cat-file -t" reports "tag" only for annotated tags, so this filters
        # out lightweight tags that should not be treated as releases.
        object_type = run_git(["cat-file", "-t", tag])
        if object_type == "tag":
            tags.append(tag)
    if not tags:
        return None
    # Compare by parsed version so 0.10.0 sorts above 0.9.0 (not lexically).
    return max(tags, key=parse_version)


def commits_since(tag: str | None) -> list[Commit]:
    """Collect commits made after the given tag (or all commits if None)."""
    revision = f"{tag}..HEAD" if tag else "HEAD"
    # Separate subject from body with a unit separator (0x1f) and delimit each
    # commit record with a record separator (0x1e). These control characters
    # never appear in commit text, so they parse unambiguously.
    output = run_git(
        [
            "log",
            "--format=%s%x1f%b%x1e",
            revision,
        ]
    )
    commits: list[Commit] = []
    for record in output.split("\x1e"):
        record = record.strip()
        if not record:
            continue
        subject, _, body = record.partition("\x1f")
        commits.append(Commit(subject=subject.strip(), body=body.strip()))
    return commits


def bump_for_commit(commit: Commit) -> str:
    """Classify a single commit's Conventional Commit type into a bump level."""
    if BREAKING_RE.match(commit.subject) or "BREAKING CHANGE:" in commit.body:
        return "major"
    if commit.subject.startswith("feat:") or commit.subject.startswith("feat("):
        return "minor"
    if commit.subject.startswith(("fix:", "fix(", "perf:", "perf(")):
        return "patch"
    return "none"


def determine_bump(commits: list[Commit]) -> str:
    """Determine the semantic version bump from commit messages."""
    bump = "none"
    for commit in commits:
        candidate = bump_for_commit(commit)
        if BUMP_ORDER[candidate] > BUMP_ORDER[bump]:
            bump = candidate
    return bump


def next_version_from(previous: str, bump: str) -> str:
    """Apply a bump level to a version, resetting lower components to zero."""
    major, minor, patch = parse_version(previous)
    if bump == "major":
        return format_version((major + 1, 0, 0))
    if bump == "minor":
        return format_version((major, minor + 1, 0))
    if bump == "patch":
        return format_version((major, minor, patch + 1))
    return format_version((major, minor, patch))


def select_release_version(
    *,
    current_version: str,
    previous_tag: str | None,
    bump: str,
    explicit_version: str | None,
    use_current_version: bool = False,
) -> str:
    """Choose the release version from CLI input or commit history."""
    if explicit_version:
        return format_version(parse_version(explicit_version))
    if use_current_version or previous_tag is None:
        return current_version
    return next_version_from(previous_tag.removeprefix("v"), bump)


def build_release_plan(args: argparse.Namespace) -> ReleasePlan:
    """Build the version and notes plan for a release."""
    current_version = read_project_version()
    excluded_tag = (
        f"v{format_version(parse_version(args.version))}" if args.version else None
    )
    previous_tag = latest_annotated_release_tag(exclude_tag=excluded_tag)
    commits = commits_since(previous_tag)
    detected_bump = determine_bump(commits)
    bump = detected_bump if args.bump == "auto" else args.bump

    release_version = select_release_version(
        current_version=current_version,
        previous_tag=previous_tag,
        bump=bump,
        explicit_version=args.version,
        use_current_version=args.tag,
    )

    return ReleasePlan(
        previous_tag=previous_tag,
        current_version=current_version,
        release_version=release_version,
        bump=bump,
        commits=commits,
    )


def format_release_notes(plan: ReleasePlan) -> str:
    """Format grouped release notes from commit history."""
    lines = [f"# ParishKit {plan.release_version}", ""]
    if plan.previous_tag:
        lines.append(f"Changes since `{plan.previous_tag}`.")
    else:
        lines.append("Initial release.")
    lines.append("")

    if not plan.commits:
        lines.extend(["No commits found for this release.", ""])
        return "\n".join(lines)

    groups = [
        ("Breaking Changes", "major"),
        ("Features", "minor"),
        ("Fixes", "patch"),
        ("Other Changes", "none"),
    ]
    for title, bump in groups:
        matching = [
            commit for commit in plan.commits if bump_for_commit(commit) == bump
        ]
        if not matching:
            continue
        lines.extend([f"## {title}", ""])
        for commit in matching:
            lines.append(f"- {commit.subject}")
        lines.append("")
    return "\n".join(lines)


def update_pyproject_version(version: str, pyproject: Path = PYPROJECT) -> None:
    """Update pyproject.toml with the release version."""
    text = pyproject.read_text(encoding="utf-8")
    updated, count = re.subn(
        r'(?m)^version = "[^"]+"$',
        f'version = "{version}"',
        text,
        count=1,
    )
    if count != 1:
        raise RuntimeError(f"could not update project version in {pyproject}")
    pyproject.write_text(updated, encoding="utf-8")


def worktree_is_clean() -> bool:
    """Return True when the working tree has no staged or unstaged changes."""
    return run_git(["status", "--porcelain"]) == ""


def version_at_head() -> str:
    """Read the project version from pyproject.toml as committed at HEAD.

    This reads the committed file rather than the working copy so tagging can
    confirm the release version was actually committed before the tag is made.
    """
    text = run_git(["show", "HEAD:pyproject.toml"])
    match = re.search(r'(?m)^version = "([^"]+)"$', text)
    if not match:
        raise RuntimeError("could not read project version from HEAD:pyproject.toml")
    return match.group(1)


def validate_tag_prerequisites(plan: ReleasePlan) -> None:
    """Validate the repository state before tagging a release."""
    if not worktree_is_clean():
        raise RuntimeError(
            "refusing to tag with uncommitted changes; commit release files first"
        )
    head_version = version_at_head()
    if head_version != plan.release_version:
        raise RuntimeError(
            "refusing to tag because HEAD:pyproject.toml has version "
            f"{head_version}, not {plan.release_version}; commit the release "
            "version before tagging"
        )


def build_artifacts() -> None:
    """Build source and wheel distributions."""
    if DIST.exists():
        shutil.rmtree(DIST)
    subprocess.run([sys.executable, "-m", "build"], cwd=ROOT, check=True)


def create_annotated_tag(plan: ReleasePlan, notes: str) -> None:
    """Create the annotated git release tag."""
    validate_tag_prerequisites(plan)
    tag = f"v{plan.release_version}"
    subprocess.run(
        ["git", "tag", "-a", tag, "-F", "-"],
        cwd=ROOT,
        check=True,
        text=True,
        input=notes,
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments and enforce release-flag invariants.

    The steps are kept explicit so operational behavior remains easy to
    audit and test.
    """
    parser = argparse.ArgumentParser(
        description="Prepare a ParishKit release from git history."
    )
    parser.add_argument(
        "--bump",
        choices=["auto", "major", "minor", "patch", "none"],
        default="auto",
        help="version bump to apply after the latest release tag",
    )
    parser.add_argument(
        "--version",
        help="explicit release version; overrides bump calculation",
    )
    parser.add_argument(
        "--write-version",
        action="store_true",
        help="update pyproject.toml to the selected release version",
    )
    parser.add_argument(
        "--write-notes",
        type=Path,
        help="write release notes to this path",
    )
    parser.add_argument(
        "--notes-file",
        type=Path,
        help="read release notes from this path when creating the release tag",
    )
    parser.add_argument(
        "--build-artifacts",
        action="store_true",
        help="build source and wheel distributions in dist/",
    )
    parser.add_argument(
        "--tag",
        action="store_true",
        help="create an annotated local vVERSION tag; does not push it",
    )
    args = parser.parse_args(argv)
    if args.tag:
        if args.write_version or args.write_notes:
            parser.error("--tag must be run after release file changes are committed")
        if args.notes_file is None:
            parser.error("--tag requires --notes-file so release notes stay consistent")
    return args


def main(argv: list[str] | None = None) -> int:
    """Run the command-line entry point."""
    args = parse_args(argv)
    plan = build_release_plan(args)
    notes = (
        args.notes_file.read_text(encoding="utf-8")
        if args.notes_file is not None
        else format_release_notes(plan)
    )

    print(f"Previous release: {plan.previous_tag or 'none'}")
    print(f"Current version: {plan.current_version}")
    print(f"Detected bump: {determine_bump(plan.commits)}")
    print(f"Selected bump: {plan.bump}")
    print(f"Release version: {plan.release_version}")
    print()
    print(notes, end="")

    if args.write_version:
        update_pyproject_version(plan.release_version)
    if args.write_notes:
        args.write_notes.write_text(notes, encoding="utf-8")
    if args.build_artifacts:
        build_artifacts()
    if args.tag:
        create_annotated_tag(plan, notes)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
