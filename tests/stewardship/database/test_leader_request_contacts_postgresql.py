"""Ministry leaders see a requesting Member's own contact details (#1591).

The Administrator decided (2026-10-17, "show own contacts") that a Member's
standing request to a Ministry is consent to be contacted about it, so that
Ministry's leaders see the requesting Member's own email and phones even when
ParishSoft does not publish them. Frozen migration 0117 puts the rule in the
two selections the request page, Export these requests and the packet read.

The first tests check 0117's closing check against 0094's bodies, as the
earlier selection migrations' tests do; each statement there runs in a
transaction that is rolled back. The rest prove, through the real web
database role, that nothing else widens: another Member of the Family, a
withdrawn request, a leave because the Member cannot participate in any
ministries (Administrator decision, 2026-10-18), another Ministry and a
reader who leads nothing keep the publish flags or read nothing, while Staff
keep operational contact.
"""

import io
import json
import re
from uuid import uuid4

import pytest
from django.db import DatabaseError, connection, transaction

from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.reports.export_tasks import load_document
from parishkit.stewardship.reports.ministry_packets import render_packet

from ..census_factory import member
from .response_builders import activate_response_service
from .test_background_grants_postgresql import task_login
from .test_hide_gone_members_postgresql import SCHEMA_FILES, definition
from .test_ministry_packets_postgresql import packet, sections
from .test_ministry_reports_postgresql import actor, page, prepare
from .test_ministry_responses_postgresql import (
    ministry_source,
    respond,
    revisit,
    start,
)
from .test_response_http_postgresql import answers_for, load_form
from .test_source_families_postgresql import promote

pytestmark = pytest.mark.django_db(transaction=True)

MIGRATION = (SCHEMA_FILES / "migrations/0117_leader_request_contacts.sql").read_text(
    encoding="utf-8"
)
EARLIER = (SCHEMA_FILES / "migrations/0094_source_status_names.sql").read_text(
    encoding="utf-8"
)
CHECK = MIGRATION[MIGRATION.index("DO $check$") :]
# The statement recording the replaced selections' grants, which the check
# compares against (a transaction-local setting).
ACL = MIGRATION[
    MIGRATION.index("SELECT set_config('stewardship.migration_0117_acl'") : (
        MIGRATION.index(", true);", MIGRATION.index("migration_0117_acl"))
        + len(", true);")
    )
]
REPLACED = re.findall(r"^CREATE OR REPLACE FUNCTION public\.(\w+)\(", MIGRATION, re.M)

# Member 3's unpublished email (the harness source publishes the phone).
EMAIL = "valid@example.org"
PHONE = "202-555-0123"
# The email the Family enters for the Member it adds on the form.
PROPOSED = "proposed@example.org"
LEADER_4 = dict(roles=("ministry_leader",), scope=(4,))
LEADER_9 = dict(roles=("ministry_leader",), scope=(9,))


def check_refuses(statement, message):
    """Run ``statement`` and then 0117's closing check, which must refuse it."""
    with (
        pytest.raises(DatabaseError, match=f"Migration 0117 {message}"),
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
    """A selection left on its 0094 body (publish flags only) is refused."""
    earlier = definition(EARLIER, name)
    assert "consented" not in earlier
    check_refuses(earlier, "\\(leaders see")


@pytest.mark.parametrize("kind", ["email", "phone"])
@pytest.mark.parametrize("name", REPLACED)
def test_the_closing_check_refuses_a_half_applied_rule(name, kind):
    """Consent in the visibility but not the value, or the reverse, is
    refused, so a leader never reads "available" beside a missing value or a
    value marked Not published."""
    own = definition(MIGRATION, name)
    for pattern, earlier in (
        (
            rf"operational OR r\.consented\n\s+OR c\.value->'publish_{kind}'",
            f"operational OR c.value->'publish_{kind}'",
        ),
        (
            rf"NOT operational AND NOT r\.consented AND coalesce\(\n"
            rf"(\s+c\.value->'publish_{kind}')",
            r"NOT operational AND coalesce(\n\1",
        ),
    ):
        changed, count = re.subn(pattern, earlier, own)
        assert count == 1, pattern
        check_refuses(changed, "\\(leaders see")


def test_the_closing_check_refuses_a_packet_without_the_exclusion():
    """A packet whose consent forgets the cannot-participate exclusion is
    refused, so such a leave never shows unpublished contacts."""
    own = definition(MIGRATION, "stewardship_ministry_packet_v1")
    changed, count = re.subn(
        r"r\.state NOT IN \('cancelled','superseded'\) AND coalesce\(.*?AS consented,",
        "r.state NOT IN ('cancelled','superseded') AS consented,",
        own,
        flags=re.S,
    )
    assert count == 1
    check_refuses(changed, "\\(leaders see")


def test_the_closing_check_refuses_changed_grants():
    """CREATE OR REPLACE keeps grants; a change to one is refused."""
    check_refuses(
        "REVOKE ALL ON FUNCTION public.stewardship_ministry_packet_v1"
        "(uuid, bigint[], boolean, boolean, bigint[]) FROM PUBLIC",
        "changed the selections' grants",
    )


def test_the_migration_installs_over_the_earlier_definitions():
    """Run whole over the 0094 state, the frozen file installs and checks.

    Rolled back afterwards, so later tests see the installed schema.
    """
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute("SET LOCAL check_function_bodies = false")
        for name in REPLACED:
            cursor.execute(definition(EARLIER, name))
        cursor.execute(MIGRATION)
        cursor.execute(ACL)
        cursor.execute(CHECK)
        transaction.set_rollback(True)


def family(harness, *, cannot_serve=False):
    """One Family's live requests: Member 3 asks to leave Choir (4) only
    (with ``cannot_serve``, because they cannot participate in any
    ministries), and a Member the Family adds on the form asks to join Food
    pantry (9)."""
    start(harness)
    snapshot, claim = prepare(ministry_source())
    promote(snapshot, claim, harness.campaign, harness.rings)
    harness = activate_response_service(harness)
    form = load_form(harness)
    answers = answers_for(form)
    added = str(uuid4())
    answers["proposed_members"][added] = member(
        first_name="New", last_name="Person", email=PROPOSED
    )
    answers["ministries"]["proposed_members"][added] = {"join": [9]}
    answers["ministries"]["members"]["3"] = {"join": [], "leave": [4]}
    if cannot_serve:
        answers["service"] = {
            "members": {"3": {"cannot_serve": True}},
            "proposed_members": {added: {"cannot_serve": False}},
        }
    respond(harness, form, answers)
    return harness, added


def packet_text(harness, request):
    """The CSV the worker renders from one packet's retained capture."""
    request.refresh_from_db()
    with task_login(ServiceRole.WORKER, exact=True, reconnect=True):
        output = io.BytesIO()
        render_packet(load_document(request), output, format="csv")
    return output.getvalue().decode()


def test_each_leader_sees_only_their_own_ministrys_requesters(response_service):
    """Food pantry's leader sees the added Member's entered email, never Member
    3's; Choir's leader sees Member 3's unpublished email on the leave request,
    never the added Member's; Staff see both; a reader who leads nothing and a
    leader asking for another Ministry read nothing."""
    harness, added = family(response_service)
    # Food pantry (9): the join request page's selection and the packet.
    rows = page(harness, ministry=9, **LEADER_9)["rows"]
    assert [(row["proposed_id"], row["emails"]) for row in rows] == [
        (added, [PROPOSED])
    ]
    assert EMAIL not in json.dumps(rows, default=str)
    request = packet(harness, actor(harness, **LEADER_9).identity)
    snapshot, found = sections(request)
    assert sorted(found) == [9]
    assert snapshot.authorization_scope["operational"] is False
    assert EMAIL not in json.dumps(snapshot.document)
    assert PROPOSED in packet_text(harness, request)
    # Choir (4): Member 3 asked to leave, so its leader sees the email too.
    request = packet(harness, actor(harness, **LEADER_4).identity)
    snapshot, found = sections(request)
    (row,) = found[4]["rows"]
    assert (row["action"], row["email_visibility"]) == ("leave", "available")
    assert EMAIL in json.dumps(row["emails"]) and PHONE in json.dumps(row["phones"])
    assert PROPOSED not in json.dumps(snapshot.document)
    text = packet_text(harness, request)
    assert EMAIL in text and "Not published" not in text
    # Neither leader reads the other's Ministry, and leading nothing reads
    # nothing.
    with pytest.raises(PermissionError):
        page(harness, ministry=4, action="leave", **LEADER_9)
    with pytest.raises(PermissionError):
        packet(harness, actor(harness, **LEADER_4).identity, (9,))
    with pytest.raises(PermissionError):
        page(harness, ministry=9, roles=("ministry_leader",), scope=(123,))
    # Staff are unchanged: operational contact for every Ministry.
    staff = actor(harness, ("staff",), ())
    snapshot, found = sections(packet(harness, staff.identity))
    assert snapshot.authorization_scope["operational"] is True
    assert EMAIL in json.dumps(found[4]["rows"]) and PROPOSED in json.dumps(
        found[9]["rows"]
    )


def test_a_withdrawn_request_keeps_the_publish_flags(response_service):
    """Once the Family withdraws Member 3's leave request, Choir's leader sees
    its history with the email Not published again (the published phone
    stays), in the packet and its file; Staff still see the email."""
    harness, _ = family(response_service)
    form = revisit(harness)
    answers = answers_for(form)
    answers["ministries"]["members"]["3"] = {"join": [], "leave": []}
    respond(harness, form, answers)
    leader = actor(harness, **LEADER_4).identity
    # By default a packet lists unresolved requests only, so nothing shows.
    assert sections(packet(harness, leader))[1][4]["rows"] == []
    request = packet(harness, leader, history=True)
    snapshot, found = sections(request)
    (row,) = found[4]["rows"]
    assert row["state"] == "cancelled"
    assert row["email_visibility"] == "not_published" and row["emails"] is None
    assert row["phone_visibility"] == "available"
    assert EMAIL not in json.dumps(snapshot.document)
    text = packet_text(harness, request)
    assert "Not published" in text and EMAIL not in text
    staff = actor(harness, ("staff",), ()).identity
    found = sections(packet(harness, staff, history=True))[1]
    assert EMAIL in json.dumps(found[4]["rows"])


def test_a_cannot_participate_leave_keeps_the_publish_flags(response_service):
    """Member 3's leave because they cannot participate in any ministries
    shows Choir's leader the email Not published (the published phone stays),
    in the packet and its file, while the added Member's ordinary join still
    shows Food pantry's leader the entered email; Staff still see both."""
    harness, added = family(response_service, cannot_serve=True)
    request = packet(harness, actor(harness, **LEADER_4).identity)
    snapshot, found = sections(request)
    (row,) = found[4]["rows"]
    assert (row["action"], row["state"]) == ("leave", "new")
    assert row["email_visibility"] == "not_published" and row["emails"] is None
    assert row["phone_visibility"] == "available"
    assert EMAIL not in json.dumps(snapshot.document)
    text = packet_text(harness, request)
    assert "Not published" in text and EMAIL not in text
    rows = page(harness, ministry=9, **LEADER_9)["rows"]
    assert [(row["proposed_id"], row["emails"]) for row in rows] == [
        (added, [PROPOSED])
    ]
    found = sections(packet(harness, actor(harness, ("staff",), ()).identity))[1]
    assert EMAIL in json.dumps(found[4]["rows"])
