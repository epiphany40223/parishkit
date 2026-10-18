"""The shared Family-name link to a Family's Timeline (#1570).

``{% family_link %}`` and ``{% family_timeline_url %}`` draw every Admin
table's Family name the same way: the name (or the shared "Not in ParishSoft"
badge), linked to the Family's Timeline only when the row carries its
campaign record id and the page's viewer may open the Timeline, and the
"Inactive in ParishSoft" badge outside the link.
"""

from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import pytest
from django.template import Context, Template

from parishkit.stewardship.accounts.policy import Principal

FAMILY = UUID(int=81)
LINK = f'<a href="/admin/reports/families/{FAMILY}/">'
TEMPLATES = (
    Path(__file__).parents[2]
    / "src/parishkit/stewardship/accounts/templates/stewardship"
)


def viewer(*roles):
    """A request whose admitted principal holds ``roles``."""
    return SimpleNamespace(principal=Principal(UUID(int=1), frozenset(roles)))


# The page's viewer for template tests that expect Family names to link.
STAFF = viewer("staff")


def draw(source, request=STAFF, **values):
    """Render ``source`` with the stewardship tags, as a page's viewer sees it."""
    template = Template("{% load stewardship %}" + source)
    return template.render(Context({"request": request, **values}))


def test_a_known_name_links_its_timeline():
    """The name is escaped and opens the Timeline by the opaque record id."""
    html = draw("{% family_link id name %}", id=FAMILY, name="Adams, Ann & <b>")
    assert html == f"{LINK}Adams, Ann &amp; &lt;b&gt;</a>"


def test_marks_follow_the_shared_convention():
    """A missing Family is the badge alone; an inactive one adds its badge."""
    missing = draw("{% family_link id None %}", id=FAMILY)
    assert missing == (
        f'{LINK}<span class="status-badge" data-source-status="missing">'
        "Not in ParishSoft</span></a>"
    )
    inactive = draw("{% family_link id 'Adams, Ann' active=False %}", id=FAMILY)
    assert inactive == (
        f'{LINK}Adams, Ann</a> <span class="status-badge" '
        'data-source-status="inactive">Inactive in ParishSoft</span>'
    )
    # A missing Family is never also marked inactive.
    assert "inactive" not in draw("{% family_link id None active=False %}", id=FAMILY)
    assert "status-badge" not in draw("{% family_link id 'A' active=True %}", id=FAMILY)


@pytest.mark.parametrize(
    "request_",
    [
        viewer("ministry_leader"),
        SimpleNamespace(principal=Principal(UUID(int=2), family_id=UUID(int=3))),
        SimpleNamespace(),
        None,
    ],
    ids=["leader", "family", "no-principal", "no-request"],
)
def test_no_link_for_a_viewer_who_may_not_open_it(request_):
    """Ministry leaders, Family sessions and unknown viewers get plain names."""
    assert draw("{% family_link id 'Adams' %}", request_, id=FAMILY) == "Adams"
    assert draw("{% family_timeline_url id %}", request_, id=FAMILY) == ""


@pytest.mark.parametrize("roles", [("administrator",), ("staff",)])
def test_administrators_and_staff_get_the_link(roles):
    """Both roles may open the Timeline (its summary, for Staff)."""
    html = draw("{% family_timeline_url id %}", viewer(*roles), id=FAMILY)
    assert html == f"/admin/reports/families/{FAMILY}/"


def test_no_link_without_a_campaign_record():
    """A Family with no campaign record yet has no Timeline to open."""
    assert draw("{% family_link id 'Newcomer' %}", id=None) == "Newcomer"
    assert draw("{% family_timeline_url id %}", id="") == ""


@pytest.mark.parametrize(
    "name", ["directory.html", "financial-report.html", "find-family-results.html"]
)
def test_the_linked_tables_use_the_shared_helper(name):
    """Every Family-name link goes through the helper, never its own URL."""
    source = (TEMPLATES / name).read_text(encoding="utf-8")
    assert "{% family_link " in source or "{% family_timeline_url " in source
    assert "admin:timeline'" not in source and 'admin:timeline"' not in source


def test_no_template_links_the_timeline_by_hand():
    """Only the helper builds a Timeline address in an Admin template."""
    for path in sorted(TEMPLATES.rglob("*.html")):
        source = path.read_text(encoding="utf-8")
        assert "'admin:timeline'" not in source, path.name
        assert "admin:family_timeline" not in source, path.name
