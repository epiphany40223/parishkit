"""Archive waits until every email of the campaign is settled (#2305).

The delivery resolution guard refuses every action on an archived
campaign's email, so a message still Not sure it arrived (or still on its
way, which may yet end so) when the campaign is archived could never be
settled and would hold a recorded mail halt for good. Frozen file 0201
replaces the campaign transition guard so that archive is refused while any
outbox message of the campaign, of any purpose and mode, is pending,
retry_wait, submitting or delivery_unknown; the closed-and-quiet and
Production receipt checks are unchanged and still come first.

A real receipt is allocated by the final Submit and delivered through the
mail role (``test_receipt_dispatch_postgresql``); the unsettled message is a
copy of it seeded as another purpose with the SQL guards paused
(``unguarded``), since only the archive guard's reading of its state is
under test here.
"""

import re
from pathlib import Path
from uuid import uuid4

import pytest
from django.db import DatabaseError, IntegrityError, connection, transaction

from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.family_delivery import FamilyDeliveryResult
from parishkit.stewardship.family_delivery import FamilyDeliveryStatus as Status
from parishkit.stewardship.jobs.family_mail_dispatch import finish_submission

from .auth_builders import unguarded
from .campaign_builders import close_campaign
from .test_background_grants_postgresql import task_login
from .test_family_mail_dispatch_postgresql import claim
from .test_receipt_dispatch_postgresql import begin, receipt

pytestmark = pytest.mark.django_db(transaction=True)

SCHEMA = Path(__file__).resolve().parents[3] / "src/parishkit/stewardship/schema"
FROZEN = SCHEMA / "migrations/0201_archive_settles_mail.sql"
GUARD = "stewardship_campaign_transition_v1"
UNSETTLED = ("pending", "retry_wait", "submitting", "delivery_unknown")
REFUSAL = "Archive requires settled campaign email"
FAMILY_PURPOSES = ("initial", "reminder", "receipt", "family_test")


def delivered_receipt(harness):
    """A Production receipt the mail role delivered, so the receipt check passes."""
    message = receipt(harness, production=True)
    with task_login(ServiceRole.MAIL_DISPATCH, exact=True):
        execution = claim(message)
        assert begin(message, execution) is not None
        finish_submission(
            message.pk, execution.claim, FamilyDeliveryResult(Status.ACCEPTED, 1)
        )
    message.refresh_from_db()
    assert message.state == "delivered"
    return message


def seed_unsettled(
    message, state, *, purpose="reminder", mode="production", campaign=True
):
    """Copy ``message`` as another email in ``state``; its id.

    The copy keeps the campaign (unless ``campaign`` is false: a campaign-less
    email such as an operational notice) and takes its own id, task and
    semantic key, with the guards paused: only its state, purpose and mode
    matter here.
    """
    copy = uuid4()
    values = {
        "id": copy,
        "task_id": uuid4(),
        "semantic_key": uuid4(),
        "purpose": purpose,
        "mode": mode,
        "routing": "production" if mode == "production" else "testing_override",
        "state": state,
        "attempt": 1,
        "finished_at": None,
    }
    if not campaign:
        # An operational notice belongs to no campaign or Family
        # (outbox_routing) and is always routed as operational.
        values.update(campaign_id=None, family_id=None, routing="operational")
    elif purpose not in FAMILY_PURPOSES:
        # A report or digest email is Family-less (outbox_family_purpose).
        values["family_id"] = None
    with unguarded(), connection.cursor() as cursor:
        cursor.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name='stewardship_outbox_message' ORDER BY ordinal_position"
        )
        columns = [name for (name,) in cursor.fetchall()]
        chosen = [name for name in columns if name in values]
        cursor.execute(
            f"INSERT INTO stewardship_outbox_message ({','.join(columns)}) "
            f"SELECT {','.join('%s' if name in values else name for name in columns)} "
            "FROM stewardship_outbox_message WHERE id=%s",
            [*(values[name] for name in chosen), message.pk],
        )
        assert cursor.rowcount == 1
    return copy


def settle(message_id, state):
    """Move a seeded email to a terminal ``state``, as its settling would."""
    with unguarded(), connection.cursor() as cursor:
        cursor.execute(
            "UPDATE stewardship_outbox_message SET state=%s,finished_at=now() "
            "WHERE id=%s",
            [state, message_id],
        )


def archive(campaign):
    """Archive through the real transition storage."""
    from parishkit.stewardship.campaigns.lifecycle import Action

    from .campaign_builders import command

    command(campaign, uuid4(), Action.ARCHIVE)


@pytest.mark.parametrize("state", UNSETTLED)
@pytest.mark.parametrize(
    "purpose,mode",
    [
        ("reminder", "production"),
        ("admin_report", "production"),
        ("initial", "testing"),
    ],
)
def test_an_unsettled_email_refuses_archive(
    live_response_service, state, purpose, mode
):
    """Each unsettled state of any purpose and mode refuses archive, and the
    campaign stays closed."""
    harness = live_response_service
    seed_unsettled(delivered_receipt(harness), state, purpose=purpose, mode=mode)
    close_campaign(harness.campaign, uuid4())
    with pytest.raises(IntegrityError, match=REFUSAL):
        archive(harness.campaign)
    harness.campaign.refresh_from_db()
    assert harness.campaign.state == "closed"


@pytest.mark.parametrize("state", UNSETTLED)
def test_an_unsettled_email_of_no_campaign_does_not_block_archive(
    live_response_service, state
):
    """An operational notice (no campaign) in any unsettled state is not the
    campaign's email: archive still succeeds. Production's only Not sure it
    arrived email today is such a notice; loosening the guard's campaign
    match would block the first real archive."""
    harness = live_response_service
    seed_unsettled(
        delivered_receipt(harness),
        state,
        purpose="operational",
        mode="production",
        campaign=False,
    )
    close_campaign(harness.campaign, uuid4())
    archive(harness.campaign)
    harness.campaign.refresh_from_db()
    assert harness.campaign.state == "archived"


@pytest.mark.parametrize("settled", ["delivered", "permanent_failure", "cancelled"])
def test_archive_succeeds_once_the_email_is_settled(live_response_service, settled):
    """A settled answer (sent, not sent, or cancelled) admits archive."""
    harness = live_response_service
    unsettled = seed_unsettled(delivered_receipt(harness), "delivery_unknown")
    close_campaign(harness.campaign, uuid4())
    with pytest.raises(IntegrityError, match=REFUSAL):
        archive(harness.campaign)
    settle(unsettled, settled)
    archive(harness.campaign)
    harness.campaign.refresh_from_db()
    assert harness.campaign.state == "archived"


def test_the_receipt_check_still_answers_first(live_response_service):
    """An undelivered Production receipt keeps its own refusal words, ahead
    of the new check, so the receipt flow's tests and callers read the same."""
    harness = live_response_service
    receipt(harness, production=True)
    close_campaign(harness.campaign, uuid4())
    with pytest.raises(IntegrityError, match="resolved submission confirmations"):
        archive(harness.campaign)


def statement(text):
    """The guard's CREATE OR REPLACE statement in a frozen file's text."""
    start = text.index(f"CREATE OR REPLACE FUNCTION public.{GUARD}(")
    return text[start : text.index("END $$;", start) + len("END $$;")]


class Rollback(Exception):
    """Ends a check that must leave the database as it found it."""


def test_the_check_refuses_the_old_guard():
    """0201's DO block passes on the installed guard, fails on the old body
    (the baseline's before #2305, without the new check), on a SECURITY
    DEFINER guard or a disabled trigger, and passes again once 0201's
    statement runs over the old body; the schema baseline carries the same
    body."""
    text = FROZEN.read_text(encoding="utf-8")
    check = re.search(r"^DO \$check\$.*?\$check\$;", text, re.M | re.S)[0]
    new = statement(text)
    # The old body is the new one without the inserted comment and IF block.
    old, count = re.subn(
        r"\n {12}-- Every email of the campaign settled first \(#2305\).*?"
        r"USING ERRCODE='23514'; END IF;",
        "",
        new,
        flags=re.S,
    )
    assert count == 1 and REFUSAL not in old
    with connection.cursor() as cursor:
        with transaction.atomic():
            cursor.execute(check)
        with pytest.raises(DatabaseError, match="Migration 0201"), transaction.atomic():
            cursor.execute(old)
            cursor.execute(check)
        with pytest.raises(DatabaseError, match="Migration 0201"), transaction.atomic():
            cursor.execute(f"ALTER FUNCTION public.{GUARD}() SECURITY DEFINER")
            cursor.execute(check)
        with pytest.raises(DatabaseError, match="Migration 0201"), transaction.atomic():
            cursor.execute(
                f"ALTER TABLE public.stewardship_campaign_transition "
                f"DISABLE TRIGGER {GUARD}"
            )
            cursor.execute(check)
        with pytest.raises(Rollback), transaction.atomic():
            cursor.execute(old)
            cursor.execute(new)
            cursor.execute(check)
            raise Rollback
    baseline = (SCHEMA / "functions.sql").read_text(encoding="utf-8")
    assert new.replace("CREATE OR REPLACE FUNCTION", "CREATE FUNCTION", 1) in baseline
