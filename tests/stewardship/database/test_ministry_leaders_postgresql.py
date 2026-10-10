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
from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.campaigns.configuration import (
    ROLE_NAME_LIMIT,
    leader_role_key,
    leader_role_label,
)
from parishkit.stewardship.campaigns.leader_roles import (
    effective_roles,
    roster_role_names,
    saveable,
)
from parishkit.stewardship.campaigns.models import Campaign
from parishkit.stewardship.deployment import ServiceRole

from ..policy_factory import address, assignment
from ..test_ministry_activity import activity
from .auth_builders import OMIT, signed_in
from .campaign_builders import change
from .leader_builders import leader, promote_leaders, with_leaders
from .response_builders import activate_response_service
from .test_background_grants_postgresql import task_login
from .test_ministry_responses_postgresql import configure, ministry_source
from .test_policy_postgresql import user
from .test_portal_identity_guards_postgresql import forge
from .test_runtime_auth_grants_postgresql import web_login
from .test_source_families_postgresql import prepare, promote

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
    # Trimmed and ASCII case-insensitive, as the Chairperson match always was,
    # and any Unicode whitespace run (no-break spaces too) counts as one space.
    leader("case@example.org", 4, role="  chairPERSON "),
    leader("nbsp@example.org", 9, role="\u00a0Staff\u2003"),
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
    leader("spaced@example.org", 9, role="team\u00a0 1\t LEADER"),
    # A label too long to save as a leader role is never offered.
    leader("long@example.org", 9, role="L" * (ROLE_NAME_LIMIT + 1)),
]


def test_leaders_come_from_current_roster_roles(response_service):
    """Only a configured role on a current roster row of a campaign Ministry."""
    harness = response_service
    promote_leaders(harness, source(), LEADERS)
    configure(harness, selected=(4, 9))
    expected = {
        "case@example.org": [4],
        "staff@example.org": [9],
        "nbsp@example.org": [9],
        "both@example.org": [4, 9],
        "member@example.org": [],
        "outside@example.org": [],
        "ended@example.org": [],
        "inactive@example.org": [],
        "shared@example.org": [4, 9],
        "team@example.org": [],
        "spaced@example.org": [],
        "long@example.org": [],
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
    assert scope(users["spaced@example.org"])[0] == [9]
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
    # The roster labels Campaign settings offers: whitespace collapsed,
    # case-folded once, and only ones that can be saved.
    offered = roster_role_names()
    assert {"Chairperson", "Member", "Staff", "Team 1 leader"} <= set(offered)
    assert len({leader_role_key(name) for name in offered}) == len(offered)
    assert all(name == leader_role_label(name) for name in offered)
    assert all(len(name) <= ROLE_NAME_LIMIT for name in offered)
    assert all(saveable(name) for name in offered)


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


def history(harness):
    """A source with Production's shape and three retained snapshots.

    About 3,000 Members on 72 Ministries, 4,500 current roster rows and 300
    leaders, promoted three times with every roster row changed each time,
    so 13,500 roster rows are kept: the leader read must touch only the
    current snapshot's. Returns the 72 Ministry DUIDs.
    """
    duids = tuple(range(100, 172))
    members = []
    for index in range(3000):
        ministries = {duids[index % 72]}
        if index % 2 == 0:
            ministries.add(duids[(index * 7 + 3) % 72])
        role = "Member"
        if index % 10 == 0:
            role = "Staff" if index % 20 else "Chairperson"
        members.append(leader(f"m{index}@example.org", *sorted(ministries), role=role))
    for generation in range(3):
        data = with_leaders(source(), members)
        for roster in data.ministry_type_memberships.values():
            for row in roster["membership"]:
                row["startDate"] = f"2020-01-0{generation + 1}"
        snapshot, claim = prepare(data)
        promote(snapshot, claim, harness.campaign, harness.rings)
    return duids


def test_scope_cost_on_a_history_sized_roster(response_service):
    """The first scope read on a fresh connection stays cheap.

    Production's web runs with CONN_MAX_AGE 0, so every request opens a new
    connection and plans the leader query afresh, and an Admin page reads the
    leader list at least twice. Each timed call here is the first on its own
    new connection, against history() (#939's review measured the earlier
    definition, which parsed every retained roster row, at 38 ms with 12,000
    rows; this one reads only the current snapshot's). The bound is generous
    for a loaded CI host; the numbers are reported.
    """
    harness = response_service
    duids = history(harness)
    configure(harness, selected=(4, 9, *duids))
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT count(*) FROM stewardship_source_roster "
            "WHERE canonical::jsonb->>'ministryRoleName' IS NOT NULL"
        )
        assert cursor.fetchone()[0] >= 13500
        cursor.execute("ANALYZE")
    target = user("m10@example.org").pk
    timings = []
    with task_login(ServiceRole.WEB, reconnect=True):
        for _ in range(3):
            # A new backend each time: nothing is planned or cached yet.
            connection.close()
            with connection.cursor() as cursor:
                started = perf_counter()
                cursor.execute(
                    "SELECT public.stewardship_ministry_leader_scope_v1(%s)", [target]
                )
                led = json.loads(cursor.fetchone()[0])
                timings.append(perf_counter() - started)
            assert led == [101, 110]
    timings.sort()
    print(
        "stewardship_ministry_leader_scope_v1, first call on a fresh connection: "
        f"{timings[0] * 1000:.1f} ms fastest, {timings[1] * 1000:.1f} ms median"
    )
    # The fastest call: reading every retained snapshot slows every call,
    # while a loaded test host only delays some of them.
    assert timings[0] < 0.1


def test_a_draft_campaign_leads_and_one_without_ministries_does_not(
    response_service,
):
    """The current campaign may still be a draft; the Ministry module is needed.

    Leaders lead as soon as the current campaign asks about Ministries,
    before it is scheduled or live. A current campaign without the Ministry
    module has no leaders at all: no scope, no role and no Admin session.
    """
    harness = response_service
    promote_leaders(harness, source(), {"lead@example.org": [9]})
    configure(harness, selected=(4, 9))
    campaign = Campaign.objects.get(pk=harness.campaign.pk)
    assert campaign.state == "draft"
    assert SystemConfiguration.objects.get().current_campaign_id == campaign.pk
    lead = user("lead@example.org").pk
    store = harness.service.store
    assert scope(lead)[0] == [9]
    assert current_principal(store, lead).roles == {"ministry_leader"}
    result = change(
        store,
        store.active(),
        uuid4(),
        [
            {
                "operation": "update",
                "section": "campaigns",
                "id": str(campaign.pk),
                "values": {"modules": ["census"], "ministry_duids": []},
            }
        ],
    )
    assert result.state == "applied"
    assert scope(lead) == ([], None)
    principal = current_principal(store, lead)
    assert not principal.roles and not principal.ministries
    with web_login(), pytest.raises(IntegrityError, match="current authorized"):
        forge(lead)


def test_sql_and_python_compare_role_names_alike():
    """stewardship_ministry_leader_role_key_v1 is leader_role_key, in SQL."""
    samples = [
        "Chairperson",
        "  chairPERSON ",
        " Staff ",
        "Team  1\t\n LEADER",
        "　Co Chair ",
        "LÉAD",
        "​Staff",
        "",
        "   ",
    ]
    with connection.cursor() as cursor:
        for sample in samples:
            cursor.execute(
                "SELECT public.stewardship_ministry_leader_role_key_v1(%s)", [sample]
            )
            assert cursor.fetchone()[0] == leader_role_key(sample), repr(sample)


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
