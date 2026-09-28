"""Ministry activity tells "active" apart from "in the current campaign"."""

from types import SimpleNamespace
from uuid import uuid4

from django.template.loader import render_to_string

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
            "table": paginate(rows, {}, carry=(("state", "all"),)),
            "query": "",
            "state": "all",
            "campaign_url": URL,
        },
    )
    assert "In current campaign" in html
    assert "This page turns Ministries on or off for parishioners." in html
    assert html.count(f'href="{URL}"') == 3
