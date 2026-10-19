"""Census additions: the service and its tables (#1710).

Staff and Administrators add a census change no Family answer asked for,
checked as an Edit's value is and against the Family, the Member and the
field's open changes; its one later step is Entered by hand or withdrawn.
The frozen file 0137 creates the tables; their guards are tested here with
the service bypassed. The worklist's side is test_census_add_page.
"""

from pathlib import Path
from uuid import uuid4

import pytest
from django.db import DatabaseError, IntegrityError, connection, transaction

from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.responses.census_additions import (
    AddRefusal,
    KeyRebound,
    add_census_change,
    parse_add,
    resolve_census_additions,
)
from parishkit.stewardship.responses.census_review import BulkStale
from parishkit.stewardship.responses.models import (
    CensusAddition,
    CensusAdditionResolution,
)

from ..census_factory import address
from .test_background_grants_postgresql import task_login
from .test_census_pipeline_review_postgresql import decide, prepared
from .test_census_resolution_postgresql import leader

pytestmark = pytest.mark.django_db(transaction=True)

FROZEN = (
    Path(__file__).resolve().parents[3]
    / "src/parishkit/stewardship/schema/migrations/0137_census_additions.sql"
)


def family_duid(rows):
    """The prepared response's Family DUID."""
    return next(iter(rows.values())).submission.family.family_duid


def member_form(rows, field, value, *, member="3", in_place_of=""):
    """The Add dialog's fields for a Member field."""
    return {
        "value_field": [field],
        "value_family": [str(family_duid(rows))],
        "value_member": [member],
        f"value_{field}": [value],
        "value_in_place_of": [in_place_of],
    }


def address_form(rows, field="mailing_address", **parts):
    """The Add dialog's fields for a Family address."""
    return {
        "value_field": [field],
        "value_family": [str(family_duid(rows))],
        "value_in_place_of": [""],
        **{f"value_{part}": [text] for part, text in address(**parts).items()},
    }


def add(harness, actor, campaign, fields, *, note="", key=None):
    """One Add through the service on the restricted web login."""
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        return add_census_change(
            harness.service.store,
            actor,
            campaign,
            parse_add(fields),
            note=note,
            request_key=key or uuid4(),
        )


def campaign_of(rows):
    """The prepared response's campaign."""
    return next(iter(rows.values())).submission.campaign_id


def test_an_address_add_follows_the_familys_address_rules(response_service):
    """A Family address is read as the Family form reads one: a refused part
    is named, and a good one is stored in the census form."""
    harness, _, staff, rows = prepared(response_service)
    campaign = campaign_of(rows)
    with pytest.raises(AddRefusal) as refused:
        add(harness, staff, campaign, address_form(rows, postal_code="402"))
    assert refused.value.field_ids == ("value_postal_code",)
    assert refused.value.code == "add_value"
    addition, _ = add(harness, staff, campaign, address_form(rows, line1="5 New St"))
    assert addition.entity_kind == "family"
    # A Family address is never read from ParishSoft: no known baseline.
    assert CensusAddition.objects.get(pk=addition.pk).baseline == {
        "available": False,
        "value": None,
    }
    assert addition.entity_key == str(family_duid(rows))
    assert addition.value["line1"] == "5 New St"


@pytest.mark.parametrize(
    ("change", "code", "field_id"),
    [
        ({"value_family": ["999999"]}, "add_no_response", "value_family"),
        ({"value_family": ["x"]}, "add_family", "value_family"),
        ({"value_member": ["987654"]}, "add_not_in_parishsoft", "value_member"),
        ({"value_mobile_phone": ["555"]}, "add_value", "value_mobile_phone"),
    ],
)
def test_an_add_the_reader_can_correct_is_refused_at_its_field(
    response_service, change, code, field_id
):
    """A Family that did not respond, a Member ParishSoft does not list there
    and a value Edit would refuse are each refused at their own field."""
    harness, _, staff, rows = prepared(response_service)
    fields = member_form(rows, "mobile_phone", "317-555-0100") | change
    with pytest.raises(AddRefusal) as refused:
        add(harness, staff, campaign_of(rows), fields)
    assert (refused.value.code, refused.value.field_ids) == (code, (field_id,))
    assert CensusAddition.objects.count() == 0


def test_an_open_change_of_the_field_refuses_a_second(response_service):
    """The Family's open change of the field is edited, not added to; an open
    addition likewise. Once the Family's is ignored, Add may name it."""
    harness, _, staff, rows = prepared(response_service)
    campaign = campaign_of(rows)
    with pytest.raises(AddRefusal) as refused:
        add(harness, staff, campaign, member_form(rows, "first_name", "Robert"))
    assert refused.value.code == "add_exists"
    with pytest.raises(AddRefusal) as refused:
        add(
            harness,
            staff,
            campaign,
            member_form(rows, "nickname", "Bob", in_place_of=str(rows["prefix"].pk)),
        )
    assert refused.value.code == "add_in_place_of"
    decide(harness, staff, [rows["first_name"]], "ignore")
    addition, _ = add(
        harness,
        staff,
        campaign,
        member_form(
            rows, "first_name", "Robert", in_place_of=str(rows["first_name"].pk)
        ),
    )
    assert addition.in_place_of_id == rows["first_name"].pk
    with pytest.raises(AddRefusal) as refused:
        add(harness, staff, campaign, member_form(rows, "first_name", "Rob"))
    assert refused.value.code == "add_exists"


def test_a_resolution_is_final_and_frees_the_field_when_withdrawn(response_service):
    """Entered by hand or withdrawn is one step: another is stale. Once one
    is withdrawn, the field may be added again; a replay records nothing."""
    harness, _, staff, rows = prepared(response_service)
    campaign = campaign_of(rows)
    first, _ = add(harness, staff, campaign, member_form(rows, "nickname", "Bo"))
    key = uuid4()
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        store = harness.service.store
        (entered,) = resolve_census_additions(
            store, staff, campaign, {first.pk: 1}, "entered", note="", request_key=key
        )
        (again,) = resolve_census_additions(
            store, staff, campaign, {first.pk: 1}, "entered", note="", request_key=key
        )
        assert again.pk == entered.pk
        with pytest.raises(BulkStale):
            resolve_census_additions(
                store,
                staff,
                campaign,
                {first.pk: 1},
                "withdrawn",
                note="",
                request_key=uuid4(),
            )
    second, _ = add(harness, staff, campaign, member_form(rows, "suffix", "Jr."))
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        resolve_census_additions(
            harness.service.store,
            staff,
            campaign,
            {second.pk: 1},
            "withdrawn",
            note="Wrong Member",
            request_key=uuid4(),
        )
    third, _ = add(harness, staff, campaign, member_form(rows, "suffix", "Sr."))
    assert third.pk != second.pk
    assert CensusAdditionResolution.objects.count() == 2


def test_a_repeated_add_replays_and_a_rebound_key_is_refused(response_service):
    """The same request key returns the first addition; with another Add it
    is refused and nothing more is written."""
    harness, _, staff, rows = prepared(response_service)
    campaign, key = campaign_of(rows), uuid4()
    first, created = add(
        harness, staff, campaign, member_form(rows, "nickname", "Bo"), key=key
    )
    again, created_again = add(
        harness, staff, campaign, member_form(rows, "nickname", "Bo"), key=key
    )
    assert created and not created_again and again.pk == first.pk
    with pytest.raises(KeyRebound):
        add(harness, staff, campaign, member_form(rows, "suffix", "Jr."), key=key)
    assert CensusAddition.objects.count() == 1


def forge(sql, params):
    """Run ``sql`` as the web login in one work transaction, bypassing the service."""
    with (
        task_login(ServiceRole.WEB, exact=True, reconnect=True),
        work_transaction(),
        connection.cursor() as cursor,
    ):
        cursor.execute(sql, params)


INSERT = (
    "INSERT INTO stewardship_census_addition (id,correlation_id,actor_id,"
    "request_key,family_id,entity_kind,entity_key,field,value,note) VALUES"
    " (gen_random_uuid(),gen_random_uuid(),%s,gen_random_uuid(),%s,%s,%s,%s,"
    "%s::jsonb,'')"
)


@pytest.mark.parametrize(
    ("who", "kind", "key", "field", "value", "message"),
    [
        ("leader", "member", "3", "nickname", '"Bo"', "current authority"),
        ("staff", "member", "3", "moved_household", "true", "current authority"),
        ("staff", "member", "987654", "nickname", '"Bo"', "current authority"),
        ("staff", "family", "999999", "home_address", "{}", "current authority"),
        ("staff", "member", "3", "nickname", '"  "', "must not be blank"),
        ("staff", "member", "3", "mobile_phone", '"5551234"', "field's shape"),
        ("staff", "member", "3", "first_name", '"Robert"', "duplicates an open"),
        ("staff", "member", "3", "nickname", '"Bo"', "requires its audit"),
    ],
)
def test_sql_refuses_a_forged_addition(
    response_service, who, kind, key, field, value, message
):
    """The 0137 guard holds every writer to Add's rules, and its deferred
    check to the audit event."""
    harness, admin, staff, rows = prepared(response_service)
    actor = leader(harness, admin) if who == "leader" else staff
    family = next(iter(rows.values())).submission.family_id
    with pytest.raises((IntegrityError, DatabaseError), match=message):
        forge(INSERT, [actor, family, kind, key, field, value])
    assert CensusAddition.objects.count() == 0


def test_sql_keeps_additions_and_their_steps_immutable(response_service):
    """Neither table takes an UPDATE or DELETE, and a step of a leader is
    refused."""
    harness, admin, staff, rows = prepared(response_service)
    first, _ = add(
        harness, staff, campaign_of(rows), member_form(rows, "nickname", "Bo")
    )
    for sql in (
        "UPDATE stewardship_census_addition SET note='x' WHERE id=%s",
        "DELETE FROM stewardship_census_addition WHERE id=%s",
    ):
        with pytest.raises((IntegrityError, DatabaseError)):
            forge(sql, [first.pk])
    with pytest.raises((IntegrityError, DatabaseError), match="current authority"):
        forge(
            "INSERT INTO stewardship_census_addition_resolution (id,correlation_id,"
            "actor_id,request_key,addition_id,action,note) VALUES (gen_random_uuid(),"
            "gen_random_uuid(),%s,gen_random_uuid(),%s,'entered','')",
            [leader(harness, admin), first.pk],
        )
    with pytest.raises((IntegrityError, DatabaseError), match="requires its audit"):
        forge(
            "INSERT INTO stewardship_census_addition_resolution (id,correlation_id,"
            "actor_id,request_key,addition_id,action,note) VALUES (gen_random_uuid(),"
            "gen_random_uuid(),%s,gen_random_uuid(),%s,'entered','')",
            [staff, first.pk],
        )
    assert CensusAdditionResolution.objects.count() == 0


def test_do_block_refuses_a_missing_guard_and_installs_whole():
    """The file's closing check passes as installed and raises once a guard's
    trigger is gone."""
    text = FROZEN.read_text(encoding="utf-8")
    check = text[text.index("DO $check$") :]
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(check)
    with (
        pytest.raises(DatabaseError, match="not installed as declared"),
        transaction.atomic(),
        connection.cursor() as cursor,
    ):
        cursor.execute(
            "DROP TRIGGER stewardship_census_addition_effect"
            " ON stewardship_census_addition"
        )
        cursor.execute(check)


def test_the_guard_records_parishsofts_value_when_added(response_service):
    """The database, not the writer, records ParishSoft's value of the field
    at the Add and the snapshot it came from (#1689 re-evaluates against it)."""
    harness, _, staff, rows = prepared(response_service)
    addition, _ = add(
        harness, staff, campaign_of(rows), member_form(rows, "last_name", "Newname")
    )
    stored = CensusAddition.objects.get(pk=addition.pk)
    assert stored.baseline["available"] is True
    assert stored.baseline_source_id == rows["first_name"].current_source_id


def test_a_replayed_key_with_another_value_is_refused(response_service):
    """The value is part of an Add's intent: the same key with another value
    is refused, not read as a replay."""
    harness, _, staff, rows = prepared(response_service)
    campaign, key = campaign_of(rows), uuid4()
    add(harness, staff, campaign, member_form(rows, "nickname", "Bo"), key=key)
    with pytest.raises(KeyRebound):
        add(harness, staff, campaign, member_form(rows, "nickname", "Bob"), key=key)
    assert CensusAddition.objects.count() == 1


def test_sql_refuses_a_forged_replacement_of_an_open_change(response_service):
    """An addition may name only the Family's open ignored change."""
    harness, _, staff, rows = prepared(response_service)
    family = rows["prefix"].submission.family_id
    with pytest.raises((IntegrityError, DatabaseError), match="replaces no ignored"):
        forge(
            "INSERT INTO stewardship_census_addition (id,correlation_id,actor_id,"
            "request_key,family_id,entity_kind,entity_key,field,value,note,"
            "in_place_of_id) VALUES (gen_random_uuid(),gen_random_uuid(),%s,"
            "gen_random_uuid(),%s,'member','3','nickname','\"Bo\"'::jsonb,'',%s)",
            [staff, family, rows["prefix"].pk],
        )
