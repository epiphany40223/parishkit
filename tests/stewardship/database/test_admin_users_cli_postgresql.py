"""``pk-admin users list`` and ``users preview-upgrade`` (#952 slice 2).

Process admission is replaced by the test's own Django assembly, as in
test_admin_status_cli_postgresql.py; everything after it is the real command
line on the restricted web login with exactly its grants, including the two
upgrade definers web now reads. The Portal users page's Upgrade preview panel
reads the same preview, so the two are compared here.
"""

import json
import re

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
from .test_user_views_postgresql import add_rules, identity, signed_in_before

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
    assert users["leader@example.org"] == {
        "email": "leader@example.org",
        "name": None,
        "role": "ministry_leader",
        "last_login": None,
    }
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


def test_preview_upgrade_matches_the_page_panel(admin, google):  # noqa: F811
    """A read-only session previews; the page's panel lists the same people."""
    sample(admin.service, google)
    browser, secret, _ = paired(admin.service, scope="read-only")
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
    # The page's panel, under the web login with exactly its grants.
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        response = browser.get("/admin/users")
    assert response.status_code == 200
    body = response.content.decode()
    panel = body[body.index('<section id="upgrade-preview"') :]
    kept = re.search(
        r'<ul aria-labelledby="upgrade-administrators">(.*?)</ul>', panel, re.S
    )
    assert re.findall(r"<li>([^<]+)</li>", kept.group(1)) == result["administrators"]
    for row in result["users"]:
        assert f'<th scope="row">{row["email"]}</th>' in panel
    assert "idle@example.org" in panel and "blocked@example.org (blocked)" in panel
    assert '<a href="#upgrade-preview">' in body


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
