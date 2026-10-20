"""Back to review undoes a parish-added change's Entered by hand (#1840).

An entered addition is reopened, with a required note, back to To enter by
hand (review pipeline spec, "Undoing a terminal stage"); then it may be
entered again or withdrawn, and every step is kept. The frozen file 0211
lets the resolution table hold more than one step per addition and checks
which step may follow which; its guards are tested here with the service
bypassed too.
"""

from pathlib import Path
from uuid import uuid4

import pytest
from django.db import DatabaseError, IntegrityError, connection, transaction

from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.reports.census_changes import addition_rows
from parishkit.stewardship.responses.census_additions import (
    resolve_census_additions,
)
from parishkit.stewardship.responses.census_review import BulkStale
from parishkit.stewardship.responses.models import (
    CensusAdditionResolution,
    open_additions,
)

from .test_background_grants_postgresql import task_login
from .test_census_additions_postgresql import (
    AddRefusal,
    add,
    campaign_of,
    forge,
    member_form,
)
from .test_census_pipeline_review_postgresql import prepared
from .test_census_resolution_postgresql import leader

pytestmark = pytest.mark.django_db(transaction=True)

MIGRATIONS = Path(__file__).resolve().parents[3] / (
    "src/parishkit/stewardship/schema/migrations"
)
FROZEN = MIGRATIONS / "0211_census_addition_undo.sql"
OLD = MIGRATIONS / "0129_census_additions.sql"


def step(harness, actor, campaign, addition, version, action, *, note="", key=None):
    """One addition step through the service on the restricted web login."""
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        (recorded,) = resolve_census_additions(
            harness.service.store,
            actor,
            campaign,
            {addition.pk: version},
            action,
            note=note,
            request_key=key or uuid4(),
        )
    return recorded


def entered(response_service):
    """A Staff member's addition, Entered by hand; (harness, admin, staff,
    rows, campaign, addition)."""
    harness, admin, staff, rows = prepared(response_service)
    campaign = campaign_of(rows)
    addition, _ = add(harness, staff, campaign, member_form(rows, "nickname", "Bo"))
    step(harness, staff, campaign, addition, 1, "entered")
    return harness, admin, staff, rows, campaign, addition


def row_of(campaign, addition):
    """The addition's worklist row, as the page shapes it."""
    return next(
        row for row in addition_rows(campaign) if row["id"].endswith(str(addition.pk))
    )


def test_back_to_review_reopens_an_entered_addition_keeping_history(
    response_service,
):
    """Back to review undoes the mark: To enter by hand again, Added by the
    parish, at the next version; it can be entered again, and every step
    stays in history with its note."""
    harness, _, staff, rows, campaign, addition = entered(response_service)
    assert row_of(campaign, addition)["stage"] == "entered_by_hand"
    key = uuid4()
    reopened = step(
        harness, staff, campaign, addition, 2, "back_to_review", note="Typo", key=key
    )
    assert (reopened.action, reopened.sequence) == ("reopened", 2)
    assert reopened.note == "Typo"
    # A replay records nothing more.
    again = step(
        harness, staff, campaign, addition, 2, "back_to_review", note="Typo", key=key
    )
    assert again.pk == reopened.pk
    shown = row_of(campaign, addition)
    assert (shown["stage"], shown["version"]) == ("to_enter_by_hand", 3)
    assert shown["placement"].decision.value == "added"
    assert open_additions().filter(pk=addition.pk).exists()
    # Open again, it still stands for its field: a second Add is refused.
    with pytest.raises(AddRefusal):
        add(harness, staff, campaign, member_form(rows, "nickname", "Bob"))
    step(harness, staff, campaign, addition, 3, "entered")
    assert row_of(campaign, addition)["stage"] == "entered_by_hand"
    assert list(
        CensusAdditionResolution.objects.filter(addition=addition)
        .order_by("sequence")
        .values_list("action", flat=True)
    ) == ["entered", "reopened", "entered"]


def test_a_reopened_addition_can_be_withdrawn(response_service):
    """After the undo, Back to review withdraws it, into History, and the
    field is free for a new Add."""
    harness, _, staff, rows, campaign, addition = entered(response_service)
    step(harness, staff, campaign, addition, 2, "back_to_review", note="Wrong")
    withdrawn = step(harness, staff, campaign, addition, 3, "back_to_review")
    assert withdrawn.action == "withdrawn"
    assert row_of(campaign, addition)["stage"] == "history"
    with pytest.raises(BulkStale):
        step(harness, staff, campaign, addition, 4, "back_to_review", note="x")
    add(harness, staff, campaign, member_form(rows, "nickname", "Bob"))


def test_the_undo_needs_a_note_and_the_current_version(response_service):
    """Without a note the undo is refused; at the version before the mark
    it is stale; nothing is recorded."""
    harness, _, staff, _, campaign, addition = entered(response_service)
    with pytest.raises(ValueError, match="needs a note"):
        step(harness, staff, campaign, addition, 2, "back_to_review", note="  ")
    with pytest.raises(BulkStale):
        step(harness, staff, campaign, addition, 1, "back_to_review", note="Typo")
    assert CensusAdditionResolution.objects.filter(addition=addition).count() == 1


RESOLUTION = (
    "INSERT INTO stewardship_census_addition_resolution (id,correlation_id,"
    "actor_id,request_key,addition_id,action,note,sequence) VALUES ("
    "gen_random_uuid(),gen_random_uuid(),%s,gen_random_uuid(),%s,%s,%s,%s)"
)


@pytest.mark.parametrize(
    ("who", "action", "note", "sequence", "message"),
    [
        # A Ministry leader never undoes a mark.
        ("leader", "reopened", "Typo", 2, "current authority"),
        # The note is required in the database too.
        ("staff", "reopened", " ", 2, "does not follow its latest step"),
        # An entered addition is reopened, never withdrawn or entered again.
        ("staff", "withdrawn", "", 2, "does not follow its latest step"),
        ("staff", "entered", "", 2, "does not follow its latest step"),
        # Only at the next sequence.
        ("staff", "reopened", "Typo", 3, "does not follow its latest step"),
        ("staff", "reopened", "Typo", 1, "does not follow its latest step"),
        # Admitted by the guard, then held to its audit event.
        ("staff", "reopened", "Typo", 2, "requires its audit"),
    ],
)
def test_sql_holds_the_undo_to_its_rules(
    response_service, who, action, note, sequence, message
):
    """The 0211 guard admits the undo only from a current Staff member or
    Administrator, with a note, as the next step after Entered by hand."""
    harness, admin, staff, _, _, addition = entered(response_service)
    actor = leader(harness, admin) if who == "leader" else staff
    with pytest.raises((IntegrityError, DatabaseError), match=message):
        forge(RESOLUTION, [actor, addition.pk, action, note, sequence])
    assert CensusAdditionResolution.objects.filter(addition=addition).count() == 1


def test_sql_refuses_a_reopen_of_an_open_addition(response_service):
    """Reopened follows only Entered by hand: an open addition is entered or
    withdrawn."""
    harness, _, staff, rows = prepared(response_service)
    addition, _ = add(
        harness, staff, campaign_of(rows), member_form(rows, "nickname", "Bo")
    )
    with pytest.raises(
        (IntegrityError, DatabaseError), match="does not follow its latest step"
    ):
        forge(RESOLUTION, [staff, addition.pk, "reopened", "Typo", 1])


def old_check():
    """0129's resolution identity check and unique addition, as statements."""
    text = OLD.read_text(encoding="utf-8")
    start = text.index('CONSTRAINT "census_addition_resolution_identity" CHECK')
    # The CREATE TABLE statement closes right after the check.
    check = text[start : text.index(";\n", start) - 1]
    return [
        "ALTER TABLE stewardship_census_addition_resolution "
        "DROP CONSTRAINT census_addition_resolution_identity",
        f"ALTER TABLE stewardship_census_addition_resolution ADD {check}",
        "ALTER TABLE stewardship_census_addition_resolution ADD UNIQUE (addition_id)",
    ]


def test_the_old_definition_refused_the_undo(response_service):
    """Before 0211, one step per addition and no 'reopened': the undo was
    refused. Put back in a rolled-back transaction."""
    _, _, staff, _, _, addition = entered(response_service)
    with (
        pytest.raises(IntegrityError, match="census_addition_resolution"),
        transaction.atomic(),
        connection.cursor() as cursor,
    ):
        for statement in old_check():
            cursor.execute(statement)
        cursor.execute(RESOLUTION, [staff, addition.pk, "reopened", "Typo", 2])


def test_do_block_passes_installed_and_refuses_an_old_body():
    """0211's closing check passes as installed, and raises once the
    resolution guard is 0129's again."""
    text = FROZEN.read_text(encoding="utf-8")
    check = text[text.index("DO $check$") :]
    old = OLD.read_text(encoding="utf-8")
    start = old.index(
        "CREATE FUNCTION public.stewardship_census_addition_resolution_guard_v1()"
    )
    body = old[start : old.index("END $$;", start) + len("END $$;")]
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(check)
    with (
        pytest.raises(DatabaseError, match="did not replace"),
        transaction.atomic(),
        connection.cursor() as cursor,
    ):
        cursor.execute(body.replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION"))
        cursor.execute(check)
