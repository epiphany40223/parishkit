"""``pk-admin users`` list, preview-upgrade, preview and confirm (#952).

Process admission is replaced by the test's own Django assembly, as in
test_admin_status_cli_postgresql.py; everything after it is the real command
line on the restricted web login with exactly its grants, including the
upgrade definers web reads. ``users preview`` and ``users confirm`` change
users through the Users page's own code (slice 5).
"""

import json
from uuid import UUID, uuid4

import pytest
from django.db import DatabaseError, connection, transaction

from parishkit.stewardship import admin_cli
from parishkit.stewardship.accounts import sessions, users_upgrade
from parishkit.stewardship.accounts.policy import Principal
from parishkit.stewardship.accounts.policy_models import PortalUser
from parishkit.stewardship.deployment import ServiceRole

from ..policy_factory import address, domain
from .auth_builders import unguarded
from .automation_builders import paired
from .test_admin_status_cli_postgresql import admin, one, views  # noqa: F401
from .test_background_grants_postgresql import task_login
from .user_builders import add_rules, identity, signed_in_before

pytestmark = pytest.mark.django_db(transaction=True)

ADMIN = "admin@example.org"


def sample(service, google):
    """Rules of every kind, a domain colleague who signed in, a disabled one.

    Returns an approved full-scope session's browser and secret, signed in
    as the deployment's Administrator.
    """
    add_rules(
        service.store,
        address("second@example.org", ("administrator", "staff")),
        address("leader@example.org", ("ministry_leader",)),
        address("blocked@example.org", ()),
        domain("example.org", ("ministry_leader", "staff")),
    )
    # A colleague with no exact rule signed in through the domain rule before
    # the users swap (#952).
    signed_in_before("colleague@example.org", hosted="example.org")
    identity("idle@example.org", hosted="example.org", disabled=True)
    google[0].update(email=ADMIN, sub="synthetic-google-subject")
    browser, secret, _ = paired(service)
    return browser, secret


def test_users_list_prints_each_user_sorted_as_the_page(admin, google):  # noqa: F811
    """Before the upgrade, each granting address rule is a user, no Name."""
    _, secret = sample(admin.service, google)
    before = views("portal_users_viewed")
    code, document = one(admin, "users", "list", secret=secret)
    assert code == 0 and document["ok"], document
    result = document["result"]
    assert set(result) == set(admin_cli.BY_NAME["users list"].result_fields)
    assert result["form"] == "sign_in_rules" and result["sort"] == "email"
    users = {row["email"]: row for row in result["users"]}
    assert [row["email"] for row in result["users"]] == sorted(users)
    # Deny and domain rules are not users; the domain colleague is in the
    # upgrade preview instead.
    assert set(users) == {ADMIN, "second@example.org", "leader@example.org"}
    leader = users["leader@example.org"]
    assert leader == {
        "id": leader["id"],
        "email": "leader@example.org",
        "name": None,
        "role": "ministry_leader",
        "last_login": None,
        "identities": [],
    }
    assert result["version"] == admin.service.store.active().digest
    # The Administrator signed in to approve the session.
    assert users[ADMIN]["last_login"] is not None
    assert users["second@example.org"]["role"] == "administrator"
    # The page's own view event, counting the rows: never an address.
    assert views("portal_users_viewed") == before + 1
    code, document = one(admin, "users", "list", "--sort=-role", secret=secret)
    assert code == 0, document
    assert [row["role"] for row in document["result"]["users"]] == [
        "administrator",
        "administrator",
        "ministry_leader",
    ]
    # A sort the page does not offer is refused before anything is read.
    code, document = one(admin, "users", "list", "--sort", "warnings", secret=secret)
    assert code == 1 and not document["ok"], document
    assert views("portal_users_viewed") == before + 2


def test_preview_upgrade_lists_what_the_upgrade_does(admin, google):  # noqa: F811
    """A read-only session previews exactly what the conversion writes."""
    sample(admin.service, google)
    _browser, secret, _ = paired(admin.service, scope="read-only")
    before = views("portal_users_viewed")
    code, document = one(admin, "users", "preview-upgrade", secret=secret)
    assert code == 0 and document["ok"], document
    result = document["result"]
    assert set(result) == set(admin_cli.BY_NAME["users preview-upgrade"].result_fields)
    # The exact Administrators the operator must confirm, from the rules.
    assert result["administrators"] == [ADMIN, "second@example.org"]
    assert {(row["email"], row["role"]) for row in result["users"]} == {
        (ADMIN, "administrator"),
        ("second@example.org", "administrator"),
        ("leader@example.org", "ministry_leader"),
        ("colleague@example.org", "staff"),
    }
    # No ParishSoft Member lists these addresses: each Name is the local part.
    assert {row["name"] for row in result["users"]} == {
        "admin",
        "second",
        "leader",
        "colleague",
    }
    assert result["not_converted"] == {
        "disabled": ["idle@example.org"],
        "never_signed_in": [],
    }
    assert result["dropped_deny_rules"] == [
        {"email": "blocked@example.org", "overrides_identity": False, "name": "blocked"}
    ]
    assert views("portal_users_viewed") == before + 1


def test_preview_upgrade_is_unavailable_once_users_exist(
    admin,  # noqa: F811
    google,
    monkeypatch,
):
    """With the configuration in the users form there is nothing to preview."""
    _, secret = sample(admin.service, google)
    monkeypatch.setattr(
        users_upgrade, "document_preview", lambda document: {"already_users": True}
    )
    before = views("portal_users_viewed")
    code, document = one(admin, "users", "preview-upgrade", secret=secret)
    assert code == 1 and document["error"]["code"] == "not_available", document
    # Nothing was shown, so nothing is recorded as viewed.
    assert views("portal_users_viewed") == before


def test_web_reads_parishsoft_names_only_for_the_upgrade_addresses(
    admin,  # noqa: F811
    google,
):
    """The preview names people from ParishSoft; nothing else is disclosed.

    Under the web login the preview's Names come from ParishSoft (the
    fixture's "Example N, Leader", unlike any local part). The names definer,
    called directly under that login, takes no address and answers only for
    the active rules' and recorded identities' addresses: a Member address
    outside them yields nothing.
    """
    from parishkit.stewardship.source.models import SourceCurrent, SourceMutationLease

    from .leader_builders import leader, with_leaders
    from .response_builders import response_source
    from .test_source_families_postgresql import publish

    _, secret = sample(admin.service, google)
    SourceMutationLease.objects.get_or_create(singleton=True)
    SourceCurrent.objects.get_or_create(singleton=True)
    publish(
        with_leaders(
            response_source(),
            [
                leader("leader@example.org", 9),
                leader("blocked@example.org", 9),
                leader("colleague@example.org", 9),
                # A Member address no rule or identity names.
                leader("outside@example.org", 9),
            ],
        )
    )
    code, document = one(admin, "users", "preview-upgrade", secret=secret)
    assert code == 0 and document["ok"], document
    result = document["result"]
    assert {row["email"]: row["name"] for row in result["users"]} == {
        ADMIN: "admin",
        "second@example.org": "second",
        "leader@example.org": "Example 0, Leader",
        "colleague@example.org": "Example 2, Leader",
    }
    assert result["dropped_deny_rules"] == [
        {
            "email": "blocked@example.org",
            "overrides_identity": False,
            "name": "Example 1, Leader",
        }
    ]
    with (
        task_login(ServiceRole.WEB, exact=True, reconnect=True),
        connection.cursor() as cursor,
    ):
        cursor.execute(
            "SELECT address, names FROM public.stewardship_member_names_v1()"
        )
        names = {
            address: json.loads(value) if isinstance(value, str) else value
            for address, value in cursor.fetchall()
        }
        # There is no form that takes an address to look up.
        with pytest.raises(DatabaseError), transaction.atomic():
            cursor.execute(
                "SELECT public.stewardship_member_names_v1(%s)",
                ["outside@example.org"],
            )
    assert names == {
        "leader@example.org": ["Example 0, Leader"],
        "blocked@example.org": ["Example 1, Leader"],
        "colleague@example.org": ["Example 2, Leader"],
    }


@pytest.mark.parametrize(
    "roles",
    [frozenset({"ministry_leader", "staff"}), frozenset({"ministry_leader"})],
    ids=["staff", "ministry_leader"],
)
def test_staff_and_ministry_leaders_are_refused_both_reads(
    admin,  # noqa: F811
    google,
    monkeypatch,
    roles,
):
    """Only an Administrator's session lists users or previews the upgrade.

    A caller whose current roles are Staff or Ministry leader is refused
    (``denied``) by both commands, and nothing is recorded as viewed. A real
    automation session that loses Administrator ends outright instead.
    """
    _, secret = sample(admin.service, google)
    admin_id = PortalUser.objects.get(email=ADMIN).pk
    real = sessions.authenticated_admin

    def lowered(caller, **options):
        """The session's real admission, with only the lesser roles."""
        principal = real(caller, **options)
        return principal and Principal(admin_id, roles)

    before = views("portal_users_viewed")
    with monkeypatch.context() as patch:
        patch.setattr(sessions, "authenticated_admin", lowered)
        for command in (("users", "list"), ("users", "preview-upgrade")):
            code, document = one(admin, *command, secret=secret)
            assert code == 1 and document["error"]["code"] == "denied", document
    # The session itself, demoted in the stored rules, is no longer usable.
    with unguarded(), connection.cursor() as cursor:
        cursor.execute(
            "UPDATE stewardship_address_rule SET roles=%s::jsonb WHERE email=%s",
            [json.dumps(sorted(roles)), ADMIN],
        )
    for command in (("users", "list"), ("users", "preview-upgrade")):
        code, document = one(admin, *command, secret=secret)
        assert code != 0 and not document["ok"], document
        assert "users" not in document.get("result", {}), document
    assert views("portal_users_viewed") == before


def _changes(*entries):
    """A ``users preview`` change document."""
    return json.dumps({"users": list(entries)})


def test_users_preview_and_confirm_change_users_as_the_page(admin, google):  # noqa: F811
    """Add, change and delete users from the command line, through the page's code.

    The preview prints each user's Name and role before and after and
    changes nothing; confirming records the request with the command's and
    the page's audit events. Granting Administrator is high impact: it leaves
    the automation policy-change notice and the usual security event.
    """
    from parishkit.stewardship.accounts.automation_models import AutomationNotice
    from parishkit.stewardship.accounts.configuration_installation import (
        install_request,
    )
    from parishkit.stewardship.accounts.policy_models import PolicySecurityEvent
    from parishkit.stewardship.audit.models import AuditEvent

    from .user_builders import convert, user_id, users

    _, secret = sample(admin.service, google)
    store = admin.service.store
    convert(store)
    code, listed = one(admin, "users", "list", secret=secret)
    assert code == 0 and listed["result"]["form"] == "users", listed
    version = listed["result"]["version"]
    leader = user_id(store, "leader@example.org")
    second = user_id(store, "second@example.org")
    changes = _changes(
        {"email": "New@Example.org", "name": "New, Nell"},
        {"id": leader, "role": "administrator"},
        {"id": second, "delete": True},
    )
    code, document = one(
        admin,
        "users",
        "preview",
        "--expected-version",
        version,
        "--changes",
        changes,
        secret=secret,
    )
    assert code == 0 and document["ok"], document
    result = document["result"]
    assert set(result) == set(admin_cli.BY_NAME["users preview"].result_fields)
    assert result["high_impact"] is True
    by_email = {change["email"]: change for change in result["changes"]}
    assert by_email["new@example.org"]["after"] == {
        "name": "New, Nell",
        "role": "ministry_leader",
    }
    assert by_email["new@example.org"]["other_names"] == 0
    assert by_email["leader@example.org"]["after"]["role"] == "administrator"
    assert by_email["second@example.org"]["operation"] == "remove"
    # Nothing changed yet.
    assert "new@example.org" not in users(store)
    code, document = one(
        admin,
        "users",
        "confirm",
        "--token",
        result["preview"]["token"],
        secret=secret,
    )
    assert code == 0 and document["ok"] and document["result"]["created"], document
    request_id = document["result"]["request"]["request_id"]
    receipt = install_request(
        store, request_id=UUID(request_id), correlation_id=uuid4()
    )
    assert receipt.state == "applied"
    assert users(store)["new@example.org"] == ("New, Nell", "ministry_leader")
    assert users(store)["leader@example.org"][1] == "administrator"
    assert "second@example.org" not in users(store)
    for event_type in ("admin_cmd_users_confirm", "user_change_requested"):
        assert AuditEvent.objects.filter(event_type=event_type).count() == 1
    assert AutomationNotice.objects.filter(kind="policy_change").count() == 1
    assert PolicySecurityEvent.objects.filter(
        target="leader@example.org", kind="administrator_granted"
    ).exists()
    # A stale version, and a change that leaves no Administrator, are refused.
    code, document = one(
        admin,
        "users",
        "preview",
        "--expected-version",
        version,
        "--changes",
        _changes({"id": leader, "name": "Lee"}),
        secret=secret,
    )
    assert code != 0 and document["error"]["code"] == "stale_version", document
    code, document = one(
        admin,
        "users",
        "preview",
        "--expected-version",
        store.active().digest,
        "--changes",
        _changes(
            {"id": user_id(store, ADMIN), "delete": True},
            {"id": user_id(store, "leader@example.org"), "role": "staff"},
        ),
        secret=secret,
    )
    assert code == 1 and document["error"]["code"] == "invalid", document
    assert "users" in json.dumps(document["error"])
    # The session acts as its Administrator, who may not demote or delete
    # their own user even while another Administrator (leader) remains.
    for entry in (
        {"id": user_id(store, ADMIN), "delete": True},
        {"id": user_id(store, ADMIN), "role": "staff"},
    ):
        code, document = one(
            admin,
            "users",
            "preview",
            "--expected-version",
            store.active().digest,
            "--changes",
            _changes(entry),
            secret=secret,
        )
        assert code == 1 and document["error"]["code"] == "invalid", document
        assert "your own Administrator role" in json.dumps(document["error"])
    assert users(store)[ADMIN][1] == "administrator"


def test_users_preview_looks_up_a_new_users_name_for_a_live_session(
    admin,  # noqa: F811
    google,
    monkeypatch,
):
    """A new user without a Name gets the ParishSoft default, under the session.

    The lookup definer receives the command session's key
    (``caller.session.session_key``, its portal-session row's
    ``session_id``). Within the idle limit it answers and audits the lookup;
    once that row is past the 60-minute idle limit (aged by another
    connection after admission, so only the definer sees it) it refuses,
    and the preview is ``session_ended`` (exit 5) with nothing audited.
    """
    import psycopg

    from parishkit.stewardship import admin_users
    from parishkit.stewardship.audit.models import AuditEvent
    from parishkit.stewardship.source.models import SourceCurrent, SourceMutationLease

    from .leader_builders import leader, with_leaders
    from .response_builders import response_source
    from .test_source_families_postgresql import publish
    from .user_builders import convert

    _, secret = sample(admin.service, google)
    store = admin.service.store
    convert(store)
    SourceMutationLease.objects.get_or_create(singleton=True)
    SourceCurrent.objects.get_or_create(singleton=True)
    publish(with_leaders(response_source(), [leader("Nell@Example.org", 9)]))
    admin_id = PortalUser.objects.get(email=ADMIN).pk
    lookups = AuditEvent.objects.filter(
        event_type="user_names_looked_up", actor_id=admin_id
    )
    version = store.active().digest
    preview = (
        "users",
        "preview",
        "--expected-version",
        version,
        "--changes",
        _changes({"email": "nell@example.org"}),
    )
    code, document = one(admin, *preview, secret=secret)
    assert code == 0 and document["ok"], document
    (change,) = document["result"]["changes"]
    assert change["after"] == {"name": "Example 0, Leader", "role": "ministry_leader"}
    assert change["other_names"] == 0
    assert lookups.count() == 1

    owner = dict(connection.settings_dict)
    real_parse = admin_users.parse_users

    def idle(text):
        """After admission, age this Administrator's sessions past idle."""
        with psycopg.connect(
            host=owner["HOST"],
            port=owner["PORT"],
            user=owner["USER"],
            password=owner["PASSWORD"],
            dbname=owner["NAME"],
        ) as other:
            other.execute("SET lock_timeout = '10s'")
            other.execute("SET session_replication_role = replica")
            other.execute(
                # A row's activity is never before its sign-in (a CHECK).
                "UPDATE stewardship_portal_session SET authenticated_at="
                "least(authenticated_at, clock_timestamp()-interval '61 minutes'),"
                " last_activity_at=clock_timestamp()-interval '61 minutes' "
                "WHERE principal_id=%s AND revoked_at IS NULL",
                [admin_id],
            )
        return real_parse(text)

    monkeypatch.setattr(admin_users, "parse_users", idle)
    code, document = one(admin, *preview, secret=secret)
    assert code == 5 and document["error"]["code"] == "session_ended", document
    assert lookups.count() == 1
