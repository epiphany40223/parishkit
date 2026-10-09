"""The aggregate report reads of ``pk-stewardship admin`` (ADM-11 PR 8c).

Process admission is replaced by the test's own Django assembly (the runner
of test_admin_task_retry_cli_postgresql.py); everything after it is the real
command line on the restricted web login, reading the campaign reports
through the pages' own functions under the same campaign read guard. Each
command's counts are the page's read, and each records the page's own view
events with the same outcomes and context members as a page view.
"""

from collections import Counter
from dataclasses import asdict
from uuid import uuid4

import pytest
from django.db import transaction

from parishkit.stewardship.accounts import automation_sessions as automation
from parishkit.stewardship.accounts.automation_models import AutomationSession
from parishkit.stewardship.accounts.policy import current_principal
from parishkit.stewardship.audit.models import AuditContext, AuditEvent
from parishkit.stewardship.campaigns.models import Campaign
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.reports.information import (
    InformationQuery,
    information_page,
)
from parishkit.stewardship.reports.ministries import MinistryQuery, ministry_page

from ..test_financial_answers import CHECK, OPTIONS
from .auth_builders import signed_in
from .response_builders import activate_response_service
from .test_admin_export_cli_postgresql import read_only_session
from .test_admin_status_cli_postgresql import (
    admin,  # noqa: F401
    one,
    session,
)
from .test_admin_task_retry_cli_postgresql import approved_by_browser, runner
from .test_background_grants_postgresql import task_login
from .test_family_mail_preparation_postgresql import family_mail  # noqa: F401
from .test_financial_report_postgresql import pledge
from .test_financial_source_postgresql import financial_source
from .test_information_followup_postgresql import search
from .test_ministry_reports_postgresql import setup as ministry_setup
from .test_report_selection_postgresql import pointer
from .test_report_workspace_postgresql import (
    http_scenario,  # noqa: F401
    read,
)
from .test_response_http_postgresql import load_form
from .test_runtime_auth_grants_postgresql import web_login

pytestmark = pytest.mark.django_db(transaction=True)


def outcomes(event_type):
    """The ``event_type`` events so far, counted by outcome."""
    return Counter(
        context["outcome"]
        for context in AuditContext.objects.filter(
            event__event_type=event_type
        ).values_list("context", flat=True)
    )


def events(event_type):
    """The ``event_type`` events so far: (outcome, context members) counted."""
    return Counter(
        (context["outcome"], frozenset(context))
        for context in AuditContext.objects.filter(
            event__event_type=event_type
        ).values_list("context", flat=True)
    )


def parity(event_type, page, command):
    """A page view and a command record the same events of ``event_type``.

    ``page()`` views the page on the restricted web login and ``command()``
    runs the command; each must record events with the same outcomes and
    the same context members.
    """
    before = events(event_type)
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        page()
    viewed = events(event_type) - before
    command()
    run = events(event_type) - before - viewed
    assert viewed and run == viewed, (viewed, run)


@pytest.fixture
def cli(settings, monkeypatch):
    """``run(*argv)`` with a full-scope session approved from the one browser.

    Returns (exit code, document). Built after the scenario's browser signed
    in, so the session acts as that page's own Administrator.
    """

    def build():
        """The runner and its session, once the browser has signed in."""
        commands = runner(settings.STEWARDSHIP_AUTH_RUNTIME, monkeypatch)
        secret = approved_by_browser(commands.service)

        def run(*argv, secret=secret):
            """One command that prints exactly one document."""
            code, document, _ = commands(*argv, secret=secret)
            return code, document

        run.service, run.secret = commands.service, secret
        return run

    return build


def test_participation_responses_information_and_the_list(http_scenario, cli):  # noqa: F811
    """The current campaign's reads match their pages, events included."""
    setup, browser = http_scenario
    facts = setup[2]
    campaign_id = facts.campaign_id
    pointer(facts)
    run = cli()
    root = "/admin/reports"

    code, document = run("report", "list")
    assert code == 0, document
    result = document["result"]
    assert result["campaign_id"] == str(campaign_id)
    offered = {row["command"]: row["available"] for row in result["reports"]}
    # The scenario's campaign has neither Financial nor Ministry stewardship,
    # so the menu greys those reports out; it never hides one by mode.
    assert offered == {
        "report responses": True,
        "report participation": True,
        "report financial": False,
        "report talents": False,
        "report information": True,
        "report ministry": False,
    }

    # Participation: the generation the page shows, whose fact set id is
    # what ``export create --fact-set`` takes, and the page's events.
    parity(
        "participation_viewed",
        lambda: read(browser, f"{root}/participation/"),
        lambda: run("report", "participation"),
    )
    code, document = run("report", "participation")
    assert code == 0, document
    result = document["result"]
    assert result["fact_set_id"] == str(facts.pk)
    assert result["status"] in {"current", "updating"}
    assert result["scope"] == "historical" and result["days"]
    assert result["statistics"]["active"]["families"] >= 1
    # The inactive subtotal option is gone (#728): the page's one comparison
    # figure is the all-Families aggregate.
    assert "inactive" not in result["statistics"]
    assert "comparison_pledge_all" in result["statistics"]
    code, document = run("report", "participation", "--scope", "current")
    assert code == 0 and document["result"]["status"] == "unavailable"
    assert document["result"]["fact_set_id"] is None
    assert document["result"]["days"] == []

    # The response dashboard: its funnel stages, in the page's order.
    parity(
        "response_dashboard_viewed",
        lambda: read(browser, f"{root}/responses/"),
        lambda: run("report", "responses"),
    )
    code, document = run("report", "responses", "--grain", "day")
    assert code == 0, document
    metrics = document["result"]["metrics"]
    assert [stage["key"] for stage in metrics["stages"]] == [
        "invited",
        "link_followed",
        "form_opened",
        "progressed",
        "submitted",
    ]
    assert metrics["grain"] == "day"

    # The information queue: the page's own count, under its filters.
    parity(
        "information_viewed",
        lambda: read(browser, f"{root}/information/"),
        lambda: run("report", "information"),
    )
    with task_login(ServiceRole.WEB, exact=True, reconnect=True), transaction.atomic():
        expected = information_page(
            campaign_id, InformationQuery(disposition="all"), page_size=1
        )["total"]
    code, document = run("report", "information", "--disposition", "all")
    assert code == 0 and document["result"]["matching"] == expected

    # A report the campaign does not include: the page refuses it after its
    # started event, and so does the command, which says not_available.
    parity(
        "financial_report_viewed",
        lambda: read(browser, f"{root}/financial/"),
        lambda: run("report", "financial"),
    )
    before = outcomes("financial_report_viewed")
    code, document = run("report", "financial")
    assert code == 1 and document["error"]["code"] == "not_available"
    assert outcomes("financial_report_viewed") - before == Counter(
        {"started": 1, "failed": 1}
    )
    # The Ministry page checks its campaigns (the module, a usable
    # ParishSoft snapshot, a leader's Ministries) before recording anything,
    # and shows its "no campaign" page: so does the command.
    before = outcomes("ministry_report_viewed")
    code, document = run("report", "ministry")
    assert code == 1 and document["error"]["code"] == "not_available"
    assert outcomes("ministry_report_viewed") == before

    # Failures the pages answer with 503 are unavailable (exit 3, retry),
    # never a refused option or a denial, and the read is recorded failed.
    from parishkit.stewardship.reports import response_dashboard, statistics

    def no_statistics(*args, **kwargs):
        """The statistics cannot be calculated from their inputs."""
        raise statistics.StatisticsUnavailable("Campaign statistics are unavailable.")

    def unshaped(*args, **kwargs):
        """A stored value the page cannot shape."""
        raise ValueError("unshaped")

    for patched, argv, event in (
        (
            (statistics, "calculate_statistics", no_statistics),
            ("report", "participation"),
            "participation_viewed",
        ),
        (
            (response_dashboard, "dashboard_context", unshaped),
            ("report", "responses"),
            "response_dashboard_viewed",
        ),
    ):
        before = outcomes(event)
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(*patched)
            code, document = run(*argv)
        assert code == 3 and document["error"]["code"] == "unavailable", argv
        assert outcomes(event) - before == Counter({"failed": 1}) + (
            Counter({"started": 1}) if event == "participation_viewed" else Counter()
        ), argv

    # Options the pages refuse are the pages' own refusals, before any read.
    before = AuditEvent.objects.count()
    for argv in (
        ("report", "participation", "--scope", "all"),
        ("report", "responses", "--mode", "rehearsal"),
        ("report", "information", "--needed", "maybe"),
        ("report", "information", "--start", "2054-13-01"),
        ("report", "ministry", "--state", "new"),
        ("report", "ministry", "--ministry", "9"),
    ):
        code, document = run(*argv)
        assert code == 1 and document["error"]["code"] == "invalid", argv
    assert AuditEvent.objects.count() == before

    # A read-only session reads every report.
    reader = read_only_session(run.service)
    code, document = run("report", "participation", secret=reader)
    assert code == 0 and document["result"]["fact_set_id"] == str(facts.pk)

    # An ended session: exit 5, and nothing read or recorded.
    row = AutomationSession.objects.get(
        secret_digest=automation.secret_digest(run.secret)
    )
    automation.revoke(
        row.pk,
        current_principal(run.service.store, row.principal_id),
        reason="revoked_by_owner",
    )
    before = AuditEvent.objects.filter(event_type="participation_viewed").count()
    for words in (("report", "list"), ("report", "participation")):
        code, document = run(*words)
        assert code == 5 and document["error"]["code"] == "session_ended", words
    assert (
        AuditEvent.objects.filter(event_type="participation_viewed").count() == before
    )


def test_ministry_summary_and_request_counts(response_service, google, cli):
    """The Ministry page's summary, and one list's count without its Members."""
    harness = ministry_setup(response_service)
    campaign_id = harness.campaign.pk
    browser, _ = signed_in()
    run = cli()
    root = "/admin/reports/ministries/"

    parity(
        "ministry_report_viewed",
        lambda: read(browser, root),
        lambda: run("report", "ministry"),
    )
    parity(
        "ministry_report_viewed",
        lambda: search(browser, root + "joining/", {"ministry": "9"}),
        lambda: run("report", "ministry", "--ministry", "9", "--requests", "join"),
    )
    code, document = run("report", "ministry")
    assert code == 0, document
    result = document["result"]
    assert [
        (row["ministry"], row["joining"], row["leaving"], row["unresolved"])
        for row in result["summaries"]
    ] == [(4, 0, 1, 1), (9, 1, 0, 1)]
    assert result["matching"] == 2 and result["ministry"] is None
    code, document = run("report", "ministry", "--ministry", "4", "--requests", "leave")
    assert code == 0, document
    result = document["result"]
    assert result["matching"] == 1 and result["requests"] == "leave"
    assert [row["ministry"] for row in result["summaries"]] == [4]
    # The count is the page's own for the same filters.
    code, document = run(
        "report",
        "ministry",
        "--ministry",
        "9",
        "--requests",
        "join",
        "--history",
        "all",
        "--state",
        "resolved",
    )
    with task_login(ServiceRole.WEB, exact=True, reconnect=True), transaction.atomic():
        actor = current_principal(run.service.store, harness_admin(run))
        expected = ministry_page(
            campaign_id,
            MinistryQuery.parse({"history": "all", "state": "resolved"}, detail=True),
            actor,
            ministry_id=9,
        )["total"]
    assert code == 0 and document["result"]["matching"] == expected
    # A Ministry outside the reader's scope (not in the campaign) is the
    # page's refusal; one outside the page's range is a refused option.
    code, document = run(
        "report", "ministry", "--ministry", "123", "--requests", "join"
    )
    assert code == 1 and document["error"]["code"] == "denied", document
    for duid in ("0", "2147483648"):
        code, document = run(
            "report", "ministry", "--ministry", duid, "--requests", "join"
        )
        assert code == 1 and document["error"]["code"] == "invalid", duid
    # A page past the last shows the last page, as on the page.
    code, page = run("report", "ministry", "--page", "9", "--size", "25")
    assert code == 0 and page["result"]["page"] == 1
    assert page["result"]["summaries"] and page["result"]["matching"] == 2
    # No Member's name, contact or DUID reaches the terminal.
    assert "@" not in str(document) and "Example" not in str(document)
    # Ministry 4's events: the summaries record the two Ministry rows shown
    # of two; its leave list records 0 rows displayed and the list's count.
    contexts = list(
        AuditContext.objects.filter(
            event__event_type="ministry_report_viewed",
            context__ministry_duid=4,
            context__outcome="succeeded",
        ).values_list("context", flat=True)
    )
    assert {(item["count"], item["matching_count"]) for item in contexts} == {
        (2, 2),
        (0, 1),
    }

    # The talents summary is the page's, counts only.
    parity(
        "talents_report_viewed",
        lambda: read(browser, "/admin/reports/talents/"),
        lambda: run("report", "talents"),
    )
    code, document = run("report", "talents")
    assert code == 0, document
    assert set(document["result"]) == {
        "campaign_id",
        "collects_talents",
        "members",
        "cannot_serve",
        "cannot_attend",
        "talents",
    }


def harness_admin(run):
    """The Administrator the command's session acts for."""
    row = AutomationSession.objects.get(
        secret_digest=automation.secret_digest(run.secret)
    )
    return row.principal_id


def test_financial_summary_without_rows(response_service, google, cli):
    """The financial report's summary over every pledge, never a Family row."""
    harness = response_service
    financial_source(harness, modules=["financial"], options=map(asdict, OPTIONS))
    harness = activate_response_service(harness)
    with web_login():
        pledge(harness, load_form(harness), shares={CHECK: ""})
    campaign_id = harness.campaign.pk
    browser, _ = signed_in()
    run = cli()
    parity(
        "financial_report_viewed",
        lambda: read(browser, "/admin/reports/financial/"),
        lambda: run("report", "financial"),
    )
    code, document = run("report", "financial")
    assert code == 0, document
    result = document["result"]
    assert result["families"] == 1 and result["annual_total"] == "1234.50"
    assert result["frequencies"] == {"monthly": 1}
    assert result["shares"] == [{"id": str(CHECK), "count": 1}]
    assert result["no_share"] == 0 and result["cannot_give"] == 0
    assert "1200.00" not in str(document) and "Example" not in str(document)
    # The page records the rows it displayed; the command displays none.
    succeeded = [
        context
        for context in AuditContext.objects.filter(
            event__event_type="financial_report_viewed"
        ).values_list("context", flat=True)
        if context["outcome"] == "succeeded"
    ]
    assert sorted((item["count"], item["matching_count"]) for item in succeeded) == [
        (0, 1),
        (0, 1),
        (1, 1),
    ]
    assert Campaign.objects.filter(pk=campaign_id).exists()


def test_without_a_current_campaign(admin, google):  # noqa: F811
    """No current campaign: the list names none, each report is not_available."""
    secret = session(admin)
    code, document = one(admin, "report", "list", secret=secret)
    assert code == 0, document
    assert document["result"]["campaign_id"] is None
    assert not any(row["available"] for row in document["result"]["reports"])
    for word in ("participation", "responses", "information", "ministry"):
        code, document = one(admin, "report", word, secret=secret)
        assert code == 1 and document["error"]["code"] == "not_available", word
    assert not AuditEvent.objects.filter(event_type__endswith="_viewed").exists()


def test_a_page_s_unknown_campaign_id_is_never_read(http_scenario, cli):  # noqa: F811
    """Reports read the current campaign only; the command takes no campaign."""
    setup, _ = http_scenario
    pointer(setup[2])
    run = cli()
    code, document = run("report", "participation", "--campaign", str(uuid4()))
    assert code == 2 and document["error"]["code"] == "usage"
