"""Report totals count the responses Staff enter after the close (#2298).

Staff's Open form can enter a Family's first response, or replace one,
after the campaign closes (#2294). The reports spec's "Responses entered
after the campaign closes" counts each on the day Staff entered it: the
daily facts run to the campaign's report end (frozen migration 0192), and
the Response dashboard's charts, the Home tiles, a report email, the
campaign archive and the comparison all read them. These tests drive real
late responses through Open form and build the generations, the report
send and the archive through their real paths.
"""

# ruff: noqa: F811 -- the imported fixture is injected by pytest name.

import re
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from django.db import DatabaseError, connection, transaction

from parishkit.stewardship.accounts.admin_dashboard import summary
from parishkit.stewardship.accounts.models import PortalSession
from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.accounts.sessions import database_now
from parishkit.stewardship.archive import check
from parishkit.stewardship.archive.export import export_archive
from parishkit.stewardship.archive.workbook import read_archive
from parishkit.stewardship.campaigns.credential_models import FamilyCampaign
from parishkit.stewardship.campaigns.models import ScheduleRevision
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.jobs.ownership import TaskClaim
from parishkit.stewardship.reports.archive_exports import (
    LATE_TODAY,
    archive_offer,
    campaign_now,
)
from parishkit.stewardship.reports.chart_specs import participation_chart
from parishkit.stewardship.reports.comparison import LateEntries
from parishkit.stewardship.reports.comparison_reads import compare, comparison_day
from parishkit.stewardship.reports.documents import participation_document
from parishkit.stewardship.reports.exact_models import ExactExportResolution
from parishkit.stewardship.reports.facts import begin_fact_set
from parishkit.stewardship.reports.materialization import (
    materialize_fact_set,
    verify_fact_set,
)
from parishkit.stewardship.reports.models import (
    CampaignDailyFact,
    CampaignDailyFactSet,
)
from parishkit.stewardship.reports.report_email_inputs import send_inputs
from parishkit.stewardship.reports.report_email_models import (
    ReportEmailPreparation,
)
from parishkit.stewardship.reports.selection import current_inputs

from .auth_builders import signed_in
from .campaign_builders import campaign_clock
from .report_builders import add_digest, execute
from .source_builders import running_source_task
from .test_background_grants_postgresql import task_login
from .test_campaign_comparison_postgresql import in_process
from .test_exact_exports_postgresql import request_exact, run_exact
from .test_ministry_reports_postgresql import actor
from .test_open_form_postgresql import (  # noqa: F401
    after_close,
    open_form,
    redeem,
    two_family_service,
)
from .test_policy_postgresql import user
from .test_report_email_send_now_postgresql import request as send_now
from .test_response_submission_postgresql import form_and_answers, submit
from .test_source_snapshots_postgresql import permit

pytestmark = pytest.mark.django_db(transaction=True)


def enter_late(harness, admin, family):
    """Enter one response for ``family`` through Staff's Open form; it.

    A changed answer, so a resubmission is a new version with review items
    of its own, as in #2294's own tests.
    """
    response, secret = open_form(admin, family.pk)
    assert response.status_code == 200 and secret
    redeemed = redeem(admin, secret)
    assert redeemed.status_code == 302
    staff = replace(harness, client=admin, request=redeemed.wsgi_request)
    form, answers = form_and_answers(staff)
    answers["family"]["email_opt_out"] = True
    late = submit(staff, form, answers).submission
    assert late is not None and late.entered_by_id is not None
    assert late.submitted_at >= harness.campaign.active_configuration.ends_at
    return late


def build(harness, population):
    """The ready generation of the current inputs of ``population``, built
    as the worker builds one, at the campaign clock."""
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        inputs, _ = current_inputs(harness.campaign.pk, population)
    values = running_source_task()
    claim = TaskClaim(values["task_id"], values["task_fence"], values["worker_id"])
    with work_transaction():
        record = begin_fact_set(inputs, claim, admit=permit)
    return inputs, materialize_fact_set(record.pk, claim, admit=permit)


def rows(generation):
    """The generation's fact rows, in date order."""
    return list(
        CampaignDailyFact.objects.filter(fact_set=generation).order_by("local_date")
    )


def local_date(harness, instant):
    """``instant``'s date in the campaign's time zone."""
    zone = harness.campaign.active_configuration.timezone
    return instant.astimezone(ZoneInfo(zone)).date()


def test_late_responses_are_in_every_report_total(
    two_family_service, google, monkeypatch
):
    """A first-ever late response (DUID 4) and a resubmission (DUID 1), both
    entered after the close, count on the day Staff entered them: in the
    daily facts of both scopes, which run past the end date and recalculate
    exactly; in Home's responded tile, which agrees with them; in the
    dashboard chart, which marks the close; in the archive, whose cutoff
    follows them and which counts them late; and beside the comparison,
    which stays at the end date."""
    monkeypatch.setattr(check, "check_archive", in_process)
    harness = two_family_service
    projection = harness.campaign.active_configuration
    end = projection.end_date
    first = FamilyCampaign.objects.get(campaign=harness.campaign, family_duid=1)
    second = FamilyCampaign.objects.get(campaign=harness.campaign, family_duid=4)
    with after_close(harness):
        admin, _ = signed_in()
        assert PortalSession.objects.filter(revoked_at__isnull=True).exists()
        new = enter_late(harness, admin, second)
        again = enter_late(harness, admin, first)
        day = local_date(harness, again.submitted_at)
        assert local_date(harness, new.submitted_at) == day and day > end

        # The facts run through the late day, in both scopes.
        generations = {}
        for population in ("historical", "current"):
            inputs, ready = build(harness, population)
            assert inputs.through_date == day
            assert (ready.state, ready.last_date) == ("ready", day)
            assert ready.expected_count == (day - projection.start_date).days + 1
            assert verify_fact_set(ready.pk, admit=permit) == ()
            generations[population] = ready
        series = rows(generations["historical"])
        by_date = {row.local_date: row for row in series}
        assert by_date[end].cumulative_responses == 1
        assert by_date[day].first_responses == 1
        assert series[-1].cumulative_responses == 2
        assert sum(row.first_responses for row in series) == 2
        # Nothing was moved back to the end date.
        assert all(
            row.first_responses == 0 for row in series if end < row.local_date < day
        )

        # Home's responded tile agrees with the report's last day.
        configuration = SystemConfiguration.objects.select_related(
            "active_configuration"
        ).get()
        configuration.current_campaign = harness.campaign
        principal = actor(harness, ("administrator",), ())
        with task_login(ServiceRole.WEB, exact=True, reconnect=True):
            home = summary(principal, configuration, database_now(), home=True)
        current = rows(generations["current"])[-1].cumulative_responses
        assert home["families"]["active_responded"] == current == 2
        tile = next(t for t in home["family_tiles"] if t.key == "responded")
        assert tile.text == "2"

        # The dashboard's chart marks the close and says so.
        document = participation_document(
            generations["historical"],
            parish_name="Parish",
            browser_timezone="UTC",
            requested_at=database_now(),
        )
        assert document.end_date == end
        assert document.closed_after == (end - projection.start_date).days + 1
        assert "The campaign closed at the end of" in (
            participation_chart(document).summary
        )

        # The comparison stays at the end date, with the late tail beside it.
        latest = comparison_day(
            document, projection.start_date, end, projection.timezone, campaign_now()
        )
        assert latest[1] == end
        found = compare(
            document,
            projection,
            latest,
            latest[0],
            status="current",
            current_year=None,
        )
        assert found.day == latest[0] and found.report_date == end
        assert found.late == LateEntries(end, 1, None)

        # The archive waits while today's late response is not a full day.
        now = campaign_now()
        with task_login(ServiceRole.WEB, exact=True, reconnect=True):
            assert archive_offer(harness.campaign.pk, now=now).reason == LATE_TODAY
    with campaign_clock(projection.ends_at + timedelta(days=5)):
        with task_login(ServiceRole.WEB, exact=True, reconnect=True):
            offer = archive_offer(harness.campaign.pk, now=campaign_now())
        assert offer.reason is None
        assert (offer.cutoff_date, offer.final) == (day, True)
        assert offer.fact_set_id == generations["historical"].pk
        written = export_archive(
            offer.fact_set_id,
            cutoff_date=offer.cutoff_date,
            final=True,
            converted_at=datetime(2054, 12, 1, tzinfo=UTC),
        )
    archive = read_archive(written.data)
    assert archive.other_counts == {"count.late_submissions": "2"}
    assert any(end.isoformat() in note for note in archive.notes.values())
    assert archive.daily[-1]["local_date"] == day
    assert archive.daily[-1]["cumulative_families"] == 2


def test_a_report_email_after_a_late_response_reports_its_day(
    live_response_service, google
):
    """A send made after a late response reports that day, not the end date:
    the guard's SQL and the worker's Python agree on its inputs (0192), and
    its pinned figures include the response. A later late response never
    moves what that send reported."""
    harness = live_response_service
    projection = harness.campaign.active_configuration
    family = FamilyCampaign.objects.get(campaign=harness.campaign, family_duid=1)
    with after_close(harness):
        admin, _ = signed_in()
        late = enter_late(harness, admin, family)
        day = local_date(harness, late.submitted_at)
        assert day > projection.end_date
    with campaign_clock(projection.ends_at + timedelta(days=5)):
        identifier = add_digest(harness.service.store, harness.campaign)
        task = send_now(harness, user("admin@example.org"), uuid4())
        execute(task)
        row = ReportEmailPreparation.objects.get(definition_id=identifier)
        assert row.phase == "complete"
        assert row.report_day == row.through_date == day
        assert row.submission_watermark == late.campaign_sequence
        # A send counts today's campaign Families (#2331).
        facts = CampaignDailyFactSet.objects.get(
            campaign=harness.campaign,
            population_scope="current",
            submission_watermark=row.submission_watermark,
            through_date=day,
            state="ready",
        )
        assert rows(facts)[-1].local_date == day
        assert rows(facts)[-1].cumulative_responses == 1

    # Another late response, two days later, moves the next send's day but
    # never this one's: its saved copy recomputes the same report day.
    with campaign_clock(projection.ends_at + timedelta(days=7)):
        admin, _ = signed_in()
        newer = enter_late(harness, admin, family)
        values = ScheduleRevision.objects.get(pk=row.revision_id).values
        kept = send_inputs(
            values,
            row.covered_slots,
            row.captured_at,
            projection,
            campaign_id=harness.campaign.pk,
            watermark=row.submission_watermark,
        )
        assert (kept.report_day, kept.held) == (day, True)
        moved = send_inputs(
            values,
            row.covered_slots,
            row.captured_at,
            projection,
            campaign_id=harness.campaign.pk,
            watermark=newer.campaign_sequence,
        )
        assert moved.report_day == row.captured_at.astimezone(
            ZoneInfo(projection.timezone)
        ).date() - timedelta(days=1)


def test_an_exact_export_after_a_late_response_runs_to_its_day(
    live_response_service, google
):
    """The export request's guard admits today capped at the report end
    (0192), the inputs the requester records, and the worker builds the
    generation through the late day."""
    harness = live_response_service
    family = FamilyCampaign.objects.get(campaign=harness.campaign, family_duid=1)
    with after_close(harness):
        admin, _ = signed_in()
        late = enter_late(harness, admin, family)
        day = local_date(harness, late.submitted_at)
    with campaign_clock(harness.campaign.active_configuration.ends_at + timedelta(5)):
        principal = user("admin@example.org")
        with task_login(ServiceRole.WEB, exact=True):
            request = request_exact(harness, principal, population_scope="historical")
        assert request.through_date == day
        assert request.submission_watermark == late.campaign_sequence
        with task_login(ServiceRole.WORKER, exact=True):
            assert run_exact(harness, request)
    resolution = ExactExportResolution.objects.get(request=request)
    assert resolution.fact_set.last_date == day
    assert rows(resolution.fact_set)[-1].cumulative_responses == 1


MIGRATION = (
    Path(__file__).resolve().parents[3]
    / "src/parishkit/stewardship/schema/migrations/0192_late_report_totals.sql"
)
# Each body 0192 replaces, with its #2298 change undone as an older or
# partial install would lack it: (function, new text, old text).
STALE_0192 = {
    "fact-set-guard": (
        "stewardship_fact_set_guard",
        "    last_day := NEW.through_date;\n",
        "    last_day := LEAST(projection.end_date,NEW.through_date);\n",
    ),
    "exact-request-guard": (
        "stewardship_exact_request_guard_v1",
        "AND r.campaign_sequence<=NEW.submission_watermark)),",
        "AND r.campaign_sequence<0)),",
    ),
    "preparation-guard": (
        "stewardship_report_email_preparation_guard_v1",
        "NEW.captured_at,c.start_date,greatest(c.end_date,(SELECT max((s.submitted_at",
        "NEW.captured_at,c.start_date,greatest(c.end_date,(SELECT min((s.submitted_at",
    ),
}


@pytest.mark.parametrize("stale", STALE_0192)
def test_migration_0192_self_check_refuses_a_stale_install(stale):
    """0192's DO block passes on the installed schema and fails when any one
    replaced body lacks #2298's change. The file's own transaction-local
    ACL capture runs first, as in the migration; each stale install runs in
    a rolled-back transaction."""
    text = MIGRATION.read_text(encoding="utf-8")
    acl = re.search(r"^SELECT set_config\(.*?, true\);", text, re.M | re.S)[0]
    check = re.search(r"^DO \$check\$.*?\$check\$;", text, re.M | re.S)[0]
    function, new, old = STALE_0192[stale]
    start = text.index(f"CREATE OR REPLACE FUNCTION public.{function}(")
    body = text[start : text.index("$$;", start) + 3]
    assert body.count(new) == 1
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(acl)
        cursor.execute(check)
        cursor.execute(body.replace(new, old))
        with (
            pytest.raises(DatabaseError, match="Migration 0192"),
            transaction.atomic(),
        ):
            cursor.execute(check)
        transaction.set_rollback(True)
