"""The refresh's last census cases (#1749), under migration 0138's guards.

The review pipeline (spec, "Refresh checks") re-evaluates an Updated in
ParishSoft change, an entered New Member and an entered home address
against each promoted refresh. The checks and the change's move to
Conflict are written by real promotions (``prepare`` and ``promote``) on
the guards migration 0138 (provisional number) installs, and read by the
real page's row reader.
"""

import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from django.db import DatabaseError, connection, transaction

from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.pipeline import Stage
from parishkit.stewardship.reports.census_worklist import worklist_rows
from parishkit.stewardship.responses import refresh_checks
from parishkit.stewardship.responses.census_resolution import InvalidMemberId
from parishkit.stewardship.responses.census_review import decide_census_changes
from parishkit.stewardship.responses.models import ProposalResolution, ProposedChange
from parishkit.stewardship.responses.refresh_checks import census_source
from parishkit.stewardship.source.corpus import normalize_core
from parishkit.stewardship.source.models import SourceCurrent
from parishkit.stewardship.workflows.models import RefreshCheck

from ..census_factory import address, member
from .test_background_grants_postgresql import task_login
from .test_census_refresh_sources_postgresql import (
    answered,
    entered,
    opt_out,
    refresh,
)
from .test_census_review_postgresql import fresh
from .test_ministry_responses_postgresql import ministry_source
from .test_publication_plans_postgresql import owner
from .test_refresh_checks_postgresql import (  # noqa: F401 (started: fixture)
    BASE,
    census_row,
    checks,
    started,
)
from .test_source_families_postgresql import TODAY, prepare, promote

pytestmark = pytest.mark.django_db(transaction=True)

MIGRATIONS = (
    Path(__file__).resolve().parents[3] / "src/parishkit/stewardship/schema/migrations"
)
FROZEN = MIGRATIONS / "0138_published_refresh_conflicts.sql"
# The New Member's ParishSoft Member ID Staff record, and their record.
ADDED = 7
# Family 1's primary address in ParishSoft, and the census answer it holds.
PRIMARY = {
    "primaryAddress1": "123 Sample Street",
    "primaryAddress2": "",
    "primaryCity": "Louisville",
    "primaryState": "KY",
    "primaryPostalCode": "40223",
}


def first_name(answers):
    """The Family changes Member 3's first name (an Automatic change)."""
    answers["members"]["3"]["first_name"] = "Requested"


def published(harness, staff, row):
    """Accept ``row`` and forge it Updated in ParishSoft, as a verified run
    leaves it (the update itself is ``test_publication_worker``'s)."""
    from .test_census_pipeline_review_postgresql import decide

    decide(harness, staff, [row], "accept")
    owner(
        "UPDATE stewardship_proposed_change SET execution='published',"
        " version=version+1 WHERE id=%s",
        [row.pk],
    )
    return fresh(row)


def test_an_updated_change_back_at_the_baseline_is_a_conflict(response_service):
    """Rule 2's exception: ParishSoft back at the Baseline (the write was
    undone) records a conflict check and moves the change to Conflict, at
    that value and the promoting snapshot, To review again; the row says
    why. A quiet refresh then writes nothing."""
    harness, staff, rows = answered(response_service, first_name)
    change = published(harness, staff, rows["first_name"])
    baseline = change.baseline_value
    refresh(harness)
    assert checks(proposal=change) == ["conflict"]
    check = RefreshCheck.objects.get(proposal=change)
    assert check.item_version == change.version
    assert check.observed == {"available": True, "value": baseline}
    moved = fresh(change)
    current = SourceCurrent.objects.get(singleton=True).snapshot_id
    assert (moved.execution, moved.decision, moved.version) == (
        "conflict",
        "unreviewed",
        change.version + 1,
    )
    assert (moved.current_available, moved.current_value) == (True, baseline)
    assert moved.current_source_id == current
    row = census_row(harness, moved)
    assert row["stage"] == Stage.CONFLICT
    assert row["refresh_conflict"] is True
    (shown,) = worklist_rows([row])
    assert shown["stage_note"] == "ParishSoft no longer holds the update."
    refresh(harness)
    assert checks(proposal=change) == ["conflict"]
    assert fresh(moved).version == moved.version


def test_an_updated_change_at_a_third_value_is_a_conflict(response_service):
    """Rule 3: ParishSoft changed after the update; the row says so until a
    review decides the Conflict again, and only a review puts it back in
    force: ParishSoft moving on and back to the Baseline leaves it To
    review, never Ready to update."""
    from .test_census_pipeline_review_postgresql import decide

    harness, staff, rows = answered(response_service, first_name)
    change = published(harness, staff, rows["first_name"])
    refresh(harness, member={"firstName": "Someone"})
    moved = fresh(change)
    assert (moved.execution, moved.current_value) == ("conflict", "Someone")
    assert census_row(harness, moved)["refresh_conflict"] is True
    refresh(harness)
    back = fresh(moved)
    assert (back.execution, back.decision) == ("pending", "unreviewed")
    assert census_row(harness, back)["stage"] == Stage.TO_REVIEW
    refresh(harness, member={"firstName": "Someone"})
    moved = fresh(back)
    # Accept decides it against ParishSoft's value: no longer a Conflict.
    decide(harness, staff, [moved], "accept")
    row = census_row(harness, fresh(moved))
    assert row["stage"] == Stage.READY_TO_UPDATE
    assert row["refresh_conflict"] is False


def test_an_updated_change_parishsoft_holds_is_confirmed(response_service):
    """The read after the run: ParishSoft holds the reviewer's value, so the
    change is Confirmed and stays Updated in ParishSoft."""
    harness, staff, rows = answered(response_service, first_name)
    change = published(harness, staff, rows["first_name"])
    refresh(harness, member={"firstName": "Requested"})
    assert checks(proposal=change) == ["confirmed"]
    assert fresh(change).execution == "published"
    assert census_row(harness, change)["stage"] == Stage.CONFIRMED


def test_the_guard_moves_a_published_change_only_with_its_check(
    response_service, monkeypatch
):
    """Without this promotion's conflict check, the worker cannot move an
    Updated in ParishSoft change to Conflict, and the whole refresh rolls
    back."""
    harness, staff, rows = answered(response_service, first_name)
    change = published(harness, staff, rows["first_name"])
    monkeypatch.setattr(
        refresh_checks.RefreshCheck.objects, "bulk_create", lambda checks: checks
    )
    with pytest.raises(DatabaseError, match="only with its refresh check"):
        refresh(harness)
    assert fresh(change).execution == "published"


@pytest.mark.parametrize(
    "case,words",
    [
        ("unchanged", "differs from its evidence"),
        ("entered", "differs from its evidence"),
    ],
)
def test_the_guard_refuses_a_forged_conflict_check(
    response_service, monkeypatch, case, words
):
    """A published change ParishSoft no longer holds is a Conflict, never
    unchanged; and only a published change takes a conflict check."""
    if case == "unchanged":
        harness, staff, rows = answered(response_service, first_name)
        target, outcome = published(harness, staff, rows["first_name"]), "unchanged"
        observed = {"available": True, "value": target.baseline_value}
        family = None
    else:
        harness, staff, rows = answered(response_service, opt_out)
        target, outcome = entered(harness, staff, rows["email_opt_out"]), "conflict"
        observed, family = {"available": True, "value": False}, {"sendNoMail": False}

    def forged(snapshot, corpus, campaign_id, full):
        """One check the guard must refuse."""
        yield RefreshCheck(
            snapshot_id=snapshot.pk,
            full_refresh=full,
            proposal_id=target.pk,
            item_version=target.version,
            outcome=outcome,
            observed=observed,
        )

    monkeypatch.setattr(refresh_checks, "_checks", forged)
    with pytest.raises(DatabaseError, match=words):
        refresh(harness, family=family)
    assert RefreshCheck.objects.count() == 0


def new_member(answers):
    """The Family adds one New Member, in no Ministry."""
    key = str(uuid4())
    answers["proposed_members"][key] = member(first_name="Newly")
    answers["ministries"]["proposed_members"][key] = {"join": []}


def mark(harness, staff, row, member_id, **extra):
    """Mark ``row`` Entered by hand on the web login, naming ``member_id``.

    Entered by hand follows an Accept (strict phase order, #1890), so a row
    still To review is accepted first, as Staff now do.
    """
    current = fresh(row)
    if (current.decision, current.execution) == ("unreviewed", "pending"):
        with task_login(ServiceRole.WEB, exact=True, reconnect=True):
            decide_census_changes(
                harness.service.store,
                staff,
                "accept",
                {current.pk: current.version},
                note="",
                request_key=uuid4(),
            )
        row = fresh(row)
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        decide_census_changes(
            harness.service.store,
            staff,
            "entered_by_hand",
            {row.pk: row.version},
            note="",
            request_key=uuid4(),
            member=member_id,
            **extra,
        )
    return fresh(row)


def with_added(harness, **record):
    """Promote a full refresh listing Member ``ADDED`` in Family 1."""
    data = ministry_source()
    data.members[3].update(BASE)
    data.members[ADDED] = {
        **ministry_source().members[3],
        "memberDUID": ADDED,
        **record,
    }
    snapshot, claim = prepare(data)
    return promote(snapshot, claim, harness.campaign, harness.rings)


def test_a_new_member_is_confirmed_by_the_member_id_recorded_at_its_mark(
    response_service,
    started,  # noqa: F811
):
    """Staff name the ParishSoft Member ID of the Member they added; the
    refresh that lists that Member, active and living, in the Family
    confirms the change. Before then it is unchanged, and Not seen after
    two full refreshes; never Entered incorrectly."""
    harness, staff, rows = answered(response_service, new_member)
    change = mark(harness, staff, rows["new_member"], f" {ADDED} ")
    resolution = ProposalResolution.objects.get(proposal=change)
    assert resolution.member_duid == ADDED
    refresh(harness)
    refresh(harness)
    assert checks(proposal=change) == ["unchanged", "unchanged"]
    row = census_row(harness, change)
    assert row["stage"] == Stage.ENTERED_BY_HAND and row["not_seen"]
    (shown,) = worklist_rows([row])
    assert shown["stage_note"] == f"ParishSoft Member ID {ADDED}."
    with_added(harness, familyDUID=2)
    assert checks(proposal=change)[-1] == "unchanged"
    with_added(harness)
    assert checks(proposal=change)[-1] == "confirmed"
    assert census_row(harness, change)["stage"] == Stage.CONFIRMED


def test_a_new_member_marked_without_an_id_stays_final(response_service):
    """No Member ID, no evidence: no check, as before #1749."""
    harness, staff, rows = answered(response_service, new_member)
    change = mark(harness, staff, rows["new_member"], "")
    assert ProposalResolution.objects.get(proposal=change).member_duid is None
    with_added(harness)
    assert checks(proposal=change) == []
    assert census_row(harness, change)["stage"] == Stage.ENTERED_BY_HAND


@pytest.mark.parametrize("member_id", ["3", "x7", "0", "-7"])
def test_a_member_id_must_be_a_new_whole_number(response_service, member_id):
    """A Member the Family listed (3) cannot be the one Staff added, and the
    ID is a whole number; nothing is recorded."""
    harness, staff, rows = answered(response_service, new_member)
    with pytest.raises(InvalidMemberId):
        mark(harness, staff, rows["new_member"], member_id)
    assert not ProposalResolution.objects.exists()


def test_only_a_new_member_takes_a_member_id(response_service):
    """Python refuses a Member ID on any other change."""
    harness, staff, rows = answered(response_service, opt_out)
    with pytest.raises(ValueError, match="Only a New Member"):
        mark(harness, staff, rows["email_opt_out"], "7")
    assert not ProposalResolution.objects.exists()


def test_sql_takes_a_member_id_only_on_a_new_members_mark(response_service):
    """SQL refuses a Member ID on any other resolution, or naming a Member
    the Family's response listed."""
    harness, staff, rows = answered(response_service, new_member)
    row = rows["new_member"]
    for duid, words in ((3, "lacks current authority"), (7, "lacks current authority")):
        with (
            task_login(ServiceRole.WEB, exact=True, reconnect=True),
            pytest.raises(DatabaseError, match=words),
            transaction.atomic(),
        ):
            # Straight to the table: an existing Member, or (7) an Ignore.
            ProposalResolution.objects.create(
                proposal_id=row.pk,
                actor_id=staff,
                expected_version=row.version,
                request_key=uuid4(),
                action="entered" if duid == 3 else "ignored",
                member_duid=duid,
            )


def home(answers, mailing=None):
    """The Family gives a home address (and a different mailing one)."""
    answers["family"]["home_address"] = address()
    answers["family"]["mailing_address"] = mailing or address()
    answers["family"]["mailing_same_as_home"] = mailing is None


def test_an_entered_home_address_is_confirmed_by_the_primary_address(
    response_service,
    started,  # noqa: F811
):
    """ParishSoft's primary address confirms an entered home address,
    compared without the country or a ZIP+4. A different one is unchanged
    (the primary may be the mailing block), never Entered incorrectly; the
    mailing address is never checked."""
    harness, staff, rows = answered(response_service, home)
    change = entered(harness, staff, rows["home_address"])
    mailing = entered(harness, staff, rows["mailing_address"])
    refresh(harness, family=PRIMARY | {"primaryAddress1": "9 Elm Street"})
    assert checks(proposal=change) == ["unchanged"]
    refresh(harness, family=PRIMARY | {"primaryPostalCode": "40223-1234"})
    assert checks(proposal=change) == ["unchanged", "confirmed"]
    assert census_row(harness, change)["stage"] == Stage.CONFIRMED
    assert checks(proposal=mailing) == []


def test_a_primary_address_holding_the_mailing_answer_is_no_evidence(
    response_service,
):
    """When ParishSoft's primary address is the Family's mailing answer, the
    primary is evidently the mailing block: the home address gets no check."""
    po_box = address(line1="PO Box 9")
    harness, staff, rows = answered(response_service, lambda a: home(a, po_box))
    change = entered(harness, staff, rows["home_address"])
    refresh(harness, family=PRIMARY | {"primaryAddress1": "PO Box 9"})
    assert checks(proposal=change) == []


def test_the_queue_step_refuses_a_member_who_left(response_service):
    """The derived guard's queue branch requires the Member's current
    standing in the change's Family (#1738's check, in SQL): with Member 3
    moved in the current data, the worker cannot queue the change."""
    from parishkit.stewardship.responses.publication import plan_entity
    from parishkit.stewardship.source.leases import acquire_source, release_source

    from .source_builders import running_source_task
    from .test_publication_plans_postgresql import confirm, forge, plan
    from .test_publication_worker_postgresql import contact, worker_update

    harness, staff, rows = answered(response_service, first_name)
    from .test_census_pipeline_review_postgresql import decide
    from .test_policy_postgresql import user

    decide(harness, staff, [rows["first_name"]], "accept")
    first = fresh(rows["first_name"])
    admin = user("admin@example.org").pk
    session = forge(admin)
    entity = plan_entity("member", first.entity_key, [first], contact(first))
    plan_row = plan(admin, [first], [entity])
    confirm(harness, admin, plan_row, session)
    owner(
        "UPDATE stewardship_source_member SET family_key='2' WHERE id=("
        " SELECT payload_id FROM stewardship_snapshot_member m"
        " JOIN stewardship_source_current c ON c.snapshot_id=m.snapshot_id"
        " WHERE c.singleton AND m.source_key='3')"
    )
    claim = acquire_source(**running_source_task(), phase="publication")
    try:
        with pytest.raises(DatabaseError, match="confirmed plan and outcome"):
            worker_update(first, execution="queued")
        owner(
            "UPDATE stewardship_source_member SET family_key='1' WHERE id=("
            " SELECT payload_id FROM stewardship_snapshot_member m"
            " JOIN stewardship_source_current c ON c.snapshot_id=m.snapshot_id"
            " WHERE c.singleton AND m.source_key='3')"
        )
        assert worker_update(first, execution="queued") == 1
    finally:
        release_source(claim)


# Family 1's primary address variants the SQL twin is compared over.
ADDRESSES = {
    "full": PRIMARY | {"primaryAddress2": "Apt 2", "primaryAddress3": "Rear"},
    "one line": {"primaryAddress1": "1 Main", "primaryCity": "Louisville"},
    "none": {},
    # ParishSoft returns a blank field as null, which reads as blank.
    "nulls": {
        "primaryAddress1": "1 Main",
        "primaryAddress2": None,
        "primaryAddress3": None,
        "primaryCity": None,
        "primaryState": None,
        "primaryPostalCode": None,
    },
}


@pytest.mark.parametrize("variant", sorted(ADDRESSES))
@pytest.mark.parametrize("active", [True, False])
def test_the_sql_home_address_agrees_with_census_source(
    response_service, variant, active
):
    """Migration 0138's stewardship_refresh_census_source_v1 reads the home
    address exactly as ``refresh_checks.census_source`` does, and the
    address keys agree, so the guard never refuses a check a promotion
    owes."""
    harness, _, rows = answered(response_service, home)
    family = rows["home_address"].submission.family_id
    data = ministry_source()
    data.members[3].update(BASE)
    data.families[1].update(ADDRESSES[variant] | ({} if active else {"famGroupID": 8}))
    data.family_groups[8] = "Inactive"
    snapshot, claim = prepare(data)
    promote(snapshot, claim, harness.campaign, harness.rings)
    corpus = normalize_core(replace(data, organization_id=12345), as_of=TODAY)
    row = SimpleNamespace(
        entity_kind="family",
        entity_key="1",
        field="home_address",
        family_duid=1,
        primary_address=corpus["address"].get("family:1:primary"),
    )
    expected = census_source(row, corpus)
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT stewardship_refresh_census_source_v1(%s,%s,%s,%s,%s)",
            [snapshot.pk, family, "family", "1", "home_address"],
        )
        (found,) = cursor.fetchone()
        found = json.loads(found) if isinstance(found, str) else found
        assert found == (
            None if expected is None else {"available": True, "value": expected.value}
        ), variant
        for value in (address(), address(postal_code="40223-9999", country="")):
            cursor.execute(
                "SELECT stewardship_refresh_address_key_v1(%s::jsonb)"
                " IS NOT DISTINCT FROM stewardship_refresh_address_key_v1(%s::jsonb)",
                [json.dumps(value), json.dumps(address(country="CA"))],
            )
            python = refresh_checks._address_key(value) == refresh_checks._address_key(
                address(country="CA")
            )
            assert cursor.fetchone()[0] is python


def _check():
    """0138's closing DO block, as the file has it."""
    text = FROZEN.read_text(encoding="utf-8")
    return text[text.index("DO $check$") :]


def _body(source, name):
    """``name``'s replacement statement from frozen file ``source``."""
    text = (MIGRATIONS / source).read_text(encoding="utf-8")
    for keyword in ("CREATE OR REPLACE FUNCTION", "CREATE FUNCTION"):
        marker = f"{keyword} public.{name}("
        if marker in text:
            start = text.index(marker)
            end = text.index("$$;", text.index("$$", start) + 2) + len("$$;")
            return "CREATE OR REPLACE FUNCTION" + text[start + len(keyword) : end]
    raise AssertionError(name)


def test_do_block_refuses_the_old_definitions(db):
    """The closing check passes as installed and raises once a guard is put
    back to its earlier body, the constraint is narrowed again, or the new
    column is gone."""
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(_check())
    for statement, words in (
        (
            _body(
                "0133_entered_incorrectly_fixes.sql",
                "stewardship_response_derived_guard_v1",
            ),
            "published Conflict rules",
        ),
        (
            _body(
                "0133_entered_incorrectly_fixes.sql",
                "stewardship_proposal_resolution_guard_v1",
            ),
            "New Member ID rule",
        ),
        (
            _body(
                "0137_census_refresh_sources.sql", "stewardship_refresh_check_guard_v1"
            ),
            "refresh_check_guard_v1",
        ),
        (
            _body(
                "0133_entered_incorrectly_fixes.sql",
                "stewardship_refresh_check_guard_v1",
            ),
            "refresh_check_guard_v1",
        ),
        (
            _body(
                "0137_census_refresh_sources.sql",
                "stewardship_refresh_census_source_v1",
            ),
            "census refresh source",
        ),
        (
            "ALTER TABLE stewardship_refresh_check"
            " DROP CONSTRAINT refresh_check_outcome",
            "refresh check outcome",
        ),
        (
            "ALTER TABLE stewardship_proposal_resolution DROP COLUMN member_duid",
            "refresh check outcome or the resolution Member ID",
        ),
    ):
        with (
            pytest.raises(DatabaseError, match=words),
            transaction.atomic(),
            connection.cursor() as cursor,
        ):
            cursor.execute(statement)
            cursor.execute(_check())


def quick(harness, *, family=None, added=False, worker=False, **record):
    """Promote a quick (delta) refresh with Member 3's record changed, Family
    1's by ``family``, and Member ``ADDED`` listed in it when ``added``;
    on the source worker's own login when ``worker``."""
    from contextlib import nullcontext

    from parishkit.stewardship.source.leases import acquire_source
    from parishkit.stewardship.source.snapshots import (
        begin_snapshot,
        finish_snapshot,
        stage_entities,
    )

    from .source_builders import running_source_task
    from .test_source_snapshots_postgresql import permit

    data = ministry_source()
    data.members[3].update(BASE | record)
    data.families[1].update(family or {})
    if added:
        data.members[ADDED] = {**ministry_source().members[3], "memberDUID": ADDED}
    corpus = normalize_core(replace(data, organization_id=12345), as_of=TODAY)
    claim = acquire_source(**running_source_task(), phase="delta")
    snapshot = begin_snapshot(claim, organization_id=12345, admit=permit)
    for kind, values in corpus.items():
        stage_entities(snapshot.pk, claim, kind=kind, entities=values, admit=permit)
    finish_snapshot(
        snapshot.pk,
        claim,
        expected_counts={kind: len(rows) for kind, rows in corpus.items()},
        cursor={},
        admit=permit,
    )
    login = (
        task_login(ServiceRole.WORKER, exact=True, reconnect=True)
        if worker
        else nullcontext()
    )
    with login:
        return promote(snapshot, claim, harness.campaign, harness.rings)


def test_a_quick_update_never_reopens_an_updated_change(response_service):
    """A quick update's change list could lag the write, so it records
    nothing for an Updated in ParishSoft change it finds at the Baseline;
    the next full refresh decides."""
    harness, staff, rows = answered(response_service, first_name)
    change = published(harness, staff, rows["first_name"])
    quick(harness)
    assert checks(proposal=change) == []
    assert fresh(change).execution == "published"
    refresh(harness)
    assert checks(proposal=change) == ["conflict"]


def test_the_worker_login_promotes_a_quick_update(response_service):
    """A quick update's promotion reads a New Member's recorded ID and the
    snapshot's primary address under the worker's own grants too."""

    def edit(answers):
        """A New Member and a home address."""
        new_member(answers)
        home(answers)

    harness, staff, rows = answered(response_service, edit)
    added = mark(harness, staff, rows["new_member"], str(ADDED))
    house = entered(harness, staff, rows["home_address"])
    quick(harness, family=PRIMARY, added=True, worker=True)
    assert checks(proposal=added) == checks(proposal=house) == ["confirmed"]


def worker_refresh(harness, *, member=None, family=None, added=False):
    """A full refresh promoted on the source worker's own login (the other
    tests promote as the database owner), with Member ``ADDED`` listed in
    Family 1 when ``added``."""
    data = ministry_source()
    data.members[3].update(BASE | (member or {}))
    data.families[1].update(family or {})
    if added:
        data.members[ADDED] = {**ministry_source().members[3], "memberDUID": ADDED}
    snapshot, claim = prepare(data)
    with task_login(ServiceRole.WORKER, exact=True, reconnect=True):
        return promote(snapshot, claim, harness.campaign, harness.rings)


def test_the_worker_login_moves_an_updated_change_to_conflict(response_service):
    """The worker's own grants carry the conflict check and the move, and
    the census guard's worker branch admits it only To review again."""
    harness, staff, rows = answered(response_service, first_name)
    change = published(harness, staff, rows["first_name"])
    worker_refresh(harness)
    assert checks(proposal=change) == ["conflict"]
    moved = fresh(change)
    assert (moved.execution, moved.decision) == ("conflict", "unreviewed")


def test_the_worker_login_checks_new_members_and_home_addresses(response_service):
    """The worker reads a New Member's recorded ID and a home address's
    mailing answer and primary address under its grants: one New Member
    entered with an ID and one without, and a home address."""

    def edit(answers):
        """Two New Members and a home address."""
        new_member(answers)
        new_member(answers)
        home(answers, address(line1="PO Box 9"))

    harness, staff, rows = answered(response_service, edit)
    first, second = ProposedChange.objects.filter(field="new_member").order_by("pk")
    with_id = mark(harness, staff, first, str(ADDED))
    without = mark(harness, staff, second, "")
    house = entered(harness, staff, rows["home_address"])
    worker_refresh(harness, family=PRIMARY, added=True)
    assert checks(proposal=with_id) == ["confirmed"]
    assert checks(proposal=without) == []
    assert checks(proposal=house) == ["confirmed"]


def test_two_new_members_cannot_share_a_member_id(response_service):
    """Two proposed Members of one Family cannot be one ParishSoft Member;
    leading zeros are the same ID."""

    def edit(answers):
        """Two New Members."""
        new_member(answers)
        new_member(answers)

    harness, staff, _ = answered(response_service, edit)
    first, second = ProposedChange.objects.filter(field="new_member").order_by("pk")
    assert mark(harness, staff, first, "0007")
    assert ProposalResolution.objects.get(proposal=first).member_duid == ADDED
    with pytest.raises(InvalidMemberId, match="Another New Member"):
        mark(harness, staff, second, "7")
    second = fresh(second)
    with (
        task_login(ServiceRole.WEB, exact=True, reconnect=True),
        pytest.raises(DatabaseError, match="lacks current authority"),
        transaction.atomic(),
    ):
        ProposalResolution.objects.create(
            proposal_id=second.pk,
            actor_id=staff,
            expected_version=second.version,
            request_key=uuid4(),
            action="entered",
            member_duid=ADDED,
        )


def test_a_quick_update_reads_the_primary_address_from_its_corpus(
    response_service,
):
    """The quick-update check runs before the update stages any address, so
    it reads the base corpus's addresses: an entered home address
    ParishSoft now holds is owed a check."""
    from parishkit.stewardship.campaigns.work_locks import work_transaction
    from parishkit.stewardship.responses.refresh_checks import (
        refresh_checks_current,
    )

    harness, staff, rows = answered(response_service, home)
    entered(harness, staff, rows["home_address"])
    data = ministry_source()
    data.members[3].update(BASE)
    data.families[1].update(PRIMARY)
    corpus = normalize_core(replace(data, organization_id=12345), as_of=TODAY)
    snapshot = SimpleNamespace(pk=uuid4())
    with work_transaction():
        assert not refresh_checks_current(
            snapshot, corpus, campaign_id=harness.campaign.pk
        )
