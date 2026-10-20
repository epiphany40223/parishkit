"""What each Family form showed: its storage (#1705, migration 0143).

The baseline records ``reviewed_view`` and ``shown_items`` and the submit
copies them, unchanged, to the immutable Submission. These tests run in a
Testing session, which is never drawn as the reviewed form (decision 3),
and force a reviewed baseline's fields to prove the storage and the copy;
``test_reviewed_form_postgresql`` covers what a live form records.
"""

import json
from contextlib import contextmanager
from pathlib import Path

import pytest
from django.db import DatabaseError, IntegrityError, connection, transaction

from parishkit.config import ConfigError
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.responses import baselines
from parishkit.stewardship.responses.models import FamilyFormBaseline, Submission
from parishkit.stewardship.runtime_grants import runtime_grants

from .test_response_http_postgresql import load_form, post
from .test_response_submission_postgresql import form_and_answers, submit
from .test_runtime_auth_grants_postgresql import web_login

pytestmark = pytest.mark.django_db(transaction=True)

FROZEN = (
    Path(__file__).resolve().parents[3]
    / "src/parishkit/stewardship/schema/migrations/0143_reviewed_form_shown_items.sql"
)
TABLES = ("stewardship_family_form_baseline", "stewardship_submission")
COLUMNS = ("reviewed_view", "shown_items")
# Entries in the shape ``responses.reviewed`` records (stream, identity,
# item, item version, decision row, which value), naming no real item, so
# the submit judges every answer as new; storage keeps any array.
SHOWN = [
    {
        "stream": "census",
        "entity": "member",
        "key": "3",
        "field": "first_name",
        "item": "00000000-0000-4000-8000-000000000001",
        "version": 2,
        "decision": None,
        "shown": "family",
    },
    {
        "stream": "financial",
        "item": "00000000-0000-4000-8000-000000000002",
        "version": 1,
        "decision": None,
        "shown": "family",
    },
]


def check_block():
    """The frozen file's closing DO block, run alone against the database."""
    text = FROZEN.read_text(encoding="utf-8")
    return text[text.index("DO $check$") :]


@contextmanager
def reviewed_baselines(monkeypatch, shown_items):
    """Issue baselines with these fields, as a reviewed form records them.

    A Testing session is never drawn as the reviewed form, so this replaces
    only the issue's choice of the two fields (``baselines._shown``);
    everything else about the baseline is the production path.
    """
    # A nested context undoes only this patch, not the test's other ones.
    with monkeypatch.context() as patch:
        patch.setattr(baselines, "_shown", lambda *args: (True, shown_items))
        yield


def test_frozen_file_installs_on_the_old_definition_and_its_check_flips():
    """On tables without the columns the check refuses; the file installs them."""
    with transaction.atomic(), connection.cursor() as cursor:
        for table in TABLES:
            for column in COLUMNS:
                cursor.execute(f"ALTER TABLE {table} DROP COLUMN {column}")
        with (
            pytest.raises(DatabaseError, match="reviewed_view is not installed"),
            transaction.atomic(),
        ):
            cursor.execute(check_block())
        cursor.execute(FROZEN.read_text(encoding="utf-8"))
        cursor.execute(check_block())
        transaction.set_rollback(True)
    with connection.cursor() as cursor:
        cursor.execute(check_block())


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (
            "ALTER TABLE stewardship_submission ALTER COLUMN reviewed_view "
            "SET DEFAULT true",
            "stewardship_submission.reviewed_view is not installed",
        ),
        (
            "ALTER TABLE stewardship_family_form_baseline ALTER COLUMN "
            "reviewed_view DROP NOT NULL",
            "stewardship_family_form_baseline.reviewed_view is not installed",
        ),
        (
            "ALTER TABLE stewardship_submission ALTER COLUMN shown_items "
            "SET DEFAULT '[]'::jsonb",
            "stewardship_submission.shown_items is not installed",
        ),
        (
            "ALTER TABLE stewardship_submission DROP CONSTRAINT submission_shown_shape",
            "submission_shown_shape is not installed",
        ),
        (
            "ALTER TABLE stewardship_family_form_baseline DROP CONSTRAINT "
            "family_baseline_shown_shape; ALTER TABLE "
            "stewardship_family_form_baseline ADD CONSTRAINT "
            "family_baseline_shown_shape CHECK (reviewed_view OR "
            "shown_items IS NULL)",
            "family_baseline_shown_shape is not installed",
        ),
        (
            "ALTER TABLE stewardship_submission DROP CONSTRAINT "
            "submission_shown_shape; ALTER TABLE stewardship_submission ADD "
            "CONSTRAINT submission_shown_shape CHECK (((NOT reviewed_view AND "
            "shown_items IS NULL) OR (reviewed_view AND shown_items IS NOT NULL "
            "AND jsonb_typeof(shown_items) = 'array'))) NOT VALID",
            "submission_shown_shape is not installed",
        ),
        (
            "ALTER TABLE stewardship_family_form_baseline DROP CONSTRAINT "
            "family_baseline_shown_shape; ALTER TABLE "
            "stewardship_family_form_baseline ALTER COLUMN shown_items TYPE json",
            "stewardship_family_form_baseline.shown_items is not installed",
        ),
    ],
)
def test_check_refuses_each_wrong_installation(change, message):
    """Each column's type, nullability and default and each check are verified."""
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(change)
        with pytest.raises(DatabaseError, match=message), transaction.atomic():
            cursor.execute(check_block())
        transaction.set_rollback(True)


def test_check_refuses_an_existing_reviewed_row(response_service, monkeypatch):
    """Additive: the file refuses to commit over an existing reviewed row."""
    with reviewed_baselines(monkeypatch, []):
        form_and_answers(response_service)
    with (
        pytest.raises(
            DatabaseError, match="must read as not drawn as the reviewed form"
        ),
        transaction.atomic(),
        connection.cursor() as cursor,
    ):
        cursor.execute(check_block())


def test_testing_form_and_response_record_nothing(response_service):
    """A Testing form is not drawn as the reviewed form, nor is its response."""
    form, answers = form_and_answers(response_service)
    assert (form.baseline.reviewed_view, form.baseline.shown_items) == (False, None)
    row = submit(response_service, form, answers).submission
    row.refresh_from_db()
    assert (row.reviewed_view, row.shown_items) == (False, None)


@pytest.mark.parametrize("restricted", [False, True])
@pytest.mark.parametrize("shown_items", [[], SHOWN])
def test_submit_copies_what_the_form_showed(
    response_service, monkeypatch, restricted, shown_items
):
    """The submit copies both fields unchanged, also under the web login."""
    from contextlib import nullcontext

    with web_login() if restricted else nullcontext():
        with reviewed_baselines(monkeypatch, shown_items):
            form, answers = form_and_answers(response_service)
        answers["members"]["3"]["first_name"] = "Updated"
        row = submit(response_service, form, answers).submission
    assert (row.reviewed_view, row.shown_items) == (True, shown_items)
    stored = Submission.objects.get(pk=row.pk)
    assert (stored.reviewed_view, stored.shown_items) == (True, shown_items)
    baseline = FamilyFormBaseline.objects.get(pk=form.baseline.pk)
    assert baseline.state == "submitted"
    assert (baseline.reviewed_view, baseline.shown_items) == (True, shown_items)


@pytest.mark.parametrize(
    ("reviewed_view", "shown_items"),
    [(True, None), (False, []), (True, {"stream": "census"}), (True, "[]")],
)
def test_baseline_refuses_unpaired_fields(
    response_service, monkeypatch, reviewed_view, shown_items
):
    """Off records no entries; on records a JSON array (possibly empty)."""
    monkeypatch.setattr(baselines, "_shown", lambda *args: (reviewed_view, shown_items))
    with pytest.raises(IntegrityError, match="family_baseline_shown_shape"):
        form_and_answers(response_service)


@pytest.mark.parametrize(
    "assignment",
    [
        "reviewed_view=true",
        "shown_items='[]'::jsonb",
        "reviewed_view=true, shown_items='{}'::jsonb",
        "reviewed_view=true, shown_items='null'::jsonb",
    ],
)
def test_submission_refuses_unpaired_fields(response_service, assignment):
    """The Submission carries the same pairing check as its baseline.

    Its guard refuses every UPDATE first, so the guard is switched off inside
    the rolled-back transaction to reach the check itself.
    """
    form, answers = form_and_answers(response_service)
    row = submit(response_service, form, answers).submission
    with (
        pytest.raises(IntegrityError, match="submission_shown_shape"),
        transaction.atomic(),
        connection.cursor() as cursor,
    ):
        cursor.execute(
            "ALTER TABLE stewardship_submission "
            "DISABLE TRIGGER stewardship_submission_guard"
        )
        cursor.execute(
            f"UPDATE stewardship_submission SET {assignment} WHERE id=%s", [row.pk]
        )


@pytest.mark.parametrize("reviewed", [False, True])
def test_shown_fields_are_fixed_once_the_baseline_is_issued(
    response_service, monkeypatch, reviewed
):
    """The baseline guard refuses rewriting what an issued form showed.

    Each UPDATE is an otherwise admissible transition (open to cancelled),
    which the control shows the guard admits, and each rewrite satisfies the
    pairing check, so only the guard can refuse it.
    """
    if reviewed:
        with reviewed_baselines(monkeypatch, []):
            form, _answers = form_and_answers(response_service)
        rewrites = (
            """, shown_items='[{"stream": "pledge"}]'::jsonb""",
            ", reviewed_view=false, shown_items=NULL",
        )
    else:
        form, _answers = form_and_answers(response_service)
        rewrites = (", reviewed_view=true, shown_items='[]'::jsonb",)
    transition = (
        "UPDATE stewardship_family_form_baseline SET state='cancelled', "
        "ended_at=clock_timestamp(), version=version+1{} WHERE id=%s"
    )
    for assignment in rewrites:
        with (
            pytest.raises(IntegrityError, match="Form metadata bindings are immutable"),
            work_transaction(),
            connection.cursor() as cursor,
        ):
            cursor.execute(transition.format(assignment), [form.baseline.pk])
    with work_transaction(), connection.cursor() as cursor:
        cursor.execute(transition.format(""), [form.baseline.pk])
        assert cursor.rowcount == 1
        transaction.set_rollback(True)


def test_web_login_reads_and_inserts_but_never_updates_the_columns():
    """The Family pages' login gets exactly what issue and submit need."""
    with web_login(), connection.cursor() as cursor:
        for table in TABLES:
            for column in COLUMNS:
                cursor.execute(
                    "SELECT has_column_privilege(current_user,%s,%s,'SELECT'), "
                    "has_column_privilege(current_user,%s,%s,'INSERT'), "
                    "has_column_privilege(current_user,%s,%s,'UPDATE')",
                    [f"public.{table}", column] * 3,
                )
                assert cursor.fetchone() == (True, True, False), (table, column)


def test_no_other_login_is_granted_the_columns():
    """Only the web login has table-level access; no column grant names them."""
    holders = set()
    for role in ServiceRole:
        try:
            tables, columns = runtime_grants(role)
        except ConfigError:
            continue
        for table in TABLES:
            if tables.get(table):
                holders.add((role, table, frozenset(tables[table])))
            for privilege, names in columns.get(table, {}).items():
                assert not set(COLUMNS) & set(names), (role, table, privilege)
    assert holders == {
        (
            ServiceRole.WEB,
            "stewardship_family_form_baseline",
            frozenset({"SELECT", "INSERT"}),
        ),
        (ServiceRole.WEB, "stewardship_submission", frozenset({"SELECT", "INSERT"})),
    }


def test_family_form_payload_and_page_carry_no_shown_storage(live_response_service):
    """The form payload and Family page are unchanged: no new key or word."""
    harness = live_response_service
    page = harness.client.get("/family/")
    assert page.status_code == 200
    form = load_form(harness)
    text = json.dumps(form) + page.content.decode()
    for word in ("reviewed_view", "shown_items", "reviewed view", "shown items"):
        assert word not in text
    response = post(harness.client, "/family/form", {})
    assert response.status_code == 200
    assert set(response.json()) == {"form"}
