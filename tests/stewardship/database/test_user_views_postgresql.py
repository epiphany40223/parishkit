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
from django.db import DatabaseError, connection

from parishkit.stewardship.accounts import user_views
from parishkit.stewardship.accounts.admin_session_store import digest
from parishkit.stewardship.accounts.models import PortalSession
from parishkit.stewardship.accounts.policy_models import PolicySecurityEvent, PortalUser
from parishkit.stewardship.accounts.request_models import ConfigurationChangeRequest
from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.accounts.user_changes import LookupLimited, member_names
from parishkit.stewardship.audit.models import AuditContext, AuditEvent
from parishkit.stewardship.deployment import ServiceRole

from ..policy_factory import address
from .auth_builders import signed_in, unguarded
from .leader_builders import grant_role
from .test_background_grants_postgresql import task_login
from .user_builders import (
    USERS,
    add_rules,
    convert,
    installed,
    post,
    review,
    token,
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
    """The Users table row whose header cell is this address.

    The address links the user's Timeline (#1866).
    """
    found = re.findall(
        r'<tr>\s*<th scope="row"><a href="/admin/users/[0-9a-f-]+/timeline/" '
        rf'class="cell-ellipsis" title="{re.escape(email)}">.*?</tr>',
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
    # The Administrator signed in through the fixture; the time is drawn in
    # the compact parish format, as other dense tables are. No row has a
    # Sign-in activity control (Administrator, 2026-10-11): the link above
    # the table opens System logs.
    assert "data-local-instant data-compact" in admin
    assert 'name="actor"' not in body and "<form" not in admin
    assert "See sign-in activity in System logs" in body
    assert f'href="{edit(store, "admin@example.org")}"' in admin
    # The reader's own row has no Delete, though another Administrator
    # remains; the other Administrator's row has one.
    assert 'data-confirm-open="user-delete"' not in admin
    assert 'data-confirm-open="user-delete"' in row(body, "second@example.org")
    assert "delete yourself" in body
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


def test_the_own_row_note_counts_every_page(auth_service, google):
    """The note under the table holds even when the reader's own row is on
    another page of the table, not only when it is shown."""
    add_rules(
        auth_service.store,
        *(address(f"staff{number:02}@example.org", ("staff",)) for number in range(25)),
    )
    convert(auth_service.store)
    browser, login = signed_in()
    assert login.status_code == 302
    note = "delete yourself"
    # Email descending puts admin@example.org on page 2 of 25 rows each.
    first = page(browser, USERS + "?users_sort=-email&users_size=25")
    assert "admin@example.org" not in first
    assert note in first
    assert note in page(
        browser, USERS + "?users_sort=-email&users_size=25&users_page=2"
    )


def test_the_page_counts_leaders_who_never_signed_in(auth_service, google):
    """The line under the table counts Ministry leaders never signed in (#1593).

    Staff without a sign-in are not counted; the About panel carries the
    shared Google Account guidance for the Administrator to pass on.
    """
    add_rules(
        auth_service.store,
        address("lee@example.org", ("ministry_leader",)),
        address("lou@example.org", ("ministry_leader",)),
        address("staff@example.org", ("staff",)),
    )
    convert(auth_service.store)
    browser, login = signed_in()
    assert login.status_code == 302
    body = page(browser)
    assert (
        '<p class="help" id="users-never-signed-in">'
        "2 Ministry leaders have never signed in.</p>"
    ) in body
    assert body.count("data-google-account-help") == 1


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
    # A confirmation (#1356): the sentence, none of the form's values, and
    # no Administrator warning for Staff.
    assert '<p id="user-review-summary">This adds the user' in text
    assert "new.person@example.org" not in text and "Person, New" not in text
    assert "Staff" not in text and "Administrator, who can see" not in text
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


def test_a_new_administrator_is_confirmed_with_its_warning(converted):
    """New user's confirmation keeps the one thing the form does not say
    (#1356): the person becomes an Administrator, whom every Administrator
    is told about. Nothing is recorded by the Review."""
    store, browser = converted
    reviewed = review(
        store,
        browser,
        NEW,
        {"email": "boss@example.org", "name": "Boss, Bo", "role": "administrator"},
    )
    assert reviewed.status_code == 200
    text = reviewed.content.decode()
    assert '<p id="user-review-summary">This adds the user' in text
    assert "This makes the person an Administrator" in text
    assert "boss@example.org" not in text and "boss@example.org" not in users(store)


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


def test_edit_is_a_compact_review_whose_preview_applies(converted):
    """Edit user's Review (#1356, class A2) answers one "Field: old → new"
    line per changed field and the Administrator warning, under the web
    role, recording nothing; Apply posts that same signed preview."""
    store, browser = converted
    path = edit(store, "staff@example.org")
    reviewed = review(
        store, browser, path, {"name": "Staff, Sam", "role": "administrator"}
    )
    assert reviewed.status_code == 200
    text = reviewed.content.decode()
    lines = re.findall(r"<li>(.*?)</li>", text, flags=re.S)
    assert len(lines) == 2
    assert lines[0].startswith('<span class="compact-review-field">Name:</span> staff ')
    assert lines[0].endswith(" Staff, Sam")
    assert lines[1].startswith('<span class="compact-review-field">Role:</span> Staff ')
    assert lines[1].endswith(" Administrator")
    assert '<ul class="compact-review" id="user-review-summary">' in text
    assert "This makes the person an Administrator" in text and "<table" not in text
    assert users(store)["staff@example.org"] == ("staff", "staff")
    signed = token(reviewed)
    with web():
        response = post(browser, USERS, {"action": "confirm", "preview": signed})
    installed(store, response)
    assert users(store)["staff@example.org"] == ("Staff, Sam", "administrator")


def test_the_only_administrator_cannot_be_demoted_or_deleted(auth_service, google):
    """The guard is shown and enforced on the server for Edit and Delete.

    The only Administrator is always the reader, so the own-Administrator
    words are the ones shown.
    """
    store = auth_service.store
    add_rules(store, address("staff@example.org", ("staff",)))
    convert(store)
    browser, login = signed_in()
    assert login.status_code == 302
    body = page(browser)
    assert 'data-confirm-open="user-delete"' not in row(body, "admin@example.org")
    assert "delete yourself" in body
    path = edit(store, "admin@example.org")
    form = page(browser, path)
    assert re.search(r'value="staff"[^>]* disabled', form)
    assert "This is your own user" in form
    refused = review(store, browser, path, {"name": "admin", "role": "staff"})
    assert refused.status_code == 400
    assert "This is your own user" in refused.content.decode()
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
    assert "delete yourself" in response.content.decode()
    assert users(store)["admin@example.org"] == ("admin", "administrator")


def test_an_administrator_cannot_demote_or_delete_themselves(
    converted, google, monkeypatch
):
    """Refused at Review and Delete, and again by the request's own check.

    Another Administrator remains, so only the own-Administrator guard
    applies; that other Administrator may still demote this one.
    """
    store, browser = converted
    path = edit(store, "admin@example.org")
    form = page(browser, path)
    assert re.search(r'value="staff"[^>]* disabled', form)
    assert re.search(r'value="ministry_leader"[^>]* disabled', form)
    assert "This is your own user" in form
    # Another user's Edit page leaves every role available.
    other = page(browser, edit(store, "second@example.org"))
    assert not re.search(r'value="staff"[^>]* disabled', other)
    refused = review(store, browser, path, {"name": "admin", "role": "staff"})
    assert refused.status_code == 400
    assert "This is your own user" in refused.content.decode()
    # A Name change of one's own user is still a change.
    assert (
        review(
            store, browser, path, {"name": "Admin, A", "role": "administrator"}
        ).status_code
        == 200
    )
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
    assert "delete yourself" in response.content.decode()
    # A review signed without the builder's guard is still refused by the
    # request's own check, in its transaction: nothing is recorded.
    real = user_views.plan_changes
    monkeypatch.setattr(
        user_views,
        "plan_changes",
        lambda records, entries, **options: real(records, entries),
    )
    signed = token(review(store, browser, path, {"name": "admin", "role": "staff"}))
    monkeypatch.setattr(user_views, "plan_changes", real)
    with web():
        applied = post(browser, USERS, {"action": "confirm", "preview": signed})
    assert applied.status_code == 400
    assert "remove your own Administrator role" in applied.content.decode()
    assert not ConfigurationChangeRequest.objects.filter(
        request_schema="users-patch-v10"
    ).exists()
    assert contexts("user_change_requested") == []
    assert users(store)["admin@example.org"] == ("admin", "administrator")
    # The other Administrator may demote this one.
    google[0].update(email="second@example.org", sub="second-subject")
    second, login = signed_in()
    assert login.status_code == 302
    user_change(store, second, path, {"name": "admin", "role": "staff"})
    assert users(store)["admin@example.org"] == ("admin", "staff")


def test_change_status_refuses_a_reader_who_lost_access(converted, google):
    """The status read a demoted reader makes is refused, never left waiting.

    Change status needs Administrator; the page's script turns this refusal
    into its final "Status unavailable" (browser-tested).
    """
    store, browser = converted
    signed = token(
        review(
            store,
            browser,
            edit(store, "staff@example.org"),
            {"name": "S", "role": "staff"},
        )
    )
    with web():
        response = post(browser, USERS, {"action": "confirm", "preview": signed})
    status = response["Location"]
    assert "data-live-refused" in page(browser, status)
    google[0].update(email="second@example.org", sub="second-subject")
    second, login = signed_in()
    assert login.status_code == 302
    user_change(
        store,
        second,
        edit(store, "admin@example.org"),
        {"name": "admin", "role": "staff"},
    )
    with web():
        assert browser.get(status).status_code in {302, 403}


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
        assert member_names(live_key(browser), "shared@example.org") == [
            "Example 1, Leader",
            "Example 0, Leader",
        ]


def live_key(browser):
    """The Admin session key this browser's cookie carries (#995).

    The database stores only its digest (``stored``), which the definer
    refuses like any other unknown key.
    """
    return browser.cookies["pk_admin"].value


def stored(key):
    """The ``stewardship_portal_session.session_id`` of a session key."""
    return digest(key)


def lookups(email="admin@example.org"):
    """The Name lookup audit events recorded for this address's user."""
    return AuditEvent.objects.filter(
        event_type="user_names_looked_up",
        actor_id=PortalUser.objects.get(email=email).id,
    )


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
        member_names("no-session", "x@example.org")


def test_the_definer_answers_only_a_live_administrator_session(converted):
    """No key, a made-up key, the stored session_id or an ended session:
    refused, nothing audited."""
    from django.utils import timezone

    store, browser = converted
    key = live_key(browser)
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        # The stored digest names no session: the definer digests what it gets.
        for refused in (None, "", "not-a-session-key", "x" * 40, stored(key)):
            with pytest.raises(DatabaseError, match="live Administrator session"):
                member_names(refused, "x@example.org")
        assert member_names(key, "x@example.org") == []
    assert lookups().count() == 1
    # A revoked session no longer answers, even under the owner.
    with unguarded():
        PortalSession.objects.filter(session_id=stored(key)).update(
            revoked_at=timezone.now()
        )
    with (
        task_login(ServiceRole.WEB, exact=True, reconnect=True),
        pytest.raises(DatabaseError, match="live Administrator session"),
    ):
        member_names(key, "x@example.org")
    assert lookups().count() == 1


def test_a_session_from_before_a_recovery_is_refused(converted):
    """An offline recovery after the session began ends its lookups.

    The recovery epoch sessions.py checks, done in SQL: the row stays
    unrevoked here (the recovery's own trigger would revoke it), so only the
    definer's recovery check refuses it.
    """
    _store, browser = converted
    key = live_key(browser)
    with unguarded(), connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO stewardship_admin_revocation "
            "(id, created_at, actor_id, correlation_id, activation_id) "
            "VALUES (%s, clock_timestamp(), NULL, %s, %s)",
            [uuid4(), uuid4(), uuid4()],
        )
    with (
        task_login(ServiceRole.WEB, exact=True, reconnect=True),
        pytest.raises(DatabaseError, match="live Administrator session"),
    ):
        member_names(key, "x@example.org")
    assert lookups().count() == 0


def test_a_staff_session_is_refused(auth_service, google):
    """Staff has a live Admin session but is not an Administrator."""
    grant_role(auth_service.store, "reader@example.org", "staff")
    convert(auth_service.store)
    google[0]["email"] = "reader@example.org"
    browser, login = signed_in()
    assert login.status_code == 302
    key = live_key(browser)
    with (
        task_login(ServiceRole.WEB, exact=True, reconnect=True),
        pytest.raises(DatabaseError, match="live Administrator session"),
    ):
        member_names(key, "x@example.org")
    assert lookups("reader@example.org").count() == 0


def test_each_lookup_is_audited_and_the_61st_in_an_hour_refused(converted):
    """One audit event per lookup, by count; 60 an hour per Administrator."""
    from datetime import timedelta

    _store, browser = converted
    key = live_key(browser)
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        for number in range(60):
            assert member_names(key, f"person{number}@example.org") == []
        with pytest.raises(LookupLimited):
            member_names(key, "one-more@example.org")
    events = lookups()
    assert events.count() == 60
    contexts_found = AuditContext.objects.filter(event__in=events)
    assert {(item.actor_kind, item.schema) for item in contexts_found} == {
        ("portal_user", "action")
    }
    assert {json.dumps(item.context, sort_keys=True) for item in contexts_found} == {
        '{"count": 0, "outcome": "succeeded"}'
    }
    # Never the address, in the event or its context.
    assert "@" not in str(list(events.values())) + str(
        list(contexts_found.values_list("context", flat=True))
    )
    # Lookups older than an hour no longer count. The audit log is append
    # only, so the oldest event is aged as only the owner could.
    oldest = events.order_by("created_at").first()
    with unguarded(), connection.cursor() as cursor:
        cursor.execute(
            "UPDATE stewardship_audit_event SET created_at=created_at-%s WHERE id=%s",
            [timedelta(hours=1, minutes=1), oldest.id],
        )
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        assert member_names(key, "after@example.org") == []
        with pytest.raises(LookupLimited):
            member_names(key, "one-more@example.org")


def test_the_page_limits_lookups_and_fails_closed(converted, monkeypatch):
    """The page's per-minute window answers 429; no limiter answers 503."""
    from parishkit.stewardship.accounts.authentication import runtime
    from parishkit.stewardship.accounts.limiting import LimiterUnavailable

    _store, browser = converted
    statuses = []
    with web():
        for number in range(user_views.LOOKUP_LIMIT):
            statuses.append(
                post(browser, NAMES, {"email": f"p{number}@example.org"}).status_code
            )
    assert statuses == [200] * (user_views.LOOKUP_LIMIT - 1) + [429]
    assert lookups().count() == user_views.LOOKUP_LIMIT - 1

    def unavailable(*_args, **_kwargs):
        """The limiter's store is down."""
        raise LimiterUnavailable("Authentication is temporarily unavailable.")

    monkeypatch.setattr(runtime().limiter, "counters", unavailable)
    with web():
        response = post(browser, NAMES, {"email": "down@example.org"})
    assert response.status_code == 503
    assert lookups().count() == user_views.LOOKUP_LIMIT - 1


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


def fetched(browser, path, values):
    """Post as the review dialog's script does (``X-Requested-With: fetch``)."""
    return browser.post(
        path,
        values | {"csrfmiddlewaretoken": browser.cookies["pk_admin_csrf"].value},
        headers={"X-Requested-With": "fetch"},
    )


def test_the_review_dialog_gets_a_fragment_and_follows_the_change(converted):
    """Review answers the dialog's fragment; Apply answers Change status,
    which the dialog follows; Users holds the status line it ends on (#750)."""
    store, browser = converted
    form = page(browser, NEW)
    assert 'data-review-open="user-review"' in form
    dialog = form[form.index('<dialog id="user-review"') :]
    assert 'data-review-return="/admin/users/"' in dialog
    assert 'data-review-outcome="users-outcome"' in dialog
    assert (
        '<form id="user-review-apply" method="post" '
        'action="/admin/users/" data-review-apply>' in dialog
    )
    values = {"email": "dialog@example.org", "name": "Dialog, Dee", "role": "staff"}
    with web():
        reviewed = fetched(
            browser,
            NEW,
            {"action": "preview", "base_digest": store.active().digest} | values,
        )
    assert reviewed.status_code == 200, reviewed.content
    text = reviewed.content.decode()
    assert "data-review-answer" in text and 'data-review-done="User added."' in text
    assert "<html" not in text and "<form" not in text and "<h1" not in text
    assert "dialog@example.org" not in users(store)
    with web():
        applied = fetched(
            browser, USERS, {"action": "confirm", "preview": token(reviewed)}
        )
    installed(store, applied)
    assert users(store)["dialog@example.org"] == ("Dialog, Dee", "staff")
    assert contexts("user_change_requested") == [{"outcome": "succeeded", "count": 1}]
    body = page(browser)
    assert re.search(
        r'<p class="review-outcome" id="users-outcome" role="status" '
        r'tabindex="-1" data-review-outcome></p>',
        body,
    )
    # Users' leader import opens the same dialog, applying here in place.
    assert 'data-review-open="leader-review"' in body
    leader = body[body.index('<dialog id="leader-review"') :]
    assert 'data-review-return=""' in leader


def test_a_field_error_answers_the_form_for_the_page_to_mark(converted):
    """A refused value is the form again (400), marked, never a review."""
    store, browser = converted
    with web():
        refused = fetched(
            browser,
            NEW,
            {
                "action": "preview",
                "base_digest": store.active().digest,
                "email": "fresh@example.org",
                "name": "  ",
                "role": "staff",
            },
        )
    assert refused.status_code == 400
    text = refused.content.decode()
    assert 'id="user-entry-form"' in text and "data-review-answer" not in text
    assert "data-error-summary" in text and 'href="#id_name"' in text
    assert re.search(r'name="name"[^>]*aria-invalid="true"', text)
    assert re.search(
        r'<ul class="errorlist" id="id_name_error">\s*<li>Enter a name.', text
    )


def test_other_refusals_reach_the_dialog_in_the_servers_words(converted, monkeypatch):
    """A stale page, an out-of-date preview and a self-demotion at Apply
    answer the dialog's script with JSON; nothing is recorded."""
    store, browser = converted
    with web():
        stale = fetched(
            browser,
            NEW,
            {
                "action": "preview",
                "base_digest": "0" * 64,
                "email": "late@example.org",
                "name": "Late, Lou",
                "role": "staff",
            },
        )
    assert stale.status_code == 409
    assert stale["Content-Type"].startswith("application/json")
    assert json.loads(stale.content)["errors"][0]["code"] == "stale_version"
    # A review made out of date by a change applied meanwhile.
    with web():
        early = fetched(
            browser,
            NEW,
            {
                "action": "preview",
                "base_digest": store.active().digest,
                "email": "late@example.org",
                "name": "Late, Lou",
                "role": "staff",
            },
        )
    signed = token(early)
    user_change(
        store, browser, edit(store, "staff@example.org"), {"name": "S", "role": "staff"}
    )
    before = ConfigurationChangeRequest.objects.count()
    with web():
        late = fetched(browser, USERS, {"action": "confirm", "preview": signed})
    assert late.status_code == 409
    assert json.loads(late.content)["refusal"]["message"] == (
        "This preview is out of date."
    )
    # A self-demotion signed without the builder's guard is refused by the
    # request's own check, in words the dialog shows.
    path = edit(store, "admin@example.org")
    real = user_views.plan_changes
    monkeypatch.setattr(
        user_views,
        "plan_changes",
        lambda records, entries, **options: real(records, entries),
    )
    with web():
        demoting = fetched(
            browser,
            path,
            {
                "action": "preview",
                "base_digest": store.active().digest,
                "name": "admin",
                "role": "staff",
            },
        )
    monkeypatch.setattr(user_views, "plan_changes", real)
    with web():
        refused = fetched(
            browser, USERS, {"action": "confirm", "preview": token(demoting)}
        )
    assert refused.status_code == 400
    refusal = json.loads(refused.content)["refusal"]
    assert "remove your own Administrator role" in refusal["message"]
    assert refusal["fix"] == "Nothing was changed."
    assert ConfigurationChangeRequest.objects.count() == before
    assert "late@example.org" not in users(store)
    assert users(store)["admin@example.org"] == ("admin", "administrator")


def test_a_refusal_of_the_whole_change_goes_to_the_dialog(converted):
    """An unchanged Review (a safeguard the gate makes unreachable) has no
    field, so it is the plain JSON refusal the dialog shows, not the form."""
    store, browser = converted
    with web():
        unchanged = fetched(
            browser,
            edit(store, "staff@example.org"),
            {
                "action": "preview",
                "base_digest": store.active().digest,
                "name": "staff",
                "role": "staff",
            },
        )
    assert unchanged.status_code == 400
    assert unchanged["Content-Type"].startswith("application/json")
    refusal = json.loads(unchanged.content)["refusal"]
    assert refusal["message"].startswith("Nothing has changed.")
    assert refusal["fix"] == "Nothing was changed."
