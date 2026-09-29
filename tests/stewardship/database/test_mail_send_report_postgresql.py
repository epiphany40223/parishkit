"""The documented mail send report runs over real outcome statistics (#284).

The SQL is read straight from docs/guides/stewardship-mail-send-report.md
and run as psql would run it, so the guide cannot drift from what works.
"""

import re
from pathlib import Path
from uuid import uuid4

import pytest
from django.db import connection, transaction
from django.utils import timezone

from parishkit.stewardship.campaigns.schedule_models import ScheduleDefinition
from parishkit.stewardship.family_delivery import FamilyDeliveryResult
from parishkit.stewardship.family_delivery import FamilyDeliveryStatus as Status

from ..family_mail_session_fakes import LIMIT
from .campaign_builders import campaign_clock
from .test_family_mail_dispatch_postgresql import prepare
from .test_family_mail_preparation_postgresql import family_mail  # noqa: F401
from .test_family_mail_session_postgresql import (  # noqa: F401
    batch,
    fast_limits,
    task,
    wait_past,
)
from .test_family_mail_worker_postgresql import (  # noqa: F401
    deliver,
    dispatch_worker,
)
from .test_mail_send_stats_postgresql import (  # noqa: F401
    fresh_admission,
    per_message_owner,
)

pytestmark = pytest.mark.django_db(transaction=True)
REPORT = (
    Path(__file__).resolve().parents[3]
    / "docs"
    / "guides"
    / "stewardship-mail-send-report.md"
)


def report_statements():
    """The report's SQL statements, exactly as the guide documents them."""
    text = REPORT.read_text()
    sql = text[text.index("```sql\n") + len("```sql\n") :]
    sql = sql[: sql.index("```")]
    statements = [part.strip() for part in re.split(r";\s*\n", sql) if part.strip()]
    return [
        statement
        for statement in statements
        if not all(line.startswith("--") for line in statement.splitlines())
    ]


def run_report(since, until, purpose="", definition=""):
    """Run the documented report as psql would, returning each result set."""
    values = {
        "since": since.isoformat(),
        "until": until.isoformat(),
        "purpose": purpose,
        "definition": definition,
    }
    results = []
    statements = report_statements()
    assert statements[0] == "BEGIN" and statements[-1] == "ROLLBACK"
    with transaction.atomic():
        with connection.cursor() as cursor:
            for statement in statements[1:-1]:
                for name, value in values.items():
                    statement = statement.replace(
                        f":'{name}'", "'" + value.replace("'", "''") + "'"
                    )
                cursor.execute(statement)
                if cursor.description:
                    columns = [column.name for column in cursor.description]
                    rows = cursor.fetchall()
                    results.append(
                        [dict(zip(columns, row, strict=True)) for row in rows]
                    )
        transaction.set_rollback(True)
    return results


def test_the_documented_report_summarizes_both_transports(
    batch,  # noqa: F811
    monkeypatch,
):
    """A limit refusal on the batched helper, then the fallback delivers it."""
    fast_limits(monkeypatch)
    gmail = batch.gmail
    begun = timezone.now()
    with campaign_clock(ScheduleDefinition.objects.get().current_revision.due_at):
        message = prepare(batch.harness)
        gmail.script(("mail", LIMIT))
        deliver(batch.harness, batch.path, message, batch.owner)
        circuit = batch.owner.admit.keywords["circuit"]
        wait_past(task(message).not_before, circuit)
        fallback = per_message_owner(
            batch.harness,
            batch.path,
            monkeypatch,
            [
                FamilyDeliveryResult(
                    Status.ACCEPTED,
                    1,
                    stats={"smtp_ms": 25, "conn_reused": False, "conn_seq": 1},
                )
            ],
        )
        deliver(batch.harness, batch.path, message, fallback)
    ended = timezone.now()
    (
        overview,
        reasons,
        buckets,
        phases,
        use,
        lifetimes,
        ends,
        limits,
        holds,
        saved,
    ) = run_report(begun, ended)
    assert overview[0]["outcomes"] == 2 and overview[0]["messages"] == 1
    assert overview[0]["accepted"] == 1 and overview[0]["without_stats"] == 0
    assert overview[0]["wall_clock"].total_seconds() > 0
    assert {(row["transport"], row["reason"]) for row in reasons} == {
        ("batched", "smtp_transient"),
        ("per_message", "smtp_accepted"),
    }
    assert sum(row["outcomes"] for row in buckets) == 2
    assert {"total_ms", "smtp_ms"} <= {row["phase"] for row in phases}
    assert {row["transport"] for row in use} == {"batched", "per_message"}
    assert {row["what"] for row in lifetimes} == {"connection", "helper"}
    assert ("batched", "helper_end", "limit") in {
        (row["transport"], row["kind"], row["reason"]) for row in ends
    }
    assert limits[0]["limit_refusals"] == 1 and limits[0]["daily"] == 1
    assert holds[0]["limit_kind"] == "daily" and holds[0]["retried"] == 1
    assert {row["transport"] for row in saved} == {"batched", "per_message"}
    # A window with no mail reports zeros, and the purpose filter applies.
    assert run_report(ended, ended)[0][0]["outcomes"] == 0
    assert run_report(begun, ended, purpose="receipt")[0][0]["outcomes"] == 0
    assert run_report(begun, ended, definition=str(uuid4()))[0][0]["outcomes"] == 0


def test_setup_phases_count_only_real_setups(batch):  # noqa: F811
    """A message on a warm helper and connection adds no setup samples.

    Otherwise reused connections would add zeros and the setup percentiles
    would read 0 ms, which says nothing about what a setup costs.
    """
    from .test_family_mail_session_postgresql import other

    begun = timezone.now()
    with campaign_clock(ScheduleDefinition.objects.get().current_revision.due_at):
        # Another Family's message warms the helper and its connection.
        other(batch)
        message = prepare(batch.harness)
        deliver(batch.harness, batch.path, message, batch.owner)
    ended = timezone.now()
    results = run_report(begun, ended)
    phases, use, saved = results[3], results[4], results[9]
    measured = {row["phase"]: row["n"] for row in phases}
    assert measured["smtp_ms"] == 1 and measured["total_ms"] == 1
    assert not {"spawn_ms", "token_ms", "connect_ms", "auth_ms"} & set(measured)
    assert use[0]["connection_reuse_ratio"] == 1 and use[0]["connections"] == 1
    assert saved[0]["connections_opened"] == 0
    assert saved[0]["connect_auth_s"] == 0 and saved[0]["token_s"] == 0
