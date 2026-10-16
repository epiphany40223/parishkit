"""The Reminder WorkGroup page confirms in the review dialog (#750, #1356).

A real signed Administrator session edits the current campaign's Reminder
WorkGroup name through the page: Save answers the review dialog's
one-sentence confirmation (never a page), Apply answers with the change's
status, which the dialog follows, a refused name is the page again (400),
and every other refusal is a plain refusal the dialog shows.
"""

import re
from html import unescape
from uuid import uuid4

import pytest
from django.urls import reverse

from parishkit.stewardship.accounts.configuration_installation import install_request
from parishkit.stewardship.accounts.request_models import ConfigurationChangeRequest
from parishkit.stewardship.campaigns.configuration import (
    REMINDER_WORKGROUP_NAME,
    REMINDER_WORKGROUP_SLUG,
)
from parishkit.stewardship.campaigns.models import Campaign

from ..campaign_factory import campaign
from .auth_builders import signed_in
from .campaign_builders import add_draft, change
from .test_admin_navigation_postgresql import STEPS, flow_steps
from .test_campaign_views_postgresql import post, requested
from .test_parish_views_postgresql import digest

pytestmark = pytest.mark.django_db(transaction=True)

URL = f"/admin/campaign/{REMINDER_WORKGROUP_SLUG}/"
NAME = "Active: Stewardship 2027"


def setup(store):
    """The current draft campaign, with no Reminder WorkGroup yet."""
    result, _, _ = add_draft(store, store.active(), uuid4(), campaign())
    assert result.state == "applied"
    return Campaign.objects.get()


def token(response):
    """The signed preview the review's Apply posts."""
    assert response.status_code == 200, response.content
    found = re.search(r'name="preview" value="([^"]+)"', response.content.decode())
    return unescape(found.group(1))


def test_save_confirms_in_the_dialog_and_apply_follows_the_change(auth_service, google):
    """Save answers the dialog's confirmation, which repeats neither name;
    Apply answers with the change's status; once applied the page draws the
    new name in the form and in "Now"."""
    store = auth_service.store
    row = setup(store)
    browser, _ = signed_in()
    page = browser.get(URL)
    assert page.status_code == 200
    assert flow_steps(page.content) == (STEPS, "Make changes")
    text = unescape(page.content.decode())
    assert 'data-review-open="workgroup-review"' in text
    assert 'aria-describedby="workgroup-review-summary"' in text
    assert "settings-review" not in text
    review = post(
        browser, URL, {"action": "preview", "name": NAME, "base_digest": digest(page)}
    )
    assert review.status_code == 200
    shown = unescape(review.content.decode())
    # The review dialog's fragment (#750), never a page; a confirmation
    # (#1356) that repeats neither the typed name nor the current one.
    assert "data-review-answer" in shown and "<html" not in shown
    assert '<p id="workgroup-review-summary">From the next ParishSoft refresh' in (
        shown
    )
    assert NAME not in shown and "Current WorkGroup" not in shown
    confirmed = post(browser, URL, {"action": "confirm", "preview": token(review)})
    request = requested(confirmed)
    status_url = reverse("admin:configuration_request", args=[request])
    assert confirmed["Location"] == status_url
    install_request(store, request_id=request, correlation_id=uuid4())
    row.refresh_from_db()
    assert row.active_configuration.values["reminder_workgroup"] == NAME
    status = browser.get(status_url).content
    assert b"Applied: your change is saved" in status
    assert f'<a href="{URL}">Return to {REMINDER_WORKGROUP_NAME}</a>'.encode() in status
    # The page the dialog redraws from once the change is applied.
    applied = browser.get(URL)
    assert digest(applied) == store.active().digest
    assert b'data-workgroup-status="pending"' in applied.content
    assert NAME in unescape(applied.content.decode())
    # Clearing it says Reminders go to every eligible Family again.
    cleared = post(
        browser, URL, {"action": "preview", "name": "", "base_digest": digest(applied)}
    )
    assert "gets the Reminders again" in unescape(cleared.content.decode())


def test_refusals_keep_the_readers_version(auth_service, google):
    """A too-long name is the page again (400) with its error; nothing
    changed, a stale page and an out-of-date Apply are plain refusals for
    the dialog; none records anything, and the page takes no query."""
    store = auth_service.store
    row = setup(store)
    browser, _ = signed_in()
    loaded = digest(browser.get(URL))
    data = {"action": "preview", "name": "", "base_digest": loaded}
    unchanged = post(browser, URL, data)
    assert unchanged.status_code == 400
    assert unchanged.json()["refusal"]["message"] == (
        f"The {REMINDER_WORKGROUP_NAME} has not changed."
    )
    too_long = post(browser, URL, data | {"name": "x" * 500})
    assert too_long.status_code == 400
    assert "ParishSoft Family WorkGroup:" in unescape(too_long.content.decode())
    assert b'aria-invalid="true"' in too_long.content
    stale = post(browser, URL, data | {"name": NAME, "base_digest": "a" * 64})
    assert stale.status_code == 409
    assert stale.json()["refusal"]["message"] == (
        "This page changed in another tab or session."
    )
    proposal = token(post(browser, URL, data | {"name": NAME}))
    patch = [
        {
            "operation": "update",
            "section": "campaigns",
            "id": str(row.pk),
            "values": {"reminder_workgroup": "Changed elsewhere"},
        }
    ]
    assert change(store, store.active(), uuid4(), patch).state == "applied"
    refused = post(browser, URL, {"action": "confirm", "preview": proposal})
    assert refused.status_code == 409
    assert "This preview is out of date." in refused.json()["refusal"]["message"]
    assert not ConfigurationChangeRequest.objects.filter(
        patch__0__values__reminder_workgroup=NAME
    ).exists()
    # The page takes no query: no ?request= status reads any more.
    assert browser.get(URL + "?extra=1").status_code == 400
    assert browser.get(f"{URL}?request={uuid4()}").status_code == 400


def test_now_and_review_say_which_families_are_skipped(
    auth_service, google, monkeypatch
):
    """While a rename waits for a refresh, "Now" names the previous WorkGroup's
    Families that are still skipped, and each review says when its change
    applies (#1305): a rename at the next refresh, a clear and the name the
    newest refresh read at once. The newest refresh's read is stood in for;
    which Families it excludes is tested in
    test_reminder_workgroup_rename_postgresql.py."""
    from parishkit.stewardship.accounts import reminder_workgroup_views as views
    from parishkit.stewardship.source import workgroups

    read = {"name": NAME, "found": True, "family_duids": [1, 2]}
    monkeypatch.setattr(workgroups, "snapshot_evidence", lambda: read)
    monkeypatch.setattr(views, "snapshot_evidence", lambda: read)
    store = auth_service.store
    row = setup(store)
    browser, _ = signed_in()

    def review(name):
        """The review dialog fragment for setting the name to ``name``."""
        page = browser.get(URL)
        answer = post(
            browser,
            URL,
            {"action": "preview", "name": name, "base_digest": digest(page)},
        )
        assert answer.status_code == 200, answer.content
        return " ".join(unescape(answer.content.decode()).split())

    def now():
        """The "Now" panel's text."""
        text = unescape(browser.get(URL).content.decode())
        start = text.index('id="workgroup-status"')
        return " ".join(text[start : text.index("</section>", start)].split())

    # Nothing set: the snapshot's read applies to nobody.
    assert "No WorkGroup is set" in now()
    # Setting the name the newest refresh read applies at once.
    assert "already read “Active: Stewardship 2027”, so the change applies at once" in (
        review(NAME)
    )
    patch = [
        {
            "operation": "update",
            "section": "campaigns",
            "id": str(row.pk),
            "values": {"reminder_workgroup": "Renamed"},
        }
    ]
    assert change(store, store.active(), uuid4(), patch).state == "applied"
    shown = now()
    assert 'data-workgroup-status="pending"' in shown
    assert (
        "Until then, the 2 Families the newest refresh found in "
        "“Active: Stewardship 2027” still get no Reminders."
    ) in shown
    # Another rename keeps the same Families until the next refresh.
    shown = review("Renamed again")
    assert "The change takes effect at the next ParishSoft refresh." in shown
    assert "the 2 Families the newest refresh found in" in shown
    # A clear applies at once.
    assert "Turning it off applies at once" in review("")
    # A refresh found no WorkGroup by the new name and kept the set before.
    read.update(name="Renamed", found=False)
    shown = now()
    assert 'data-workgroup-status="missing"' in shown
    assert (
        "Until a refresh finds it, the 2 Families skipped before still get no "
        "Reminders."
    ) in shown
    assert "the 2 Families skipped now still get no Reminders" in review("Other")
    # With no read recorded, a pending name skips nobody meanwhile.
    read.clear()
    monkeypatch.setattr(workgroups, "snapshot_evidence", lambda: None)
    monkeypatch.setattr(views, "snapshot_evidence", lambda: None)
    assert "Until then, no Family's Reminders are skipped." in now()
    assert "Until then, no Family's Reminders are skipped." in review("Renamed again")
