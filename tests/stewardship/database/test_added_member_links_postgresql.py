"""Added Member links for the cannot-participate contact rule (#1670).

A Member the Family adds on the form has only the form's local UUID. Once
Staff create the person in ParishSoft and mark the New Member change Entered
by hand, the next promotion links the two keys
(``responses/added_members.py``, frozen migration 0141), and the Ministry
selections read them as one person. These tests run real promotions and
the leader's selections, and check 0141's guard and closing check.
"""

import json
import re
from datetime import date
from uuid import uuid4

import pytest
from django.db import DatabaseError, connection, transaction

from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.responses import added_members
from parishkit.stewardship.responses.added_members import added_members_current
from parishkit.stewardship.responses.models import AddedMemberLink, ProposedChange
from parishkit.stewardship.source.leases import release_source
from parishkit.stewardship.workflows.models import MinistryRequest

from ..census_factory import member
from .test_background_grants_postgresql import task_login
from .test_census_resolution_postgresql import resolve
from .test_hide_gone_members_postgresql import SCHEMA_FILES, definition
from .test_ministry_exports_postgresql import leader
from .test_ministry_packets_postgresql import packet, sections
from .test_ministry_reports_postgresql import page, prepare, setup
from .test_ministry_responses_postgresql import ministry_source, respond, revisit
from .test_policy_postgresql import user
from .test_response_http_postgresql import answers_for
from .test_source_families_postgresql import promote

pytestmark = pytest.mark.django_db(transaction=True)

# The email the Family types for the Member it adds.
TYPED = "added@example.org"


def added(harness, *, cannot_serve=False):
    """The Family adds a Member with an email who asks to join Food pantry (9),
    or, with ``cannot_serve``, who cannot participate (so joins nothing)."""
    form = revisit(harness)
    answers = answers_for(form)
    key = str(uuid4())
    answers["proposed_members"][key] = member(
        first_name="New", last_name="Person", email=TYPED
    )
    answers["ministries"]["proposed_members"][key] = {
        "join": [] if cannot_serve else [9]
    }
    if cannot_serve:
        answers["service"] = {
            "members": {"3": {"cannot_serve": False}},
            "proposed_members": {key: {"cannot_serve": True}},
        }
    respond(harness, form, answers)
    return key


def created(harness, *duids, status="Active", login=None):
    """Promote ParishSoft with these new Members in the harness Family.

    ``status`` is their ParishSoft member status; ``login`` runs the
    promotion on that service's own database login.
    """
    data = ministry_source()
    for duid in duids:
        data.members[duid] = {
            "memberDUID": duid,
            "familyDUID": 1,
            "firstName": "New",
            "lastName": "Person",
            "memberType": "Child",
            "memberStatus": status,
            "birthdate": date(2010, 1, 1),
            "sex": "Unspecified",
            "language": "English",
        }
    snapshot, claim = prepare(data)
    try:
        if login is None:
            promote(snapshot, claim, harness.campaign, harness.rings)
        else:
            with task_login(login, exact=True, reconnect=True):
                promote(snapshot, claim, harness.campaign, harness.rings)
    except DatabaseError:
        # A refused promotion still gives the source lease back, as the
        # refresh task would, so the next promotion can claim it.
        release_source(claim)
        raise
    return snapshot


def entered(harness, key):
    """Staff mark the added Member's New Member change Entered by hand."""
    proposal = ProposedChange.objects.exclude(
        execution__in=["superseded", "cancelled"]
    ).get(entity_kind="proposed_member", entity_key=key, field="new_member")
    resolve(harness, user("admin@example.org").pk, proposal, "entered")
    return proposal


def leader_rows(harness):
    """Food pantry's join rows, with history, as its leader reads them."""
    return page(
        harness,
        roles=("ministry_leader",),
        scope=(9,),
        ministry=9,
        action="join",
        history="all",
        state="any",
    )["rows"]


def cannot_participate(harness, duid):
    """The Family answers again and checks the physical-limitations box for
    ParishSoft Member ``duid`` (on no roster, so nothing to leave)."""
    form = revisit(harness)
    answers = answers_for(form)
    assert answers["proposed_members"] == {}
    answers["service"] = {
        "members": {"3": {"cannot_serve": False}, str(duid): {"cannot_serve": True}},
        "proposed_members": {},
    }
    respond(harness, form, answers)


def added_join(harness, key):
    """The leader's row for the added Member's join, read with history."""
    request = MinistryRequest.objects.get(entity_kind="proposed_member", entity_key=key)
    (row,) = [row for row in leader_rows(harness) if row["id"] == str(request.pk)]
    return row


def test_a_later_answer_for_the_linked_member_hides_the_typed_contact(
    response_service, google
):
    """The Family adds a Member with an email who asks to join Food pantry;
    Staff create them in ParishSoft (Member 8) and mark the New Member
    Entered by hand; the next refresh links the two keys. When the Family
    then checks cannot participate for Member 8, the added Member's join no
    longer shares the typed email with Food pantry's leader, in the report
    selection or the packet (#1670)."""
    harness = setup(response_service)
    _, head, _, _ = leader(harness, google)
    key = added(harness)
    proposal = entered(harness, key)
    snapshot = created(harness, 8)
    (link,) = AddedMemberLink.objects.all()
    assert (link.proposed_key, link.member_key, link.proposal_id, link.snapshot_id) == (
        key,
        "8",
        proposal.pk,
        snapshot.pk,
    )
    assert link.family_id == proposal.submission.family_id and link.actor_id is None
    # Linked, but nothing answered for Member 8 yet: the request still shares.
    row = added_join(harness, key)
    assert row["emails"] == [TYPED] and row["email_visibility"] == "available"
    # A refresh again records nothing more: the link is the evidence's one.
    created(harness, 8)
    assert AddedMemberLink.objects.count() == 1
    cannot_participate(harness, 8)
    row = added_join(harness, key)
    assert row["emails"] == [] and TYPED not in json.dumps(row, default=str)
    _, found = sections(packet(harness, head, history=True))
    assert TYPED not in json.dumps(found, default=str)


def test_no_link_before_entered_by_hand(response_service):
    """A new ParishSoft Member alone is not evidence: until Staff mark the
    New Member Entered by hand there is no link, and the quick update's skip
    check then sees one waiting, so it promotes."""
    harness = setup(response_service)
    key = added(harness)
    snapshot = created(harness, 8)
    assert not AddedMemberLink.objects.exists()
    assert added_members_current(snapshot.pk, campaign_id=harness.campaign.pk)
    entered(harness, key)
    assert not added_members_current(snapshot.pk, campaign_id=harness.campaign.pk)
    created(harness, 8)
    assert AddedMemberLink.objects.get().member_key == "8"


def test_an_ambiguous_family_gets_no_link(response_service):
    """Two new ParishSoft Members for one Entered by hand added Member: no
    link, and the request keeps the Family's own answer for the person."""
    harness = setup(response_service)
    key = added(harness)
    entered(harness, key)
    created(harness, 8, 10)
    assert not AddedMemberLink.objects.exists()
    assert added_join(harness, key)["emails"] == [TYPED]


def test_a_member_on_a_later_response_before_the_mark_is_still_linked(
    response_service,
):
    """Staff create Member 8 and a refresh shows them before the New Member
    is marked; the Family answers again in between, listing Member 8 in its
    household beside the still pending added Member. The household check
    stops at the response that first added the person, so once Staff mark
    it Entered by hand the next refresh links Member 8."""
    harness = setup(response_service)
    key = added(harness)
    created(harness, 8)
    form = revisit(harness)
    answers = answers_for(form)
    assert "8" in answers["members"] and key in answers["proposed_members"]
    respond(harness, form, answers)
    entered(harness, key)
    created(harness, 8)
    assert AddedMemberLink.objects.get().member_key == "8"


def test_two_entered_added_members_get_no_link(response_service):
    """Two Entered by hand added Members and one new ParishSoft Member: which
    one it is is unknown, so neither is linked."""
    harness = setup(response_service)
    first = added(harness)
    second = added(harness)
    entered(harness, first)
    entered(harness, second)
    created(harness, 8)
    assert not AddedMemberLink.objects.exists()


@pytest.mark.parametrize("status", ["Inactive", "Deceased"])
def test_an_inactive_or_deceased_new_member_is_not_linked(response_service, status):
    """Only an active, living ParishSoft Member can be the added person."""
    harness = setup(response_service)
    entered(harness, added(harness))
    created(harness, 8, status=status)
    assert not AddedMemberLink.objects.exists()


def test_the_added_members_checked_box_also_withholds_the_linked_members_requests(
    response_service,
):
    """The Family adds a Member who cannot participate; once linked, the
    ParishSoft Member's own later join (answered without the box) still
    follows the publish flags: a link never lets a request share that would
    not share without it, so a wrong link can only hide more."""
    harness = setup(response_service)
    key = added(harness, cannot_serve=True)
    entered(harness, key)
    created(harness, 8)
    assert AddedMemberLink.objects.get().member_key == "8"
    form = revisit(harness)
    answers = answers_for(form)
    answers["ministries"]["members"]["8"] = {"join": [9], "leave": []}
    answers["service"] = {
        "members": {"3": {"cannot_serve": False}, "8": {"cannot_serve": False}},
        "proposed_members": {},
    }
    respond(harness, form, answers)
    request = MinistryRequest.objects.get(
        entity_kind="member", entity_key="8", action="join"
    )
    (row,) = [row for row in leader_rows(harness) if row["id"] == str(request.pk)]
    assert row["email_visibility"] == "not_published" and row["emails"] == []


def test_a_forged_link_is_refused_inside_promotion(response_service, monkeypatch):
    """Inside a real promotion, a link the evidence does not give (another
    Member, another local id, another change) is refused by the guard, and
    the next promotion records the right one."""
    harness = setup(response_service)
    key = added(harness)
    proposal = entered(harness, key)
    real = added_members._candidates
    for forged in (
        lambda row: (row[0], row[1], row[2], "3"),
        lambda row: (row[0], row[1], str(uuid4()), row[3]),
        lambda row: (row[0], uuid4(), row[2], row[3]),
    ):
        monkeypatch.setattr(
            added_members,
            "_candidates",
            lambda *args, forged=forged: [forged(row) for row in real(*args)],
        )
        with pytest.raises(DatabaseError, match="requires its evidence"):
            created(harness, 8)
        assert not AddedMemberLink.objects.exists()
    monkeypatch.setattr(added_members, "_candidates", real)
    created(harness, 8)
    assert AddedMemberLink.objects.get().proposal_id == proposal.pk


def test_the_worker_records_the_link_on_its_own_login(response_service):
    """The general worker's own login runs the promotion: its EXECUTE on the
    candidates function, INSERT on links and the invoker guard all hold."""
    harness = setup(response_service)
    entered(harness, added(harness))
    created(harness, 8, login=ServiceRole.WORKER)
    assert AddedMemberLink.objects.get().member_key == "8"


def test_a_member_already_in_the_household_is_not_linked(response_service):
    """Member 3 was in the household when the person was added, so even as
    the only Member the snapshot shows, it is never their link."""
    harness = setup(response_service)
    key = added(harness)
    entered(harness, key)
    created(harness)
    assert not AddedMemberLink.objects.exists()


def test_the_guard_refuses_links_outside_promotion_and_any_change(response_service):
    """A link only inside the promotion of the current snapshot (the owner
    check), and never updated or deleted."""
    harness = setup(response_service)
    key = added(harness)
    proposal = entered(harness, key)
    snapshot = created(harness, 8)
    link = AddedMemberLink.objects.get()
    with (
        pytest.raises(DatabaseError, match="require the promoting source worker"),
        transaction.atomic(),
    ):
        AddedMemberLink.objects.create(
            family_id=link.family_id,
            proposal_id=proposal.pk,
            proposed_key=str(uuid4()),
            member_key="10",
            snapshot_id=snapshot.pk,
        )
    for statement in (
        "UPDATE stewardship_added_member_link SET member_key='10'",
        "DELETE FROM stewardship_added_member_link",
    ):
        with (
            pytest.raises(DatabaseError, match="Added Member links are immutable"),
            transaction.atomic(),
            connection.cursor() as cursor,
        ):
            cursor.execute(statement)
    assert AddedMemberLink.objects.get().member_key == "8"


def test_only_the_worker_may_ask_for_candidates(response_service):
    """Web has no EXECUTE on the definer function, nor any write on links."""
    harness = setup(response_service)
    with (
        task_login(ServiceRole.WEB, exact=True, reconnect=True),
        pytest.raises(DatabaseError, match="permission denied"),
        transaction.atomic(),
        connection.cursor() as cursor,
    ):
        cursor.execute(
            "SELECT * FROM public.stewardship_added_member_candidates_v1(%s, %s)",
            [uuid4(), harness.campaign.pk],
        )


MIGRATION = (SCHEMA_FILES / "migrations/0141_added_member_links.sql").read_text(
    encoding="utf-8"
)
EARLIER = (SCHEMA_FILES / "migrations/0140_edited_ministry_contacts.sql").read_text(
    encoding="utf-8"
)
CHECK = MIGRATION[MIGRATION.index("DO $check$") :]
ACL = MIGRATION[
    MIGRATION.index("SELECT set_config('stewardship.migration_0141_acl'") : (
        MIGRATION.index(", true);", MIGRATION.index("migration_0141_acl"))
        + len(", true);")
    )
]
REPLACED = re.findall(r"^CREATE OR REPLACE FUNCTION public\.(\w+)\(", MIGRATION, re.M)
CANDIDATES = "public.stewardship_added_member_candidates_v1(uuid, uuid)"


def check_refuses(statement, message):
    """Run ``statement`` and then 0141's closing check, which must refuse it."""
    with (
        pytest.raises(DatabaseError, match=message),
        transaction.atomic(),
        connection.cursor() as cursor,
    ):
        cursor.execute("SET LOCAL check_function_bodies = false")
        cursor.execute(ACL)
        cursor.execute(statement)
        cursor.execute(CHECK)


def test_the_closing_check_passes_on_the_installed_schema():
    """The check is the one the migration ran; it still holds afterwards."""
    assert sorted(REPLACED) == [
        "stewardship_ministry_packet_v1",
        "stewardship_ministry_report_v3",
    ]
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(ACL)
        cursor.execute(CHECK)
        transaction.set_rollback(True)


@pytest.mark.parametrize("name", REPLACED)
def test_the_closing_check_refuses_each_earlier_body(name):
    """A selection left on its 0140 body (no links read) is refused."""
    earlier = definition(EARLIER, name)
    assert "stewardship_added_member_link" not in earlier
    check_refuses(earlier, "Migration 0141 \\(added Member links\\)")


@pytest.mark.parametrize(
    ("statement", "message"),
    [
        (
            f"ALTER FUNCTION {CANDIDATES} SECURITY INVOKER",
            "candidates function is not installed",
        ),
        (
            f"GRANT EXECUTE ON FUNCTION {CANDIDATES} TO PUBLIC",
            "candidates function is not installed",
        ),
        (
            "ALTER TABLE stewardship_added_member_link "
            "DISABLE TRIGGER stewardship_added_member_link_guard",
            "not guarded as declared",
        ),
        (
            "ALTER TABLE stewardship_added_member_link "
            "DROP CONSTRAINT added_member_link_member_once",
            "lacks its checks",
        ),
        (
            "GRANT SELECT ON stewardship_added_member_link TO PUBLIC",
            "granted to PUBLIC",
        ),
        (
            "REVOKE ALL ON FUNCTION public.stewardship_ministry_packet_v1"
            "(uuid, bigint[], boolean, boolean, bigint[]) FROM PUBLIC",
            "changed the selections' grants",
        ),
    ],
)
def test_the_closing_check_refuses_a_changed_install(statement, message):
    """A definer made invoker, PUBLIC execute, a disabled guard, a dropped
    constraint and changed selection grants are each refused."""
    check_refuses(statement, message)
