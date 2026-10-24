"""The shared Family-name link to a Family's Timeline (#1570).

``{% family_link %}`` and ``{% family_timeline_url %}`` draw every Admin
table's Family name the same way: the name (or the shared "Not in ParishSoft"
badge), linked to the Family's Timeline only when the row carries its
campaign record id and the page's viewer may open the Timeline, and the
"No longer in this campaign" badge outside the link.
"""

import re
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
        'data-source-status="left">No longer in this campaign</span>'
    )
    # A blank name is a missing one too, never an empty link.
    assert draw("{% family_link id '' %}", id=FAMILY) == missing
    badge = missing.removeprefix(LINK).removesuffix("</a>")
    assert draw("{% family_link None '' %}") == badge
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


# Every Admin template that names a Family (#1570 slice 1b). Each draws the
# name with the shared tag; the ones that must not link pass no record id.
# The guard below only sees ``family_name`` prints and a "Family" column, so a
# new page that names a Family through another key (``row.name``, a heading)
# must be added here to be checked for the helper.
FAMILY_NAME_PAGES = {
    "directory.html": "Campaign Family directory",
    "find-family-results.html": "Find a Family results",
    "financial-report.html": "Financial stewardship",
    "update-parishsoft.html": "Update ParishSoft, newest check",
    # The shared review worklist (#1681) draws the worklists' names: Census
    # changes (#1683), Ministry requests (#1684), Limitations (#1685) and the
    # others on it.
    "components/review-worklist.html": "Review worklists' Family column",
    "presence.html": "Families on the form now",
    "information.html": "Additional information request's heading",
    "weekly-digest.html": "Weekly report items",
    "delivery-refusals.html": "Refused addresses",
    "delivery-refusal.html": "Refused address",
    "source-form.html": "Families the form cannot open",
    "go-live-families.html": "Testing submissions (no link: Testing only)",
    "campaign-mail-families.html": "Send to chosen Families (no link: Testing)",
    "system-health-accept.html": "Refused load examples (no link)",
    "deliveries.html": "Sent mail Recipient (Production Family emails, #1562)",
}

# The justified exceptions: pages another open change converts. The Mail
# message page's Family line follows #1563; remove the entry when it lands.
NOT_YET = {"delivery.html": "#1563"}

# A printed Family name: a {{ ... }} expression naming family_name.
PRINTED_NAME = re.compile(r"\{\{[^}]*\bfamily_name\b[^}]*\}\}")
# A table column headed "Family", by the shared sort heading or a plain one.
FAMILY_COLUMN = re.compile(
    r'\{% translate "Family" as heading %\}'
    r'|<th scope="col"[^>]*>\{% translate "Family" %\}</th>'
)
HELPER = re.compile(r"\{% family_(link|timeline_url) ")


def violations(source):
    """Why ``source`` (one template) names a Family without the helper."""
    found = [f"prints {match}" for match in PRINTED_NAME.findall(source)]
    if FAMILY_COLUMN.search(source) and not HELPER.search(source):
        found.append("has a Family column but never uses family_link")
    return found


def test_the_guard_catches_a_name_drawn_by_hand():
    """The guard itself: a printed name, or a Family column without the tag."""
    assert violations("<td>{{ row.family_name }}</td>")
    assert violations("<td>{{ row.family_name|default:'' }}</td>")
    assert violations('<th scope="col">{% translate "Family" %}</th><td>{{ x }}</td>')
    assert not violations(
        '{% translate "Family" as heading %}{% sort_heading t "f" heading %}'
        "<td>{% family_link row.family_id row.family_name %}</td>"
    )


def test_no_admin_template_names_a_family_without_the_helper():
    """Every Family name and Family column goes through ``family_link``."""
    found = {}
    for path in sorted(TEMPLATES.rglob("*.html")):
        if path.name in NOT_YET:
            continue
        problems = violations(path.read_text(encoding="utf-8"))
        if problems:
            found[path.name] = problems
    assert found == {}


@pytest.mark.parametrize("name", sorted(FAMILY_NAME_PAGES))
def test_every_family_name_page_uses_the_helper(name):
    """Each listed page draws its Family names with the shared tag."""
    source = (TEMPLATES / name).read_text(encoding="utf-8")
    assert HELPER.search(source), FAMILY_NAME_PAGES[name]


def test_the_exceptions_are_still_needed():
    """An exception goes once its page no longer prints a name by hand."""
    for name, issue in NOT_YET.items():
        source = (TEMPLATES / name).read_text(encoding="utf-8")
        assert violations(source), f"{name} is converted; drop it ({issue})"
