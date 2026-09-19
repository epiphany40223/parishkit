"""Reference-load confirmation stays metadata-only while real catch-up stays bounded."""

# ruff: noqa: F811 -- imported fixture dependencies are injected by pytest name.

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from time import perf_counter
from uuid import uuid4

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from parishkit.stewardship.accounts.confirmation_commands import confirm
from parishkit.stewardship.accounts.family_authentication import FamilyRuntime
from parishkit.stewardship.accounts.sessions import database_now
from parishkit.stewardship.accounts.setup_drafts import save_section
from parishkit.stewardship.campaigns.catchup_preparation import prepare_batch
from parishkit.stewardship.campaigns.catchup_tasks import TASK_TYPE, catchup_handler
from parishkit.stewardship.campaigns.credential_models import FamilyCampaign
from parishkit.stewardship.campaigns.family_identity import code_context
from parishkit.stewardship.campaigns.models import (
    ActivationCatchUpDemand,
    ScheduleOccurrence,
)
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.jobs.dispatch import claim_hint
from parishkit.stewardship.jobs.lifetime import maintain_execution
from parishkit.stewardship.jobs.outbox_models import OutboxMessage
from parishkit.stewardship.jobs.queues import WorkQueue
from parishkit.stewardship.responses.models import Submission

from ..campaign_factory import schedule
from ..content_factory import content
from . import test_go_live_cleanup_postgresql as cleanup_inputs
from . import test_setup_preparation_postgresql as setup_inputs
from . import test_setup_preview_postgresql as preview_inputs
from .test_background_grants_postgresql import task_login
from .test_confirmation_readiness_postgresql import (  # noqa: F401
    bootstrapped,
    config_role,
    prepare,
    ready_cleanup,
    ready_links,
    setup_service,
)
from .test_confirmation_sql_postgresql import fresh
from .test_family_auth_postgresql import login as family_login
from .test_response_http_postgresql import answers_for, post
from .test_setup_mail_views_postgresql import web_login

pytestmark = pytest.mark.django_db(transaction=True)


def reference_inputs(monkeypatch):
    """Stage 5,000 provider-backed Families and 20 overdue logical schedules once."""
    start = database_now().date() - timedelta(days=2)
    original_campaign = setup_inputs.first_campaign
    original_schedule = setup_inputs.schedule
    original_pages = cleanup_inputs.pages
    original_rehearsals = cleanup_inputs.prepare_rehearsals
    original_schedules = preview_inputs.with_schedules

    def dated_campaign(*args, **kwargs):
        """Keep actual domain time, source freshness and Google time in agreement."""
        return original_campaign(
            *args,
            **(
                kwargs
                | {
                    "start_date": start.isoformat(),
                    "end_date": (start + timedelta(days=30)).isoformat(),
                }
            ),
        )

    def dated_schedule(*args, **kwargs):
        """The initial invitation is genuinely overdue when activation commits."""
        return original_schedule(*args, **(kwargs | {"date": start.isoformat()}))

    def many_pages():
        """Use published pagination metadata; normalization and promotion stay real."""
        values = original_pages()
        family, member = values[1][0], values[4][0]
        families = [
            family
            | {
                "familyDUID": i,
                "familyID": i + 10000,
                "totalResults": 5000,
                "rowNumber": i,
            }
            for i in range(1, 5001)
        ]
        members = [
            member
            | {
                "memberDUID": i,
                "familyDUID": i,
                "recordCount": 5000,
                "rowNum": i,
                "lastName": "Example",
                "birthdate": "1980-01-01",
                "sex": "Unspecified",
                "language": "English",
            }
            for i in range(1, 5001)
        ]
        return [
            values[0],
            *[families[i : i + 500] for i in range(0, 5000, 500)],
            *values[2:4],
            *[members[i : i + 500] for i in range(0, 5000, 500)],
            *values[5:],
        ]

    def one_rehearsal(*args, **kwargs):
        """Keep one Testing artifact; reference load concerns live Families."""
        return original_rehearsals(
            *args, **(kwargs | {"family_ids": kwargs["family_ids"][:1]})
        )

    def many_schedules(service, patch):
        """Persist every reminder and its proper content slot through real setup."""
        request, status, initial, row = original_schedules(service, patch)
        reminder = content(str(status.attempt_id), kind="email", slot="reminder")
        with web_login():
            status = save_section(
                request,
                service,
                status.attempt_id,
                step="email_reminder",
                values=reminder,
                expected_version=status.version,
            )
            records = [row] + [
                schedule(
                    str(status.attempt_id),
                    kind="reminder",
                    date=(start + timedelta(days=1)).isoformat(),
                    time=f"{hour:02d}:00:00",
                    template_version=reminder["id"],
                    subject=reminder["values"]["subject"],
                )
                for hour in range(19)
            ]
            status = save_section(
                request,
                service,
                status.attempt_id,
                step="schedules",
                values={"records": records},
                expected_version=status.version,
            )
        return request, status, initial, row

    monkeypatch.setattr(setup_inputs, "first_campaign", dated_campaign)
    monkeypatch.setattr(setup_inputs, "schedule", dated_schedule)
    monkeypatch.setattr(cleanup_inputs, "pages", many_pages)
    monkeypatch.setattr(cleanup_inputs, "prepare_rehearsals", one_rehearsal)
    monkeypatch.setattr(preview_inputs, "with_schedules", many_schedules)


def test_reference_confirmation_and_family_submit_during_incomplete_catchup(
    request, monkeypatch, settings, record_property
):
    """Measure final locks without draining 100,000 slots just to test admission."""
    reference_inputs(monkeypatch)
    links = request.getfixturevalue("ready_links")
    preparation, arguments = prepare(links)
    _, service, campaign_id = arguments[:3]
    ring = links[2]
    assert FamilyCampaign.objects.filter(campaign_id=campaign_id).count() == 5000
    with web_login():
        preview, verified, token = fresh(arguments)
        assert verified and token and preview.families.counts.active == 5000
        assert preview.families.counts.messages == 5000
        assert preview.families.counts.coalesced_slots == 95000
        with CaptureQueriesContext(connection) as queries:
            began = perf_counter()
            receipt = confirm(*arguments, token=token, typed="Production")
            elapsed = perf_counter() - began
        assert elapsed < 2.0, elapsed
        assert not any(
            'FROM "stewardship_family_campaign"' in row["sql"]
            or 'FROM "stewardship_schedule_occurrence"' in row["sql"]
            for row in queries
        )
    assert not ScheduleOccurrence.objects.exists()
    assert not OutboxMessage.objects.exists()
    demand = ActivationCatchUpDemand.objects.get(activation_id=receipt.activation_id)
    record_property("final_confirmation_seconds", elapsed)
    record_property("final_confirmation_queries", len(queries))
    settings.STEWARDSHIP_FAMILY_RUNTIME = FamilyRuntime(
        service.store, service.limiter, ring.general, ring.mac, ring.public
    )
    family = FamilyCampaign.objects.get(campaign_id=campaign_id, family_duid=1)
    code = ring.general.decrypt(
        family.code_ciphertext, context=code_context(family.pk)
    ).decode()

    def submit_family():
        """Use an independent web connection alongside the maintained worker."""
        connection.close()
        try:
            with connection.cursor() as cursor:
                cursor.execute("SET SESSION AUTHORIZATION pk_stewardship_web")
            browser, response = family_login(code)
            assert response.status_code == 302, response.content
            began = perf_counter()
            response = post(browser, "/family/form", {"testing_acknowledged": False})
            assert response.status_code == 200, response.content
            form = response.json()["form"]
            submitted = post(
                browser,
                "/family/submit",
                {"baseline": form["baseline"], "answers": answers_for(form)},
            )
            assert submitted.status_code == 200, submitted.content
            assert submitted.json() == {"accepted": True}
            return perf_counter() - began
        finally:
            connection.close()

    # Both disposable roles must be provisioned by the fixture owner before
    # either runtime operates. Each actual connection then remains restricted.
    with web_login(), ThreadPoolExecutor(max_workers=1) as pool:
        with connection.cursor() as cursor:
            cursor.execute("RESET SESSION AUTHORIZATION")
        _exercise_catchup(demand, pool, submit_family, record_property)
    assert Submission.objects.filter(campaign_id=campaign_id, mode="live").count() == 1
    demand.refresh_from_db()
    assert demand.completed_at is None and demand.groups_completed == 2


def _exercise_catchup(demand, pool, submit_family, record_property):
    """Commit bounded effects around a genuinely separate Family request process."""
    with task_login(ServiceRole.WORKER, exact=True, reconnect=True):
        execution = claim_hint(
            demand.task_root_id,
            queue=WorkQueue.GENERAL,
            worker_id=uuid4(),
            handlers={TASK_TYPE: catchup_handler()},
        )
        assert execution is not None
        with maintain_execution(execution):
            with execution.effect():
                prepare_batch(demand, execution.claim)
            demand.refresh_from_db()
            assert demand.completed_at is None and demand.groups_completed == 1
            # The maintained worker stays alive, but bounded effects release the
            # common lock so actual Family entry and submission can commit.
            submission_seconds = pool.submit(submit_family).result(timeout=10)
            assert submission_seconds < 2.0, submission_seconds
            with execution.effect():
                prepare_batch(demand, execution.claim)
    record_property("family_form_and_submit_seconds", submission_seconds)
