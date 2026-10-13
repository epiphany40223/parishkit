"""The example Families of a refused large change, under the real guard (#530).

ADM-13 PR 5c: when a full refresh is refused for a drop, the worker records,
in the refusal's own transaction, up to five example Families per failing
Family, Member or contact count, named as the directory names them from the
current promoted data. Only the worker records them, only with their
refusal, never for an empty load; nobody changes one. One SQL function names
the refusal whose examples may be shown (the newest, not promoted over, not
accepted, under seven days old); the readers use it and the worker's hourly
maintenance deletes every other example.
"""

import logging
from dataclasses import replace
from datetime import timedelta
from importlib import import_module
from uuid import uuid4

import pytest
from django.db import DatabaseError, connection, transaction

from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.source import drop_examples
from parishkit.stewardship.source.attempts import begin_refresh_attempt
from parishkit.stewardship.source.corpus import normalize_core
from parishkit.stewardship.source.drop_examples import (
    SHOWN_ATTEMPT_SQL,
    counted_keys,
    prune_drop_examples,
    shown_examples,
)
from parishkit.stewardship.source.drop_models import (
    SourceDropCount,
    SourceDropExample,
)
from parishkit.stewardship.source.failures import settle_failed_read
from parishkit.stewardship.source.loading import (
    CountCheck,
    DestructiveSourceChange,
    SourceLoad,
)
from parishkit.stewardship.source.snapshot_names import snapshot_family_names

from ..test_source_corpus import source
from .test_background_grants_postgresql import task_login
from .test_source_attempts_postgresql import setup
from .test_source_drop_acceptance_postgresql import (
    administrator,
    database_now,
    owner_accept,
    refused,
    source_singletons,  # noqa: F401
    web_accept,
    web_with,
)
from .test_source_refreshing_postgresql import next_full, seed_full

pytestmark = pytest.mark.django_db(transaction=True)
drop_examples_migration = import_module(
    "parishkit.stewardship.source.migrations.0009_drop_examples"
)

FAMILIES = 8


def parish(count, *, drop=(), unmailed=()):
    """A synthetic parish of ``count`` Families, each with one active head
    who has a valid email, without the Families numbered in ``drop`` and
    with the heads numbered in ``unmailed`` having no email."""
    families, members, infos = {}, {}, {}
    for n in range(1, count + 1):
        if n in drop:
            continue
        family, member = 100 + n, 1000 + n
        families[family] = {
            "familyDUID": family,
            "registeredOrganizationID": 12345,
            "famGroupID": 7,
            "lastName": f"Family{n}",
        }
        members[member] = {
            "memberDUID": member,
            "familyDUID": family,
            "firstName": f"Head{n}",
            "lastName": f"Family{n}",
            "memberType": "Head",
            "memberStatus": "Active",
            "emailAddress": None if n in unmailed else f"head{n}@example.org",
        }
        infos[member] = {"memberDUID": member}
    return replace(
        source(),
        organization_id=12345,
        families=families,
        members=members,
        member_contactinfos=infos,
        ministry_types={},
        ministry_type_memberships={},
    )


def seed(credential, execution, lease):
    """Promote the synthetic parish as the current data."""

    def load(as_of):
        """The synthetic parish as one full load's result."""
        corpus = normalize_core(parish(FAMILIES), as_of=as_of)
        return SourceLoad(
            corpus,
            {kind: len(rows) for kind, rows in corpus.items()},
            {
                "giving_as_of_date": as_of.isoformat(),
                "anonymous_pledges": 0,
                "anonymous_contributions": 0,
            },
        )

    return seed_full(credential, execution, lease, load=load)


# Families fell too far, Members did not, and Families with an active head
# and valid email addresses did.
CHECKS = (
    CountCheck("family", 8, 1, 25, True),
    CountCheck("active_head_families", 8, 1, 25, True),
    CountCheck("member", 8, 7, 25, False),
    CountCheck("ministry", 4, 0, 25, True),
    CountCheck("valid_email_contacts", 8, 5, 25, True),
)


def refusal(checks=CHECKS, **changes):
    """The refused load the checks describe: ``parish`` with ``changes``.

    With no checks it records no drop counts, as a lapsed acceptance's
    refusal does, leaving the test to write its own.
    """
    first = checks[0] if checks else CountCheck(None, None, 0, 25, False)
    error = DestructiveSourceChange(
        "PRIVATE",
        measure=first.measure,
        before=first.before,
        after=first.after,
        checks=checks,
    )
    error.counted = counted_keys(
        normalize_core(parish(FAMILIES, **changes), as_of=database_now().date())
    )
    return error


def refuse(credential, error):
    """Run one more full refresh and settle it as ``error``'s refusal.

    The refusal is settled on the worker's own login, as in production, so
    the drop counts and the examples go through the worker's grants and the
    tables' guards (not the schema owner's exemption).
    """
    execution, lease = next_full()
    attempt = begin_refresh_attempt(execution, lease, credential)
    with task_login(ServiceRole.WORKER, exact=True):
        settle_failed_read(execution, error, source_claim=lease)
    return attempt


def examples(attempt):
    """The recorded examples of ``attempt``, by measure, in order."""
    found = {}
    for measure, duid, name in (
        SourceDropExample.objects.filter(attempt=attempt)
        .order_by("measure", "position")
        .values_list("measure", "family_duid", "family_name")
    ):
        found.setdefault(measure, []).append((duid, name))
    return found


def execute(statement):
    """Run one statement on the examples (``refused`` holds the transaction)."""
    with connection.cursor() as cursor:
        cursor.execute(statement)


def shown():
    """The refusal whose examples may be shown, as SQL answers it."""
    with connection.cursor() as cursor:
        cursor.execute(SHOWN_ATTEMPT_SQL)
        return cursor.fetchone()[0]


def test_a_refusal_records_named_examples_for_its_failing_counts(tmp_path):
    """Up to five lowest-DUID Families per failing Family, Member or contact
    count, named from the current data as the directory names them; none for
    a passing count or a ministry count."""
    credential, execution, lease, *_ = setup(tmp_path)
    current = seed(credential, execution, lease)
    # The refused load lost Families 2 to 8, and Family 1's head lost the
    # email, so Families 1 to 8 lost a valid email address.
    attempt = refuse(credential, refusal(drop=range(2, 9), unmailed={1}))
    found = examples(attempt)
    assert set(found) == {"family", "active_head_families", "valid_email_contacts"}
    names = snapshot_family_names(current.pk, range(101, 109))
    assert found["family"] == [(duid, names[duid]) for duid in range(102, 107)]
    assert found["active_head_families"] == found["family"]
    assert found["valid_email_contacts"] == [
        (duid, names[duid]) for duid in range(101, 106)
    ]
    assert names[102] == "Family2, Head2"
    # The drop counts were recorded as before, and the examples may be shown.
    assert SourceDropCount.objects.filter(attempt=attempt).count() == len(CHECKS)
    assert shown() == attempt.pk
    assert shown_examples(attempt.pk)["family"][0] == (102, "Family2, Head2")


def test_an_empty_load_or_a_refusal_without_keys_records_none(tmp_path):
    """An empty load can never be accepted; a refusal that carries no keys
    (a lapsed acceptance, say) has nothing to compare."""
    credential, execution, lease, *_ = setup(tmp_path)
    seed(credential, execution, lease)
    empty = (
        CountCheck("family", 8, 0, 25, True),
        CountCheck("member", 8, 0, 25, True),
    )
    first = refuse(credential, refusal(empty, drop=range(1, 9)))
    keyless = refusal()
    keyless.counted = {}
    second = refuse(credential, keyless)
    assert not SourceDropExample.objects.filter(attempt__in=(first, second)).exists()
    assert SourceDropCount.objects.filter(attempt=second).exists()


def test_a_failure_while_recording_examples_keeps_the_refusal(
    tmp_path, monkeypatch, caplog
):
    """Best effort: the refusal and its counts commit without examples, and
    one WARNING names only the failure's category, never a Family."""
    credential, execution, lease, *_ = setup(tmp_path)
    seed(credential, execution, lease)

    def broken():
        """Reading the current data fails."""
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1/0")

    monkeypatch.setattr(drop_examples, "_baseline", broken)
    with caplog.at_level(logging.WARNING):
        attempt = refuse(credential, refusal(drop=range(2, 9)))
    assert SourceDropCount.objects.filter(attempt=attempt).count() == len(CHECKS)
    assert not SourceDropExample.objects.exists()
    assert "task_failed" in caplog.text
    assert "Family2" not in caplog.text


def test_only_the_worker_records_examples_with_their_refusal(tmp_path):
    """The guard: the worker login, in the refusal's own transaction, for a
    count that failed; another login is refused even with grants added."""
    credential, execution, lease, *_ = setup(tmp_path)
    seed(credential, execution, lease)
    attempt = refuse(credential, refusal(drop=range(2, 9)))

    def add(measure="family", position=5, duid=999):
        """Insert one example of ``attempt`` in its own transaction."""
        with transaction.atomic():
            SourceDropExample.objects.create(
                attempt=attempt,
                measure=measure,
                position=position,
                family_duid=duid,
                family_name="Synthetic",
            )

    # The schema owner (the restore procedure) may delete one.
    SourceDropExample.objects.filter(
        attempt=attempt, measure="family", position=5
    ).delete()
    with task_login(ServiceRole.WORKER, exact=True):
        # A later transaction is not the refusal's.
        with refused("recorded with its refusal"):
            add()
        with refused("recorded with its refusal"):
            add(measure="member", position=1)
        # No login holds UPDATE; the guard refuses it even to the owner.
        with refused("permission denied"):
            execute("UPDATE stewardship_source_drop_example SET family_name='x'")
    with task_login(ServiceRole.WEB, exact=True):
        with refused("permission denied"):
            add()
        with connection.cursor() as cursor:
            cursor.execute("RESET SESSION AUTHORIZATION")
            cursor.execute(
                "GRANT INSERT, DELETE ON stewardship_source_drop_example "
                "TO pk_stewardship_web"
            )
            cursor.execute("SET SESSION AUTHORIZATION pk_stewardship_web")
        with refused("Only the worker"):
            add()
        with refused("Only the worker"):
            execute("DELETE FROM stewardship_source_drop_example")
    # The schema owner cannot change one either.
    with refused("never changed"):
        execute("UPDATE stewardship_source_drop_example SET position=1")
    # At most five per count, structurally.
    with refused("source_drop_example_place"):
        add(position=6)
    assert SourceDropExample.objects.filter(attempt=attempt).count() == 4 + 5 + 5


def test_the_worker_records_examples_in_its_own_refusal_transaction(tmp_path):
    """The guard admits the worker's insert beside the refusal's counts."""
    credential, execution, lease, *_ = setup(tmp_path)
    seed(credential, execution, lease)
    keyless = refusal(checks=())
    keyless.counted = {}
    attempt = refuse(credential, keyless)
    with task_login(ServiceRole.WORKER, exact=True), transaction.atomic():
        SourceDropCount.objects.create(
            correlation_id=uuid4(),
            attempt=attempt,
            measure="family",
            before=8,
            after=1,
            limit_percent=25,
            failed=True,
        )
        SourceDropExample.objects.create(
            attempt=attempt,
            measure="family",
            position=1,
            family_duid=102,
            family_name="Family2, Head2",
        )
        # Not for a count that passed (Members, recorded here within the
        # limit), nor one that was not recorded at all.
        SourceDropCount.objects.create(
            correlation_id=uuid4(),
            attempt=attempt,
            measure="member",
            before=8,
            after=7,
            limit_percent=25,
            failed=False,
        )
        for measure in ("member", "valid_email_contacts"):
            with (
                pytest.raises(DatabaseError, match="for a count that failed"),
                transaction.atomic(),
            ):
                SourceDropExample.objects.create(
                    attempt=attempt,
                    measure=measure,
                    position=1,
                    family_duid=102,
                    family_name="Family2, Head2",
                )
    example = SourceDropExample.objects.get()
    assert abs(database_now() - example.created_at) < timedelta(minutes=1)


def test_examples_are_shown_only_until_superseded_promoted_over_or_accepted(
    tmp_path,
):
    """The function the page and the maintenance share: a newer refusal, an
    acceptance or a promoted full refresh ends the examples' showing, and the
    worker's housekeeping then deletes them."""
    credential, execution, lease, *_ = setup(tmp_path)
    seed(credential, execution, lease)
    first = refuse(credential, refusal(drop=range(2, 9)))
    second = refuse(credential, refusal(drop=range(3, 9)))
    assert shown() == second.pk
    assert shown_examples(first.pk) == {}
    assert shown_examples(second.pk)["family"][0][0] == 103
    # The web login reads them (its grants and the invoker function).
    with task_login(ServiceRole.WEB, exact=True):
        assert shown_examples(second.pk)["family"][0][0] == 103
    # The worker's housekeeping deletes the superseded refusal's only.
    superseded = sum(len(pairs) for pairs in examples(first).values())
    assert superseded == 15
    with task_login(ServiceRole.WORKER, exact=True):
        assert prune_drop_examples() == superseded
        assert prune_drop_examples() == 0
    assert examples(first) == {} and examples(second)
    # Accepting the refusal ends its examples: the decision is made.
    administrator()
    with web_with("SELECT", "INSERT"):
        web_accept(second)
    assert shown() is None and shown_examples(second.pk) == {}
    with task_login(ServiceRole.WORKER, exact=True):
        assert prune_drop_examples() > 0
    assert not SourceDropExample.objects.exists()
    # A promoted full refresh ends a refusal's examples too.
    third = refuse(credential, refusal(drop=range(2, 9)))
    assert shown() == third.pk
    execution, lease = next_full()
    seed(credential, execution, lease)
    assert shown() is None and shown_examples(third.pk) == {}


def test_examples_are_shown_for_seven_days_at_most(tmp_path):
    """A refusal older than a week shows none, even before housekeeping.

    No full refresh is promoted here, so only the age decides.
    """
    credential, execution, lease, *_ = setup(tmp_path)
    attempt = begin_refresh_attempt(execution, lease, credential)
    keyless = refusal(checks=())
    keyless.counted = {}
    settle_failed_read(execution, keyless, source_claim=lease)

    def count(measure, age, failed):
        """One drop count of ``attempt`` recorded ``age`` ago, as the owner."""
        SourceDropCount.objects.create(
            correlation_id=uuid4(),
            attempt=attempt,
            measure=measure,
            before=8,
            after=1 if failed else 8,
            limit_percent=25,
            failed=failed,
            created_at=database_now() - age,
        )

    count("family", timedelta(days=7, minutes=1), True)
    SourceDropExample.objects.create(
        attempt=attempt,
        measure="family",
        position=1,
        family_duid=102,
        family_name="Family2, Head2",
    )
    assert shown() is None and shown_examples(attempt.pk) == {}
    # Six days old, it is shown.
    count("member", timedelta(days=6), False)
    assert shown() == attempt.pk
    assert shown_examples(attempt.pk) == {"family": ((102, "Family2, Head2"),)}
    assert prune_drop_examples() == 0


def test_a_lapsed_acceptance_still_hides_the_examples(tmp_path):
    """Accepted once means decided, whether or not the acceptance is live."""
    credential, execution, lease, *_ = setup(tmp_path)
    seed(credential, execution, lease)
    attempt = refuse(credential, refusal(drop=range(2, 9)))
    owner_accept(attempt, expires_at=database_now() - timedelta(seconds=1))
    assert shown() is None and shown_examples(attempt.pk) == {}


# The frozen file's closing check (it ran when the migration installed).
CHECK = drop_examples_migration.FROZEN_SQL.read_text(encoding="utf-8")
CHECK = CHECK[CHECK.index("DO $check$") :]


def test_the_closing_check_passes_as_installed():
    """The installed schema is the one the file declares."""
    with transaction.atomic():
        execute(CHECK)
        transaction.set_rollback(True)


@pytest.mark.parametrize(
    ("statement", "message"),
    [
        (
            "ALTER TABLE stewardship_source_drop_example "
            "DROP CONSTRAINT source_drop_example_place",
            "keys and checks were not created",
        ),
        (
            "DROP INDEX stewardship_source_drop_example_attempt_id_c1ad1892",
            "index was not created",
        ),
        (
            "GRANT SELECT ON stewardship_source_drop_example TO PUBLIC",
            "PUBLIC holds a privilege",
        ),
        (
            "GRANT EXECUTE ON FUNCTION "
            "public.stewardship_source_drop_example_guard_v1() TO PUBLIC",
            "guard is not the one",
        ),
        (
            "DROP TRIGGER stewardship_source_drop_example_guard "
            "ON stewardship_source_drop_example",
            "guard trigger was not created",
        ),
        (
            "ALTER FUNCTION public.stewardship_source_drop_example_attempt_v1() "
            "VOLATILE",
            "visibility is not installed as declared",
        ),
    ],
)
def test_the_closing_check_refuses_what_the_file_did_not_install(statement, message):
    """Each object the file creates is checked, so a partial or altered
    install cannot commit."""
    with pytest.raises(DatabaseError, match=message), transaction.atomic():
        execute(statement)
        execute(CHECK)
