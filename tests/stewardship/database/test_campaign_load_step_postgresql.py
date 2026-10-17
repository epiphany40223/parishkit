"""Load ParishSoft data, step 2 of the Campaign setup stepper (#1404 PR 5).

Real Google sessions, the restricted web role and the real refresh page:
while the current campaign is a Testing draft, Refresh from ParishSoft is
the stepper's Load ParishSoft data. Its own button is the step's primary
until a load is under way, then the action row offers Continue; a failed
load says so on this step and every other one, with Try again; the
campaign's Families complete the step; outside the stepper the page is
unchanged.
"""

import re
from datetime import timedelta
from uuid import uuid4

import pytest
from django.urls import reverse

from parishkit.stewardship.campaigns.family_identity import FamilyStatus
from parishkit.stewardship.campaigns.lifecycle import Action
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.source.requests import TASK_TYPE

from .auth_builders import signed_in, unguarded
from .campaign_builders import command
from .credential_builders import keys, populate
from .test_background_grants_postgresql import task_login
from .test_campaign_stepper_postgresql import status_of, stepper
from .test_share_views_postgresql import setup

pytestmark = pytest.mark.django_db(transaction=True)
URL = reverse("admin:source_refresh")
INTRO = "This adds the campaign's Families, Family codes and giving."
# The step's own button before any load, its label in a span of its own.
OFFER = "data-campaign-load-start><span>Load the campaign's ParishSoft data</span>"


def web():
    """Every browser request runs under the real restricted web role."""
    return task_login(ServiceRole.WEB, exact=True, reconnect=True)


def page(browser, path=URL):
    """One page, read under the web role, as text."""
    with web():
        response = browser.get(path)
    assert response.status_code == 200
    return response.content.decode()


def actions(html):
    """The stepper's action row at the bottom of the page."""
    match = re.search(r'<div id="campaign-step-actions".*?</div>', html, flags=re.S)
    return match.group(0)


def refresh_run(state, **fields):
    """Seed one ParishSoft refresh run in ``state``, as the worker leaves it.

    Seeded as only a superuser could (the task tables' guards admit only
    their own owners), with the run as its own retry root. ``fields`` sets
    other columns, such as an earlier ``created_at``.
    """
    task = uuid4()
    with unguarded():
        TaskRun.objects.create(
            id=task, root_id=task, task_type=TASK_TYPE, state=state, **fields
        )
    return task


def test_load_step_offers_the_load_then_continue(auth_service, google):
    """The step's primary is the load until it runs, then Continue (PR 5)."""
    row, _, _ = setup(auth_service.store)
    browser, _ = signed_in()
    html = page(browser)
    region = stepper(html.encode())
    assert "Step 2 of 8:" in region
    assert "<strong>Load ParishSoft data</strong>" in region
    assert status_of(region, "Load ParishSoft data") == "Current step — Not done yet"
    assert INTRO in html
    # The page's own form is the primary, in place, and its answer also
    # redraws the action row; the step draws no load notice of its own.
    assert OFFER in html
    assert 'data-in-place-only="campaign-step-actions"' in html
    assert "data-campaign-load " not in html and "data-campaign-load>" not in html
    row_html = actions(html)
    assert f'href="{reverse("admin:campaign_settings")}"' in row_html
    assert "Continue" not in row_html
    # Once a load is under way, Continue to the next step; Refresh now is
    # secondary, since Continue is the step's one primary.
    refresh_run("queued")
    html = page(browser)
    row_html = actions(html)
    assert "Continue to Share options" in row_html
    assert f'href="{reverse("admin:share_settings")}"' in row_html
    assert "data-campaign-load-start" not in html
    assert re.search(
        r'<button type="submit" class="[^"]*secondary[^"]*"[^>]*>'
        r"<span>Refresh now</span>",
        html,
    )
    # Every other step says the data is loading.
    content = page(browser, reverse("admin:content_catalog"))
    assert "ParishSoft data is loading; Go-live readiness waits for it." in content
    # Outside the stepper (the campaign is no longer a draft) the page is
    # the ordinary Refresh from ParishSoft.
    command(row, uuid4(), Action.ACTIVATE)
    html = page(browser)
    assert stepper(html.encode()) is None and INTRO not in html
    assert "data-in-place-only " in html or "data-in-place-only data-in" in html


def test_failed_load_says_so_and_offers_try_again(auth_service, google):
    """A failed load is named on the step and every other one (PR 5, L6)."""
    setup(auth_service.store)
    browser, _ = signed_in()
    task = refresh_run("failed")
    task_link = f'href="{reverse("admin:background_task_page", args=[task])}"'
    html = page(browser)
    assert "The campaign's ParishSoft data did not load." in html
    assert task_link in html
    assert "data-campaign-load-start><span>Try again</span>" in html
    assert "Continue" not in actions(html)
    content = page(browser, reverse("admin:content_catalog"))
    assert "The campaign's ParishSoft data did not load." in content
    assert task_link in content and ">Try again<" in content
    assert '<input type="hidden" name="return_step" value="content">' in content
    # A cancelled run reads the same; a newer run under way replaces it.
    cancelled = refresh_run("cancelled")
    assert f'href="{reverse("admin:background_task_page", args=[cancelled])}"' in (
        page(browser)
    )
    refresh_run("queued")
    html = page(browser)
    assert "did not load" not in html and "Continue to Share options" in actions(html)


def test_families_complete_the_load_step(auth_service, google):
    """The campaign's Families complete the step, with no stored review."""
    row, _, _ = setup(auth_service.store)
    browser, _ = signed_in()
    # Even after a failed run, Families mean the data is loaded.
    refresh_run("failed")
    populate(row, keys(), [FamilyStatus(1, True, True, True, True)])
    html = page(browser)
    region = stepper(html.encode())
    assert status_of(region, "Load ParishSoft data") == "Current step — Completed"
    assert "2 of 8 steps completed" in region
    assert "did not load" not in html and "data-campaign-load-start" not in html
    assert "Continue to Share options" in actions(html)
    # Every other step drops the load notice, and Home goes on past it.
    assert "data-campaign-load" not in page(browser, reverse("admin:content_catalog"))
    assert "Continue campaign setup: Share options" in page(browser, "/admin/")


def test_only_refreshes_since_creation_and_families_count(auth_service, google):
    """A run before the campaign, or one with no Families, is not a load.

    A refresh requested before the campaign was created could not load it
    (its request binds no campaign), so even a failed one leaves the plain
    offer. A newest run that succeeded but left no Families also reads as
    the offer: only the Families complete the step.
    """
    row, _, _ = setup(auth_service.store)
    browser, _ = signed_in()
    refresh_run("failed", created_at=row.created_at - timedelta(days=1))
    html = page(browser)
    assert OFFER in html and "did not load" not in html
    assert "Continue" not in actions(html)
    refresh_run("succeeded")
    html = page(browser)
    region = stepper(html.encode())
    assert status_of(region, "Load ParishSoft data") == "Current step — Not done yet"
    assert OFFER in html and "did not load" not in html
    assert "Continue" not in actions(html)
