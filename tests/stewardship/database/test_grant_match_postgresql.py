"""An Administrator grant names a standing verified-address match (#2363).

Frozen migration 0204's definer trigger sets an ``administrator_granted``
event's ``account_email`` from the standing match of its address, so the
alert and the Home notice say which Google account the new Administrator
signs in through. These tests raise a matched user to Administrator, prove
both content compilers agree on the new row, that a grant with no standing
match of its user (an ended one, none, or one left standing by a removed
user of the same address) is unchanged, that a match still standing but
unusable at that moment (the setting off, or a lapsed confirmation) is
named, deliberately, that no caller can supply the account, and that the
migration's DO block refuses the earlier definitions.
"""

import json
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from django.conf import settings
from django.db import DatabaseError, IntegrityError, connection, transaction

from parishkit.stewardship.accounts.configuration_installation import (
    install_request,
)
from parishkit.stewardship.accounts.configuration_requests import (
    policy_operation_id,
    record_request,
)
from parishkit.stewardship.accounts.policy_models import PolicySecurityEvent
from parishkit.stewardship.accounts.security_events import open_events
from parishkit.stewardship.campaigns.domain import SystemMode
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.jobs.security_content import render_security_alert
from parishkit.stewardship.jobs.security_routing import event_alert

from ..policy_factory import address
from .campaign_builders import change
from .test_address_match_postgresql import (
    PERSONAL,
    STAFF,
    confirmed_days_ago,
    end,
    matched,
    put_back,
    record_id,
    verified,
    web,
)
from .test_operational_routing_postgresql import routing as routing_fixture

routing = routing_fixture
pytestmark = pytest.mark.django_db(transaction=True)

SCHEMA = Path(__file__).resolve().parents[3] / "src/parishkit/stewardship/schema"
FROZEN = (SCHEMA / "migrations/0204_grant_match.sql").read_text(encoding="utf-8")
# The file's closing self-check, run on its own against chosen states.
CHECK = FROZEN[FROZEN.index("DO $check$") :]
ROW = f"Signs in through Google account: {PERSONAL}\n"


def raise_to_administrator(store, actor, email=STAFF):
    """Activate a configuration that adds Administrator to ``email``'s user."""
    rule = next(
        record
        for record in store.active().document()["sections"]["login_rules"]
        if record["values"].get("email") == email
    )
    # A new manual grant names its own request's operation, as the users
    # page records it (campaign_builders.change binds only additions).
    key = uuid4()
    manual = {"manual": str(policy_operation_id(actor, key))}
    grants = rule["values"]["grants"] | {"administrator": manual}
    operation = {
        "operation": "update",
        "section": "login_rules",
        "id": rule["id"],
        "values": {"roles": sorted(grants), "grants": grants},
    }
    receipt = record_request(
        base_digest=store.active().digest,
        patch=[operation],
        actor_id=actor,
        request_key=key,
        correlation_id=uuid4(),
    )
    result = install_request(
        store, request_id=receipt.request_id, correlation_id=uuid4()
    )
    assert result.state == "applied"
    return PolicySecurityEvent.objects.get(kind="administrator_granted", target=email)


def viewer(actor):
    """The dashboard viewer of one portal user, as open_events reads it."""
    return SimpleNamespace(identity=actor)


def compiled(event, mode, configuration):
    """Both twins' content for one event: (SQL's, Python's)."""
    with work_transaction():
        alert = event_alert(event.pk, mode, configuration)
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT stewardship_security_content_v2(%s,%s,%s)",
                [event.pk, mode.value, configuration],
            )
            sql = cursor.fetchone()[0]
    return (json.loads(sql) if isinstance(sql, str) else sql), asdict(
        render_security_alert(alert)
    )


def test_a_grant_names_the_account_of_a_standing_match(routing):
    """The event, both alert twins and the Home row name the Google account."""
    store, actor, _, _ = routing
    portal, _ = matched(store)
    event = raise_to_administrator(store, actor)
    assert event.account_email == PERSONAL
    assert event.address_match_id is None and event.replaced_account_email is None
    configuration = store.active().version_id
    for mode in SystemMode:
        sql, python = compiled(event, mode, configuration)
        assert sql == python
        assert f"Target: {STAFF}\n{ROW}Roles before: Staff\n" in sql["text"]
    row = next(row for row in open_events(viewer(portal.pk)) if row["id"] == event.pk)
    assert row["account"] == PERSONAL


def test_a_grant_without_a_standing_match_is_unchanged(routing):
    """An ended match names nothing; nor does an address never matched."""
    store, actor, _, _ = routing
    portal, made = matched(store)
    with web(), transaction.atomic():
        put_back(portal)
        verified(portal)
        end(made, "not_confirmed")
    event = raise_to_administrator(store, actor)
    assert event.account_email is None
    sql, python = compiled(event, SystemMode.PRODUCTION, store.active().version_id)
    assert sql == python
    assert "Google account" not in sql["text"]
    leader = raise_to_administrator(store, actor, "minister@example.org")
    assert leader.account_email is None
    row = next(row for row in open_events(viewer(portal.pk)) if row["id"] == event.pk)
    assert row["account"] is None


def test_a_removed_users_standing_match_is_not_named_for_a_new_record(routing):
    """Nothing ends a match when its user is removed (the account's next
    sign-in ends it as user_gone), so the address added again as an
    Administrator, a new user record, must not name the old account."""
    store, actor, _, _ = routing
    _, made = matched(store)
    remove = {"operation": "remove", "section": "login_rules"}
    add = {"operation": "add", "section": "login_rules"}
    for patch in (
        [remove | {"id": record_id(store, STAFF)}],
        [add | address(STAFF, ("administrator",))],
    ):
        assert change(store, store.active(), actor, patch).state == "applied"
    made.refresh_from_db()
    assert made.ended_at is None
    assert record_id(store, STAFF) != str(made.user_record_id)
    event = PolicySecurityEvent.objects.get(kind="administrator_granted", target=STAFF)
    assert event.account_email is None


def test_a_standing_but_unusable_match_is_still_named(routing):
    """With the setting off and the confirmation lapsed the match cannot sign
    in now, but it stands and signs in again once the setting is back on or
    it is re-checked: the grant names it, so the Administrators learn of it."""
    store, actor, _, _ = routing
    assert settings.STEWARDSHIP_ALTERNATE_ADDRESS_SIGN_IN is False
    _, made = matched(store)
    confirmed_days_ago(made, 31)
    event = raise_to_administrator(store, actor)
    assert event.account_email == PERSONAL


def test_no_caller_names_the_account_and_other_kinds_carry_none(routing):
    """The trigger overwrites a supplied account; the check refuses one on
    another kind and an untrimmed one."""
    store, actor, _, _ = routing
    grant = raise_to_administrator(store, actor)
    values = {
        "created_at": grant.created_at,
        "correlation_id": uuid4(),
        "activation_id": grant.activation_id,
        "target": STAFF,
        "before_roles": [],
        "after_roles": ["administrator"],
        "recipients": [],
    }
    forged = PolicySecurityEvent.objects.create(
        **values,
        kind="administrator_granted",
        rule_record_id=uuid4(),
        account_email="forged@personal.example",
    )
    forged.refresh_from_db()
    assert forged.account_email is None
    with pytest.raises(IntegrityError), transaction.atomic():
        PolicySecurityEvent.objects.create(
            **values,
            kind="domain_created",
            rule_record_id=uuid4(),
            account_email="forged@personal.example",
        )
    with pytest.raises(IntegrityError), transaction.atomic():
        # The check itself, with the trigger out of the way.
        with connection.cursor() as cursor:
            cursor.execute(
                "ALTER TABLE stewardship_policy_security_event "
                "DISABLE TRIGGER stewardship_grant_match_v1"
            )
        PolicySecurityEvent.objects.create(
            **values,
            kind="administrator_granted",
            rule_record_id=uuid4(),
            account_email=" Forged@personal.example",
        )


def from_0200(start, end):
    """One statement of 0200's file, from ``start`` to the end of ``end``."""
    text = (SCHEMA / "migrations/0200_address_match.sql").read_text(encoding="utf-8")
    first = text.index(start)
    return text[first : text.index(end, first) + len(end)]


OLD_DEFINITIONS = {
    "old_check": (
        "ALTER TABLE stewardship_policy_security_event "
        "DROP CONSTRAINT policy_security_event_account;"
        + from_0200(
            'ALTER TABLE "stewardship_policy_security_event" ADD CONSTRAINT '
            '"policy_security_event_account"',
            ";",
        )
    ),
    "content": from_0200(
        "CREATE OR REPLACE FUNCTION public.stewardship_security_content_v2(", "END $$;"
    ),
    "no_trigger": (
        "DROP TRIGGER stewardship_grant_match_v1 ON stewardship_policy_security_event"
    ),
    "after_trigger": (
        "DROP TRIGGER stewardship_grant_match_v1 ON stewardship_policy_security_event;"
        "CREATE TRIGGER stewardship_grant_match_v1 AFTER INSERT ON "
        "stewardship_policy_security_event FOR EACH ROW EXECUTE FUNCTION "
        "stewardship_grant_match_v1()"
    ),
    "invoker": "ALTER FUNCTION public.stewardship_grant_match_v1() SECURITY INVOKER",
    "unpinned": "ALTER FUNCTION public.stewardship_grant_match_v1() RESET search_path",
    "public_execute": (
        "GRANT EXECUTE ON FUNCTION public.stewardship_grant_match_v1() TO PUBLIC"
    ),
}


def test_the_do_block_passes_on_the_installed_schema():
    """The migration's own check passes on what it installed."""
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(CHECK)
        transaction.set_rollback(True)


@pytest.mark.parametrize("name", sorted(OLD_DEFINITIONS))
def test_the_do_block_refuses_each_earlier_or_weakened_definition(name):
    """0200's check and compiler, a missing or AFTER trigger, an invoker,
    an unpinned path or PUBLIC EXECUTE: the check refuses."""
    with (
        pytest.raises(DatabaseError, match="Migration 0204"),
        transaction.atomic(),
        connection.cursor() as cursor,
    ):
        cursor.execute(OLD_DEFINITIONS[name])
        cursor.execute(CHECK)
