"""Update ParishSoft's page under the real roles (#528, #1688).

The page is the Administrator's: it reads what is Ready to update and the
newest plan, Check ParishSoft queues a preflight through
``publication_tasks.request_preflight``, Update's confirmation calls
``confirm_and_publish`` and Run again ``rerun_publication``. The tasks run
on the restricted worker login through the real dispatcher with the
in-memory fake gateway, so nothing calls ParishSoft. The update stays off
unless a test turns the web's setting on.
"""

import json
import re
from datetime import timedelta
from uuid import uuid4

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from parishkit.stewardship.audit.models import AuditContext, AuditEvent
from parishkit.stewardship.campaigns.models import Campaign
from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.reports import census_publication as publication
from parishkit.stewardship.responses import publication_tasks as tasks
from parishkit.stewardship.responses.models import (
    PublicationConfirmation,
    PublicationPlan,
)

from .auth_builders import signed_in
from .family_link_checks import link_reads
from .leader_builders import make_leader
from .test_information_followup_postgresql import search
from .test_publication_plans_postgresql import owner, ready  # noqa: F401
from .test_publication_tasks_postgresql import world  # noqa: F401
from .test_report_workspace_postgresql import read as get

pytestmark = pytest.mark.django_db(transaction=True)

ROUTE = "/admin/reports/census/update-parishsoft/"
CONFIRMATION = ROUTE + "confirmation/"
RETRIES = ROUTE + "retries/"
OFF = b"Updating ParishSoft is switched off"


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
    assert b"<h1>Update ParishSoft</h1>" in body and b"Publish" not in body
    response, body = search(browser, ROUTE, {"request_key": str(uuid4())})
    assert response.status_code == 409 and OFF in body
    assert not checks().exists()
    response, body = get(browser, "/admin/reports/census/")
    assert response.status_code == 200
    assert b'aria-describedby="publication-off-reason"' in body
    assert b'href="/admin/reports/census/update-parishsoft/"' not in body
    assert viewed() == [{"outcome": "succeeded", "count": 0}]
    # The page's old address is gone, with no redirect (#864).
    assert get(browser, "/admin/reports/census/publication/")[0].status_code == 404
    # The dialog's posts are refused too, before anything is read.
    for route in (CONFIRMATION, RETRIES):
        response, body = search(
            browser, route, {"action": "preview", "plan": str(uuid4())}
        )
        assert response.status_code == 409 and OFF in body


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
    assert response.status_code == 200 and b"Update ParishSoft" not in body
    for route in (CONFIRMATION, RETRIES):
        response, _ = search(
            browser, route, {"action": "preview", "plan": str(uuid4())}
        )
        assert response.status_code == 403


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
    assert b"1 change is Ready to update, for 1 Member." in body
    assert (
        b"Check ParishSoft</button>" in body
        and b"disabled"
        not in body.split(b"Check ParishSoft</button>")[0].rsplit(b"<button", 1)[1]
    )
    assert b"has not been checked for this campaign yet" in body
    response, body = get(browser, "/admin/reports/census/")
    assert b'href="/admin/reports/census/update-parishsoft/"' in body
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
    assert b"An update is running. Wait for it to finish." in body
    before = checks().count()
    response, body = search(browser, ROUTE, {"request_key": str(uuid4())})
    assert response.status_code == 409 and b"An update is running." in body
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
    for route in (CONFIRMATION, RETRIES):
        response, _ = search(
            browser, route, {"action": "preview", "plan": str(uuid4())}
        )
        assert response.status_code == 403


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
    assert b"Updating waits until other campaign work" in body
    assert not checks().exists()


def answer(body):
    """The hidden fields of a review dialog answer, by name."""
    return dict(re.findall(rb'name="([a-z_]+)" value="([^"]*)"', body))


def refusal(body):
    """The closed JSON refusal's message, as the dialog shows it."""
    return json.loads(body)["refusal"]["message"]


def review(browser, plan, route=CONFIRMATION):
    """Post a review step for ``plan``; return the answer and its fields."""
    response, body = search(browser, route, {"action": "preview", "plan": str(plan.pk)})
    return response, body, answer(body)


def apply(browser, fields, route=CONFIRMATION):
    """Post Apply with the review's hidden fields, as the dialog does."""
    values = {name.decode(): value.decode() for name, value in fields.items()}
    return search(browser, route, {"action": "confirm"} | values)


def runs():
    """Every update run task, oldest first."""
    return TaskRun.objects.filter(task_type=tasks.PUBLICATION_TASK_TYPE).order_by(
        "created_at"
    )


def test_update_confirms_the_newest_plan_and_shows_its_run(
    world,  # noqa: F811
    google,
    settings,
):
    """Update opens its confirmation with the plan's counts; Apply confirms
    the plan and queues one run (a repeat returns it); the page then shows
    the run's live status instead of Update, and once the run ends each
    change's result and the final refresh (#1688)."""
    settings.STEWARDSHIP_PARISHSOFT_PUBLICATION = True
    plan = world.planned(uuid4())
    browser, login = signed_in()
    assert login.status_code == 302
    response, body = get(browser, ROUTE)
    assert response.status_code == 200
    assert b'data-review-open="update-review"' in body
    assert b"Update 1 change</button>" in body
    assert f'name="plan" value="{plan.pk}"'.encode() in body
    response, body, fields = review(browser, plan)
    assert response.status_code == 200 and b"data-review-answer" in body
    assert b'data-review-title="Write 1 change for 1 Member to ParishSoft?"' in body
    assert fields[b"plan"] == str(plan.pk).encode()
    assert (fields[b"write_entities"], fields[b"write_fields"]) == (b"1", b"1")
    # A review records nothing.
    assert not runs().exists() and not PublicationConfirmation.objects.exists()
    for _ in range(2):
        response, body = apply(browser, fields)
        assert response.status_code == 200 and b"Update started." in body
        assert b"data-live-status" not in body
    (run,) = runs()
    (confirmation,) = PublicationConfirmation.objects.all()
    # The signed-in Administrator confirms, whoever made the check.
    assert confirmation.plan_id == plan.pk and confirmation.actor_id != world.admin
    assert run.domain_request_id == plan.pk
    assert run.initiated_by_id == confirmation.actor_id
    response, body = get(browser, ROUTE)
    assert f"/admin/system/background/{run.pk}/status/?view=update".encode() in body
    assert b"View this update" in body and b"s Background task" in body
    assert b'data-review-open="update-review"' not in body
    status, fragment = get(
        browser, f"/admin/system/background/{run.pk}/status/?view=update"
    )
    assert status.status_code == 200 and b"data-live-pending" in fragment
    assert world.execute(run.pk) and TaskRun.objects.get(pk=run.pk).state == (
        "succeeded"
    )
    assert len(world.gateway.writes) == 1
    response, body = get(browser, ROUTE)
    assert b"<td>Updated in ParishSoft</td>" in body
    assert b"ParishSoft is being refreshed so Census changes shows the new values." in (
        body
    )
    assert b'data-review-open="rerun-review"' not in body
    # Nothing is Ready any more, so Check ParishSoft says why.
    assert b"No changes are Ready to update." in body


def test_update_refuses_what_the_page_would_not_offer(
    world,  # noqa: F811
    google,
    settings,
    monkeypatch,
):
    """An older plan, an hour-old plan, counts that differ from the plan's,
    and Testing mode are each refused in the dialog with the page's words,
    and nothing is confirmed."""
    settings.STEWARDSHIP_PARISHSOFT_PUBLICATION = True
    older = world.planned(uuid4())
    browser, login = signed_in()
    assert login.status_code == 302
    newer = world.planned(uuid4())
    response, body, _ = review(browser, older)
    assert response.status_code == 409
    assert refusal(body) == "A newer check exists. Review it first."
    response, body, fields = review(browser, newer)
    assert response.status_code == 200
    response, body = apply(browser, fields | {b"write_fields": b"2"})
    assert response.status_code == 409
    assert refusal(body) == "Something changed since the check. Check ParishSoft again."
    monkeypatch.setattr(publication, "PLAN_LIFETIME", timedelta(0))
    response, body = get(browser, ROUTE)
    assert b"This check is more than an hour old. Check ParishSoft again." in body
    response, body = apply(browser, fields)
    assert response.status_code == 409
    assert refusal(body).startswith("This check is more than an hour old.")
    monkeypatch.undo()
    owner("UPDATE stewardship_system_configuration SET mode='testing'")
    response, body = apply(browser, fields)
    assert response.status_code == 409
    assert refusal(body) == "Testing mode: nothing is written to ParishSoft."
    assert not PublicationConfirmation.objects.exists() and not runs().exists()


def test_a_failed_write_is_shown_with_its_reason(world, google, settings):  # noqa: F811
    """A Member ParishSoft refuses on every attempt is Failed with the
    plain reason, and the run asks for no refresh."""
    settings.STEWARDSHIP_PARISHSOFT_PUBLICATION = True
    plan = world.planned(uuid4())
    _, run = world.confirm(plan)
    world.gateway.write_errors = ["write_rejected"] * 3
    assert world.execute(run.run_id)
    browser, login = signed_in()
    assert login.status_code == 302
    response, body = get(browser, ROUTE)
    assert b"<td>Failed: ParishSoft refused the change</td>" in body
    assert b"ParishSoft is being refreshed" not in body


def test_run_again_retries_a_run_that_did_not_finish(
    world,  # noqa: F811
    google,
    settings,
):
    """A run refused by Testing mode leaves its Member Not finished; back
    in Production the page offers Run again, whose confirmation names the
    Member left, and its Apply retries the plan's own run once."""
    settings.STEWARDSHIP_PARISHSOFT_PUBLICATION = True
    plan = world.planned(uuid4())
    _, run = world.confirm(plan)
    owner("UPDATE stewardship_system_configuration SET mode='testing'")
    assert world.execute(run.run_id)
    owner("UPDATE stewardship_system_configuration SET mode='production'")
    browser, login = signed_in()
    assert login.status_code == 302
    response, body = get(browser, ROUTE)
    assert b"Run again</button>" in body and b"<td>Not finished</td>" in body
    # Check ParishSoft waits for Run again: a newer check would strand it.
    assert b"An update stopped before it finished." in body
    before = checks().count()
    refused, _ = search(browser, ROUTE, {"request_key": str(uuid4())})
    assert refused.status_code == 409 and checks().count() == before
    assert b'data-review-open="update-review"' not in body
    assert b"This update stopped before it finished." in body
    response, body, fields = review(browser, plan, RETRIES)
    assert response.status_code == 200
    assert b'data-review-title="Try the 1 Member this run did not finish?"' in body
    for _ in range(2):
        response, body = apply(browser, fields, RETRIES)
        assert response.status_code == 200 and b"Run again started." in body
    first, retry = runs()
    assert retry.root_id == first.root_id
    assert world.execute(retry.pk)
    response, body = get(browser, ROUTE)
    assert b"<td>Updated in ParishSoft</td>" in body
    assert b"Run again</button>" not in body
    # Every Member is finished now: a stale Run again is refused.
    response, body, _ = review(browser, plan, RETRIES)
    assert response.status_code == 409
    assert refusal(body) == "Every Member in this check is finished."
