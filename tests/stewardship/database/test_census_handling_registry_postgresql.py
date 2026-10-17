"""The census handling registry from the live ParishSoft write test (#1596).

Migration 0113 (``schema/migrations/0113_census_handling_registry.sql``)
replaces the derived guard's expected handling: a death date, any answer that
removes a value and every Family field are ``manual``, because the v2 API
leaves a Member Active when given a death date, cannot clear a value and
cannot read Family addresses back. It also re-marks open changes recorded
before it. These tests run the real response path and SQL guard, Staff's
worklist, the file's one-time correction against rows in the old state, and
its DO block against the body it replaces.
"""

from pathlib import Path

import pytest
from django.db import DatabaseError, connection, transaction

from parishkit.stewardship.audit.models import AuditContext, AuditEvent
from parishkit.stewardship.responses.models import ProposedChange

from .response_builders import activate_response_service
from .test_census_changes_postgresql import STAFF, submit_live, worklist
from .test_member_requests_postgresql import send
from .test_ministry_responses_postgresql import respond, revisit, start
from .test_publication_plans_postgresql import owner
from .test_publication_worker_postgresql import _replaced
from .test_response_http_postgresql import answers_for, load_form
from .test_runtime_auth_grants_postgresql import web_login

pytestmark = pytest.mark.django_db(transaction=True)

MIGRATIONS = Path(__file__).resolve().parents[3] / "src/parishkit/stewardship/schema"
FROZEN = MIGRATIONS / "migrations/0113_census_handling_registry.sql"
# Member fields a Family may blank; one of them holds a value in the fixture.
CLEARABLE = (
    "middle_name",
    "nickname",
    "maiden_name",
    "email",
    "home_phone",
    "mobile_phone",
    "work_phone",
)


def correction():
    """The file's one-time correction: from disabling the guard to enabling it."""
    text = FROZEN.read_text(encoding="utf-8")
    start_at = text.index("ALTER TABLE public.stewardship_proposed_change DISABLE")
    end = text.index("DO $check$")
    return text[start_at:end]


def check():
    """The file's closing DO block."""
    text = FROZEN.read_text(encoding="utf-8")
    return text[text.index("DO $check$") :]


def corrected_events():
    """The census_handling_corrected events' counts, oldest first."""
    events = AuditEvent.objects.filter(event_type="census_handling_corrected")
    return [
        AuditContext.objects.get(event_id=event.pk).context
        for event in events.order_by("created_at")
    ]


def test_new_proposals_follow_the_corrected_registry(response_service):
    """A Family address is By hand and a renamed Member stays Automatic; an
    answer that blanks a recorded value is By hand and shows in Staff's
    default worklist as To do."""
    harness = response_service
    start(harness)
    harness = activate_response_service(harness)
    submit_live(harness)
    rows = {row.field: row for row in ProposedChange.objects.all()}
    assert rows["home_address"].handling == "manual"
    assert rows["first_name"].handling == "api"
    with web_login():
        form = revisit(harness)
        answers = answers_for(form)
        member = answers["members"]["3"]
        field = next(name for name in CLEARABLE if member.get(name))
        member[field] = ""
        respond(harness, form, answers)
    cleared = ProposedChange.objects.get(field=field, execution="pending")
    assert cleared.handling == "manual" and cleared.submitted_value in ("", None)
    staff = {row["label"] for row in worklist(harness, STAFF)["rows"]}
    shown = [
        row
        for row in worklist(harness, STAFF, status="all", route="by_hand")["rows"]
        if row["who"] != "Family"
    ]
    assert len(shown) == 1 and shown[0]["route_label"] == "By hand"
    assert shown[0]["status"] == "to_do" and shown[0]["label"] in staff


def test_a_death_date_is_by_hand_with_the_deceased_status(live_response_service):
    """Writing dateOfDeath leaves the Member Active, so the date is By hand."""
    harness = live_response_service
    with web_login():
        form = load_form(harness)
        answers = answers_for(form)
        answers["members"]["3"] = {
            "deceased_status": True,
            "confirmed": True,
            "death_date": "2026-01-01",
        }
        send(harness, form, answers)
    handling = dict(
        ProposedChange.objects.filter(
            field__in=("death_date", "deceased_status")
        ).values_list("field", "handling")
    )
    assert handling == {"death_date": "manual", "deceased_status": "manual"}


def test_the_correction_re_marks_only_open_rows(response_service):
    """Rows stored as Automatic before 0113 are re-marked By hand at the next
    version when still open; resolved rows and writable changes are left as
    they are, and one event records only the count."""
    harness = response_service
    start(harness)
    harness = activate_response_service(harness)
    submit_live(harness)
    home = ProposedChange.objects.get(field="home_address")
    name = ProposedChange.objects.get(field="first_name")
    # The pre-0113 state: the address stored as Automatic and ignored.
    owner(
        "UPDATE stewardship_proposed_change SET handling='api',"
        " decision='ignored' WHERE id=%s",
        [home.pk],
    )
    # A resolved row of an affected kind, and a resolved clearing row.
    owner(
        "UPDATE stewardship_proposed_change SET field='death_date',"
        " handling='api', execution='resolved_external' WHERE id=%s",
        [name.pk],
    )
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(correction())
    home_after = ProposedChange.objects.get(pk=home.pk)
    assert (home_after.handling, home_after.version) == ("manual", home.version + 1)
    # The decision is kept: an ignored change stays ignored.
    assert home_after.decision == "ignored" and home_after.execution == "pending"
    resolved = ProposedChange.objects.get(pk=name.pk)
    assert (resolved.handling, resolved.version) == ("api", name.version)
    assert corrected_events() == [{"outcome": "changed", "count": 1}]
    event = AuditEvent.objects.get(event_type="census_handling_corrected")
    assert event.actor_id is None and event.subject_id is None
    # The guard is enabled again, so the check passes; a second run changes
    # nothing and records nothing.
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(check())
        cursor.execute(correction())
    assert len(corrected_events()) == 1


def test_the_correction_keeps_writable_open_changes_automatic(response_service):
    """An open Automatic rename is not touched, nor audited."""
    harness = response_service
    start(harness)
    harness = activate_response_service(harness)
    submit_live(harness)
    name = ProposedChange.objects.get(field="first_name")
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(correction())
    after = ProposedChange.objects.get(pk=name.pk)
    assert (after.handling, after.version) == ("api", name.version)
    assert corrected_events() == []


def test_do_block_refuses_the_old_definition_and_leftover_rows(response_service):
    """The closing check passes as installed, raises once the derived guard is
    put back to 0112's body or its trigger is left disabled, and raises while
    an open row is still marked Automatic for a change publication cannot
    carry."""
    harness = response_service
    start(harness)
    harness = activate_response_service(harness)
    submit_live(harness)
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(check())
    old = _replaced(
        (MIGRATIONS / "migrations/0112_upstream_at_check.sql").read_text(
            encoding="utf-8"
        ),
        "stewardship_response_derived_guard_v1",
    )
    for statement in (
        old,
        "ALTER TABLE public.stewardship_proposed_change"
        " DISABLE TRIGGER stewardship_proposed_change_guard",
    ):
        with (
            pytest.raises(DatabaseError, match="lacks the corrected census handling"),
            transaction.atomic(),
            connection.cursor() as cursor,
        ):
            cursor.execute(statement)
            cursor.execute(check())
    home = ProposedChange.objects.get(field="home_address")
    owner(
        "UPDATE stewardship_proposed_change SET handling='api' WHERE id=%s",
        [home.pk],
    )
    with (
        pytest.raises(DatabaseError, match="still marked Automatic"),
        transaction.atomic(),
        connection.cursor() as cursor,
    ):
        cursor.execute(check())


def test_the_old_guard_refused_the_corrected_handling(response_service):
    """Under 0112's guard a Family address had to be stored as Automatic, so
    the corrected registry needs this migration: the response is refused."""
    harness = response_service
    start(harness)
    harness = activate_response_service(harness)
    old = _replaced(
        (MIGRATIONS / "migrations/0112_upstream_at_check.sql").read_text(
            encoding="utf-8"
        ),
        "stewardship_response_derived_guard_v1",
    )
    current = _replaced(
        FROZEN.read_text(encoding="utf-8"), "stewardship_response_derived_guard_v1"
    )
    with connection.cursor() as cursor:
        cursor.execute(old)
    try:
        with pytest.raises(Exception, match="Proposal differs from its immutable"):
            submit_live(harness)
    finally:
        with connection.cursor() as cursor:
            cursor.execute(current)
    assert not ProposedChange.objects.exists()
