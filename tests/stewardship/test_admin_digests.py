"""The saved digests' documents and the report-email commands' plumbing.

Pure tests: the golden documents ``report-email copy`` prints for a daily or
weekly digest saved before the conversion (#1786; once ``digest daily`` and
``digest weekly``, ADM-11 PR 8d), built through the projections the command
uses from stand-ins for the pages' reads, with the personal-data pattern
applied at every depth; the catalog entries; and Send now's confirmation.
The commands against a real database are in
database/test_admin_digest_cli_postgresql.py and
database/test_report_emails_page_postgresql.py.
"""

import io
import json
from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID

import pytest

from parishkit.stewardship import admin_cli, admin_digests
from parishkit.stewardship.reports.money import MoneyAmount

from .test_admin_reads import FORBIDDEN, members
from .test_admin_report_reads import COUNTS, POPULATION

SNAPSHOT = UUID("00000000-0000-4000-8000-000000000051")
CAMPAIGN = UUID("00000000-0000-4000-8000-000000000052")
FACTS = UUID("00000000-0000-4000-8000-000000000053")
ITEM = UUID("00000000-0000-4000-8000-000000000054")
NOW = datetime(2054, 10, 5, 14, 0, tzinfo=UTC)
ISO = "2054-10-05T14:00:00+00:00"


def daily():
    """A retained daily report's document, as ``retained_document`` gives it."""
    chart = SimpleNamespace(
        fact_set_id=FACTS,
        population_scope="historical",
        source_generation=7,
        source_as_of=NOW,
        submission_watermark=11,
        first_date=date(2054, 10, 1),
        last_date=date(2054, 10, 1),
        financial_enabled=True,
        parish_name="Private parish",
        days=(
            SimpleNamespace(
                local_date=date(2054, 10, 1),
                first_responses=2,
                cumulative_responses=3,
                cohort_denominator=10,
                population_available=True,
                pledge_available=True,
                pledge_total=Decimal("1234.50"),
            ),
        ),
    )
    statistics = SimpleNamespace(
        observed_at=NOW,
        source_generation=7,
        source_as_of=NOW,
        submission_watermark=11,
        financial_enabled=True,
        active=SimpleNamespace(
            families=10,
            active_members=20,
            eligible_email=9,
            deliverable_email=8,
            responses=3,
            annual_pledge=MoneyAmount(123456),
            comparison_pledge=MoneyAmount(None),
        ),
    )
    document = SimpleNamespace(
        participation=chart, statistics=statistics, comparison=SECTION
    )
    return admin_digests.daily_model(SNAPSHOT, CAMPAIGN, document, "production")


# A retained Compared with past campaigns section (#1018 slice 7); the
# command prints it without the charts' specifications.
SECTION = {
    "day": 1,
    "report_date": "2054-10-01",
    "compared_with": "2053",
    "tiles": [
        {
            "label": "Families responded",
            "value": "3",
            "lines": ["Last year on day 1: 2 (↑ 50% more)"],
        }
    ],
    "archives": [{"year": 2053, "checksum": "a" * 64}],
    "campaign": "Private campaign",
    "financial": True,
    "charts": [{"key": "comparison-families", "spec": {"description": "x"}}],
}
SECTION_FIGURES = {
    "day": 1,
    "report_date": "2054-10-01",
    "compared_with": "2053",
    "figures": [
        {
            "figure": "Families responded",
            "value": "3",
            "comparisons": ["Last year on day 1: 2 (↑ 50% more)"],
        }
    ],
    "archives": [{"year": 2053, "checksum": "a" * 64}],
}


def weekly():
    """A retained weekly report's page context with one changed item."""
    context = {
        "snapshot": SimpleNamespace(observed_at=NOW),
        "manual": True,
        "information_count": 1,
        "correction_count": 0,
        "total": 1,
        "page": 1,
        "pages": 1,
        "rows": [
            {
                "value": SimpleNamespace(item_id=ITEM, text="Private Family text"),
                "information": True,
                "captured": "Current actionable request",
                "current": "Superseded by a later response",
                "captured_key": "current_actionable",
                "current_key": "superseded",
                "changed": True,
                "text": "Private Family text",
            }
        ],
    }
    return admin_digests.weekly_model(SNAPSHOT, CAMPAIGN, context)


GOLDEN = {
    "report-email copy, daily": (
        daily,
        {
            "kind": "daily",
            "snapshot_id": str(SNAPSHOT),
            "campaign_id": str(CAMPAIGN),
            "mode": "production",
            "observed_at": ISO,
            "participation": {
                "fact_set_id": str(FACTS),
                "scope": "historical",
                "source_generation": 7,
                "source_as_of": ISO,
                "submission_watermark": 11,
                "first_date": "2054-10-01",
                "last_date": "2054-10-01",
                "financial_enabled": True,
            },
            "statistics": {
                "source_generation": 7,
                "source_as_of": ISO,
                "submission_watermark": 11,
                "active": POPULATION,
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
            "comparison": SECTION_FIGURES,
        },
    ),
    "report-email copy, weekly": (
        weekly,
        {
            "kind": "weekly",
            "snapshot_id": str(SNAPSHOT),
            "campaign_id": str(CAMPAIGN),
            "manual": True,
            "observed_at": ISO,
            "information_count": 1,
            "correction_count": 0,
            "total": 1,
            "page": 1,
            "pages": 1,
            "items": [
                {
                    "item_id": str(ITEM),
                    "information": True,
                    "captured": "current_actionable",
                    "current": "superseded",
                    "changed": True,
                }
            ],
        },
    ),
}


@pytest.mark.parametrize("command", sorted(GOLDEN))
def test_each_document_is_its_golden_projection(command):
    """The exact document, from the projection the command uses."""
    build, expected = GOLDEN[command]
    assert build().to_document() == expected


@pytest.mark.parametrize("command", sorted(GOLDEN))
def test_no_member_or_value_is_personal_data(command):
    """No member name is personal data; no Family text or parish name leaks."""
    document = GOLDEN[command][0]().to_document()
    for name in set(members(document)) - COUNTS:
        assert FORBIDDEN.search(name) is None, (command, name)
    text = json.dumps(document)
    assert "@" not in text and "Private" not in text, command


def test_every_report_email_command_has_its_models_fields():
    """Each report-email command's catalog fields are its model's (#1786)."""
    from parishkit.stewardship import admin_report_emails as models

    entries = {entry["name"]: entry for entry in admin_cli.catalog()}
    expected = {
        "report-email list": models.ReportEmailList,
        "report-email show": models.ReportEmailShow,
        "report-email sends": models.ReportEmailSends,
        "report-email copy": models.ReportEmailCopy,
        "report-email render": models.ReportEmailRender,
        "report-email send-now": models.ReportEmailSendNow,
        "report-email preview": models.ReportEmailPreview,
        "report-email confirm": models.ReportEmailConfirm,
    }
    names = {
        spec.name for spec in admin_cli.COMMANDS if spec.name[:13] == "report-email "
    }
    assert names == set(expected)
    # The digest commands are gone, with no aliases.
    assert not any(spec.name.startswith("digest ") for spec in admin_cli.COMMANDS)
    for name, model in expected.items():
        assert entries[name]["result_fields"] == list(model.field_names()), name
        assert entries[name]["pr"] == 8


def test_send_now_needs_the_confirmation(monkeypatch):
    """Without --yes or a typed yes nothing is sent: exit 4 (#1786)."""
    monkeypatch.setattr(
        "parishkit.stewardship.observability.configure_logging", lambda: None
    )

    monkeypatch.setattr(
        "parishkit.stewardship.deployment.load_deployment", lambda path: object()
    )
    from parishkit.stewardship import admin_report_emails

    called = []
    monkeypatch.setattr(
        admin_report_emails, "send_now", lambda *args, **kwargs: called.append(1)
    )
    lines = ("Send Weekly report now.", "It is sent now to its 2 recipients.")
    monkeypatch.setattr(
        admin_report_emails, "send_now_lines", lambda *a, **k: (lines, None)
    )

    class Runtime:
        """A stand-in admission that yields no runtime."""

        def __enter__(self):
            return None

        def __exit__(self, *error):
            return False

    monkeypatch.setattr(admin_cli, "ADMISSION", lambda configuration: Runtime())
    # The role check that comes before the prompt (#1341) admits.
    monkeypatch.setattr(
        "parishkit.stewardship.admin_changes._admit_change", lambda *a, **k: None
    )
    monkeypatch.setattr(
        admin_cli,
        "admit_session",
        lambda *args: SimpleNamespace(
            automation_session=None, portal_session=None, read_only=False
        ),
    )
    monkeypatch.setattr(
        "parishkit.stewardship.accounts.automation_sessions.close_command_session",
        lambda session: None,
    )
    out, err = io.StringIO(), io.StringIO()
    code = admin_cli.main(
        [
            "report-email",
            "send-now",
            str(SNAPSHOT),
            "--config",
            "web.yaml",
            "--session-stdin",
        ],
        stdin=io.BytesIO(f"pk-admin-session/1 {'A' * 43} {'f' * 64}\n".encode()),
        stdout=out,
        stderr=err,
    )
    assert code == 4 and not called
    assert json.loads(out.getvalue())["error"]["code"] == "confirmation_required"
    assert lines[1] in err.getvalue()
    assert "Type yes to continue" in err.getvalue()
    # No request key was made: nothing was about to be done.
    assert "request key" not in err.getvalue()


@pytest.mark.parametrize(
    "argv",
    [
        ["report-email", "copy", "not-a-uuid"],
        ["report-email", "copy"],
        ["report-email", "show"],
        ["report-email", "list", str(SNAPSHOT)],
        ["report-email", "send-now", "not-a-uuid"],
        ["digest", "list"],
    ],
)
def test_malformed_report_email_commands_are_usage_errors(argv):
    """Ids are canonical UUIDs; the retired digest commands are unknown."""
    out = io.StringIO()
    code = admin_cli.main(
        [*argv, "--config", "web.yaml", "--session-stdin"],
        stdin=io.BytesIO(b""),
        stdout=out,
        stderr=out,
    )
    assert code == 2 and json.loads(out.getvalue())["error"]["code"] == "usage"
