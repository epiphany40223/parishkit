"""Mail dispatch reads one claimed message's link token, never the table (#389 L1).

Migration 0073 revokes the mail-dispatch login's reads of the Family token
and rehearsal token ciphertext and adds stewardship_dispatch_family_token_v1,
which answers only for an unsent Family message whose delivery task the
caller holds under a live lease. These tests run as the exact provisioned
mail-dispatch login.
"""

from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

import pytest
from django.db import DatabaseError, ProgrammingError, connection, transaction

from parishkit.stewardship.campaigns.credential_models import (
    FamilyAccessToken,
    FamilyCampaign,
    RehearsalCredential,
)
from parishkit.stewardship.campaigns.schedule_models import ScheduleDefinition
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.family_delivery import FamilyDeliveryResult
from parishkit.stewardship.family_delivery import FamilyDeliveryStatus as Status
from parishkit.stewardship.jobs.family_mail_dispatch import (
    begin_submission,
    finish_submission,
)

from .campaign_builders import campaign_clock, complete_empty_catchup
from .response_builders import activate_response_service
from .test_background_grants_postgresql import task_login
from .test_family_mail_dispatch_postgresql import claim, prepare
from .test_family_mail_preparation_postgresql import (  # noqa: F401
    family_mail,
)

pytestmark = pytest.mark.django_db(transaction=True)

MIGRATION = (
    Path(__file__).resolve().parents[3]
    / "src/parishkit/stewardship/schema/migrations/0073_dispatch_token_access.sql"
)
FUNCTION = "stewardship_dispatch_family_token_v1(uuid, uuid, uuid, bigint, uuid)"
MAIL = "pk_stewardship_mail_dispatch"


def answer(message, token, run, fence, worker):
    """The definer's rows for one call, as the current login."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT ciphertext, digest FROM "
            "stewardship_dispatch_family_token_v1(%s, %s, %s, %s, %s)",
            [message, token, run, fence, worker],
        )
        return cursor.fetchall()


def stored_token(message):
    """The id, ciphertext and digest of the message's own token (as the owner)."""
    if message.mode == "testing":
        row = RehearsalCredential.objects.get(
            family_id=message.family_id, epoch_id=message.rehearsal_epoch_id
        )
        return row.pk, row.token_ciphertext, row.token_digest
    row = FamilyAccessToken.objects.get(
        family_id=message.family_id,
        campaign_id=message.campaign_id,
        generation_id=message.token_generation_id,
    )
    return row.pk, row.ciphertext, row.digest


def shift_lease(run, change):
    """Move one run's lease by ``change`` as the owner, around the task guards.

    The guards admit a lease change only from its claim's own transitions;
    replica mode skips them for this one transaction.
    """
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute("SELECT current_user")
        (login,) = cursor.fetchone()
        cursor.execute("RESET SESSION AUTHORIZATION")
        cursor.execute("SET LOCAL session_replication_role = replica")
        cursor.execute(
            "UPDATE stewardship_task_run "
            "SET lease_expires_at=lease_expires_at+%s::interval WHERE id=%s",
            [change, run],
        )
        cursor.execute(f'SET SESSION AUTHORIZATION "{login}"')


@contextmanager
def lease_expired(run):
    """One run's lease a day in the past, then back where it was."""
    shift_lease(run, "-1 day")
    try:
        yield
    finally:
        shift_lease(run, "1 day")


def stored_decoys(message, token):
    """Two real credential rows the message does not name, as the owner.

    One is another Family's token in the message's own campaign and
    generation (Production) or epoch (Testing), copied from the message's
    token; the other is the message's own Family's credential of the other
    mode. Replica mode skips the insert guards (and foreign keys) for this
    one fixture transaction. Returns ``(other_family, other_mode)`` ids.
    """
    other_family = FamilyCampaign.objects.exclude(pk=message.family_id).get().pk
    family_token, rehearsal = uuid4(), uuid4()
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute("SET LOCAL session_replication_role = replica")
        if message.mode == "testing":
            cursor.execute(
                "INSERT INTO stewardship_rehearsal_credential (id, created_at, "
                "actor_id, correlation_id, updated_at, version, code_ciphertext, "
                "token_ciphertext, token_digest, family_id, epoch_id) "
                "SELECT %s, created_at, actor_id, correlation_id, updated_at, "
                "version, code_ciphertext, token_ciphertext, "
                "md5(random()::text) || md5(random()::text), %s, epoch_id "
                "FROM stewardship_rehearsal_credential WHERE id=%s",
                [rehearsal, other_family, token],
            )
            # A live Production token for the Testing message's own Family.
            cursor.execute(
                "INSERT INTO stewardship_family_token (id, created_at, actor_id, "
                "correlation_id, updated_at, version, ciphertext, digest, "
                "destroyed_at, rotated_at, campaign_id, generation_id, family_id) "
                "VALUES (%s, now(), NULL, gen_random_uuid(), now(), 1, 'sealed', "
                "md5(random()::text) || md5(random()::text), NULL, NULL, %s, "
                "coalesce(%s, gen_random_uuid()), %s)",
                [
                    family_token,
                    message.campaign_id,
                    message.token_generation_id,
                    message.family_id,
                ],
            )
            return rehearsal, family_token
        cursor.execute(
            "INSERT INTO stewardship_family_token (id, created_at, actor_id, "
            "correlation_id, updated_at, version, ciphertext, digest, destroyed_at, "
            "rotated_at, campaign_id, generation_id, family_id) "
            "SELECT %s, created_at, actor_id, correlation_id, updated_at, version, "
            "ciphertext, md5(random()::text) || md5(random()::text), NULL, "
            "rotated_at, campaign_id, generation_id, %s "
            "FROM stewardship_family_token WHERE id=%s",
            [family_token, other_family, token],
        )
        # A Testing credential for the Production message's own Family.
        cursor.execute(
            "INSERT INTO stewardship_rehearsal_credential (id, created_at, "
            "actor_id, correlation_id, updated_at, version, code_ciphertext, "
            "token_ciphertext, token_digest, family_id, epoch_id) "
            "VALUES (%s, now(), NULL, gen_random_uuid(), now(), 1, 'sealed', "
            "'sealed', md5(random()::text) || md5(random()::text), %s, "
            "coalesce(%s, gen_random_uuid()))",
            [rehearsal, message.family_id, message.rehearsal_epoch_id],
        )
        return family_token, rehearsal


def denied(statement):
    """Whether the current login is refused ``statement`` for lack of privilege."""
    try:
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute(statement)
    except ProgrammingError as error:
        return "permission denied" in str(error)
    return False


@pytest.mark.parametrize("production", [False, True])
def test_mail_dispatch_reads_only_its_claimed_messages_token(
    family_mail,  # noqa: F811
    production,
):
    """One live claim answers its own message's token, and nothing else does."""
    if production:
        family_mail = activate_response_service(family_mail)
        complete_empty_catchup(family_mail.campaign, uuid4())
    with campaign_clock(ScheduleDefinition.objects.get().current_revision.due_at):
        message = prepare(family_mail)
        token, ciphertext, digest = stored_token(message)
        other_family, other_mode = stored_decoys(message, token)
        with task_login(ServiceRole.MAIL_DISPATCH, exact=True):
            assert denied("SELECT ciphertext FROM stewardship_family_token")
            assert denied(
                "SELECT token_ciphertext FROM stewardship_rehearsal_credential"
            )
            execution = claim(message)
            live = (execution.claim.run_id, execution.claim.fence)
            worker = execution.claim.worker_id
            assert answer(message.pk, token, *live, worker) == [(ciphertext, digest)]
            # A wrong fence, worker or run, a token the message does not
            # name, or another message answers nothing.
            assert answer(message.pk, token, live[0], live[1] + 1, worker) == []
            assert answer(message.pk, token, *live, uuid4()) == []
            assert answer(message.pk, token, uuid4(), live[1], worker) == []
            assert answer(message.pk, uuid4(), *live, worker) == []
            assert answer(uuid4(), token, *live, worker) == []
            # Real stored credentials under the same valid claim: another
            # Family's token, and the message's own Family's token of the
            # other mode (a Production token for a Testing message, and the
            # reverse).
            assert answer(message.pk, other_family, *live, worker) == []
            assert answer(message.pk, other_mode, *live, worker) == []
            # The same claim once its lease has run out answers nothing.
            with lease_expired(live[0]):
                assert answer(message.pk, token, *live, worker) == []
            assert answer(message.pk, token, *live, worker) == [(ciphertext, digest)]
            # The real dispatch path reads the token through the definer.
            mail, _, _, _ = begin_submission(
                message.pk,
                execution.claim,
                private=family_mail.rings.private,
                public_origin="http://localhost:8000",
            )
            assert family_mail.code in mail.text and "/access/" in mail.text
            # Submitted: the message is no longer unsent.
            assert answer(message.pk, token, *live, worker) == []
            finish_submission(
                message.pk, execution.claim, FamilyDeliveryResult(Status.ACCEPTED, 1)
            )
            assert answer(message.pk, token, *live, worker) == []


def test_only_mail_dispatch_may_call_the_token_definer(family_mail):  # noqa: F811
    """Every other runtime login is refused EXECUTE, as PUBLIC is."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT has_function_privilege('public', %s, 'EXECUTE')",
            [f"public.{FUNCTION}"],
        )
        assert cursor.fetchone()[0] is False
    for role in (ServiceRole.WORKER, ServiceRole.SCHEDULER, ServiceRole.WEB):
        with task_login(role, exact=True):
            assert denied(
                "SELECT * FROM stewardship_dispatch_family_token_v1("
                "gen_random_uuid(), gen_random_uuid(), gen_random_uuid(), 0, "
                "gen_random_uuid())"
            )


def migrate_from_previous_release(cursor, table_grant=False):
    """Run 0073 on the earlier release's catalog: no definer, the old reads.

    The mail-dispatch login holds what the earlier release declared (column
    SELECT on both ciphertext columns), or, with ``table_grant``, a drifted
    whole-table SELECT that a column REVOKE cannot remove.
    """
    cursor.execute(f"DROP FUNCTION public.{FUNCTION}")
    cursor.execute(f"CREATE ROLE {MAIL} NOLOGIN")
    if table_grant:
        cursor.execute(f"GRANT SELECT ON public.stewardship_family_token TO {MAIL}")
    else:
        cursor.execute(
            f"GRANT SELECT (id, ciphertext, digest) "
            f"ON public.stewardship_family_token TO {MAIL}"
        )
    cursor.execute(
        f"GRANT SELECT (id, token_ciphertext, token_digest) "
        f"ON public.stewardship_rehearsal_credential TO {MAIL}"
    )
    cursor.execute(MIGRATION.read_text(encoding="utf-8"))


def privilege(cursor, table, column):
    """Whether the mail-dispatch login may SELECT one column."""
    cursor.execute(
        "SELECT has_column_privilege(%s, %s, %s, 'SELECT')",
        [MAIL, f"public.{table}", column],
    )
    return cursor.fetchone()[0]


def test_the_migration_revokes_the_previous_releases_reads():
    """0073 on the earlier catalog revokes both reads and keeps the metadata."""
    with transaction.atomic(), connection.cursor() as cursor:
        migrate_from_previous_release(cursor)
        assert not privilege(cursor, "stewardship_family_token", "ciphertext")
        assert not privilege(
            cursor, "stewardship_rehearsal_credential", "token_ciphertext"
        )
        assert privilege(cursor, "stewardship_family_token", "digest")
        assert privilege(cursor, "stewardship_rehearsal_credential", "token_digest")
        cursor.execute(
            "SELECT prosecdef FROM pg_proc WHERE oid=%s::regprocedure",
            [f"public.{FUNCTION}"],
        )
        assert cursor.fetchone()[0] is True
        # Leave the database as the test found it.
        transaction.set_rollback(True)


def test_the_migration_refuses_a_ciphertext_read_it_cannot_revoke():
    """A whole-table SELECT survives the column REVOKE, so 0073 refuses."""
    with (
        pytest.raises(DatabaseError, match="can still read token ciphertext"),
        transaction.atomic(),
        connection.cursor() as cursor,
    ):
        migrate_from_previous_release(cursor, table_grant=True)


# Earlier or weaker bodies of the read: each removes one gate the check
# names (the text replaced, and what replaces it).
WEAKER_BODIES = {
    "no lease check": ("          AND r.lease_expires_at>clock_timestamp()\n", ""),
    "no purpose gate": (
        "          AND m.purpose IN ('initial','reminder','family_test')\n",
        "",
    ),
    "no Production mode gate": (
        "WHERE c.mode='production' AND t.family_id",
        "WHERE t.family_id",
    ),
    "no Production Family gate": (
        "AND t.family_id=c.family_id",
        "AND true",
    ),
    "no Testing mode gate": (
        "WHERE c.mode='testing' AND rc.family_id",
        "WHERE rc.family_id",
    ),
    "no Testing Family gate": (
        "AND rc.family_id=c.family_id",
        "AND true",
    ),
}


@pytest.mark.parametrize(
    "drift",
    ["missing", "invoker", "public execute", *WEAKER_BODIES],
)
def test_the_migration_check_refuses_a_definer_other_than_its_own(drift):
    """The closing DO block fails on each way the definer could differ."""
    text = MIGRATION.read_text(encoding="utf-8")
    check = text[text.index("DO $check$") :]
    with (
        pytest.raises(DatabaseError, match="Migration 0073"),
        transaction.atomic(),
        connection.cursor() as cursor,
    ):
        if drift == "missing":
            cursor.execute(f"DROP FUNCTION public.{FUNCTION}")
        elif drift == "invoker":
            cursor.execute(f"ALTER FUNCTION public.{FUNCTION} SECURITY INVOKER")
        elif drift == "public execute":
            cursor.execute(f"GRANT EXECUTE ON FUNCTION public.{FUNCTION} TO PUBLIC")
        else:
            # The previous state for this case: the weaker body installed.
            removed, replacement = WEAKER_BODIES[drift]
            body = text[: text.index("DO $revoke$")]
            assert body.count(removed) == 1
            cursor.execute(f"DROP FUNCTION public.{FUNCTION}")
            cursor.execute(body.replace(removed, replacement))
        cursor.execute(check)
