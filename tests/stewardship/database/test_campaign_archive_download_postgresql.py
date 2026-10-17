"""Download campaign archive (#1018 part (a), slice A2), on PostgreSQL.

The comparison scenario (``test_campaign_comparison_postgresql``: a real
financial campaign with four households' invented live pledges on day 1 and
a ready historical fact set) is exported through Past campaigns' Download
campaign archive and ``pk-admin export archive``, rendered by the real
export worker under the worker login and downloaded under the restricted
download login. The written file is read back in this process, as the
comparison tests do (the isolated helper runs the installed package, which a
local worktree may not be). The export kind's SQL guards, the worker's
definer read and the frozen file's closing check are exercised directly.
"""

# ruff: noqa: F811 -- the imported scenario and cli fixtures are injected by name.

import io
import time
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from django.db import DatabaseError, connection, transaction
from django.db.models import F
from django.urls import reverse

from parishkit.stewardship.accounts.policy_models import PortalUser
from parishkit.stewardship.archive import check
from parishkit.stewardship.archive.workbook import read_archive
from parishkit.stewardship.audit.models import AuditContext
from parishkit.stewardship.campaigns.read_guards import DownloadPool, ReadLimits
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.jobs.dispatch import execute_hint
from parishkit.stewardship.jobs.queues import WorkQueue
from parishkit.stewardship.reports.archive_exports import (
    BEFORE_DAY_1,
    archive_offer,
    create_archive_export,
)
from parishkit.stewardship.reports.export_models import (
    ExportPublication,
    ExportRequest,
)
from parishkit.stewardship.reports.export_services import TASK_TYPE, export_status
from parishkit.stewardship.reports.export_tasks import export_handler
from parishkit.stewardship.reports.models import CampaignFactPin

from ..export_urls import export_action
from .campaign_builders import campaign_clock, close_campaign
from .test_admin_report_cli_postgresql import cli  # noqa: F401
from .test_background_grants_postgresql import task_login
from .test_campaign_comparison_postgresql import (  # noqa: F401
    CURRENT_YEAR,
    PLEDGES,
    in_process,
    scenario,
    stewardship_year,
)
from .test_export_views_postgresql import post, restricted_download_pool
from .test_information_followup_postgresql import search
from .test_report_workspace_postgresql import read

pytestmark = pytest.mark.django_db(transaction=True)

ROOT = Path(__file__).resolve().parents[3]
FROZEN = (
    ROOT
    / "src/parishkit/stewardship/schema/migrations/0084_campaign_archive_exports.sql"
)
ROUTE = "/admin/parish/past-campaigns/archive/"
ANSWERS = "public.stewardship_campaign_archive_answers_v1(%s,%s)"


@pytest.fixture(autouse=True)
def reader_here(monkeypatch):
    """Read written files back in this process (see the module docstring)."""
    monkeypatch.setattr(check, "check_archive", in_process)


def day_two(harness):
    """The clock at noon of the campaign's day 2, so day 1 is complete."""
    opened = harness.campaign.active_configuration.starts_at
    return campaign_clock(opened + timedelta(days=1, hours=12))


def work(harness, request, root):
    """Render one export with the real worker, as the worker login."""
    with task_login(ServiceRole.WORKER, exact=True, reconnect=True):
        assert execute_hint(
            request.task_id,
            queue=WorkQueue.GENERAL,
            worker_id=uuid4(),
            handlers={
                TASK_TYPE: export_handler(store=harness.service.store, root=root)
            },
        )


def test_download_queues_renders_and_downloads_the_partial_archive(
    scenario, settings, tmp_path, monkeypatch
):
    """The page's section and form, the worker's file, the audit and a second
    export of the same day."""
    harness, ready, browser, actor = scenario
    root = tmp_path / "archive-reports"
    root.mkdir(mode=0o700)
    settings.STEWARDSHIP_REPORTS_ROOT = root
    settings.STEWARDSHIP_DOWNLOAD_POOL = DownloadPool(ReadLimits(process_pool_size=1))
    assert reverse("admin:past_campaign_archive") == ROUTE
    fields = {"request_key": str(uuid4())}
    with day_two(harness), task_login(ServiceRole.WEB, exact=True, reconnect=True):
        response, body = read(browser, reverse("admin:past_campaigns"))
        assert response.status_code == 200
        assert b"This campaign's archive" in body
        assert b"A partial archive of" in body and b"through day 1" in body
        assert b"stewardship-%d-partial.xlsx" % CURRENT_YEAR in body
        assert b'id="campaign-archive-reason"' not in body
        # No CSRF header, a GET, an extra or repeated field, or a query.
        assert browser.post(ROUTE, fields).status_code == 403
        assert browser.get(ROUTE).status_code == 405
        for invalid in ({"extra": "x"}, {"request_key": [str(uuid4())] * 2}):
            assert post(browser, ROUTE, fields | invalid).status_code == 400
        assert post(browser, ROUTE + "?x=1", fields).status_code == 400
        response = post(browser, ROUTE, fields)
        assert response.status_code == 302, response
        # The same form again is the same export.
        assert post(browser, ROUTE, fields)["Location"] == response["Location"]
        job = response["Location"]
        status, body = read(browser, job)
        assert status.status_code == 200 and b"Campaign archive" in body
    request = ExportRequest.objects.get(request_key=fields["request_key"])
    assert (request.report, request.format, request.fact_set_id) == (
        "campaign_archive",
        "xlsx",
        ready.pk,
    )
    cutoff = ready.first_date.isoformat()
    assert request.parameters == {
        "cutoff_date": cutoff,
        "final": False,
        "year": CURRENT_YEAR,
    }
    assert CampaignFactPin.objects.filter(
        fact_set=ready, parent_kind="export", parent_id=request.pk
    ).exists()
    work(harness, request, root)
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        state = export_status(harness.service.store, actor, request.pk)
    assert state["state"] == "ready"
    with restricted_download_pool(settings):
        response, body = search(browser, export_action(job, "download"), {})
    assert response.status_code == 200
    assert f"stewardship-{CURRENT_YEAR}-partial.xlsx" in response["Content-Disposition"]
    archive = read_archive(bytes(body))
    assert {f["family_id"] for f in archive.families} == set(PLEDGES)
    assert archive.metadata["is_final"] is False
    contexts = list(
        AuditContext.objects.filter(
            event__event_type="campaign_archive_exported"
        ).values_list("context", flat=True)
    )
    assert contexts == [
        {
            "outcome": "started",
            "count": 1,
            "report": "campaign_archive",
            "format": "xlsx",
        }
    ]
    # Exporting again the same day (there is no Regenerate, #1398) pins the
    # same fact set and day. converted_at is the request time in whole
    # seconds: let one pass.
    time.sleep(1.1)
    with day_two(harness), task_login(ServiceRole.WEB, exact=True, reconnect=True):
        again_key = {"request_key": str(uuid4())}
        response = post(browser, ROUTE, again_key)
        assert response.status_code == 302, response
    again = ExportRequest.objects.get(request_key=again_key["request_key"])
    assert (again.fact_set_id, again.parameters) == (ready.pk, request.parameters)
    work(harness, again, root)
    first, second = (
        ExportPublication.objects.get(request=row) for row in (request, again)
    )
    # The same records, a new request time: a new converted_at and checksum.
    assert first.row_count == second.row_count == len(PLEDGES)
    assert first.sha256 != second.sha256
    assert again.created_at.replace(microsecond=0) > request.created_at


def test_an_early_closed_campaign_gets_its_final_archive_at_once(
    scenario, settings, tmp_path
):
    """Closed today with no response today: final now, through yesterday.

    The Administrator's decision of 2026-10-13 (#1018): a campaign closed
    early no longer waits for its end date. The campaign is closed through
    the real boundary ledgers, then seen on day 2, long before its end
    date, with no response on day 2; the offer, the request, the SQL guard
    and the worker's file are all final, cut off at day 1.
    """
    harness, ready, browser, actor = scenario
    close_campaign(harness.campaign, actor)
    assert harness.campaign.state == "closed"
    root = tmp_path / "archive-reports"
    root.mkdir(mode=0o700)
    settings.STEWARDSHIP_REPORTS_ROOT = root
    settings.STEWARDSHIP_DOWNLOAD_POOL = DownloadPool(ReadLimits(process_pool_size=1))
    fields = {"request_key": str(uuid4())}
    with day_two(harness), task_login(ServiceRole.WEB, exact=True, reconnect=True):
        response, body = read(browser, reverse("admin:past_campaigns"))
        assert response.status_code == 200
        assert b"A final archive of" in body and b"through day 1" in body
        assert b"its last full day" in body
        assert b"stewardship-%d.xlsx" % CURRENT_YEAR in body
        assert b'id="campaign-archive-reason"' not in body
        response = post(browser, ROUTE, fields)
        assert response.status_code == 302, response
        job = response["Location"]
    request = ExportRequest.objects.get(request_key=fields["request_key"])
    assert request.fact_set_id == ready.pk
    assert request.parameters == {
        "cutoff_date": ready.first_date.isoformat(),
        "final": True,
        "year": CURRENT_YEAR,
    }
    work(harness, request, root)
    with restricted_download_pool(settings):
        response, body = search(browser, export_action(job, "download"), {})
    assert response.status_code == 200
    assert f"stewardship-{CURRENT_YEAR}.xlsx" in response["Content-Disposition"]
    archive = read_archive(bytes(body))
    assert archive.metadata["is_final"] is True
    assert {f["family_id"] for f in archive.families} == set(PLEDGES)


def test_closed_today_with_a_response_today_waits_for_the_next_day(scenario):
    """A response after the cutoff holds the final file until the next day.

    Household 2 revises at 06:00 on day 2; the campaign then closes. Seen at
    noon on day 2, a final file cut off at day 1 would leave that response
    out and change tomorrow, so the page and the service (and so the
    command) refuse with the reason. On day 3, once a fact set reaches day 2,
    the final file is offered through day 2, the closing day (the review's
    refinement of the 2026-10-13 decision).
    """
    from parishkit.stewardship.reports.archive_exports import (
        CLOSED_TODAY,
        campaign_now,
    )
    from parishkit.stewardship.reports.facts import FactUnavailable
    from parishkit.stewardship.reports.materialization import materialize_fact_set
    from parishkit.stewardship.source.snapshot_models import SourceSnapshot

    from .test_fact_materialization_postgresql import allocation
    from .test_source_snapshots_postgresql import permit

    harness, ready, browser, actor = scenario
    opened = harness.campaign.active_configuration.starts_at
    revise(harness, opened + timedelta(days=1, hours=6))
    close_campaign(harness.campaign, actor)
    assert harness.campaign.state == "closed"
    with day_two(harness):
        offer = archive_offer(harness.campaign.pk, now=campaign_now())
        assert (offer.reason, offer.final) == (CLOSED_TODAY, True)
        assert offer.cutoff_date == ready.first_date
        with task_login(ServiceRole.WEB, exact=True, reconnect=True):
            body = read(browser, reverse("admin:past_campaigns"))[1]
            assert b'id="campaign-archive-reason"' in body
            assert b"Available tomorrow: the campaign closed today" in body
            answer = post(browser, ROUTE, {"request_key": str(uuid4())})
            assert answer.status_code == 409
            # The command's path: the same service, refused the same way.
            with pytest.raises(FactUnavailable):
                create_archive_export(
                    harness.service.store,
                    actor,
                    campaign_id=harness.campaign.pk,
                    request_key=uuid4(),
                )
    assert not ExportRequest.objects.exists()
    day_three = campaign_clock(opened + timedelta(days=2, hours=12))
    newest = SourceSnapshot.objects.filter(state="promoted").latest("generation")
    with day_three:
        record, claim = allocation(harness, watermark=len(PLEDGES) + 1, source=newest)
        materialize_fact_set(record.pk, claim, admit=permit, interactive=True)
        offer = archive_offer(harness.campaign.pk, now=campaign_now())
    assert offer.available and offer.final
    assert offer.cutoff_date == ready.first_date + timedelta(days=1)


def test_before_day_1_the_download_is_unavailable_with_its_reason(scenario):
    """The button is unavailable with the reason; a post queues nothing."""
    harness, _, browser, _ = scenario
    opened = harness.campaign.active_configuration.starts_at
    with (
        campaign_clock(opened + timedelta(hours=6)),
        task_login(ServiceRole.WEB, exact=True, reconnect=True),
    ):
        response, body = read(browser, reverse("admin:past_campaigns"))
        assert b'id="campaign-archive-reason"' in body
        assert b"first day has ended" in body
        response = post(browser, ROUTE, {"request_key": str(uuid4())})
        assert response.status_code == 409, response.content
    assert archive_offer(harness.campaign.pk, now=opened).reason == BEFORE_DAY_1
    assert not ExportRequest.objects.exists()


def test_an_old_sign_in_queues_the_export(scenario):
    """A sign-in older than five minutes queues the export (#1547)."""
    from .auth_builders import stale_sign_in

    harness, _, browser, _ = scenario
    stale_sign_in()
    with day_two(harness), task_login(ServiceRole.WEB, exact=True, reconnect=True):
        response = post(browser, ROUTE, {"request_key": str(uuid4())})
        assert response.status_code == 302
    assert ExportRequest.objects.count() == 1


def test_cli_export_archive_is_the_page_form(scenario, cli):
    """``export archive`` queues the same export and records its events."""
    harness, ready, _, _ = scenario
    run = cli()
    with day_two(harness):
        code, document = run("export", "archive", "--yes")
        assert code == 0, document
        result = document["result"]
        assert result["created"] is True
        request = ExportRequest.objects.get(pk=result["export"]["id"])
        assert (request.report, request.fact_set_id) == ("campaign_archive", ready.pk)
    opened = harness.campaign.active_configuration.starts_at
    with campaign_clock(opened + timedelta(hours=6)):
        code, document = run("export", "archive", "--yes")
        assert code == 1 and document["error"]["code"] == "not_available", document
    assert (
        AuditContext.objects.filter(
            event__event_type="admin_cmd_export_archive"
        ).count()
        == 1
    )


def test_only_a_current_administrator_may_export(scenario, cli, google):
    """A Ministry leader and a disabled Administrator are refused by every path."""
    from .auth_builders import signed_in

    harness, _, browser, actor = scenario
    run = cli()
    google[0].update(email="valid@example.org", sub="chairperson-subject")
    leader_browser, login = signed_in()
    assert login.status_code == 403
    leader = PortalUser.objects.get(email="valid@example.org").pk
    with day_two(harness), task_login(ServiceRole.WEB, exact=True, reconnect=True):
        # The leader's sign-in was refused, so the post has no Admin session.
        answer = post(leader_browser, ROUTE, {"request_key": str(uuid4())})
        assert answer.status_code == 403
        with pytest.raises(PermissionError):
            create_archive_export(
                harness.service.store,
                leader,
                campaign_id=harness.campaign.pk,
                request_key=uuid4(),
            )
    PortalUser.objects.filter(pk=actor).update(disabled=True, version=F("version") + 1)
    with day_two(harness):
        with task_login(ServiceRole.WEB, exact=True, reconnect=True):
            refused = post(browser, ROUTE, {"request_key": str(uuid4())})
            assert "/admin/reports/exports/" not in refused.get("Location", "")
        code, document = run("export", "archive", "--yes")
        assert code != 0 and not document["ok"], document
    assert not ExportRequest.objects.exists()


def test_the_answers_read_is_the_workers_only(scenario):
    """Only the worker login (and the owner) may read the answers' parts."""
    harness, ready, *_ = scenario
    opened = harness.campaign.active_configuration.starts_at
    cutoff = opened + timedelta(days=1)
    with (
        task_login(ServiceRole.WORKER, exact=True, reconnect=True),
        connection.cursor() as cursor,
    ):
        cursor.execute("SELECT * FROM " + ANSWERS, [ready.pk, cutoff])
        rows = cursor.fetchall()
    assert len(rows) == len(PLEDGES)
    with (
        task_login(ServiceRole.WEB, exact=True, reconnect=True),
        pytest.raises(DatabaseError),
        transaction.atomic(),
        connection.cursor() as cursor,
    ):
        cursor.execute("SELECT * FROM " + ANSWERS, [ready.pk, cutoff])
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute("CREATE ROLE p1018a_probe LOGIN NOINHERIT")
        cursor.execute(
            "GRANT EXECUTE ON FUNCTION public.stewardship_campaign_archive_answers_v1"
            "(uuid, timestamp with time zone) TO p1018a_probe"
        )
        cursor.execute("SET LOCAL SESSION AUTHORIZATION p1018a_probe")
        with (
            pytest.raises(DatabaseError, match="Only the general worker"),
            transaction.atomic(),
        ):
            cursor.execute("SELECT * FROM " + ANSWERS, [ready.pk, cutoff])
        transaction.set_rollback(True)


def test_the_request_guard_refuses_a_cutoff_its_fact_set_lacks(scenario):
    """The SQL guard, not only the service, binds the cutoff and final mark."""
    from dataclasses import replace
    from datetime import date

    from parishkit.stewardship.reports.archive_exports import campaign_now

    harness, _, _, actor = scenario
    with day_two(harness), task_login(ServiceRole.WEB, exact=True, reconnect=True):
        offer = archive_offer(harness.campaign.pk, now=campaign_now())
        assert offer.available
        with pytest.raises(DatabaseError, match="exact authorized input"):
            create_archive_export(
                harness.service.store,
                actor,
                campaign_id=harness.campaign.pk,
                request_key=uuid4(),
                offer=replace(offer, cutoff_date=date(2000, 1, 1)),
            )
        # Only a closed or archived campaign's archive may be final.
        with pytest.raises(DatabaseError, match="exact authorized input"):
            create_archive_export(
                harness.service.store,
                actor,
                campaign_id=harness.campaign.pk,
                request_key=uuid4(),
                offer=replace(offer, final=True),
            )
    assert not ExportRequest.objects.exists()


def test_migration_check_refuses_a_missing_part():
    """The frozen file's DO block fails without its function, kind or branch."""
    text = FROZEN.read_text(encoding="utf-8")
    block = text[text.index("DO $check$") : text.index("$check$;") + len("$check$;")]
    old = (
        (ROOT / "src/parishkit/stewardship/schema/migrations")
        .joinpath("0071_comparison_exports.sql")
        .read_text(encoding="utf-8")
    )
    known = next(
        line
        for line in old.splitlines()
        if line.startswith(
            "ALTER TABLE public.stewardship_export_request "
            "ADD CONSTRAINT export_report_known"
        )
    )
    # 0109 (#1398) relaxes the check, since an ended export's discard
    # clears its fact_set_id, so the block runs on 0084's own declaration.
    archive = next(
        line
        for line in text.splitlines()
        if line.startswith(
            "ALTER TABLE public.stewardship_export_request "
            "ADD CONSTRAINT export_report_known"
        )
    )
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(
            "ALTER TABLE stewardship_export_request DROP CONSTRAINT "
            "export_report_known; " + archive
        )
        cursor.execute(block)  # 0084's schema passes.
        for change in (
            "ALTER FUNCTION public.stewardship_campaign_archive_answers_v1"
            "(uuid, timestamp with time zone) SECURITY INVOKER",
            "ALTER TABLE stewardship_export_request DROP CONSTRAINT "
            "export_report_known; " + known,
            "ALTER FUNCTION public.stewardship_export_pin_guard_v1() SECURITY DEFINER",
        ):
            with (
                pytest.raises(DatabaseError, match="Migration 0084"),
                transaction.atomic(),
            ):
                cursor.execute(change)
                cursor.execute(block)
        cursor.execute(block)
        # This module runs with transaction=True, so the outer block would
        # commit 0084's stricter constraint and leave it for later tests on
        # the same database: a later discard (0109) could then not clear a
        # participation or archive export's fact_set_id. Roll it back.
        transaction.set_rollback(True)


def test_the_checked_file_is_read_back(scenario):
    """The worker's document is the reader-accepted file of the request."""
    from parishkit.stewardship.reports.archive_exports import archive_document

    harness, ready, _, actor = scenario
    with day_two(harness), task_login(ServiceRole.WEB, exact=True, reconnect=True):
        request = create_archive_export(
            harness.service.store,
            actor,
            campaign_id=harness.campaign.pk,
            request_key=uuid4(),
        )
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        document = archive_document(request)
    assert document.item_count == len(PLEDGES)
    assert document.file_name == f"stewardship-{CURRENT_YEAR}-partial.xlsx"
    assert read_archive(io.BytesIO(document.data).getvalue()).metadata[
        "active_families"
    ]


def test_staff_cannot_export_the_archive(scenario, google):
    """Staff see neither Past campaigns nor its download, by page or service."""
    from ..policy_factory import address
    from .auth_builders import signed_in
    from .campaign_builders import change

    harness, _, _, _ = scenario
    store = harness.service.store
    staff = address("staff@example.org", roles=("staff",))
    change(
        store,
        store.active(),
        uuid4(),
        [{"operation": "add", "section": "login_rules", **staff}],
    )
    google[0].update(email="staff@example.org", sub="synthetic-staff")
    browser, login = signed_in()
    assert login.status_code == 302
    member = PortalUser.objects.get(email="staff@example.org").pk
    with day_two(harness), task_login(ServiceRole.WEB, exact=True, reconnect=True):
        assert read(browser, reverse("admin:past_campaigns"))[0].status_code == 403
        assert post(browser, ROUTE, {"request_key": str(uuid4())}).status_code == 403
        with pytest.raises(PermissionError):
            create_archive_export(
                store, member, campaign_id=harness.campaign.pk, request_key=uuid4()
            )
    assert not ExportRequest.objects.exists()


def test_a_fact_set_that_misses_a_response_is_never_offered(scenario):
    """A set built during the cutoff day can miss a later response that day.

    Household 2 revises at 06:00 after day 1 opened, after the scenario's
    set was built: on day 2 the download is unavailable (pinning that set
    would fail every attempt), until a set covering the revision is ready.
    """
    from parishkit.stewardship.reports.archive_exports import NOT_READY, campaign_now
    from parishkit.stewardship.reports.materialization import materialize_fact_set
    from parishkit.stewardship.source.snapshot_models import SourceSnapshot

    from .test_fact_materialization_postgresql import allocation
    from .test_source_snapshots_postgresql import permit

    harness, ready, browser, _ = scenario
    opened = harness.campaign.active_configuration.starts_at
    revise(harness, opened + timedelta(hours=6))
    with day_two(harness):
        offer = archive_offer(harness.campaign.pk, now=campaign_now())
        assert offer.reason == NOT_READY
        with task_login(ServiceRole.WEB, exact=True, reconnect=True):
            body = read(browser, reverse("admin:past_campaigns"))[1]
            assert b"recalculated automatically" in body
            answer = post(browser, ROUTE, {"request_key": str(uuid4())})
            assert answer.status_code == 409
    assert not ExportRequest.objects.exists()
    newest = SourceSnapshot.objects.filter(state="promoted").latest("generation")
    record, claim = allocation(harness, watermark=len(PLEDGES) + 1, source=newest)
    covering = materialize_fact_set(record.pk, claim, admit=permit, interactive=True)
    with day_two(harness):
        offer = archive_offer(harness.campaign.pk, now=campaign_now())
    assert offer.available and offer.fact_set_id == covering.pk != ready.pk


def revise(harness, when):
    """Household 2 revises its pledge at ``when``."""
    from ..test_financial_answers import CHECK
    from .test_financial_report_postgresql import family_session, pledge
    from .test_response_http_postgresql import load_form
    from .test_runtime_auth_grants_postgresql import web_login

    with campaign_clock(when), web_login():
        family = family_session(harness, 2)
        pledge(
            family,
            load_form(family),
            annual_pledge="999",
            frequency="annual",
            shares={CHECK: ""},
        )
