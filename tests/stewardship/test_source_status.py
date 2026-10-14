"""One phrase and one badge for Members and Families no longer in ParishSoft.

ui-conventions "Members and Families no longer in ParishSoft" (#1216): the
shared words in ``source/source_status.py``, the ``{% source_status %}``
template tag, and a guard that keeps the retired phrases out of the pages.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest
from django.template import Context, Template

from parishkit.stewardship.source.source_status import (
    DECEASED_IN_PARISHSOFT,
    INACTIVE_IN_PARISHSOFT,
    NOT_IN_PARISHSOFT,
    family_status,
    source_status_label,
)

PACKAGE = Path(__file__).resolve().parents[2] / "src/parishkit/stewardship"
# Phrases the convention replaced. Installed SQL (schema/*.sql and frozen
# migrations) still prints "Unavailable Member" and "Unavailable Family"
# until later changes of #1216 replace those selections with a migration.
RETIRED = (
    "Unavailable Member",
    "Unavailable Family",
    "Not in the latest ParishSoft data",
    "Not in current ParishSoft data",
    "not in current ParishSoft data",
    "Name not in the current ParishSoft data",
    "No longer active in ParishSoft",
)


@pytest.mark.parametrize(
    ("status", "label"),
    [
        (None, ""),
        ("current", ""),
        ("missing", NOT_IN_PARISHSOFT),
        ("moved", NOT_IN_PARISHSOFT),
        ("inactive", INACTIVE_IN_PARISHSOFT),
        ("deceased", DECEASED_IN_PARISHSOFT),
    ],
)
def test_each_status_has_one_phrase(status, label):
    """Missing and moved read alike; current needs no mark."""
    assert source_status_label(status) == label


def test_an_unknown_status_fails_loudly():
    """A new status cannot reach a page without its words."""
    with pytest.raises(KeyError):
        source_status_label("archived")


@pytest.mark.parametrize(
    ("name", "active", "status"),
    [
        (None, True, "missing"),
        # A Family the data no longer has is never also called inactive.
        (None, False, "missing"),
        ("Example", True, "current"),
        ("Example", False, "inactive"),
        # A blank but present name is a data gap, not a removal.
        ("", True, "current"),
    ],
)
def test_family_status_reads_a_family_row(name, active, status):
    """A download's ParishSoft status cell comes from the row's own read."""
    assert family_status(name, active=active) == status


def _render(status):
    """The tag's output for one status."""
    template = Template("{% load stewardship %}[{% source_status status %}]")
    return template.render(Context({"status": status}))


def test_the_tag_renders_the_shared_badge():
    """The badge's text is the whole phrase; a current one renders nothing."""
    assert _render("deceased") == (
        '[<span class="status-badge" data-source-status="deceased">'
        "Deceased in ParishSoft</span>]"
    )
    assert _render("moved") == (
        '[<span class="status-badge" data-source-status="moved">'
        "Not in ParishSoft</span>]"
    )
    assert _render(None) == "[]"
    assert _render("current") == "[]"


def _sources():
    """Every template, script and Python module of the package."""
    for pattern in ("*.html", "*.js", "*.py"):
        yield from PACKAGE.rglob(pattern)


def test_pages_and_code_do_not_use_the_retired_phrases():
    """Templates and Python use the shared words, not the six old ones."""
    found = [
        f"{path.relative_to(PACKAGE)}: {phrase}"
        for path in _sources()
        if "migrations" not in path.parts
        for phrase in RETIRED
        if phrase in path.read_text()
    ]
    assert found == []


@pytest.mark.parametrize(
    "module",
    [
        "parishkit.stewardship.source.source_status",
        "parishkit.stewardship.source.loading",
    ],
)
def test_the_words_import_before_django_is_configured(module):
    """The installer imports them through ``source.loading`` before it sets
    up Django, so a module-level translation call there fails the deploy."""
    source = PACKAGE.parents[1]
    # A clean environment with only this checkout on the path: no settings
    # module, and never another checkout's installed copy of the package.
    env = {"PATH": os.environ.get("PATH", ""), "PYTHONPATH": str(source)}
    result = subprocess.run(
        [sys.executable, "-c", f"import {module}"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
