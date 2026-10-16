"""The Campaign setup stepper on the real campaign pages (#142 slice 4).

Real Google sessions, configuration requests and the installer: the stepper
shows on the campaign pages while the current campaign is a Testing draft,
reads each page step's completion from what was applied since the campaign
was created, offers the next step once a change is applied, and goes away
once the campaign leaves draft.
"""

import re
from uuid import uuid4

import pytest
from django.urls import reverse

from parishkit.stewardship.campaigns.lifecycle import Action

from ..test_share_forms import data_for
from .auth_builders import signed_in
from .campaign_builders import command
from .test_campaign_views_postgresql import apply, post, requested
from .test_parish_views_postgresql import token
from .test_share_views_postgresql import setup

pytestmark = pytest.mark.django_db(transaction=True)


def stepper(content):
    """The stepper region's HTML, or None when the page draws none."""
    match = re.search(
        r'<nav id="campaign-stepper".*?</nav>', content.decode(), flags=re.S
    )
    return match.group(0) if match else None


def status_of(region, label):
    """The status text the full list gives one step."""
    item = re.search(
        rf"{re.escape(label)}</span>\s*<span class=\"setup-step-status\">(.*?)</span>",
        region,
        flags=re.S,
    )
    return item.group(1) if item else None


def test_stepper_follows_an_applied_change_and_offers_the_next_step(
    auth_service, google
):
    """Share options completes once its change is applied, never by defaults."""
    store = auth_service.store
    row, previous, path = setup(store)
    browser, _ = signed_in()
    region = stepper(browser.get(path).content)
    assert region is not None
    assert "Step 2 of 7:" in region and "<strong>Share options</strong>" in region
    # Campaign settings is completed by creating the campaign; the default
    # share options the campaign started with complete nothing.
    assert status_of(region, "Campaign settings") == "Completed"
    assert status_of(region, "Share options") == "Current step — Not done yet"
    assert "1 of 7 steps completed" in region
    settings = reverse("admin:campaign_settings")
    content = reverse("admin:content_catalog")
    # The summary names the next step as text; Back and Continue are the
    # action row at the bottom of the page (#1404).
    assert "<span data-campaign-step-next>Next: Pages and emails</span>" in region
    assert "data-campaign-step-back" not in region
    # Apply a change on the page itself, as its review dialog does.
    data = data_for(previous) | {
        "base_digest": store.active().digest,
        "options-0-label": "Updated option",
    }
    proposal = token(post(browser, path, data))
    response = post(browser, path, {"action": "confirm", "preview": proposal})
    apply(store, response)
    # The dialog then redraws the page's regions from its own address: the
    # stepper marks the step completed (as on a reload).
    region = stepper(browser.get(path).content)
    assert status_of(region, "Share options") == "Current step — Completed"
    assert "2 of 7 steps completed" in region
    assert "Next: Pages and emails" in region
    # Change status, which the dialog follows, offers the same next step.
    status_url = reverse("admin:configuration_request", args=[requested(response)])
    assert (
        f'<a href="{content}">Continue to the next step: Pages and emails</a>'
        in browser.get(status_url).content.decode()
    )
    # The other steps' pages draw it too, each as its own current step.
    whole = browser.get(settings).content
    page = stepper(whole)
    assert "Step 1 of 7:" in page and b"data-campaign-step-back" not in whole
    assert "Step 7 of 7:" in stepper(browser.get(reverse("admin:go_live")).content)
    # Pages that are not steps, Home among them, draw none.
    assert stepper(browser.get("/admin/").content) is None
    # Once the campaign leaves draft there is no stepper and no Continue.
    command(row, uuid4(), Action.ACTIVATE)
    assert stepper(browser.get(settings).content) is None
    assert b"Continue to the next step" not in browser.get(status_url).content
