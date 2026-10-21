"""Every LOCAL branch in the package code is listed, with its reason, in the spec.

#1399 removed the LOCAL branches that only refused an integration; the ones
that stay are listed in the local-environment specification's "Remaining
LOCAL branches" table. This test finds every comparison with the LOCAL
profile in ``src/parishkit`` and checks that the table names the module and
the top-level function, class or module-level name that holds it, and that
the table names nothing that holds none. A new LOCAL branch therefore needs a
stated reason in the spec before it merges.

It also pins the negative tests against another profile (``!= DEVELOPMENT``
and the like), which give LOCAL the other branch without naming it; the
spec prohibits new ones.
"""

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "src" / "parishkit"
SPEC = ROOT / "docs" / "specs" / "stewardship" / "local-environment" / "spec.md"
HEADING = "### Remaining LOCAL branches"


def _is_local_constant(node):
    """Whether a node is the string constant ``"local"``."""
    return isinstance(node, ast.Constant) and node.value == "local"


def _is_branch(node):
    """Whether one AST node compares with, or names, the LOCAL profile.

    The three forms the package uses: ``DeploymentProfile.LOCAL``, the
    environment-free helpers' ``LOCAL_PROFILE`` constant, and a comparison
    with the string ``"local"`` (the spike's ``--profile`` argument), whether
    ``==``, ``in`` a set, tuple or list, or a ``case "local":`` pattern.
    ``LOCAL`` on any name counts, so an aliased ``DeploymentProfile`` import
    cannot hide a branch, and so do ``DeploymentProfile("local")`` and
    ``DeploymentProfile["LOCAL"]``.
    """
    if isinstance(node, ast.Attribute):
        return node.attr == "LOCAL" and isinstance(node.value, ast.Name)
    if isinstance(node, ast.Call):
        return _is_profile_class(node.func) and any(
            _is_local_constant(arg) for arg in node.args
        )
    if isinstance(node, ast.Subscript):
        return (
            _is_profile_class(node.value)
            and isinstance(node.slice, ast.Constant)
            and node.slice.value == "LOCAL"
        )
    if isinstance(node, ast.Name):
        return node.id == "LOCAL_PROFILE" and isinstance(node.ctx, ast.Load)
    if isinstance(node, ast.Compare):
        # An operand is the constant itself or a set, tuple or list holding it.
        operands = [node.left, *node.comparators]
        operands += [
            element
            for item in operands
            if isinstance(item, (ast.Set, ast.Tuple, ast.List))
            for element in item.elts
        ]
        return any(_is_local_constant(item) for item in operands)
    if isinstance(node, ast.MatchValue):
        return _is_local_constant(node.value)
    return False


def _is_profile_class(node):
    """Whether a node names the ``DeploymentProfile`` class."""
    return isinstance(node, ast.Name) and node.id == "DeploymentProfile"


def _is_negative_profile_test(node):
    """Whether a comparison is ``!=``, ``is not`` or ``not in`` another profile.

    Such a test gives LOCAL, and any later profile, the other branch without
    naming it, which the spec prohibits for new code. Only
    ``DeploymentProfile`` members are scanned: a string such as
    ``"production"`` is usually a campaign mode, not a profile.
    """
    if not isinstance(node, ast.Compare):
        return False
    if not any(isinstance(op, (ast.NotEq, ast.IsNot, ast.NotIn)) for op in node.ops):
        return False
    return any(
        isinstance(item, ast.Attribute)
        and _is_profile_class(item.value)
        and item.attr != "LOCAL"
        for operand in [node.left, *node.comparators]
        for item in ast.walk(operand)
    )


# The negative profile tests that predate LOCAL. Each refuses in the safe
# direction and is a row of the spec's "Existing profile branches" table;
# a new one fails the test below.
NEGATIVE_PROFILE_TESTS = {
    ("stewardship/cli.py", "main"),
    ("stewardship/runtime_ingress.py", "production_hostname"),
    ("stewardship/runtime_topology.py", "render_runtime"),
    ("stewardship/services.py", "run_service"),
}


def _owner(statement):
    """The name a top-level statement defines, which the table uses."""
    if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return statement.name
    if isinstance(statement, ast.Assign) and isinstance(statement.targets[0], ast.Name):
        return statement.targets[0].id
    if isinstance(statement, ast.AnnAssign) and isinstance(statement.target, ast.Name):
        return statement.target.id
    return None


def code_branches(predicate=None):
    """Every (module, top-level name) pair in the package matching a predicate.

    The predicate defaults to ``_is_branch``. The module is relative to
    ``src/parishkit``. The definition of the LOCAL constants themselves
    (``LOCAL = "local"`` and ``LOCAL_PROFILE = "local"``) is not a
    comparison, so it is not found.
    """
    predicate = predicate or _is_branch
    found = set()
    for path in sorted(PACKAGE.rglob("*.py")):
        module = path.relative_to(PACKAGE).as_posix()
        for statement in ast.parse(path.read_text(encoding="utf-8")).body:
            if any(predicate(node) for node in ast.walk(statement)):
                found.add((module, _owner(statement) or "<module>"))
    return found


def spec_branches():
    """Every (module, name) pair the spec's table names.

    Each row's first cell is ``module.py``: ``name``, ``name``; ... with
    further ``other.py``: names groups separated by semicolons.
    """
    text = SPEC.read_text(encoding="utf-8")
    section = text.split(HEADING + "\n", 1)[1].split("\n#", 1)[0]
    listed = set()
    for line in section.splitlines():
        if not line.startswith("| `"):
            continue
        cell = line.split("|")[1]
        for group in cell.split(";"):
            module, _, names = group.partition(":")
            module = module.strip().strip("`")
            assert module.endswith(".py"), group
            for name in re.findall(r"`([^`]+)`", names):
                listed.add((module, name))
    return listed


def test_the_scan_finds_the_known_forms():
    """The scanner recognizes each form; otherwise the inventory is hollow."""
    found = code_branches()
    assert ("stewardship/runtime_topology.py", "_project_name") in found
    assert ("parishsoft_http_worker.py", "default_base_url") in found
    assert ("stewardship/local/spike.py", "execute_spike") in found
    assert ("stewardship/deployment.py", "PROXIED_PROFILES") in found


def test_the_scanner_catches_membership_and_case_forms():
    """Set, tuple and list membership and ``case "local":`` are branches."""
    sources = [
        'x == "local"',
        'x in {"local", "test"}',
        'x in ("local",)',
        'x not in ["development", "local"]',
        'match x:\n    case "local":\n        pass',
        'match x:\n    case "test" | "local":\n        pass',
        "p is Profile.LOCAL",
        'p == DeploymentProfile("local")',
        'p is DeploymentProfile["LOCAL"]',
    ]
    for source in sources:
        nodes = ast.walk(ast.parse(source))
        assert any(_is_branch(node) for node in nodes), source
    for source in ['x == "test"', 'x in {"test", "production"}', 'y = "local"']:
        nodes = ast.walk(ast.parse(source))
        assert not any(_is_branch(node) for node in nodes), source


def test_every_local_branch_has_a_reason_in_the_spec():
    """The spec's table and the code name exactly the same branches."""
    found = code_branches()
    listed = spec_branches()
    assert sorted(found - listed) == [], "LOCAL branches missing from the spec table"
    assert sorted(listed - found) == [], "spec table names code with no LOCAL branch"


def test_the_scanner_catches_negative_profile_tests():
    """``!=``, ``is not`` and ``not in`` another profile are found; LOCAL is not."""
    for source in [
        "p != DeploymentProfile.DEVELOPMENT",
        "p is not DeploymentProfile.PRODUCTION",
        "p not in {DeploymentProfile.TEST, DeploymentProfile.PRODUCTION}",
    ]:
        nodes = ast.walk(ast.parse(source))
        assert any(_is_negative_profile_test(node) for node in nodes), source
    for source in [
        "p is DeploymentProfile.PRODUCTION",
        "p is not DeploymentProfile.LOCAL",
        'mode != "production"',
    ]:
        nodes = ast.walk(ast.parse(source))
        assert not any(_is_negative_profile_test(node) for node in nodes), source


def test_no_new_negative_profile_test():
    """Only the negative profile tests that predate LOCAL remain."""
    assert code_branches(_is_negative_profile_test) == NEGATIVE_PROFILE_TESTS
