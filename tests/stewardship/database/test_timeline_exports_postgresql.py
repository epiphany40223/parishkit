"""The Family timeline export: page, command line and SQL (ADM-11 PR 8g, 0044).

The campaign is the response-metrics ``funnel`` fixture, as the timeline
page's own tests use it: the corpus Family signs in, opens the form, gets
past the first step and submits in Testing. Its timeline is exported from the
page's form and from ``export family-timeline``; both make the same record
and event, the real worker renders the file with the Family code for an
Administrator, and the command's stream is the page's download byte for byte.
The capture's SQL checks refuse what the web must never store.
"""

from uuid import uuid4

import pytest
from django.db import DatabaseError, transaction
from django.urls import reverse

from parishkit.stewardship.audit.models import AuditContext, AuditEvent
from parishkit.stewardship.campaigns.credential_models import FamilyCampaign
from parishkit.stewardship.campaigns.family_identity import code_context
from parishkit.stewardship.campaigns.schedule_models import ScheduleDefinition
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.jobs.dispatch import execute_hint
from parishkit.stewardship.jobs.queues import WorkQueue
from parishkit.stewardship.jobs.storage import _status
from parishkit.stewardship.reports.export_models import (
    ExportRequest,
    TimelineExportSnapshot,
)
from parishkit.stewardship.reports.export_services import TASK_TYPE
from parishkit.stewardship.reports.export_tasks import export_handler

from ..policy_factory import address
from .auth_builders import signed_in, stale_sign_in
from .campaign_builders import campaign_clock, change
from .test_admin_export_cli_postgresql import download, read_only_session
from .test_admin_family_export_cli_postgresql import reports_root  # noqa: F401
from .test_admin_report_cli_postgresql import cli  # noqa: F401
from .test_background_grants_postgresql import task_login
from .test_export_views_postgresql import post, restricted_download_pool
from .test_information_followup_postgresql import search
from .test_report_workspace_postgresql import read as get
from .test_response_metrics_postgresql import (
    dispatch_all,
    funnel,  # noqa: F401 (the fixture)
    open_form,
    prepare_all,
    progress_to,
    respond,
)
from .test_taskrun_postgresql import act

pytestmark = pytest.mark.django_db(transaction=True)


def rehearsed_timeline(request):
    """The funnel campaign with the corpus Family's Testing activity."""
    harness, _epoch = request.getfixturevalue("funnel")
    families = dict(FamilyCampaign.objects.values_list("family_duid", "pk"))
    due = ScheduleDefinition.objects.get(kind="initial").current_revision.due_at
    with campaign_clock(due):
        rehearsal = prepare_all(harness, "testing")
    open_form(harness)
    progress_to(harness, "census")
    respond(harness)
    dispatch_all(harness, rehearsal, families, due)
    return harness, families


def requested():
    """Each ``export_requested`` event's context members, in order."""
    return [
        frozenset(context)
        for context in AuditContext.objects.filter(event__event_type="export_requested")
        .order_by("event__created_at")
        .values_list("context", flat=True)
    ]


def render(harness, root, export_id):
    """The real worker's render, with the general keyring for the code."""
    request = ExportRequest.objects.get(pk=export_id)
    with task_login(ServiceRole.WORKER, exact=True, reconnect=True):
        assert execute_hint(
            request.task_id,
            queue=WorkQueue.GENERAL,
            worker_id=uuid4(),
            handlers={
                TASK_TYPE: export_handler(
                    store=harness.service.store,
                    root=root,
                    general=harness.rings.general,
                )
            },
        )


def test_the_page_and_the_command_export_the_same_timeline(
    request,
    auth_service,
    google,
    cli,  # noqa: F811
    reports_root,  # noqa: F811
    settings,
):
    """One record kind, one event, one file, from the page or the command."""
    harness, families = rehearsed_timeline(request)
    family = families[1]
    page = reverse("admin:family_timeline", args=[family])
    browser, _ = signed_in()
    run = cli()
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        response, body = get(browser, page + "?mode=testing")
    assert response.status_code == 200 and b"Export this timeline" in body
    assert f'action="{page}exports/"'.encode() in body
    fields = {
        "mode": "testing",
        "format": "csv",
        "browser_timezone": "UTC",
        "request_key": str(uuid4()),
    }
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        assert post(browser, page + "exports/", fields).status_code == 302
        # The same form again returns the same export; another format with
        # the same key is the page's 409.
        assert post(browser, page + "exports/", fields).status_code == 302
        assert (
            post(browser, page + "exports/", fields | {"format": "pdf"}).status_code
            == 409
        )
        assert (
            post(browser, page + "exports/", fields | {"extra": "x"}).status_code == 400
        )
    by_page = ExportRequest.objects.get(request_key=fields["request_key"])
    argv = ("export", "family-timeline", str(family), "--mode", "testing")
    # The file can hold the Family's code, so the command is fresh-gated
    # (#547): it asks first, and unanswered nothing is made.
    gates = AuditEvent.objects.filter(event_type="automation_fresh_gate")
    code, document = run(*argv, "--format", "csv", "--timezone", "UTC")
    assert code == 4 and document["error"]["code"] == "confirmation_required"
    assert ExportRequest.objects.filter(report="family_timeline").count() == 1
    assert not gates.exists()
    argv = (*argv, "--yes")
    code, document = run(*argv, "--format", "csv", "--timezone", "UTC")
    assert code == 0, document
    # The session stood in for the page's recent sign-in.
    assert gates.count() == 1
    by_command = ExportRequest.objects.get(pk=document["result"]["export"]["id"])
    for field in ("report", "campaign_id", "parameters", "format", "browser_timezone"):
        assert getattr(by_command, field) == getattr(by_page, field), field
    assert by_command.report == "family_timeline"
    page_capture = TimelineExportSnapshot.objects.get(pk=by_page.timeline_snapshot_id)
    command_capture = TimelineExportSnapshot.objects.get(
        pk=by_command.timeline_snapshot_id
    )
    assert {**page_capture.document, "as_of": None} == {
        **command_capture.document,
        "as_of": None,
    }
    whats = [event["what"] for event in command_capture.document["events"]]
    assert "Submitted a response" in whats and "Signed in" in whats
    assert command_capture.row_count == len(whats)
    first, second = requested()
    assert first == second
    assert (
        AuditEvent.objects.filter(event_type="admin_cmd_export_family_timeline").count()
        == 1
    )
    # Nothing of the Family reaches the terminal.
    name = command_capture.document["family"]["name"]
    assert name not in str(document)

    # The worker writes the code into the file for an Administrator; the
    # command's stream is the page's download of the same export.
    render(harness, reports_root, by_command.pk)
    family_row = FamilyCampaign.objects.get(pk=family)
    code_text = harness.rings.general.decrypt(
        family_row.code_ciphertext, context=code_context(family_row.pk)
    ).decode()
    status, body, streamed = download(by_command.pk, run.secret)
    assert status == 0, streamed
    assert code_text.encode() in body and b"Submitted a response" in body
    assert code_text not in str(streamed)
    with restricted_download_pool(settings):
        response, page_body = search(
            browser, reverse("admin:report_export_download", args=[by_command.pk]), {}
        )
    assert response.status_code == 200 and page_body == body

    # The command's refusals: a read-only session, another campaign's or an
    # unknown Family, a mode the page does not offer.
    reader = read_only_session(run.service)
    code, document = run(*argv, "--format", "csv", "--timezone", "UTC", secret=reader)
    assert code == 1 and document["error"]["code"] == "denied"
    code, document = run(
        "export",
        "family-timeline",
        str(uuid4()),
        "--format",
        "csv",
        "--timezone",
        "UTC",
        "--yes",
    )
    assert code == 1 and document["error"]["code"] == "not_available", document


def test_the_timeline_export_needs_a_fresh_sign_in(request, auth_service, google):
    """A stale sign-in queues nothing and returns to the timeline (#547); after
    the step-up the same form queues the export."""
    harness, families = rehearsed_timeline(request)
    page = reverse("admin:family_timeline", args=[families[1]])
    browser, _ = signed_in()
    fields = {
        "mode": "production",
        "format": "csv",
        "browser_timezone": "UTC",
        "request_key": str(uuid4()),
    }
    stale_sign_in()
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        refused = post(browser, page + "exports/", fields, HTTP_ACCEPT="text/html")
    assert refused.status_code == 403
    body = refused.content.decode()
    assert "Confirm with Google" in body and "Nothing was done" in body
    assert f'name="next" value="{page}"' in body
    assert not ExportRequest.objects.filter(report="family_timeline").exists()
    signed_in(browser)
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        assert post(browser, page + "exports/", fields).status_code == 302
    assert ExportRequest.objects.filter(report="family_timeline").count() == 1


def test_staff_get_no_timeline_export(request, auth_service, google):
    """Staff see the summary only, so they get no form and the post is denied."""
    harness, families = rehearsed_timeline(request)
    store = harness.service.store
    change(
        store,
        store.active(),
        uuid4(),
        [
            {
                "operation": "add",
                "section": "login_rules",
                **address("staff@example.org", roles=("staff",)),
            }
        ],
    )
    google[0]["email"] = "staff@example.org"
    browser, _ = signed_in()
    page = reverse("admin:family_timeline", args=[families[1]])
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        response, body = get(browser, page)
        assert response.status_code == 200 and b"Export this timeline" not in body
        fields = {
            "mode": "production",
            "format": "csv",
            "browser_timezone": "UTC",
            "request_key": str(uuid4()),
        }
        assert post(browser, page + "exports/", fields).status_code == 403
    assert not ExportRequest.objects.filter(report="family_timeline").exists()


def test_the_capture_refuses_what_the_web_must_not_store(request, auth_service):
    """SQL admits only a well-formed capture of an Administrator, and never
    changes one."""
    from parishkit.stewardship.accounts.runtime_models import SystemConfiguration

    from .test_policy_postgresql import user

    harness, families = rehearsed_timeline(request)
    family = FamilyCampaign.objects.get(pk=families[1])
    admin = user("admin@example.org")
    configuration = SystemConfiguration.objects.get().active_configuration_id
    good = {
        "family": {
            "id": str(family.pk),
            "duid": family.family_duid,
            "name": "Example",
            "envelope": None,
            "reach": "Yes",
        },
        "mode": "production",
        "as_of": "2054-10-05T14:00:00+00:00",
        "summary": {
            "submissions": 0,
            "first_submitted_at": None,
            "last_submitted_at": None,
            "last_email": None,
            "furthest_step": None,
            "furthest_at": None,
            "last_seen_at": None,
        },
        "events": [{"at": "2054-10-01T13:00:00+00:00", "what": "x", "detail": ""}],
    }
    parameters = {
        "family_id": str(family.pk),
        "mode": "production",
        "rehearsal_epoch_id": None,
    }

    def insert(actor=admin.pk, document=good, values=parameters, role=ServiceRole.WEB):
        """Try one capture as a restricted login, in its own transaction."""
        with (
            task_login(role, exact=True, reconnect=True),
            transaction.atomic(),
        ):
            TimelineExportSnapshot.objects.create(
                campaign_id=harness.campaign.pk,
                family=family,
                configuration_id=configuration,
                actor_id=actor,
                correlation_id=uuid4(),
                parameters=values,
                document=document,
            )
            transaction.set_rollback(True)

    for bad in (
        {**good, "code": "ABCD-EFGH"},
        {**good, "family": {**good["family"], "code": "ABCD-EFGH"}},
        {**good, "events": [{"at": "x", "what": "x", "detail": "", "code": "y"}]},
        {**good, "events": ["not an object"]},
        {**good, "mode": "testing"},
        {**good, "family": {**good["family"], "id": str(uuid4())}},
        {k: v for k, v in good.items() if k != "summary"},
        {**good, "summary": {**good["summary"], "code": "ABCD-EFGH"}},
        {**good, "summary": {**good["summary"], "submissions": "1"}},
        {**good, "summary": {**good["summary"], "last_email": {"name": "x"}}},
        # Another Family's DUID, the DUID as a string, and a document over
        # the 128 KiB cap.
        {**good, "family": {**good["family"], "duid": family.family_duid + 1}},
        {**good, "family": {**good["family"], "duid": str(family.family_duid)}},
        {**good, "events": [{"at": "x", "what": "x" * 131072, "detail": ""}]},
    ):
        with pytest.raises(DatabaseError, match="Invalid timeline export"):
            insert(document=bad)
    with pytest.raises(DatabaseError, match="Invalid timeline export"):
        insert(values={**parameters, "mode": "testing"})
    # Not an Administrator: an unknown user, or a real Staff member.
    with pytest.raises(DatabaseError, match="capture is unavailable"):
        insert(actor=uuid4())
    store = harness.service.store
    change(
        store,
        store.active(),
        uuid4(),
        [
            {
                "operation": "add",
                "section": "login_rules",
                **address("staff@example.org", roles=("staff",)),
            }
        ],
    )
    staff = user("staff@example.org")
    configuration = SystemConfiguration.objects.get().active_configuration_id
    with pytest.raises(DatabaseError, match="capture is unavailable"):
        insert(actor=staff.pk)
    # The worker never captures, even for an Administrator.
    with pytest.raises(DatabaseError, match="capture is unavailable|permission denied"):
        insert(role=ServiceRole.WORKER)
    # A well-formed capture without its export request fails at commit.
    with (
        pytest.raises(DatabaseError, match="requires its request"),
        task_login(ServiceRole.WEB, exact=True, reconnect=True),
        transaction.atomic(),
    ):
        TimelineExportSnapshot.objects.create(
            campaign_id=harness.campaign.pk,
            family=family,
            configuration_id=configuration,
            actor_id=admin.pk,
            correlation_id=uuid4(),
            parameters=parameters,
            document=good,
        )
    assert not TimelineExportSnapshot.objects.exists()


def test_a_production_export_matches_its_page_and_regenerates(
    request,
    auth_service,
    google,
    cli,  # noqa: F811
    reports_root,  # noqa: F811
):
    """The file's lines are the page's; a regeneration reuses the capture, which
    nothing may change or delete."""
    from .test_export_cleanup_postgresql import expire_publication
    from .test_family_timeline_postgresql import lines

    harness, families = rehearsed_timeline(request)
    # The Production view of an invited Family: whatever lines it has live.
    family = families[11]
    page = reverse("admin:family_timeline", args=[family])
    browser, _ = signed_in()
    run = cli()
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        _, body = get(browser, page + "?size=all&sort=when")
    shown = lines(body)
    code, document = run(
        "export",
        "family-timeline",
        str(family),
        "--format",
        "csv",
        "--timezone",
        "UTC",
        "--yes",
    )
    assert code == 0, document
    made = ExportRequest.objects.get(pk=document["result"]["export"]["id"])
    assert made.parameters["mode"] == "production"
    capture = TimelineExportSnapshot.objects.get(pk=made.timeline_snapshot_id)
    # The file's lines are the page's, oldest first as the When heading sorts.
    assert [event["what"] for event in capture.document["events"]] == shown
    render(harness, reports_root, made.pk)
    status, body, _ = download(made.pk, run.secret)
    assert status == 0
    for what in shown:
        assert what.encode() in body

    # The capture is immutable, even to the web that wrote it.
    from django.db import connection

    for statement in (
        "UPDATE stewardship_timeline_export_snapshot SET row_count=0 WHERE id=%s",
        "DELETE FROM stewardship_timeline_export_snapshot WHERE id=%s",
    ):
        with (
            pytest.raises(DatabaseError, match="immutable|permission denied"),
            task_login(ServiceRole.WEB, exact=True, reconnect=True),
            transaction.atomic(),
            connection.cursor() as cursor,
        ):
            cursor.execute(statement, [capture.pk])
    # An expired file is regenerated from the same capture.
    expire_publication(made)
    # A timeline file is fresh-gated to regenerate too, as on its page.
    regenerate = ("export", "regenerate", str(made.pk), "--request-key", str(uuid4()))
    code, document = run(*regenerate)
    assert code == 4 and document["error"]["code"] == "confirmation_required"
    code, document = run(*regenerate, "--yes")
    assert code == 0, document
    again = ExportRequest.objects.get(pk=document["result"]["export"]["id"])
    assert again.pk != made.pk and again.timeline_snapshot_id == capture.pk


def test_a_family_of_another_campaign_is_refused(
    response_service, auth_service, google
):
    """Under a real successor campaign, the first campaign's Family is refused
    by the page's POST and by the capture's own SQL check."""
    from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
    from parishkit.stewardship.campaigns.lifecycle import Action
    from parishkit.stewardship.campaigns.models import Campaign
    from parishkit.stewardship.campaigns.runtime import return_to_testing
    from parishkit.stewardship.jobs.models import TaskRun

    from ..campaign_factory import campaign as campaign_record
    from .campaign_builders import add_draft, admit_test_work, close_campaign, command
    from .response_builders import activate_response_service
    from .test_policy_postgresql import user

    harness = activate_response_service(response_service)
    family = FamilyCampaign.objects.filter(campaign=harness.campaign).first()
    actor = uuid4()
    close_campaign(harness.campaign, actor)
    for row in TaskRun.objects.filter(state="running"):
        act(_status(row), "permanent_failure")
    with campaign_clock(harness.campaign.active_configuration.ends_at):
        command(harness.campaign, actor, Action.ARCHIVE)
        return_to_testing(
            campaign_id=harness.campaign.pk,
            request_id=uuid4(),
            expected_runtime_version=SystemConfiguration.objects.get().version,
            actor_id=actor,
            correlation_id=uuid4(),
            admit=admit_test_work,
        )
        store = auth_service.store
        result, row, _ = add_draft(
            store, store.active(), actor, campaign_record(name="Successor")
        )
        assert result.state == "applied" and Campaign.objects.count() == 2
        successor = row["id"]
        browser, _ = signed_in()
        with task_login(ServiceRole.WEB, exact=True, reconnect=True):
            response = post(
                browser,
                reverse("admin:family_timeline_export", args=[family.pk]),
                {
                    "mode": "production",
                    "format": "csv",
                    "browser_timezone": "UTC",
                    "request_key": str(uuid4()),
                },
            )
        assert response.status_code == 403
        admin = user("admin@example.org")
        configuration = SystemConfiguration.objects.get().active_configuration_id
        with (
            pytest.raises(DatabaseError, match="capture is unavailable"),
            task_login(ServiceRole.WEB, exact=True, reconnect=True),
            transaction.atomic(),
        ):
            TimelineExportSnapshot.objects.create(
                campaign_id=successor,
                family=family,
                configuration_id=configuration,
                actor_id=admin.pk,
                correlation_id=uuid4(),
                parameters={
                    "family_id": str(family.pk),
                    "mode": "production",
                    "rehearsal_epoch_id": None,
                },
                document={},
            )
    assert not ExportRequest.objects.filter(report="family_timeline").exists()
