"""Browser tests must not wait with ``wait_for_function`` (#1155).

When its condition is not already true, ``page.wait_for_function`` re-polls
the predicate inside the page with eval, which the stewardship CSP
(``script-src 'self'``, no ``'unsafe-eval'``) refuses. The wait then fails
only on a slow runner, where the first check comes too early. Use an
``expect(locator)`` assertion or ``waits.eventually`` instead (see
``tests/stewardship/browser/waits.py``).
"""

import ast
from pathlib import Path

BROWSER = Path(__file__).resolve().parent / "browser"

# File name -> why its wait_for_function cannot be blocked by the CSP (for
# example, a page served without the stewardship policy). Empty by default.
ALLOWED = {}


def uses(source, filename="<test>"):
    """Return the line numbers where ``source`` refers to ``wait_for_function``.

    Any attribute access counts, not only a direct call, so aliasing the
    method (``wait = page.wait_for_function``) is caught; so is the bare
    name as a string, which catches ``getattr(page, "wait_for_function")``.
    """
    tree = ast.parse(source, filename=filename)
    return sorted(
        node.lineno
        for node in ast.walk(tree)
        if (isinstance(node, ast.Attribute) and node.attr == "wait_for_function")
        or (isinstance(node, ast.Constant) and node.value == "wait_for_function")
    )


def calls(path):
    """Return the line numbers where the file ``path`` uses wait_for_function."""
    return uses(path.read_text(encoding="utf-8"), str(path))


def test_no_eval_based_waits_in_browser_tests():
    """No browser test calls wait_for_function unless allowlisted with a reason."""
    found = {
        path.name: lines
        for path in sorted(BROWSER.rglob("*.py"))
        if (lines := calls(path)) and path.name not in ALLOWED
    }
    assert found == {}, (
        "wait_for_function polls with eval, which the CSP blocks; use "
        f"expect(locator) or waits.eventually instead: {found}"
    )


def test_allowlist_entries_have_reasons_and_are_still_needed():
    """Each allowlist entry names a reason and a file that still needs it."""
    for name, reason in ALLOWED.items():
        assert reason.strip(), name
        assert calls(BROWSER / name), f"{name} no longer calls wait_for_function"


def test_aliased_and_getattr_waits_are_caught():
    """Aliasing the method or fetching it by name does not slip past the guard."""
    assert uses("wait = page.wait_for_function\nwait('1')\n") == [1]
    assert uses("getattr(page, 'wait_for_function')('1')\n") == [1]
    assert uses("page.wait_for_function('1')\n") == [1]
    assert uses("page.wait_for_timeout(50)\n# wait_for_function\n") == []
