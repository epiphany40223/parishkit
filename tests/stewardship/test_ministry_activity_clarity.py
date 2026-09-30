"""Ministry activity tells "active" apart from "in the current campaign"."""

from types import SimpleNamespace
from uuid import uuid4

from django.template.loader import render_to_string

from parishkit.stewardship.accounts import ministry_views
from parishkit.stewardship.accounts.ministry_views import campaign_ministries_url
from parishkit.stewardship.web.tables import paginate

CAMPAIGN = uuid4()
URL = f"/admin/campaign/{CAMPAIGN}/settings#ministry-selections"


def ministry(name, duid, *, included):
    """One catalog row as the Ministry activity view builds it."""
    return {"duid": duid, "name": name, "active": True, "included": included}


def test_campaign_link_names_the_ministry_selections():
    """The link opens the current campaign's settings at Ministry selections."""
    assert campaign_ministries_url(SimpleNamespace(current_campaign_id=CAMPAIGN)) == URL
    assert campaign_ministries_url(SimpleNamespace(current_campaign_id=None)) is None


def test_preview_explains_that_activation_does_not_include():
    """Selected Ministries outside the campaign are named, with the way to add them."""
    rows = [ministry("Choir", 1, included=True), ministry("Ushers", 2, included=False)]
    html = render_to_string(
        "stewardship/ministry-preview.html",
        {
            "changing": [],
            "unchanged": rows,
            "not_included": [rows[1]],
            "campaign_url": URL,
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
            "campaign_url": URL,
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
            "campaign_url": URL,
        },
    )
    assert "In current campaign" in html
    assert "This page turns Ministries on or off for parishioners." in html
    assert html.count(f'href="{URL}"') == 3


def test_only_activation_of_excluded_ministries_needs_the_note():
    """Inactivation, or no current campaign, never shows the inclusion note."""
    rows = [{"duid": 1, "included": True}, {"duid": 2, "included": False}]
    url = "/admin/campaigns/x/settings#ministry-selections"
    assert ministry_views.not_included(rows, True, url) == [rows[1]]
    assert ministry_views.not_included(rows, False, url) == []
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
