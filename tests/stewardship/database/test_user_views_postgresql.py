"""The Users page (#952 slice 5) under the real web database role.

One table of users, New and Edit user with a review and Apply, Delete from
the table's dialog and New user's Name lookup. Each change is one recorded,
installed configuration request with its audit event; the last-Administrator
guard, the capability and CSRF are checked on the server.
"""

import json
import re
from uuid import uuid4

import psycopg
import pytest
from django.db import connection

from parishkit.stewardship.accounts import user_views
from parishkit.stewardship.accounts.policy_models import PolicySecurityEvent, PortalUser
from parishkit.stewardship.accounts.request_models import ConfigurationChangeRequest
from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.accounts.user_changes import member_names
from parishkit.stewardship.audit.models import AuditContext, AuditEvent
from parishkit.stewardship.deployment import ServiceRole

from ..policy_factory import address
from .auth_builders import signed_in
from .leader_builders import grant_role
from .test_background_grants_postgresql import task_login
from .user_builders import (
    USERS,
    add_rules,
    convert,
    installed,
    post,
    review,
    user_change,
    user_id,
    users,
    web,
)

pytestmark = pytest.mark.django_db(transaction=True)
NEW = "/admin/users/new/"
DELETE = "/admin/users/deletion/"
NAMES = "/admin/users/new/names/"


def contexts(event_type):
    """The audit contexts recorded for one event type."""
    return list(
        AuditContext.objects.filter(event__event_type=event_type).values_list(
            "context", flat=True
        )
    )


def edit(store, email):
    """Edit user's address for one active user."""
    return f"/admin/users/{user_id(store, email)}/edit/"


def page(browser, path=USERS):
    """One page as the signed-in browser sees it now, under the web role."""
    with web():
        response = browser.get(path)
    assert response.status_code == 200, response.content
    return response.content.decode()


def row(body, email):
    """The Users table row whose header cell is this address."""
    found = re.findall(
        r'<tr>\s*<th scope="row"><span class="cell-ellipsis" '
        rf'title="{re.escape(email)}">.*?</tr>',
        body,
        flags=re.S,
    )
    assert len(found) == 1, email
    return found[0]


@pytest.fixture
def converted(auth_service, google):
    """A users deployment: the Administrator, a second one and a Staff user."""
    add_rules(
        auth_service.store,
        address("second@example.org"),
        address("staff@example.org", ("staff",)),
    )
    convert(auth_service.store)
    browser, login = signed_in()
    assert login.status_code == 302
    return auth_service.store, browser


def test_the_table_lists_users_and_audits_only_a_count(converted):
    """Email first, Name, Role, sign-in and actions; one line; the view audited."""
    store, browser = converted
    body = page(browser)
    assert "<h1>Users</h1>" in body
    admin = row(body, "admin@example.org")
    # The Administrator signed in through the fixture: one identity, so the
    # row's Sign-in activity posts it to System logs, never in an address.
    assert "data-local-instant" in admin
    assert 'name="actor" value="' in admin and 'action="/admin/system/logs/"' in admin
    assert f'href="{edit(store, "admin@example.org")}"' in admin
    assert 'data-confirm-open="user-delete"' in admin
    assert "admin (Administrator)" not in admin
    staff = row(body, "staff@example.org")
    assert '<td class="nowrap">Staff</td>' in staff and "Never" in staff
    # Rows come in Email order by default; Role sorts by level, Administrators
    # first, on its first choice.
    assert body.index("admin@example.org") < body.index("second@example.org")
    assert body.index("second@example.org") < body.index("staff@example.org")
    ordered = page(browser, USERS + "?users_sort=-role")
    assert ordered.index("second@example.org") < ordered.index("staff@example.org")
    with web():
        assert browser.get(USERS + "?users_sort=warnings").status_code == 400
        assert browser.get(USERS + "?sort=email").status_code == 400
    assert contexts("portal_users_viewed")[0] == {"outcome": "succeeded", "count": 3}
    assert "@" not in str(contexts("portal_users_viewed"))
    # The old Portal users addresses are gone (#864).
    with web():
        assert browser.get("/admin/users/rules").status_code == 404


@pytest.mark.parametrize("role", ["staff", "ministry_leader"])
def test_other_roles_get_none_of_the_users_routes(auth_service, google, role):
    """Who may sign in, and changing it, is Administrator-only."""
    grant_role(auth_service.store, "reader@example.org", role)
    convert(auth_service.store)
    google[0]["email"] = "reader@example.org"
    browser, login = signed_in()
    assert login.status_code == 302
    target = edit(auth_service.store, "admin@example.org")
    with web():
        for path in (USERS, NEW, target):
            response = browser.get(path)
            assert response.status_code == 403, path
            assert b"admin@example.org" not in response.content
        for path, values in (
            (NEW, {"action": "preview", "email": "x@example.org", "name": "X"}),
            (DELETE, {"user_id": user_id(auth_service.store, "admin@example.org")}),
            (NAMES, {"email": "x@example.org"}),
        ):
            assert post(browser, path, values).status_code == 403, path
    assert contexts("portal_users_viewed") == []
    assert contexts("user_names_looked_up") == []


def test_new_user_defaults_to_ministry_leader_and_applies_audited(converted):
    """New user's form, review and Apply: one request, its audit, then a user."""
    store, browser = converted
    form = page(browser, NEW)
    assert 'value="ministry_leader" id="id_role_0"' in form
    assert re.search(r'value="ministry_leader"[^>]* checked', form)
    reviewed = review(
        store,
        browser,
        NEW,
        {"email": " New.Person@Example.org ", "name": " Person, New ", "role": "staff"},
    )
    text = reviewed.content.decode()
    assert "new.person@example.org" in text and "Person, New" in text
    assert "Staff" in text and "This adds a new user." in text
    request = user_change(
        store,
        browser,
        NEW,
        {"email": "new.person@example.org", "name": "Person, New", "role": "staff"},
    )
    assert users(store)["new.person@example.org"] == ("Person, New", "staff")
    assert request.request_schema == "users-patch-v10"
    event = AuditEvent.objects.get(
        event_type="user_change_requested", subject_id=request.pk
    )
    assert event.auditcontext.context == {"outcome": "succeeded", "count": 1}
    # Granting Staff raises no security alert.
    assert not PolicySecurityEvent.objects.filter(
        target="new.person@example.org"
    ).exists()


def test_new_user_refusals_are_shown_at_their_fields(converted):
    """An address in use or malformed, and a missing Name, are refused in place."""
    store, browser = converted
    for values, field, message in (
        (
            {"email": "Staff@example.org", "name": "Again", "role": "staff"},
            "id_email",
            "This address is already a user.",
        ),
        (
            {"email": "not-an-address", "name": "X", "role": "staff"},
            "id_email",
            "Enter one email address",
        ),
        (
            {"email": "fresh@example.org", "name": "  ", "role": "staff"},
            "id_name",
            "Enter a name.",
        ),
        (
            {"email": "fresh@example.org", "name": "X", "role": "owner"},
            "id_role",
            "Select a valid choice",
        ),
    ):
        response = review(store, browser, NEW, values)
        assert response.status_code == 400, values
        text = response.content.decode()
        assert message in text and f'href="#{field}' in text
    assert not ConfigurationChangeRequest.objects.filter(
        request_schema="users-patch-v10"
    ).exists()


def test_edit_changes_name_and_role_and_grants_administrator(converted):
    """Edit shows the user; Review shows before and after; Administrator alerts."""
    store, browser = converted
    path = edit(store, "staff@example.org")
    form = page(browser, path)
    assert "staff@example.org" in form and 'name="email"' not in form
    reviewed = review(store, browser, path, {"name": "Staff, Sam", "role": "staff"})
    assert "Staff, Sam" in reviewed.content.decode()
    unchanged = review(store, browser, path, {"name": "staff", "role": "staff"})
    assert unchanged.status_code == 400
    assert "Nothing has changed." in unchanged.content.decode()
    user_change(store, browser, path, {"name": "Staff, Sam", "role": "administrator"})
    assert users(store)["staff@example.org"] == ("Staff, Sam", "administrator")
    event = PolicySecurityEvent.objects.get(target="staff@example.org")
    assert event.kind == "administrator_granted"
    # An unknown or deleted user is refused, even from an old link.
    with web():
        assert browser.get(f"/admin/users/{uuid4()}/edit/").status_code == 404


def test_the_only_administrator_cannot_be_demoted_or_deleted(auth_service, google):
    """The guard is shown and enforced on the server for Edit and Delete."""
    store = auth_service.store
    add_rules(store, address("staff@example.org", ("staff",)))
    convert(store)
    browser, login = signed_in()
    assert login.status_code == 302
    body = page(browser)
    assert 'data-confirm-open="user-delete"' not in row(body, "admin@example.org")
    assert "The only Administrator can't be deleted." in body
    path = edit(store, "admin@example.org")
    form = page(browser, path)
    assert re.search(r'value="staff"[^>]* disabled', form)
    assert "This is the only Administrator" in form
    refused = review(store, browser, path, {"name": "admin", "role": "staff"})
    assert refused.status_code == 400
    assert "This is the only Administrator" in refused.content.decode()
    with web():
        response = post(
            browser,
            DELETE,
            {
                "base_digest": store.active().digest,
                "user_id": user_id(store, "admin@example.org"),
            },
        )
    assert response.status_code == 400
    assert "must stay an Administrator" in response.content.decode()
    assert users(store)["admin@example.org"] == ("admin", "administrator")


def test_delete_records_one_change_and_ends_access(converted, google):
    """The dialog's POST records a change at once; the person can't sign in."""
    store, browser = converted
    google[0].update(email="second@example.org", sub="second-subject")
    other, login = signed_in()
    assert login.status_code == 302
    with web():
        assert other.get(USERS).status_code == 200
        response = post(
            browser,
            DELETE,
            {
                "base_digest": store.active().digest,
                "user_id": user_id(store, "second@example.org"),
            },
        )
    request = installed(store, response)
    assert "second@example.org" not in users(store)
    assert AuditEvent.objects.filter(
        event_type="user_change_requested", subject_id=request.pk
    ).exists()
    # Deletion is the emergency switch: the open session ends on its next
    # request.
    with web():
        assert other.get(USERS).status_code in {302, 403}
    # A stale page's delete, or one naming nobody, changes nothing.
    with web():
        assert (
            post(
                browser,
                DELETE,
                {
                    "base_digest": "0" * 64,
                    "user_id": user_id(store, "staff@example.org"),
                },
            ).status_code
            == 409
        )
        assert (
            post(
                browser,
                DELETE,
                {"base_digest": store.active().digest, "user_id": str(uuid4())},
            ).status_code
            == 404
        )
        assert (
            browser.post(
                DELETE,
                {
                    "base_digest": store.active().digest,
                    "user_id": user_id(store, "staff@example.org"),
                },
            ).status_code
            == 403
        )
    assert "staff@example.org" in users(store)


def test_access_lost_during_the_page_discloses_and_audits_nothing(
    converted, monkeypatch
):
    """A demotion committed while the page is observed is caught by the recheck."""
    store, browser = converted
    admin = PortalUser.objects.get(email="admin@example.org")
    genuine = user_views.policy_identities
    settings = connection.settings_dict

    def disabling(records):
        """Observe as usual while another connection disables the identity."""
        with psycopg.connect(
            host=settings["HOST"],
            port=settings["PORT"],
            user=settings["USER"],
            password=settings["PASSWORD"],
            dbname=settings["NAME"],
        ) as other:
            other.execute(
                "UPDATE stewardship_portal_user SET disabled=true, "
                "version=version+1 WHERE id=%s",
                [admin.pk],
            )
        return genuine(records)

    monkeypatch.setattr(user_views, "policy_identities", disabling)
    with web():
        response = browser.get(USERS)
    assert response.status_code == 403
    assert b"staff@example.org" not in response.content
    assert contexts("portal_users_viewed") == []


def test_name_lookup_checks_the_address_and_suggests_names(converted):
    """The lookup answers JSON, audits only a count, and needs CSRF."""
    from parishkit.stewardship.campaigns.models import Campaign
    from parishkit.stewardship.source.models import SourceCurrent, SourceMutationLease

    from .campaign_builders import add_draft
    from .credential_builders import keys
    from .leader_builders import leader, with_leaders
    from .response_builders import response_source
    from .test_source_families_postgresql import prepare, promote

    store, browser = converted
    add_draft(store, store.active(), uuid4())
    campaign = Campaign.objects.get(
        pk=SystemConfiguration.objects.get().current_campaign_id
    )
    SourceMutationLease.objects.get_or_create(singleton=True)
    SourceCurrent.objects.get_or_create(singleton=True)
    snapshot, claim = prepare(
        with_leaders(
            response_source(),
            [
                leader("shared@example.org", 9, family="inactive"),
                leader(" Shared@Example.org ", 9),
            ],
        )
    )
    promote(snapshot, claim, campaign, keys())

    def ask(email):
        """Post the lookup as the page's script does."""
        with web():
            response = post(browser, NAMES, {"email": email})
        assert response.status_code == 200, response.content
        return json.loads(response.content)

    # Any address, not only a rule's or an identity's: the active Family's
    # Member first, matched trimmed and case-insensitively.
    assert ask(" SHARED@example.org ") == {
        "error": None,
        "names": ["Example 1, Leader", "Example 0, Leader"],
    }
    assert ask("nobody@example.org") == {"error": None, "names": []}
    assert ask("Staff@example.org")["error"].startswith("This address is already")
    assert ask("not-an-address")["error"].startswith("Enter one email address")
    # Only the two real lookups are audited, by count alone.
    looked_up = contexts("user_names_looked_up")
    assert sorted(item["count"] for item in looked_up) == [0, 2]
    assert {item["outcome"] for item in looked_up} == {"succeeded"}
    with web():
        assert browser.post(NAMES, {"email": "x@example.org"}).status_code == 403
        assert post(browser, NAMES + "?email=x", {"email": "x"}).status_code == 400
    # The web login runs the definer itself; it holds no Member grant.
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        assert member_names("shared@example.org") == [
            "Example 1, Leader",
            "Example 0, Leader",
        ]


def test_the_name_lookup_definer_is_installed_as_declared():
    """Migration 0053: a definer with a fixed path, web's alone to run."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT p.prosecdef, p.proconfig, "
            "has_function_privilege('public', p.oid, 'EXECUTE') "
            "FROM pg_proc p WHERE p.proname='stewardship_member_names_for_v1'"
        )
        assert cursor.fetchall() == [
            (True, ["search_path=pg_catalog, public, pg_temp"], False)
        ]
    with (
        task_login(ServiceRole.WORKER, exact=True, reconnect=True),
        pytest.raises(Exception, match="permission denied"),
    ):
        member_names("x@example.org")


def test_an_added_user_signs_in_with_its_role(converted, google):
    """A user added through the page is admitted by SQL as that role."""
    store, browser = converted
    user_change(
        store,
        browser,
        NEW,
        {
            "email": "leader@example.org",
            "name": "Leader, Lee",
            "role": "ministry_leader",
        },
    )
    google[0].update(email="leader@example.org", sub="leader-subject")
    leader_browser, login = signed_in()
    assert login.status_code == 302
    # A Ministry leader is not an Administrator: Users is refused.
    with web():
        assert leader_browser.get(USERS).status_code == 403
