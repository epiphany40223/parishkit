"""Ministry leaders come from ParishSoft roster roles (#922, migration 0042).

A person leads a Ministry when an active Member holds one of the campaign's
leader roles (by default Chairperson or Staff) on that Ministry's current
roster, the Ministry is in the current campaign and not marked inactive, and
the person signs in with a valid email address that Member's record lists.
SQL holds the one definition; Python's principal, the Ministry scope, the
Admin session guard and every export check read it.
"""

import json
from pathlib import Path
from time import perf_counter
from uuid import uuid4

import pytest
from django.db import DatabaseError, IntegrityError, connection, transaction
from django.db.models import F

from parishkit.config import ConfigError
from parishkit.stewardship.accounts.models import PortalSession, PortalUser
from parishkit.stewardship.accounts.policy import (
    Capability,
    allows,
    current_principal,
)
from parishkit.stewardship.campaigns.leader_roles import (
    effective_roles,
    roster_role_names,
)
from parishkit.stewardship.campaigns.models import Campaign
from parishkit.stewardship.deployment import ServiceRole

from ..policy_factory import address, assignment
from ..test_ministry_activity import activity
from .auth_builders import OMIT, signed_in
from .campaign_builders import change
from .leader_builders import leader, promote_leaders
from .response_builders import activate_response_service
from .test_background_grants_postgresql import task_login
from .test_ministry_responses_postgresql import configure, ministry_source
from .test_policy_postgresql import user
from .test_portal_identity_guards_postgresql import forge
from .test_runtime_auth_grants_postgresql import web_login

pytestmark = pytest.mark.django_db(transaction=True)

MIGRATION = (
    Path(__file__).resolve().parents[3]
    / "src/parishkit/stewardship/schema/migrations/0042_ministry_leaders_from_roles.sql"
)


def scope(user_id):
    """The role-derived scope as the web login reads it."""
    with task_login(ServiceRole.WEB), connection.cursor() as cursor:
        cursor.execute(
            "SELECT public.stewardship_ministry_leader_scope_v1(%s), "
            "public.stewardship_ministry_scope_v1(%s)",
            [user_id, user_id],
        )
        return tuple(
            json.loads(value) if isinstance(value, str) else value
            for value in cursor.fetchone()
        )


def rules(harness, *records):
    """Apply login rule records through the real configuration path."""
    store = harness.service.store
    result = change(
        store,
        store.active(),
        uuid4(),
        [{"operation": "add", "section": "login_rules", **row} for row in records],
    )
    assert result.state == "applied"


def set_roles(harness, roles):
    """Apply the campaign's Ministry leader roles."""
    store = harness.service.store
    result = change(
        store,
        store.active(),
        uuid4(),
        [
            {
                "operation": "update",
                "section": "campaigns",
                "id": str(harness.campaign.pk),
                "values": {"ministry_leader_roles": roles},
            }
        ],
    )
    assert result.state == "applied"
    return result


def source():
    """The response fixture's Choir (4) and Food pantry (9), plus Ushers (5)."""
    data = ministry_source()
    data.ministry_types[5] = {"id": 5, "name": "Ushers"}
    data.ministry_type_memberships[5] = {"membership": []}
    return data


LEADERS = [
    # Trimmed and ASCII case-insensitive, as the Chairperson match always was.
    leader("case@example.org", 4, role="  chairPERSON "),
    leader("staff@example.org", 9),
    leader("both@example.org", 4, 9, role="Chairperson"),
    # Not a leader role, a Ministry outside the campaign, an ended roster
    # row and an inactive Member each lead nothing.
    leader("member@example.org", 9, role="Member"),
    leader("outside@example.org", 5, role="Chairperson"),
    leader("ended@example.org", 9, ended=True),
    leader("inactive@example.org", 9, status="Inactive"),
    # Two Members sharing one address: whoever controls it leads both.
    leader("shared@example.org", 4),
    leader("shared@example.org", 9, role="Chairperson"),
    leader("team@example.org", 9, role="Team 1 leader"),
]


def test_leaders_come_from_current_roster_roles(response_service):
    """Only a configured role on a current roster row of a campaign Ministry."""
    harness = response_service
    promote_leaders(harness, source(), LEADERS)
    configure(harness, selected=(4, 9))
    expected = {
        "case@example.org": [4],
        "staff@example.org": [9],
        "both@example.org": [4, 9],
        "member@example.org": [],
        "outside@example.org": [],
        "ended@example.org": [],
        "inactive@example.org": [],
        "shared@example.org": [4, 9],
        "team@example.org": [],
        # No Member lists this address at all.
        "nobody@example.org": [],
    }
    users = {email: user(email).pk for email in expected}
    for email, ministries in expected.items():
        led, report = scope(users[email])
        assert led == ministries, email
        assert report == (
            {"capability": "ministry_report", "operational": False, "ministries": led}
            if led
            else None
        ), email
        principal = current_principal(harness.service.store, users[email])
        assert principal.ministries == frozenset(ministries), email
        assert principal.roles == (
            frozenset({"ministry_leader"}) if ministries else frozenset()
        ), email
    # A custom role list replaces the default: the team leader leads, the
    # Staff and Chairperson holders no longer do.
    set_roles(harness, ["Team 1 Leader"])
    assert scope(users["team@example.org"])[0] == [9]
    assert scope(users["both@example.org"]) == ([], None)
    assert effective_roles(
        Campaign.objects.get(pk=harness.campaign.pk).active_configuration.values
    ) == ["Team 1 Leader"]
    # A Ministry taken out of the campaign, or marked inactive here, drops
    # out; a campaign without Ministry stewardship has no leaders.
    set_roles(harness, ["Chairperson", "Staff"])
    configure(harness, selected=(4,))
    assert scope(users["both@example.org"])[0] == [4]
    configure(harness, selected=(4, 9))
    assert scope(users["both@example.org"])[0] == [4, 9]
    configure(
        harness,
        selected=(4, 9),
        patches=[
            {
                "operation": "add",
                "section": "ministries",
                **activity(ministry_duid=9, active=False),
            }
        ],
    )
    assert scope(users["both@example.org"])[0] == [4]
    # The roster labels Campaign settings offers, case-folded once.
    assert {"Chairperson", "Member", "Staff", "Team 1 leader"} <= set(
        roster_role_names()
    )


def test_rule_roles_assignments_and_denial(response_service):
    """Rules no longer make leaders; an explicit deny still refuses a leader.

    Staff and Administrators keep their access; a leader who is also Staff
    keeps Staff's and gains the Ministries. A disabled identity leads
    nothing.
    """
    harness = response_service
    promote_leaders(
        harness,
        source(),
        {
            "staffleader@example.org": [9],
            "denied@example.org": [4],
            "disabled@example.org": [4],
        },
    )
    configure(harness, selected=(4, 9))
    rules(
        harness,
        address("ruleonly@example.org", roles=("ministry_leader",)),
        assignment("ruleonly@example.org", ministry=9),
        address("staffleader@example.org", roles=("staff",)),
        address("denied@example.org", roles=()),
    )
    store = harness.service.store
    rule_only = user("ruleonly@example.org").pk
    assert scope(rule_only) == ([], None)
    principal = current_principal(store, rule_only)
    assert not principal.roles and not principal.ministries
    staff = user("staffleader@example.org").pk
    led, report = scope(staff)
    assert led == [9] and report["operational"] is True
    principal = current_principal(store, staff)
    assert principal.roles == {"staff", "ministry_leader"}
    assert allows(principal, Capability.CAMPAIGN_REPORT)
    denied = user("denied@example.org").pk
    assert scope(denied) == ([], None)
    assert not current_principal(store, denied).roles
    disabled = user("disabled@example.org").pk
    assert scope(disabled)[0] == [4]
    PortalUser.objects.filter(pk=disabled).update(
        disabled=True, version=F("version") + 1
    )
    assert scope(disabled) == ([], None)


def test_the_session_guard_admits_a_leader_without_any_rule(response_service):
    """SQL admits an Admin session for a role-derived leader (#306 guard).

    A rule's Ministry leader role alone is no longer enough.
    """
    harness = response_service
    promote_leaders(harness, source(), {"lead@example.org": [9]})
    configure(harness, selected=(4, 9))
    rules(harness, address("ruleonly@example.org", roles=("ministry_leader",)))
    lead, rule_only = user("lead@example.org").pk, user("ruleonly@example.org").pk
    with web_login():
        assert forge(lead).principal_id == lead
        with pytest.raises(IntegrityError, match="current authorized principal"):
            forge(rule_only)


def test_a_leader_signs_in_with_google_and_no_rule(response_service, google):
    """A verified Google address on a leader's Member record is admitted."""
    harness = response_service
    promote_leaders(harness, source(), {"lead@example.org": [9]})
    configure(harness, selected=(4, 9))
    claims, _ = google
    claims.update(email="Lead@Example.org", hd=OMIT, sub="leader-subject")
    client, response = signed_in()
    assert response.status_code == 302
    session = PortalSession.objects.get()
    principal = current_principal(harness.service.store, session.principal_id)
    assert principal.roles == {"ministry_leader"}
    assert principal.ministries == {9}
    assert allows(principal, Capability.MINISTRY_REPORT, ministry_id=9)
    assert not allows(principal, Capability.MINISTRY_REPORT, ministry_id=4)
    assert not allows(principal, Capability.CAMPAIGN_REPORT)
    # Someone ParishSoft does not list is refused, as before.
    claims.update(email="stranger@example.org", sub="stranger-subject")
    _, response = signed_in()
    assert response.status_code == 403


def test_the_leader_roles_stay_editable_on_a_live_campaign(response_service):
    """The live structural lock exempts ministry_leader_roles, in Python and SQL."""
    harness = activate_response_service(response_service)
    campaign = Campaign.objects.get(pk=harness.campaign.pk)
    assert campaign.structural_locked
    assert "ministry_leader_roles" not in campaign.active_configuration.values
    set_roles(harness, ["Chairperson"])
    campaign.refresh_from_db()
    assert campaign.active_configuration.values["ministry_leader_roles"] == [
        "Chairperson"
    ]
    # Another structural value is still locked, and an empty list is refused.
    flipped = not campaign.active_configuration.values["additional_information"]
    for values in (
        {"additional_information": flipped},
        {"ministry_leader_roles": []},
    ):
        store = harness.service.store
        patch = [
            {
                "operation": "update",
                "section": "campaigns",
                "id": str(campaign.pk),
                "values": values,
            }
        ]
        try:
            refused = change(store, store.active(), uuid4(), patch).state != "applied"
        except (ConfigError, DatabaseError):
            refused = True
        assert refused, values
    campaign.refresh_from_db()
    assert campaign.active_configuration.values["ministry_leader_roles"] == [
        "Chairperson"
    ]


def test_scope_cost_on_a_production_sized_roster(response_service):
    """One scope read stays cheap with thousands of roster rows.

    Production has about 72 Ministries and a few thousand roster rows. This
    builds 400 Members on 40 Ministries with 2,000 roster rows and times the
    web login's per-request read; the bound is generous, the number is
    reported.
    """
    harness = response_service
    data = source()
    leaders = [
        leader(f"m{index}@example.org", *range(100, 105), role="Member")
        for index in range(400)
    ]
    leaders[7]["role"] = "Staff"
    duids = tuple(sorted({4, 9, *range(100, 140)}))
    for index, item in enumerate(leaders):
        item["ministries"] = tuple(100 + (index + step) % 40 for step in range(5))
    promote_leaders(harness, data, leaders)
    configure(harness, selected=duids)
    target = user("m7@example.org").pk
    scope(target)
    timings = []
    with task_login(ServiceRole.WEB), connection.cursor() as cursor:
        for _ in range(10):
            started = perf_counter()
            cursor.execute(
                "SELECT public.stewardship_ministry_leader_scope_v1(%s)", [target]
            )
            assert json.loads(cursor.fetchone()[0]) == [107, 108, 109, 110, 111]
            timings.append(perf_counter() - started)
    timings.sort()
    print(
        "stewardship_ministry_leader_scope_v1: "
        f"{timings[0] * 1000:.1f} ms fastest, {timings[5] * 1000:.1f} ms median"
    )
    # The fastest call: a plan problem slows every call, while a loaded test
    # host only delays some of them.
    assert timings[0] < 0.5


def test_migration_check_refuses_the_old_definitions():
    """The frozen file's DO block fails against a pre-#922 definition."""
    text = MIGRATION.read_text(encoding="utf-8")
    check = text[text.index("DO $check$") : text.index("$check$;") + len("$check$;")]
    earlier = (MIGRATION.parent / "0015_reminder_workgroup_setting.sql").read_text(
        encoding="utf-8"
    )
    start = earlier.index("CREATE OR REPLACE FUNCTION")
    old_pointer = earlier[start : earlier.index("END $$;", start) + len("END $$;")]
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(check)  # The installed definitions pass.
        for old in (
            old_pointer,
            "ALTER FUNCTION public.stewardship_ministry_leader_scope_v1(uuid) "
            "SECURITY INVOKER",
            "GRANT EXECUTE ON FUNCTION public.stewardship_ministry_leaders_v1() "
            "TO PUBLIC",
        ):
            with (
                pytest.raises(DatabaseError, match="Migration 0042"),
                transaction.atomic(),
            ):
                cursor.execute(old)
                cursor.execute(check)
        cursor.execute(check)  # Rolled back to the new definitions.
