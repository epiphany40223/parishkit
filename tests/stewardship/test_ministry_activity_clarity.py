"""Ministry activity tells "active" apart from "in the current campaign"."""

from types import SimpleNamespace
from uuid import uuid4

from django.template.loader import render_to_string

from parishkit.stewardship.accounts import ministry_views
from parishkit.stewardship.accounts.ministry_views import campaign_ministries_link
from parishkit.stewardship.web.tables import paginate

CAMPAIGN = uuid4()
URL = "/admin/campaign/settings/#ministry-selections"
LINK = {"url": URL, "label": "Campaign settings → Ministry selections"}


def ministry(name, duid, *, included):
    """One catalog row as the Ministry activity view builds it."""
    return {"duid": duid, "name": name, "active": True, "included": included}


def _current(monkeypatch, campaign):
    """Make ``campaign`` the row the link helper reads for the current campaign."""
    query = SimpleNamespace(first=lambda: campaign)
    objects = SimpleNamespace(
        select_related=lambda *_: SimpleNamespace(filter=lambda **_: query)
    )
    monkeypatch.setattr(ministry_views, "Campaign", SimpleNamespace(objects=objects))


def _campaign(state, *, locked):
    """A current campaign in ``state`` that asks about Ministries."""
    return SimpleNamespace(
        pk=CAMPAIGN,
        state=state,
        structural_locked=locked,
        active_configuration=SimpleNamespace(values={"modules": ["ministry"]}),
    )


def test_campaign_link_opens_where_the_selections_change(monkeypatch):
    """A draft changes them on Campaign settings; a live campaign on its own page.

    Campaign Ministries sits under Ministries (one home per concept), so
    Ministries links it directly while the campaign is live (NAV-10).
    """
    current = SimpleNamespace(current_campaign_id=CAMPAIGN)
    _current(monkeypatch, _campaign("draft", locked=False))
    assert campaign_ministries_link(current) == LINK
    _current(monkeypatch, _campaign("active", locked=True))
    assert campaign_ministries_link(current) == {
        "url": "/admin/parish/ministries/campaign/",
        "label": "Campaign Ministries",
    }
    assert campaign_ministries_link(SimpleNamespace(current_campaign_id=None)) is None


def test_listing_links_campaign_ministries_while_live():
    """A live campaign's selections link straight to Campaign Ministries."""
    link = {"url": "/admin/parish/ministries/campaign/", "label": "Campaign Ministries"}
    html = render_to_string(
        "stewardship/ministries.html",
        {
            "table": paginate(
                [ministry("Choir", 1, included=True)],
                {},
                carry=(("state", "all"),),
                sorting=ministry_views.CATALOG_SORTING,
            ),
            "query": "",
            "state": "all",
            "campaign_link": link,
        },
    )
    link = '<a href="/admin/parish/ministries/campaign/">Campaign Ministries</a>'
    assert link in html
    assert "Ministry selections" not in html


def test_preview_explains_that_activation_does_not_include():
    """Selected Ministries outside the campaign are named, with the way to add them."""
    rows = [ministry("Choir", 1, included=True), ministry("Ushers", 2, included=False)]
    html = render_to_string(
        "stewardship/ministry-preview.html",
        {
            "changing": [],
            "unchanged": rows,
            "not_included": [rows[1]],
            "campaign_link": LINK,
            "new_active": True,
        },
    )
    assert "Nothing to change" in html
    assert "Activation doesn’t add Ministries to the campaign" in html
    note = html.split("These are not in the current campaign:")[1].split("</p>")[0]
    assert "Ushers" in note and "Choir" not in note
    assert f'href="{URL}"' in html


def test_preview_without_excluded_selection_has_no_note():
    """When every selected Ministry is already included, no note is shown."""
    html = render_to_string(
        "stewardship/ministry-preview.html",
        {
            "changing": [],
            "unchanged": [ministry("Choir", 1, included=True)],
            "not_included": [],
            "campaign_link": LINK,
            "new_active": True,
        },
    )
    assert "Activation doesn’t add" not in html


def test_listing_links_campaign_column_and_intro():
    """The column reads "In current campaign" and links to Ministry selections."""
    rows = [ministry("Choir", 1, included=True), ministry("Ushers", 2, included=False)]
    html = render_to_string(
        "stewardship/ministries.html",
        {
            "table": paginate(
                rows,
                {},
                carry=(("state", "all"),),
                sorting=ministry_views.CATALOG_SORTING,
            ),
            "query": "",
            "state": "all",
            "campaign_link": LINK,
        },
    )
    assert "In current campaign" in html
    assert "This page turns Ministries on or off for parishioners." in html
    assert html.count(f'href="{URL}"') == 3


def test_only_activation_of_excluded_ministries_needs_the_note():
    """Inactivation, or no current campaign, never shows the inclusion note."""
    rows = [{"duid": 1, "included": True}, {"duid": 2, "included": False}]
    assert ministry_views.not_included(rows, True, LINK) == [rows[1]]
    assert ministry_views.not_included(rows, False, LINK) == []
    assert ministry_views.not_included(rows, True, None) == []


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
    assert names("-duid") == ["choir", "Altar", "Ushers"]
    assert names("included") == ["Ushers", "choir", "Altar"]
    with pytest.raises(ValueError):
        paginate(rows, {"sort": "payload__name"}, sorting=sorting)
    html = render_to_string(
        "stewardship/ministries.html",
        {"table": paginate(rows, {"sort": "-name", "size": "25"}, sorting=sorting)},
    )
    assert 'aria-sort="descending"' in html and "Page 1 of 1" in html
    assert '<input type="hidden" name="sort" value="-name">' in html
