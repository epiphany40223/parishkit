"""Load ParishSoft data, step 2 of the Campaign setup stepper (#1404 PR 5).

Real Google sessions, the restricted web role and the real refresh page:
while the current campaign is a Testing draft, Refresh from ParishSoft is
the stepper's Load ParishSoft data, which shows exactly one state: not
loaded (the load button), loading (the refresh's stage boxes following the
run, no button and no Continue), loaded (the Family count and Continue) or
failed (See what went wrong and Try again), with no Refresh now and no
queue wording; a failed load says so on every other step too; the
campaign's Families complete the step; outside the stepper the page is
unchanged.
"""

import re
from datetime import timedelta
from uuid import uuid4

import pytest
from django.urls import reverse
from django.utils import timezone

from parishkit.stewardship.accounts.models import PortalSession
from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.accounts.stages import QUEUED
from parishkit.stewardship.campaigns.family_identity import FamilyStatus
from parishkit.stewardship.campaigns.lifecycle import Action
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.source.canonical import canonical_payload
from parishkit.stewardship.source.refresh_models import SourceRefreshRequest
from parishkit.stewardship.source.requests import TASK_TYPE

from .auth_builders import signed_in, unguarded
from .campaign_builders import command
from .credential_builders import keys, populate
from .test_background_grants_postgresql import task_login
from .test_campaign_stepper_postgresql import status_of, stepper
from .test_share_views_postgresql import setup

pytestmark = pytest.mark.django_db(transaction=True)
URL = reverse("admin:source_refresh")
NO_FAMILIES = "The campaign's ParishSoft data loaded, but no Families matched."
NOT_LOADED = "This campaign's ParishSoft data hasn't been loaded yet."
LOADING = "Loading the campaign's ParishSoft data…"
# The step's own button before any load.
OFFER = "data-campaign-load-start>Load the campaign's ParishSoft data</button>"
# The regular page's wording, never drawn on the step.
QUEUE = "No second refresh starts beside it"


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


def refresh_run(state, kind="full", **fields):
    """Seed one ParishSoft refresh run in ``state``, as the worker leaves it.

    Seeded as only a superuser could (the task tables' guards admit only
    their own owners), with the run as its own retry root and a request of
    the given ``kind`` ("full" or "delta"). ``fields`` sets other columns,
    such as an earlier ``created_at``.
    """
    task, request = uuid4(), uuid4()
    canonical, digest = canonical_payload({"campaign_id": None, "periods": []})
    with unguarded():
        TaskRun.objects.create(
            id=task,
            root_id=task,
            task_type=TASK_TYPE,
            state=state,
            domain_request_id=request,
            **fields,
        )
        SourceRefreshRequest.objects.create(
            id=request,
            task_root_id=task,
            configuration_id=SystemConfiguration.objects.get().active_configuration_id,
            organization_id=12345,
            kind=kind,
            window_canonical=canonical,
            window_digest=digest,
        )
    return task


def state(html):
    """The step's one state, as its status region names it."""
    return re.search(r'data-campaign-load-state="([a-z]+)"', html).group(1)


def summary(html):
    """The load region's visible summary line, without its sizing copies.

    The region also draws invisible, aria-hidden copies of every state's
    wording to size that line (#1618), so wording checks read the line itself.
    """
    return re.search(
        r'<p class="campaign-load-summary"[^>]*>(.*?)</p>', html, re.DOTALL
    ).group(1)


def test_load_step_shows_one_state_at_a_time(auth_service, google):
    """Not loaded, then loading with no button, then loaded (PR 5, D2.1)."""
    row, _, _ = setup(auth_service.store)
    browser, _ = signed_in()
    html = page(browser)
    region = stepper(html.encode())
    assert "Step 2 of 8:" in region
    assert "<strong>Load ParishSoft data</strong>" in region
    assert status_of(region, "Load ParishSoft data") == "Current step — Not done yet"
    # Not loaded: the line, every stage box waiting, and the load button,
    # which acts in place and also redraws the action row.
    assert state(html) == "offer" and NOT_LOADED in summary(html)
    assert html.count('data-stage-state="waiting"') == 5
    assert OFFER in html
    assert 'data-in-place-only="campaign-step-actions"' in html
    assert "Refresh now" not in html and QUEUE not in html
    # The step draws no load notice of its own above the page.
    assert "data-campaign-load " not in html and "data-campaign-load>" not in html
    row_html = actions(html)
    assert f'href="{reverse("admin:campaign_settings")}"' in row_html
    assert "Continue" not in row_html
    # Just requested, no worker has the run yet: Start the load is in
    # progress and the line after the boxes says it waits (#1916).
    refresh_run("queued")
    html = page(browser)
    assert state(html) == "loading" and LOADING in summary(html)
    assert 'data-load-stage="start" data-stage-state="active"' in html
    assert 'data-load-stage="fetching" data-stage-state="waiting"' in html
    assert f"data-campaign-load-count>{QUEUED}</p>" in html
    # Loading: the boxes follow the run, polled through its passive status
    # (?view=load); no button at all, and no Continue until it is loaded.
    claimed = timezone.now()
    task = refresh_run(
        "running",
        phase="staging",
        progress_current=10,
        progress_total=40,
        # A claimed run, as the worker leaves it (task_claim_shape).
        attempt=1,
        fence=1,
        worker_id=uuid4(),
        heartbeat_at=claimed,
        lease_expires_at=claimed + timedelta(minutes=5),
    )
    html = page(browser)
    assert state(html) == "loading" and LOADING in summary(html)
    status = reverse("admin:background_task_status", args=[task]) + "?view=load"
    assert f'data-live-url="{status}"' in html and "data-live-pending" in html
    assert 'data-load-stage="fetching" data-stage-state="done"' in html
    assert 'data-load-stage="staging" data-stage-state="active"' in html
    assert (
        "data-campaign-load-start" not in html
        and "<button"
        not in (
            html[html.index('id="refresh-run"') :].split('id="campaign-step-actions"')[
                0
            ]
        )
    )
    assert "Refresh now" not in html and QUEUE not in html
    assert "Continue" not in actions(html)
    # The poll draws the same region from the run alone, and is passive:
    # polling never renews the session's idle time (#1517 review, L4).
    before = PortalSession.objects.get().last_activity_at
    for _ in range(2):
        polled = page(browser, status)
        assert state(polled) == "loading" and "data-live-pending" in polled
        assert 'data-load-stage="staging" data-stage-state="active"' in polled
    assert PortalSession.objects.get().last_activity_at == before
    # Every other step says the data is loading.
    content = page(browser, reverse("admin:content_catalog"))
    assert "ParishSoft data is loading; going live waits for it." in content
    # Outside the stepper (the campaign is no longer a draft) the page is
    # the ordinary Refresh from ParishSoft, with Refresh now and its wording.
    command(row, uuid4(), Action.ACTIVATE)
    html = page(browser)
    assert stepper(html.encode()) is None and NOT_LOADED not in html
    assert "data-campaign-load-state" not in html and ">Refresh now<" in html
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
    assert state(html) == "failed" and ">See what went wrong<" in html
    assert "data-campaign-load-start>Try again</button>" in html
    assert 'data-stage-state="failed"' in html and "Refresh now" not in html
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
    assert state(html) == "loading" and "did not load" not in summary(html)
    assert "Continue" not in actions(html)


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
    assert "did not load" not in summary(html)
    assert "data-campaign-load-start" not in html
    # Loaded: every box done, the Family count, and Continue.
    assert state(html) == "loaded" and html.count('data-stage-state="done"') == 5
    assert "The campaign's ParishSoft data is loaded." in summary(html)
    assert re.search(r"data-campaign-load-count>1 Family[,.]", html)
    assert (
        "Refresh now" not in html
        and "<button"
        not in (
            html[html.index('id="refresh-run"') :].split('id="campaign-step-actions"')[
                0
            ]
        )
    )
    assert "Continue to Share options" in actions(html)
    # Every other step drops the load notice, and Home goes on past it.
    assert "data-campaign-load" not in page(browser, reverse("admin:content_catalog"))
    assert "Continue campaign setup: Share options" in page(browser, "/admin/")


def test_only_refreshes_since_creation_and_families_count(auth_service, google):
    """A run before the campaign, or one with no Families, is not a load.

    A refresh requested before the campaign was created could not load it
    (its request binds no campaign), so even a failed one leaves the plain
    offer. A newest run that succeeded but left no Families reads as
    "loaded, no Families", on the page and in its status poll alike: only
    the Families complete the step.
    """
    row, _, _ = setup(auth_service.store)
    browser, _ = signed_in()
    refresh_run("failed", created_at=row.created_at - timedelta(days=1))
    html = page(browser)
    assert OFFER in html and "did not load" not in summary(html)
    assert "Continue" not in actions(html)
    succeeded = refresh_run("succeeded")
    html = page(browser)
    region = stepper(html.encode())
    assert status_of(region, "Load ParishSoft data") == "Current step — Not done yet"
    assert state(html) == "empty" and NO_FAMILIES in summary(html)
    assert OFFER not in html and NOT_LOADED not in summary(html)
    # No button at all: not the plain Load again, and no Continue.
    assert (
        "<button"
        not in html[html.index('id="refresh-run"') :].split(
            'id="campaign-step-actions"'
        )[0]
    )
    assert "Continue" not in actions(html)
    # Every stage is done; the run and Refresh from ParishSoft are linked.
    assert html.count('data-stage-state="done"') == 5
    assert f'href="{reverse("admin:background_task_page", args=[succeeded])}"' in html
    assert ">See the load<" in html and ">Refresh from ParishSoft<" in html
    assert "Check that ParishSoft has active Parishioner Families" in summary(html)
    # Every other step says the same instead of offering the load.
    content = page(browser, reverse("admin:content_catalog"))
    assert NO_FAMILIES in content and "data-campaign-load-start" not in content
    # Its poll says the same, never "loaded" just before the re-read says
    # it is not (#1517 review, L2); once the Families exist, it is loaded.
    status = reverse("admin:background_task_status", args=[succeeded]) + "?view=load"
    polled = page(browser, status)
    assert state(polled) == "empty" and NO_FAMILIES in summary(polled)
    assert "is loaded." not in summary(polled) and "data-live-follow" in polled
    populate(row, keys(), [FamilyStatus(1, True, True, True, True)])
    polled = page(browser, status)
    assert state(polled) == "loaded" and "is loaded." in summary(polled)


def test_quick_refreshes_never_count_as_the_load(auth_service, google):
    """Only full refreshes decide the step; a quick one is invisible (#1587).

    A quick refresh under way does not show Loading, a newer failed or
    succeeded one does not hide the full load's failure, and with only quick
    refreshes the step is still the plain offer.
    """
    setup(auth_service.store)
    browser, _ = signed_in()
    refresh_run("queued", kind="delta")
    refresh_run("failed", kind="delta")
    html = page(browser)
    assert state(html) == "offer" and OFFER in html and NOT_LOADED in summary(html)
    failed = refresh_run("failed")
    refresh_run("succeeded", kind="delta")
    refresh_run("queued", kind="delta")
    html = page(browser)
    assert state(html) == "failed" and "data-campaign-load-start>Try again" in html
    assert f'href="{reverse("admin:background_task_page", args=[failed])}"' in html
