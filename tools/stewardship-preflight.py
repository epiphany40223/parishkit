#!/usr/bin/env python3
"""Run the fast stewardship pre-flight checks before a PR joins a release train.

Implementers run this before marking a PR ready for a train. Train builders
run it on the assembled train tree before dispatching CI (#1156). Each check
targets a failure class that cost failed or cancelled train runs: JavaScript
that git merged cleanly but no longer parses, lint, wording/retired-name/help
guards, the traceability manifest, migration files and, with ``--browsers``,
Firefox/WebKit differences that only show up on Linux.

The script finds its checkout from its own path, so running
``tools/stewardship-preflight.py`` from any worktree checks that worktree. It
runs Python checks with the interpreter that runs it (use a venv that has the
development requirements) and puts this checkout's ``src`` first on
``PYTHONPATH``; it never uses ``python -I``, which would ignore that and
import whatever editable install the venv points at. No check needs real
credentials.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.request
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATIC_JS = "src/parishkit/stewardship"
BROWSER_TESTS = "tests/stewardship/browser/"
# The same digest-pinned image CI's browser jobs run in (#2053);
# test_browser_ci.py keeps the two and the playwright pin in step.
BROWSER_IMAGE_BASE = (
    "mcr.microsoft.com/playwright/python:v1.62.0-noble"
    "@sha256:aa81288e738725378becba5b3e06cb0f3a7f012a610e87e8d767a090ea3f740d"
)
BROWSER_ENGINES = ("firefox", "webkit")

# Fast, database-free guard tests that other PRs in a train most often break
# (#1156). They share one pytest process, so Django starts up only once.
GUARD_TESTS = (
    "tests/stewardship/test_traceability.py",
    "tests/stewardship/test_admin_wording.py",
    "tests/stewardship/test_task_wording.py",
    "tests/stewardship/test_local_hint_wording.py",
    "tests/stewardship/test_admin_help_guards.py",
    "tests/stewardship/test_setup_help.py",
    "tests/stewardship/test_admin_page_names.py",
    "tests/stewardship/test_page_name_placeholders.py",
    "tests/stewardship/test_button_guard.py",
    "tests/stewardship/test_content_defaults.py",
    "tests/stewardship/test_schema_migration_files.py",
    "tests/stewardship/test_source_migrations.py",
    "tests/stewardship/test_storage_migrations.py",
)


@dataclass(frozen=True)
class Result:
    """One check's outcome for the summary table."""

    name: str
    status: str  # "pass", "FAIL" or "skip"
    detail: str = ""
    seconds: float = 0.0


# ---------------------------------------------------------------------------
# Changed-file selection
# ---------------------------------------------------------------------------


def git(*args: str) -> str:
    """Run git in this checkout and return stdout (raises on failure)."""
    return subprocess.run(
        ["git", *args], cwd=ROOT, check=True, capture_output=True, text=True
    ).stdout


def changed_files(base: str) -> list[str]:
    """Return files changed since the merge base with ``base``, as repo paths.

    Committed, staged, unstaged and untracked (not ignored) files all count,
    so the check covers exactly what the PR will contain once committed.
    Deleted files are dropped because there is nothing left to check.
    """
    merge_base = git("merge-base", base, "HEAD").strip()
    tracked = git("diff", "-z", "--name-only", "--diff-filter=ACMR", merge_base)
    untracked = git("ls-files", "-z", "--others", "--exclude-standard")
    return sorted(set(nul_split(tracked)) | set(nul_split(untracked)))


def nul_split(output: str) -> list[str]:
    """Split git ``-z`` output, so paths with spaces or non-ASCII survive."""
    return [path for path in output.split("\0") if path]


def all_files() -> list[str]:
    """Return every tracked or untracked (not ignored) file in the checkout."""
    listing = git("ls-files", "-z", "--cached", "--others", "--exclude-standard")
    return sorted(set(nul_split(listing)))


def is_shell_script(path: str, first_line: str) -> bool:
    """Whether a file is a shell script, by suffix or by its shebang."""
    if path.endswith(".sh"):
        return True
    return bool(re.match(r"#!.*\b(ba|da|k)?sh\b", first_line))


def first_line(path: str) -> str:
    """Return the first line of a repo file, or "" when unreadable."""
    try:
        with open(ROOT / path, encoding="utf-8", errors="replace") as handle:
            return handle.readline()
    except OSError:
        return ""


def select(paths: Iterable[str]) -> dict[str, list[str]]:
    """Group existing files into the kinds the per-file checks need.

    ``markdown`` and ``shell`` feed pymarkdown and shellcheck; ``browser``
    lists browser test modules that run under Firefox or WebKit (a module that
    only runs Chromium has nothing for the Linux container to add).
    """
    groups: dict[str, list[str]] = {"markdown": [], "shell": [], "browser": []}
    for path in paths:
        full = ROOT / path
        if not full.is_file() or "node_modules" in Path(path).parts:
            continue
        if path.endswith(".md"):
            groups["markdown"].append(path)
        elif is_shell_script(path, first_line(path)):
            groups["shell"].append(path)
        elif (
            path.startswith(BROWSER_TESTS)
            and Path(path).name.startswith("test_")
            and path.endswith(".py")
        ):
            text = full.read_text(encoding="utf-8", errors="replace")
            if any(f'"{engine}"' in text for engine in BROWSER_ENGINES):
                groups["browser"].append(path)
    return groups


def stewardship_js() -> list[Path]:
    """Return every shipped stewardship JS file, excluding vendored libraries.

    These are always checked in full, not just changed ones: the collisions
    this catches come from another PR's change to the same file.
    """
    return sorted(
        path
        for path in (ROOT / STATIC_JS).rglob("*.js")
        if "vendor" not in path.parts and "node_modules" not in path.parts
    )


# ---------------------------------------------------------------------------
# Duplicate declaration check
# ---------------------------------------------------------------------------

# A "/" after one of these characters (or at the start) begins a regex
# literal; after an identifier, number or closing bracket it is division.
_REGEX_PRECEDERS = set("(,=:[!&|?{};+-*%<>~^")
_DECLARATIONS = ("const", "let", "class", "function")


def _skip_string(source: str, index: int) -> int:
    """Return the index just past the quoted string starting at ``index``."""
    quote = source[index]
    index += 1
    while index < len(source) and source[index] != quote:
        index += 2 if source[index] == "\\" else 1
    return index + 1


def _skip_regex(source: str, index: int) -> int:
    """Return the index just past the regex literal starting at ``index``."""
    index += 1
    in_class = False
    while index < len(source):
        char = source[index]
        if char == "\\":
            index += 2
            continue
        if char == "[":
            in_class = True
        elif char == "]":
            in_class = False
        elif char == "\n" or (char == "/" and not in_class):
            break
        index += 1
    index += 1
    while index < len(source) and (source[index].isalnum()):
        index += 1
    return index


def tokens(source: str) -> Iterable[tuple[int, str]]:
    """Yield (line, token) for identifiers and punctuation, skipping the rest.

    This is a small hand tokenizer, not a JavaScript parser. It removes
    comments, strings, regex literals and template-literal text, and yields
    the code inside ``${...}`` template substitutions, which is enough to
    follow braces and spot declarations. Its regex-versus-division guess
    uses the previous significant character, the usual heuristic.
    """
    index, line, previous = 0, 1, ""
    # Each "{" pushes "code"; each "${" pushes "template" so the matching
    # "}" resumes the enclosing template literal instead of closing a block.
    braces: list[str] = []
    in_template = False
    length = len(source)
    while index < length:
        char = source[index]
        if in_template:
            if char == "\\":
                index += 2
            elif char == "`":
                in_template, previous, index = False, "x", index + 1
            elif source.startswith("${", index):
                braces.append("template")
                in_template, previous, index = False, "{", index + 2
            else:
                line += char == "\n"
                index += 1
            continue
        if char == "\n":
            line += 1
            index += 1
        elif char.isspace():
            index += 1
        elif source.startswith("//", index):
            index = source.find("\n", index)
            index = length if index < 0 else index
        elif source.startswith("/*", index):
            end = source.find("*/", index + 2)
            end = length if end < 0 else end + 2
            line += source.count("\n", index, end)
            index = end
        elif char in "\"'":
            index, previous = _skip_string(source, index), "x"
        elif char == "`":
            in_template, index = True, index + 1
        elif char == "/" and (previous == "" or previous in _REGEX_PRECEDERS):
            index, previous = _skip_regex(source, index), "x"
        elif char.isalpha() or char in "_$":
            match = re.compile(r"[\w$]+").match(source, index)
            word = match.group()
            yield line, word
            # Keywords that can precede a regex literal behave like operators.
            previous = "(" if word in ("return", "typeof", "case") else "x"
            index = match.end()
        elif char == "}" and braces and braces[-1] == "template":
            braces.pop()
            in_template = True
            index += 1
        else:
            if char == "{":
                braces.append("code")
            elif char == "}" and braces:
                braces.pop()
            yield line, char
            previous = char if not char.isalnum() else "x"
            index += 1


def duplicate_declarations(source: str) -> list[tuple[int, str]]:
    """Return (line, name) for each repeated declaration in the same scope.

    A scope is one ``{...}`` block (or the file itself). Only statement-level
    ``const``, ``let``, ``class`` and ``function`` declarations count, and
    only the first name of a declarator list; destructuring is ignored.
    Repeated ``const``/``let`` is also a SyntaxError that ``node --check``
    reports, but a repeated ``function`` in a function body is legal, and
    the later one silently replaces the earlier one. Both happen when git
    cleanly merges two PRs that add the same helper (#1156).
    """
    # Assumes semicolon-terminated statements, as these files use: a repeated
    # function after a bare statement with no ";" (ASI) is missed, though a
    # repeated const/let there is still caught by node --check.
    scopes: list[dict[str, int]] = [{}]
    duplicates: list[tuple[int, str]] = []
    statement_start = True
    pending: str | None = None  # a declaration keyword awaiting its name
    for line, token in tokens(source):
        if pending is not None:
            if token == "*" and pending == "function":
                continue  # generator: function* name()
            pending = None
            if re.fullmatch(r"[A-Za-z_$][\w$]*", token):
                if token in scopes[-1]:
                    duplicates.append((line, token))
                else:
                    scopes[-1][token] = line
                statement_start = False
                continue
            # Destructuring ("const {a} = ...") or an anonymous class: the
            # token is ordinary code, so fall through and track its braces.
        if statement_start and token in _DECLARATIONS:
            pending = token
            continue
        if token == "async" and statement_start:
            continue  # "async function name" is still a declaration
        if token == "{":
            scopes.append({})
        elif token == "}" and len(scopes) > 1:
            scopes.pop()
        statement_start = token in ("{", "}", ";")
    return duplicates


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------


def python_env() -> dict[str, str]:
    """Return an environment that imports this checkout's package first."""
    env = dict(os.environ)
    paths = [str(ROOT / "src"), env.get("PYTHONPATH", "")]
    env["PYTHONPATH"] = os.pathsep.join(path for path in paths if path)
    return env


def run(name: str, command: list[str], timeout: int, verbose: bool) -> Result:
    """Run one check command, keeping its output only when it fails."""
    started = time.monotonic()
    try:
        completed = subprocess.run(
            command,
            cwd=ROOT,
            env=python_env(),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        seconds = time.monotonic() - started
        return Result(
            name,
            "FAIL",
            f"timed out after {seconds:.0f} s (limit {timeout} s)",
            seconds,
        )
    except FileNotFoundError as error:
        return Result(name, "FAIL", f"cannot run: {error}", time.monotonic() - started)
    seconds = time.monotonic() - started
    output = (completed.stdout + completed.stderr).strip()
    if completed.returncode == 0:
        if verbose and output:
            print(f"--- {name} ---\n{output}\n")
        return Result(name, "pass", "", seconds)
    print(f"--- {name} FAILED (exit {completed.returncode}) ---\n{output}\n")
    return Result(name, "FAIL", f"exit {completed.returncode}", seconds)


def counted(result: Result, count: int, noun: str) -> Result:
    """Prefix a result's detail with how many files the check covered."""
    detail = "; ".join(part for part in (f"{count} {noun}", result.detail) if part)
    return Result(result.name, result.status, detail, result.seconds)


def check_node(files: list[Path]) -> Result:
    """Run ``node --check`` on every shipped stewardship JS file."""
    started = time.monotonic()
    if shutil.which("node") is None:
        return Result("node --check", "FAIL", "node is not installed")
    failures = []
    limit = 60
    for path in files:
        try:
            completed = subprocess.run(
                ["node", "--check", str(path)],
                capture_output=True,
                text=True,
                timeout=limit,
            )
        except subprocess.TimeoutExpired:
            # Name what was killed, its limit and how long it ran (#287 rule).
            seconds = time.monotonic() - started
            detail = (
                f"killed node --check {path.relative_to(ROOT)} at its {limit} s"
                f" limit after {seconds:.0f} s in total"
            )
            print(f"--- node --check ---\n{detail}\n")
            return Result("node --check", "FAIL", detail, seconds)
        if completed.returncode:
            failures.append(path)
            name = path.relative_to(ROOT)
            print(f"--- node --check {name} ---\n{completed.stderr.strip()}\n")
    seconds = time.monotonic() - started
    if failures:
        return Result(
            "node --check", "FAIL", f"{len(failures)} of {len(files)} files", seconds
        )
    return Result("node --check", "pass", f"{len(files)} files", seconds)


def check_duplicates(files: list[Path]) -> Result:
    """Report repeated declarations in the same scope of any shipped JS file."""
    started = time.monotonic()
    found = []
    for path in files:
        for line, name in duplicate_declarations(path.read_text(encoding="utf-8")):
            found.append(f"{path.relative_to(ROOT)}:{line}: {name} declared again")
    seconds = time.monotonic() - started
    if found:
        print("--- duplicate JS declarations ---\n" + "\n".join(found) + "\n")
        return Result("JS duplicate names", "FAIL", f"{len(found)} found", seconds)
    return Result("JS duplicate names", "pass", f"{len(files)} files", seconds)


def fetch_axe(lock: Path, destination: Path) -> None:
    """Download the locked axe-core tarball and verify its integrity hash.

    The Playwright image has no npm, so the image build copies this tarball
    in instead of running ``npm ci``.
    """
    entry = json.loads(lock.read_text())["packages"]["node_modules/axe-core"]
    with urllib.request.urlopen(entry["resolved"], timeout=120) as response:
        data = response.read()
    digest = base64.b64encode(hashlib.sha512(data).digest()).decode()
    if f"sha512-{digest}" != entry["integrity"]:
        raise RuntimeError(f"axe-core download does not match {lock.name}")
    destination.write_bytes(data)


def browser_image() -> str:
    """Return the local Linux Playwright image tag, building it if missing.

    The image matches CI's browser engines: the pinned Playwright image plus
    ``fonts-dejavu-core`` (its default font otherwise differs from the CI
    runner's and changes text wrapping), the locked requirements and the
    axe-core package the accessibility checks load. The tag is a hash of
    those inputs, so it is rebuilt only when they change.
    """
    inputs = [
        ROOT / "requirements/stewardship.txt",
        ROOT / "requirements/stewardship-browser.txt",
        ROOT / "tests/stewardship/browser/package-lock.json",
    ]
    digest = hashlib.sha256(BROWSER_IMAGE_BASE.encode())
    for path in inputs:
        digest.update(path.read_bytes())
    tag = f"parishkit-preflight-browser:{digest.hexdigest()[:12]}"
    present = subprocess.run(["docker", "image", "inspect", tag], capture_output=True)
    if present.returncode == 0:
        return tag
    print(f"Building {tag} (first run only; a few minutes) ...", flush=True)
    with tempfile.TemporaryDirectory() as context:
        for path in inputs:
            shutil.copy(path, Path(context) / path.name)
        fetch_axe(inputs[-1], Path(context) / "axe-core.tgz")
        # ADD unpacks a local tarball; npm packs its files under "package/".
        (Path(context) / "Dockerfile").write_text(
            f"FROM {BROWSER_IMAGE_BASE}\n"
            "RUN apt-get update && apt-get install -y --no-install-recommends"
            " fonts-dejavu-core && rm -rf /var/lib/apt/lists/*\n"
            "COPY stewardship.txt stewardship-browser.txt /opt/req/\n"
            "RUN pip install --break-system-packages --no-cache-dir"
            " -r /opt/req/stewardship.txt -r /opt/req/stewardship-browser.txt\n"
            "ADD axe-core.tgz /opt/axe/\n"
            "RUN mkdir -p /opt/node_modules"
            " && mv /opt/axe/package /opt/node_modules/axe-core\n"
        )
        build = ["docker", "build", "-q", "-t", tag, context]
        subprocess.run(build, check=True, timeout=1800)
    return tag


def checkout_tar(paths: list[str]) -> bytes:
    """Return a tar of the checkout's files, including uncommitted edits."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        for path in paths:
            if (ROOT / path).is_file():
                archive.add(ROOT / path, arcname=path, recursive=False)
    return buffer.getvalue()


def check_browsers(files: list[str], timeout: int) -> Result:
    """Run changed browser test files for Firefox and WebKit on Linux.

    The checkout (with uncommitted edits) is copied into a fresh container,
    so nothing in the worktree changes. ``--require-no-skips`` matches CI.
    The container has a unique name, so a timeout removes exactly it.
    """
    name = "Linux Firefox/WebKit"
    if not files:
        return Result(name, "skip", "no changed Firefox/WebKit test files")
    if shutil.which("docker") is None:
        return Result(name, "FAIL", "docker is not installed")
    started = time.monotonic()
    try:
        image = browser_image()
    except (OSError, RuntimeError, subprocess.SubprocessError) as error:
        return Result(
            name, "FAIL", f"image build failed: {error}", time.monotonic() - started
        )
    container = f"parishkit-preflight-{os.getpid()}"
    selection = " or ".join(BROWSER_ENGINES)
    script = (
        "set -e; mkdir /work; cd /work; tar -x;"
        " ln -s /opt/node_modules tests/stewardship/browser/node_modules;"
        f" python3 -m pytest -q --require-no-skips -k '{selection}'"
        f" {' '.join(shlex.quote(path) for path in files)}"
    )
    command = [
        "docker", "run", "--rm", "-i", "--name", container,
        "-e", "PARISHKIT_RUN_BROWSER_TESTS=1", "-e", "PYTHONPATH=/work/src",
        image, "bash", "-c", script,
    ]  # fmt: skip
    try:
        completed = subprocess.run(
            command,
            input=checkout_tar(all_files()),
            capture_output=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        removed = subprocess.run(
            ["docker", "rm", "-f", container], capture_output=True, text=True
        )
        seconds = time.monotonic() - started
        detail = (
            f"killed container {container} after {seconds:.0f} s (limit {timeout} s)"
        )
        if removed.returncode:
            detail += f"; docker rm -f failed: {removed.stderr.strip()}"
        print(f"--- {name} FAILED ---\n{detail}\n")
        return Result(name, "FAIL", detail, seconds)
    seconds = time.monotonic() - started
    output = (completed.stdout + completed.stderr).decode(errors="replace").strip()
    tail = output.splitlines()[-1] if output else ""
    if completed.returncode:
        print(f"--- {name} FAILED ---\n{output}\n")
        return Result(name, "FAIL", tail, seconds)
    return Result(name, "pass", f"{len(files)} files: {tail}", seconds)


def summarize(results: list[Result], elapsed: float) -> int:
    """Print the pass/fail table and return the process exit status."""
    width = max(len(result.name) for result in results)
    print("Stewardship pre-flight")
    for result in results:
        line = f"  {result.status:4}  {result.name:<{width}}  {result.seconds:5.1f} s"
        print(f"{line}  {result.detail}".rstrip())
    failed = [result for result in results if result.status == "FAIL"]
    verdict = f"FAILED ({len(failed)} checks)" if failed else "passed"
    print(f"Pre-flight {verdict} in {elapsed:.0f} s")
    return 1 if failed else 0


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    """Parse command-line options."""
    parser = argparse.ArgumentParser(
        description="Fast local checks to run before a PR joins a release train "
        "and before a train is dispatched.",
    )
    parser.add_argument(
        "--base",
        default="origin/main",
        help="Find changed files since the merge base with this ref "
        "(default: origin/main)",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Check every Markdown and shell file, not only changed ones; with "
        "--browsers, run every Firefox/WebKit browser test file (a long run)",
    )
    parser.add_argument(
        "--browsers",
        action="store_true",
        help="Also run changed browser test files for Firefox and WebKit in "
        "the Linux Playwright container (needs Docker)",
    )
    parser.add_argument(
        "--browser-timeout",
        type=int,
        default=1800,
        help="Seconds allowed for the browser container run (default: 1800)",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="Show output of passing checks too"
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Run every check, print the summary and return the exit status."""
    args = parse_args(argv)
    started = time.monotonic()
    try:
        paths = all_files() if args.all else changed_files(args.base)
    except subprocess.CalledProcessError as error:
        print(f"Cannot find changed files against {args.base}: {error.stderr.strip()}")
        return 2
    groups = select(paths)
    scope = "all files" if args.all else f"{len(paths)} files changed since {args.base}"
    print(f"Checking {ROOT} ({scope})", flush=True)

    python = sys.executable
    js = stewardship_js()
    results = [check_node(js), check_duplicates(js)]
    results.append(
        run("ruff check", [python, "-m", "ruff", "check", "."], 300, args.verbose)
    )
    results.append(
        run(
            "ruff format",
            [python, "-m", "ruff", "format", "--check", "."],
            300,
            args.verbose,
        )
    )
    markdown = groups["markdown"]
    if markdown:
        command = [
            python,
            "-m",
            "pymarkdown",
            "--config",
            ".pymarkdown.json",
            "scan",
            *markdown,
        ]
        results.append(
            counted(
                run("pymarkdown", command, 300, args.verbose), len(markdown), "files"
            )
        )
    else:
        results.append(Result("pymarkdown", "skip", "no changed Markdown"))
    shell = groups["shell"]
    if not shell:
        results.append(Result("shellcheck", "skip", "no changed shell scripts"))
    elif shutil.which("shellcheck") is None:
        results.append(Result("shellcheck", "FAIL", "shellcheck is not installed"))
    else:
        result = run("shellcheck", ["shellcheck", *shell], 300, args.verbose)
        results.append(counted(result, len(shell), "files"))
    guard = [python, "-m", "pytest", "-q", "--ds=parishkit.stewardship.settings.test"]
    result = run("guard tests", [*guard, *GUARD_TESTS], 600, args.verbose)
    results.append(counted(result, len(GUARD_TESTS), "test files"))
    if args.browsers:
        results.append(check_browsers(groups["browser"], args.browser_timeout))
    return summarize(results, time.monotonic() - started)


if __name__ == "__main__":
    sys.exit(main())
