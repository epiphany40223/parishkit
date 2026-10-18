"""Publish to ParishSoft's page, read and check, under the real roles (#528).

The page (ADM-09's second slice) is the Administrator's: it reads what is
waiting and the newest plan on the web login, and Check ParishSoft queues a
preflight through ``publication_tasks.request_preflight``. The preflight
runs on the restricted worker login through the real dispatcher with the
in-memory fake gateway, so nothing calls ParishSoft. Publication stays off
unless a test turns the web's setting on.
"""

from uuid import uuid4

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from parishkit.stewardship.audit.models import AuditContext, AuditEvent
from parishkit.stewardship.campaigns.models import Campaign
from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.responses import publication_tasks as tasks
from parishkit.stewardship.responses.models import PublicationPlan

from .auth_builders import signed_in
from .family_link_checks import link_reads
from .leader_builders import make_leader
from .test_information_followup_postgresql import search
from .test_publication_plans_postgresql import ready  # noqa: F401
from .test_publication_tasks_postgresql import world  # noqa: F401
from .test_report_workspace_postgresql import read as get

pytestmark = pytest.mark.django_db(transaction=True)

ROUTE = "/admin/reports/census/publication/"
OFF = b"Publishing to ParishSoft is switched off."


def checks():
    """Every preflight task queued so far."""
    return TaskRun.objects.filter(task_type=tasks.PREFLIGHT_TASK_TYPE)


def viewed():
    """Each census_publication_viewed context, oldest first."""
    return [
        AuditContext.objects.get(event=event).context
        for event in AuditEvent.objects.filter(
            event_type="census_publication_viewed"
        ).order_by("created_at")
    ]


def test_switched_off_page_is_inert(world, google):  # noqa: F811
    """While publication is off the page says so and offers no button, every
    post is refused with nothing queued, and Census changes shows the link
    unavailable with its reason."""
    browser, login = signed_in()
    assert login.status_code == 302
    response, body = get(browser, ROUTE)
    assert response.status_code == 200 and response["Cache-Control"] == "no-store"
    assert OFF in body and b"Check ParishSoft</button>" not in body
    assert b"Publish to ParishSoft" in body
    response, body = search(browser, ROUTE, {"request_key": str(uuid4())})
    assert response.status_code == 409 and OFF in body
    assert not checks().exists()
    response, body = get(browser, "/admin/reports/census/")
    assert response.status_code == 200
    assert b'aria-describedby="publication-off-reason"' in body
    assert b'href="/admin/reports/census/publication/"' not in body
    assert viewed() == [{"outcome": "succeeded", "count": 0}]


def test_staff_never_open_the_page(world, google, settings):  # noqa: F811
    """Staff are denied the page and its post, and see no link to it."""
    settings.STEWARDSHIP_PARISHSOFT_PUBLICATION = True
    google[0]["email"] = "staff@example.org"
    browser, login = signed_in()
    assert login.status_code == 302
    response, body = get(browser, ROUTE)
    assert response.status_code == 403 and b"Check ParishSoft" not in body
    response, _ = search(browser, ROUTE, {"request_key": str(uuid4())})
    assert response.status_code == 403 and not checks().exists()
    response, body = get(browser, "/admin/reports/census/")
    assert response.status_code == 200 and b"Publish to ParishSoft" not in body


def test_check_parishsoft_queues_one_check_and_shows_its_plan(
    world,  # noqa: F811
    google,
    settings,
):
    """Check ParishSoft queues one preflight per key and the page follows it;
    once it has run, the page shows the plan's rows and counts, and each
    view is audited with the number of rows."""
    settings.STEWARDSHIP_PARISHSOFT_PUBLICATION = True
    browser, login = signed_in()
    assert login.status_code == 302
    response, body = get(browser, ROUTE)
    assert response.status_code == 200
    assert b"1 approved Automatic change is waiting to be published" in body
    assert (
        b"Check ParishSoft</button>" in body
        and b"disabled"
        not in body.split(b"Check ParishSoft</button>")[0].rsplit(b"<button", 1)[1]
    )
    assert b"has not been checked for this campaign yet" in body
    response, body = get(browser, "/admin/reports/census/")
    assert b'href="/admin/reports/census/publication/"' in body
    key = str(uuid4())
    for _ in range(2):
        response, _ = search(browser, ROUTE, {"request_key": key})
        assert response.status_code == 302
        assert response["Location"] == ROUTE + "#publication-check"
    (task,) = checks()
    # One request, audited once, as the Administrator who clicked.
    (requested,) = AuditEvent.objects.filter(
        event_type="census_publication_preflight_requested"
    )
    assert requested.actor_id == task.initiated_by_id is not None
    response, body = get(browser, ROUTE)
    assert f"/admin/system/background/{task.pk}/".encode() in body
    assert b"?view=publication" in body
    status, fragment = get(
        browser, f"/admin/system/background/{task.pk}/status/?view=publication"
    )
    assert status.status_code == 200 and b"data-live-pending" in fragment
    assert world.execute(task.pk) and TaskRun.objects.get(pk=task.pk).state == (
        "succeeded"
    )
    assert world.gateway.writes == []
    plan = PublicationPlan.objects.get()
    with CaptureQueriesContext(connection) as queries:
        response, body = get(browser, ROUTE)
    assert response.status_code == 200
    assert b"1 change for 1 Member will be written." in body
    # The row's Family name opens its Timeline (#1570): one record id read.
    assert b'<td><a href="/admin/reports/families/' in body
    assert len(link_reads(queries)) == 1
    assert b"Will be written" in body and b"Requested" in body
    assert b"admin@example.org" in body and not plan.testing
    # Sorting and paging are refused when malformed, as on every table.
    assert get(browser, ROUTE + "?sort=nope")[0].status_code == 400
    assert get(browser, ROUTE + "?sort=-outcome")[0].status_code == 200
    assert viewed()[-1] == {"outcome": "succeeded", "count": 1}


def test_check_is_unavailable_while_a_run_is_unfinished(
    world,  # noqa: F811
    google,
    settings,
):
    """A queued publish run makes Check ParishSoft unavailable with its
    reason, and a post meets the same refusal; nothing else is queued."""
    settings.STEWARDSHIP_PARISHSOFT_PUBLICATION = True
    plan = world.planned(uuid4())
    world.confirm(plan)
    browser, login = signed_in()
    assert login.status_code == 302
    response, body = get(browser, ROUTE)
    assert response.status_code == 200
    assert b'aria-describedby="publication-check-reason"' in body
    assert b"Publishing is running. Wait for it to finish." in body
    before = checks().count()
    response, body = search(browser, ROUTE, {"request_key": str(uuid4())})
    assert response.status_code == 409 and b"Publishing is running." in body
    assert checks().count() == before


def test_an_unconfirmed_contact_read_is_explained(
    world,  # noqa: F811
    google,
    settings,
):
    """A check refused because the contact read is not confirmed yet plans
    nothing, and the page says why in the check's status region."""
    settings.STEWARDSHIP_PARISHSOFT_PUBLICATION = True
    world.gateway.read_errors = ["read_unconfirmed"]
    browser, login = signed_in()
    assert login.status_code == 302
    response, _ = search(browser, ROUTE, {"request_key": str(uuid4())})
    assert response.status_code == 302
    (task,) = checks()
    world.execute(task.pk)
    assert TaskRun.objects.get(pk=task.pk).state == "failed"
    assert not PublicationPlan.objects.exists()
    response, body = get(browser, ROUTE)
    assert b"cannot be checked yet, so nothing was planned" in body
    _, fragment = get(
        browser, f"/admin/system/background/{task.pk}/status/?view=publication"
    )
    assert b"cannot be checked yet, so nothing was planned" in fragment


def test_ministry_leaders_never_open_the_page(world, google, settings):  # noqa: F811
    """A Ministry leader is denied the page and its post; nothing is queued."""
    settings.STEWARDSHIP_PARISHSOFT_PUBLICATION = True
    make_leader(world.store, Campaign.objects.get(), "leader@example.org")
    google[0]["email"] = "leader@example.org"
    browser, login = signed_in()
    assert login.status_code == 302
    response, body = get(browser, ROUTE)
    assert response.status_code == 403 and b"Check ParishSoft" not in body
    response, _ = search(browser, ROUTE, {"request_key": str(uuid4())})
    assert response.status_code == 403 and not checks().exists()


def test_a_post_without_the_csrf_token_is_refused(world, google, settings):  # noqa: F811
    """The check is a protected post: without the token it is a 403 and
    nothing is queued."""
    settings.STEWARDSHIP_PARISHSOFT_PUBLICATION = True
    browser, login = signed_in()
    assert login.status_code == 302
    response = browser.post(ROUTE, {"request_key": str(uuid4())})
    assert response.status_code == 403 and not checks().exists()


def test_a_campaign_held_after_the_gate_is_refused_in_place(
    world,  # noqa: F811
    google,
    settings,
    monkeypatch,
):
    """Other campaign work that takes the campaign between the page's gate
    and the request is refused as publication_campaign_held (409), not as a
    denied page, and queues nothing."""
    settings.STEWARDSHIP_PARISHSOFT_PUBLICATION = True

    def held(campaign_id, *, mutating):
        raise PermissionError("held")

    # Only the service's own admission sees the race; the gate's passed.
    monkeypatch.setattr(tasks, "admit_campaign", held)
    browser, login = signed_in()
    assert login.status_code == 302
    response, body = search(browser, ROUTE, {"request_key": str(uuid4())})
    assert response.status_code == 409
    assert b"Publishing waits until other campaign work" in body
    assert not checks().exists()
