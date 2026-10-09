"""The digest commands' documents and plumbing (ADM-11 PR 8d).

Pure tests: the golden documents of ``digest daily``, ``digest weekly`` and
``digest weekly-request``, built through the projections the commands use
from stand-ins for the pages' reads, with the personal-data pattern applied
at every depth; the catalog entries; and the manual request's
acknowledgement. The commands against a real database are in
database/test_admin_digest_cli_postgresql.py.
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
TASK = UUID("00000000-0000-4000-8000-000000000055")
KEY = UUID("00000000-0000-4000-8000-000000000056")
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
    document = SimpleNamespace(participation=chart, statistics=statistics)
    return admin_digests.daily_model(SNAPSHOT, CAMPAIGN, document, "production")


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


def request():
    """A created manual weekly report."""
    return admin_digests.WeeklyRequest(
        created=True,
        request_key=KEY,
        task={
            "id": str(TASK),
            "root_id": str(TASK),
            "parent_id": None,
            "retry_sequence": 0,
            "type": "weekly_digest_prepare",
            "state": "queued",
        },
    )


GOLDEN = {
    "digest daily": (
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
        },
    ),
    "digest weekly": (
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
    "digest weekly-request": (
        request,
        {
            "created": True,
            "request_key": str(KEY),
            "task": {
                "id": str(TASK),
                "root_id": str(TASK),
                "parent_id": None,
                "retry_sequence": 0,
                "type": "weekly_digest_prepare",
                "state": "queued",
            },
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


def test_every_digest_command_has_its_models_fields():
    """Each 8d command's catalog fields are its model's, in PR 8."""
    entries = {entry["name"]: entry for entry in admin_cli.catalog()}
    models = {
        "digest daily": admin_digests.DailyDigest,
        "digest weekly": admin_digests.WeeklyDigest,
        "digest weekly-request": admin_digests.WeeklyRequest,
    }
    digests = {spec.name for spec in admin_cli.COMMANDS if spec.name[:7] == "digest "}
    assert digests == set(models)
    for name, model in models.items():
        assert entries[name]["result_fields"] == list(model.field_names()), name
        assert entries[name]["pr"] == 8


def test_the_manual_request_needs_the_acknowledgement(monkeypatch):
    """Without --yes or a typed yes nothing is requested: exit 4."""
    monkeypatch.setattr(
        "parishkit.stewardship.observability.configure_logging", lambda: None
    )

    monkeypatch.setattr(
        "parishkit.stewardship.deployment.load_deployment", lambda path: object()
    )
    called = []
    monkeypatch.setattr(
        admin_digests, "request_weekly", lambda *args, **kwargs: called.append(1)
    )

    class Runtime:
        """A stand-in admission that yields no runtime."""

        def __enter__(self):
            return None

        def __exit__(self, *error):
            return False

    monkeypatch.setattr(admin_cli, "ADMISSION", lambda configuration: Runtime())
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
        ["digest", "weekly-request", "--config", "web.yaml", "--session-stdin"],
        stdin=io.BytesIO(f"pk-admin-session/1 {'A' * 43} {'f' * 64}\n".encode()),
        stdout=out,
        stderr=err,
    )
    assert code == 4 and not called
    assert json.loads(out.getvalue())["error"]["code"] == "confirmation_required"
    assert admin_digests.acknowledgement() in err.getvalue()
    assert "Type yes to continue" in err.getvalue()
    # No request key was made: nothing was about to be done.
    assert "request key" not in err.getvalue()


@pytest.mark.parametrize(
    "argv",
    [
        ["digest", "daily", "not-a-uuid"],
        ["digest", "daily", str(SNAPSHOT), "--page", "2"],
        ["digest", "weekly"],
    ],
)
def test_malformed_digest_commands_are_usage_errors(argv):
    """A snapshot is a canonical UUID; only the weekly report has pages."""
    out = io.StringIO()
    code = admin_cli.main(
        [*argv, "--config", "web.yaml", "--session-stdin"],
        stdin=io.BytesIO(b""),
        stdout=out,
        stderr=out,
    )
    assert code == 2 and json.loads(out.getvalue())["error"]["code"] == "usage"
