"""The aggregate report reads' documents (ADM-11 PR 8c).

Pure tests: the golden documents of each ``report …`` command, built through
the projections the commands use from stand-ins for the pages' reads, with
an exact allowlist of member names and the personal-data pattern applied at
every depth; and the catalog entries. The commands against a real database
are in database/test_admin_report_cli_postgresql.py.
"""

import json
from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID

import pytest

from parishkit.stewardship import admin_cli, admin_report_reads
from parishkit.stewardship.jobs.send_history import SendKey
from parishkit.stewardship.reports.money import MoneyAmount

from .test_admin_reads import FORBIDDEN, members

CAMPAIGN = UUID("00000000-0000-4000-8000-000000000041")
FACTS = UUID("00000000-0000-4000-8000-000000000042")
SCHEDULE = UUID("00000000-0000-4000-8000-000000000043")
REVISION = UUID("00000000-0000-4000-8000-000000000044")
SHARE = "00000000-0000-4000-8000-000000000045"
NOW = datetime(2054, 10, 5, 14, 0, tzinfo=UTC)
ISO = "2054-10-05T14:00:00+00:00"


def report_list():
    """The reports root with one report greyed out."""
    return admin_report_reads.ReportList(
        campaign_id=CAMPAIGN,
        reports=[
            {
                "command": "report participation",
                "page": "participation",
                "available": True,
            },
            {
                "command": "report financial",
                "page": "financial_report",
                "available": False,
            },
        ],
    )


def people(families):
    """One population's statistics, with its pledge totals."""
    return SimpleNamespace(
        families=families,
        active_members=families * 2,
        eligible_email=families - 1,
        deliverable_email=families - 2,
        responses=3,
        annual_pledge=MoneyAmount(123456),
    )


def participation(*, financial=True):
    """The Participation page's read of one current generation."""
    day = SimpleNamespace(
        local_date=date(2054, 10, 1),
        first_responses=2,
        cumulative_responses=3,
        cohort_denominator=10,
        population_available=True,
        pledge_available=True,
        pledge_total=Decimal("1234.50"),
    )
    document = SimpleNamespace(
        fact_set_id=FACTS,
        source_generation=7,
        source_as_of=NOW,
        submission_watermark=11,
        first_date=date(2054, 10, 1),
        last_date=date(2054, 10, 1),
        days=(day,),
    )
    selection = SimpleNamespace(selected_at=NOW, status="current", document=document)
    statistics = SimpleNamespace(
        observed_at=NOW,
        source_generation=7,
        source_as_of=NOW,
        submission_watermark=11,
        financial_enabled=financial,
        active=people(10),
        comparison_pledge_all=MoneyAmount(654321),
    )
    query = SimpleNamespace(scope="historical")
    return admin_report_reads.participation_model(
        CAMPAIGN, query, selection, statistics
    )


def responses():
    """The response dashboard's context with one stage, bucket and send."""
    metrics = SimpleNamespace(
        as_of=NOW,
        timezone="America/New_York",
        grain="hour",
        stages=(SimpleNamespace(key="invited", count=5, note="private note"),),
        skipped_responded=1,
        submitted_uninvited=2,
        submitted_again=0,
        activity=(SimpleNamespace(start=NOW, links=1, forms=1, submissions=1),),
        sends=(
            SimpleNamespace(
                key=SendKey(SCHEDULE, REVISION, "production", 1),
                kind="initial",
                name="Invitation",
                scheduled=NOW,
                delivered=5,
                first_delivered_at=NOW,
                last_delivered_at=None,
            ),
        ),
        families=(),
    )
    query = SimpleNamespace(mode="production")
    return admin_report_reads.response_model(CAMPAIGN, query, {"metrics": metrics})


def no_rehearsal():
    """Testing mode with no active rehearsal: no figures, as on the page."""
    query = SimpleNamespace(mode="testing")
    return admin_report_reads.response_model(CAMPAIGN, query, {"metrics": None})


def financial():
    """``financial_page``'s shaped result: the summary, metadata and a row."""
    return admin_report_reads.financial_model(
        CAMPAIGN,
        {
            "metadata": {
                "name": "Private campaign name",
                "source_generation": 7,
                "source_as_of": NOW,
                "comparison_start": "2053-07-01",
                "comparison_end": "2054-06-30",
                "giving_through": date(2054, 9, 30),
            },
            "summary": {
                "families": 4,
                "annual_total": MoneyAmount(520000),
                "frequency_counts": {"monthly": 3, "none": 1},
                "share_counts": {SHARE: 2},
                "no_share": 2,
                "cannot_give": 1,
            },
            "share_choices": [(SHARE, "Private label")],
            "rows": [{"family_name": "Private Family"}],
            "total": 4,
        },
    )


def talents():
    """``talents_report``'s shaped result: the summary and a Member row."""
    return admin_report_reads.talents_model(
        CAMPAIGN,
        {
            "collects_talents": True,
            "summary": {
                "members": 3,
                "cannot_serve": 1,
                "cannot_attend": 1,
                "talent_counts": {"music": 2, "retired": 1},
            },
            "talent_choices": [("music", "Private label")],
            "members": [{"member_name": "Private Member"}],
        },
    )


def information():
    """The information queue's matching count."""
    return admin_report_reads.InformationReport(
        campaign_id=CAMPAIGN,
        filters={
            "disposition": "current_actionable",
            "needed": "any",
            "completed": "any",
            "start": "",
            "end": "",
        },
        source_as_of=ISO,
        matching=6,
    )


def ministry_query(**values):
    """The Ministry page's query, as its parser builds it."""
    from parishkit.stewardship.reports.ministries import MinistryQuery

    return MinistryQuery.parse(values, detail="history" in values)


MINISTRY_ROW = {
    "duid": 9,
    "name": "Choir",
    "active": True,
    "in_campaign": True,
    "joining": 2,
    "leaving": 1,
    "unresolved": 3,
    "requests": 3,
    "completed": 0,
    "progress": "0 of 3 (0%)",
}


def ministry_summary():
    """One summary page with one Ministry."""
    return admin_report_reads.ministry_model(
        CAMPAIGN,
        ministry_query(),
        {"summaries": [MINISTRY_ROW], "rows": [], "total": 1},
        ministry=None,
        requests=None,
    )


def ministry_list():
    """One Ministry's join list: its count and its summary row, no Member."""
    return admin_report_reads.ministry_model(
        CAMPAIGN,
        ministry_query(history="all", state="unresolved"),
        {
            "summaries": [MINISTRY_ROW],
            "rows": [{"member_name": "Private Member"}],
            "total": 2,
        },
        ministry=9,
        requests="join",
    )


POPULATION = {
    "families": 10,
    "active_members": 20,
    "eligible_email": 9,
    "deliverable_email": 8,
    "responses": 3,
    "annual_pledge": "1234.56",
}
PARTICIPATION = {
    "campaign_id": str(CAMPAIGN),
    "scope": "historical",
    "status": "current",
    "selected_at": ISO,
    "fact_set_id": str(FACTS),
    "source_generation": 7,
    "source_as_of": ISO,
    "submission_watermark": 11,
    "first_date": "2054-10-01",
    "last_date": "2054-10-01",
    "financial_enabled": True,
    "statistics": {
        "observed_at": ISO,
        "source_generation": 7,
        "source_as_of": ISO,
        "submission_watermark": 11,
        "active": POPULATION,
        "comparison_pledge_all": "6543.21",
    },
    "days": [
        {
            "date": "2054-10-01",
            "first_responses": 2,
            "cumulative_responses": 3,
            "cohort_denominator": 10,
            "pledge_total": "1234.50",
        }
    ],
}
MINISTRY = {
    "ministry": 9,
    "name": "Choir",
    "active": True,
    "in_campaign": True,
    "joining": 2,
    "leaving": 1,
    "unresolved": 3,
    "requests": 3,
    "completed": 0,
}
GOLDEN = {
    "report list": (
        report_list,
        {
            "campaign_id": str(CAMPAIGN),
            "reports": [
                {
                    "command": "report participation",
                    "page": "participation",
                    "available": True,
                },
                {
                    "command": "report financial",
                    "page": "financial_report",
                    "available": False,
                },
            ],
        },
    ),
    "report participation": (participation, PARTICIPATION),
    "report participation (no financial)": (
        lambda: participation(financial=False),
        PARTICIPATION
        | {
            "financial_enabled": False,
            "statistics": PARTICIPATION["statistics"]
            | {
                "active": POPULATION | {"annual_pledge": None},
                "comparison_pledge_all": None,
            },
        },
    ),
    "report responses": (
        responses,
        {
            "campaign_id": str(CAMPAIGN),
            "mode": "production",
            "metrics": {
                "as_of": ISO,
                "timezone": "America/New_York",
                "grain": "hour",
                "stages": [{"key": "invited", "count": 5}],
                "skipped_responded": 1,
                "submitted_uninvited": 2,
                "submitted_again": 0,
                "activity": [{"start": ISO, "links": 1, "forms": 1, "submissions": 1}],
                "sends": [
                    {
                        "send": f"{SCHEDULE}:{REVISION}:production:1",
                        "kind": "initial",
                        "scheduled": ISO,
                        "delivered": 5,
                        "first_delivered_at": ISO,
                        "last_delivered_at": None,
                    }
                ],
                "lists": {
                    "submitted": 0,
                    "started": 0,
                    "not-opened": 0,
                    "more-than-once": 0,
                },
            },
        },
    ),
    "report responses (no rehearsal)": (
        no_rehearsal,
        {"campaign_id": str(CAMPAIGN), "mode": "testing", "metrics": None},
    ),
    "report financial": (
        financial,
        {
            "campaign_id": str(CAMPAIGN),
            "source_generation": 7,
            "source_as_of": ISO,
            "comparison_start": "2053-07-01",
            "comparison_end": "2054-06-30",
            "giving_through": "2054-09-30",
            "families": 4,
            "annual_total": "5200.00",
            "frequencies": {"monthly": 3, "none": 1},
            "shares": [{"id": SHARE, "count": 2}],
            "no_share": 2,
            "cannot_give": 1,
        },
    ),
    "report talents": (
        talents,
        {
            "campaign_id": str(CAMPAIGN),
            "collects_talents": True,
            "members": 3,
            "cannot_serve": 1,
            "cannot_attend": 1,
            "talents": [{"key": "music", "count": 2}, {"key": "retired", "count": 1}],
        },
    ),
    "report information": (
        information,
        {
            "campaign_id": str(CAMPAIGN),
            "filters": {
                "disposition": "current_actionable",
                "needed": "any",
                "completed": "any",
                "start": "",
                "end": "",
            },
            "source_as_of": ISO,
            "matching": 6,
        },
    ),
    "report ministry": (
        ministry_summary,
        {
            "campaign_id": str(CAMPAIGN),
            "ministry": None,
            "requests": None,
            "filters": {
                "activity": "any",
                "history": "current",
                "state": "any",
                "start": "",
                "end": "",
            },
            "page": 1,
            "size": 50,
            "matching": 1,
            "summaries": [MINISTRY],
        },
    ),
    "report ministry (requests)": (
        ministry_list,
        {
            "campaign_id": str(CAMPAIGN),
            "ministry": 9,
            "requests": "join",
            "filters": {
                "activity": "any",
                "history": "all",
                "state": "unresolved",
                "start": "",
                "end": "",
            },
            "page": 1,
            "size": 50,
            "matching": 2,
            "summaries": [MINISTRY],
        },
    ),
}


# Counts of Families whose email the campaign may use, named as the
# statistics name them: numbers, never an address.
COUNTS = {"eligible_email", "deliverable_email"}


def allowed(document):
    """A golden document's member names: exactly what the projection may print."""
    return set(members(document))


@pytest.mark.parametrize("command", sorted(GOLDEN))
def test_each_document_is_its_golden_projection(command):
    """The exact document, from the projection the command uses."""
    build, expected = GOLDEN[command]
    assert build().to_document() == expected


@pytest.mark.parametrize("command", sorted(GOLDEN))
def test_no_member_or_value_is_personal_data(command):
    """No member name matches the personal-data pattern; no private value leaks.

    The stand-ins carry a Family name, a Member name, private labels and a
    campaign name next to the summaries; none of them reaches a document.
    """
    document = GOLDEN[command][0]().to_document()
    for name in allowed(document) - COUNTS:
        assert FORBIDDEN.search(name) is None, (command, name)
    text = json.dumps(document)
    assert "@" not in text and "Private" not in text, command


def test_every_report_command_has_its_models_fields():
    """Each 8c command's catalog fields are its model's, in PR 8."""
    entries = {entry["name"]: entry for entry in admin_cli.catalog()}
    models = {
        "report list": admin_report_reads.ReportList,
        "report participation": admin_report_reads.ParticipationReport,
        "report responses": admin_report_reads.ResponseReport,
        "report financial": admin_report_reads.FinancialReport,
        "report talents": admin_report_reads.TalentsReport,
        "report information": admin_report_reads.InformationReport,
        "report ministry": admin_report_reads.MinistryReport,
    }
    reports = {spec.name for spec in admin_cli.COMMANDS if spec.name[:7] == "report "}
    assert reports == set(models)
    assert set(admin_report_reads.REPORT_COMMANDS.values()) == reports - {"report list"}
    for name, model in models.items():
        assert entries[name]["result_fields"] == list(model.field_names()), name
        assert entries[name]["pr"] == 8


def test_the_report_commands_name_menu_entries():
    """``report list`` reads the Admin menu's own entries for these pages."""
    from parishkit.stewardship.accounts.admin_navigation import MENU_NAMES

    assert set(admin_report_reads.REPORT_COMMANDS) <= MENU_NAMES


@pytest.mark.parametrize(
    "argv",
    [
        ["report", "ministry", "--ministry", "09"],
        ["report", "ministry", "--ministry", "nine"],
        ["report", "ministry", "--requests", "maybe"],
        ["report", "participation", "--campaign", str(CAMPAIGN)],
        ["report", "information", "--search", "Private"],
    ],
)
def test_options_outside_the_pages_vocabulary_are_usage_errors(argv):
    """No campaign or search option, and a Ministry is a canonical DUID."""
    import io

    out = io.StringIO()
    code = admin_cli.main(
        [*argv, "--config", "web.yaml", "--session-stdin"],
        stdin=io.BytesIO(b""),
        stdout=out,
        stderr=out,
    )
    assert code == 2
    assert json.loads(out.getvalue())["error"]["code"] == "usage"
