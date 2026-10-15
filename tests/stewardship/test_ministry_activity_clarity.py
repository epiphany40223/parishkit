"""The read-only Ministries page, with no local activity (#1267)."""

from types import SimpleNamespace
from uuid import uuid4

import pytest
from django.template.loader import render_to_string

from parishkit.stewardship.accounts import ministry_views
from parishkit.stewardship.accounts.ministry_views import campaign_ministries_link
from parishkit.stewardship.accounts.policy import Principal
from parishkit.stewardship.reports.ministries import (
    MinistryQuery,
    reads_every_ministry,
)
from parishkit.stewardship.web.tables import paginate

CAMPAIGN = uuid4()
URL = "/admin/campaign/settings/#ministry-selections"
LINK = {"url": URL, "label": "Campaign settings → Ministry selections"}


def ministry(name, duid, *, included):
    """One catalog row as the Ministries view builds it."""
    return {"duid": duid, "name": name, "included": included}


def test_campaign_link_opens_where_the_selections_change():
    """A draft's and a live campaign's selections both change on Campaign
    settings' Ministry selections (#1119); with no campaign there is none."""
    current = SimpleNamespace(current_campaign_id=CAMPAIGN)
    assert campaign_ministries_link(current) == LINK
    assert campaign_ministries_link(SimpleNamespace(current_campaign_id=None)) is None


def test_listing_links_campaign_settings_ministry_selections():
    """The listing's selections link to Campaign settings' Ministry list."""
    html = render_to_string(
        "stewardship/ministries.html",
        {
            "table": paginate(
                [ministry("Choir", 1, included=True)],
                {},
                carry=(),
                sorting=ministry_views.CATALOG_SORTING,
            ),
            "query": "",
            "campaign_link": LINK,
        },
    )
    assert f'<a href="{URL}">' in html


def test_listing_links_campaign_column_and_intro():
    """The column reads "In current campaign" and links to Ministry selections.

    The page's introduction waits in the closed About panel (#227); the
    one-sentence hint that inclusion changes elsewhere stays visible, with
    its link, above the table.
    """
    rows = [ministry("Choir", 1, included=True), ministry("Ushers", 2, included=False)]
    html = render_to_string(
        "stewardship/ministries.html",
        {
            "table": paginate(
                rows,
                {},
                carry=(),
                sorting=ministry_views.CATALOG_SORTING,
            ),
            "query": "",
            "campaign_link": LINK,
        },
    )
    assert "In current campaign" in html
    start = html.index('data-about-page="ministries"')
    about, visible = html[start:].split("</details>", 1)
    assert "It changes nothing." in about and "It changes nothing." not in visible
    assert "Choose this campaign's Ministries in" in visible
    assert html.count(f'href="{URL}"') == 3


def test_listing_has_no_activity_column_filter_or_actions():
    """There is no local activity (Administrator, 2026-10-13; #1267): no
    Activity column, sort, filter or badge, no row selection and no Review
    activation or Review inactivation buttons; the page changes nothing."""
    html = render_to_string(
        "stewardship/ministries.html",
        {
            "table": paginate(
                [
                    ministry("Choir", 1, included=True),
                    ministry("Ushers", 2, included=False),
                ],
                {},
                carry=(),
                sorting=ministry_views.CATALOG_SORTING,
            ),
            "query": "",
        },
    )
    assert ">Activity<" not in html and 'name="state"' not in html
    assert "status-badge" not in html and "sort=active" not in html
    assert "Review activation" not in html and "Review inactivation" not in html
    assert 'method="post"' not in html and "data-select-row" not in html
    assert "settings-review" not in html and "nactive" not in html
    assert set(ministry_views.CATALOG_SORTING.orders) == {"name", "included"}
    assert {"q", "page", "size", "sort"} == ministry_views.VIEW_FIELDS


def test_listing_sorts_every_column_on_the_server():
    """Headings sort the whole catalog; raw field names are refused."""
    import pytest

    rows = [
        ministry("choir", 9, included=False),
        ministry("Ushers", 2, included=True),
        ministry("Altar", 5, included=False),
    ]
    sorting = ministry_views.CATALOG_SORTING

    def names(token):
        """Ministry names in the order one sort token shows them."""
        table = paginate(rows, {"sort": token}, sorting=sorting)
        return [row["name"] for row in table.rows]

    assert names("name") == ["Altar", "choir", "Ushers"]
    assert names("-name") == ["Ushers", "choir", "Altar"]
    assert names("included") == ["Ushers", "choir", "Altar"]
    # No Ministry DUID is shown, so none sorts (#1123).
    for refused in ("payload__name", "duid", "-duid"):
        with pytest.raises(ValueError):
            paginate(rows, {"sort": refused}, sorting=sorting)
    html = render_to_string(
        "stewardship/ministries.html",
        {"table": paginate(rows, {"sort": "-name", "size": "25"}, sorting=sorting)},
    )
    assert 'aria-sort="descending"' in html and "Page 1 of 1" in html
    assert '<input type="hidden" name="sort" value="-name">' in html


def test_the_ministry_report_has_no_activity_filter():
    """The report's Ministry activity filter is gone for everyone (#1267):
    the field is refused as unknown, and the SQL selection still gets the
    neutral "any" its frozen contract reads. Only Staff and Administrators
    read every Ministry (#1135)."""
    for value in ("any", "active", "inactive", "unavailable"):
        with pytest.raises(ValueError):
            MinistryQuery.parse({"activity": value})
    query = MinistryQuery.parse({})
    assert "activity" not in query.form_values()
    assert query.selection_filters() == query.form_values() | {"activity": "any"}
    leader = Principal(uuid4(), frozenset({"ministry_leader"}), frozenset({9}))
    for role in ("staff", "administrator"):
        assert reads_every_ministry(Principal(uuid4(), frozenset({role})))
    assert not reads_every_ministry(leader)


@pytest.mark.parametrize(
    ("query", "message"),
    [
        ("", "No Ministries yet. They come from ParishSoft"),
        ("absent", "No Ministries match these filters. Clear Search by name"),
    ],
)
def test_an_empty_listing_says_why_and_what_to_do(query, message):
    """An empty listing tells a search that matches nothing apart from a
    catalog with no Ministries yet, and names the next step for each (#227)."""
    html = render_to_string(
        "stewardship/ministries.html",
        {
            "table": paginate(
                [],
                {},
                carry=(("q", query),),
                sorting=ministry_views.CATALOG_SORTING,
            ),
            "query": query,
            "campaign_link": LINK,
        },
    )
    assert message in html
