"""ParishSoft Ministry leaders without a user, and their import (#952 slice 6).

Under the real web database role: the leaders definer (migration 0054)
answers only a live Administrator session, agrees with 0042's leaders
definition, audits each read by count; the Users page lists the leaders
below the users table; Import selected and Import all review the chosen
leaders and Apply records one configuration change with its audit rows;
``pk-admin users import-leaders`` runs the same code.
"""

import re
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from django.db import DatabaseError, connection, transaction

from parishkit.stewardship import admin_cli
from parishkit.stewardship.accounts.leader_import import LeadersRefused, read_leaders
from parishkit.stewardship.accounts.models import PortalSession
from parishkit.stewardship.accounts.policy_models import PolicySecurityEvent, PortalUser
from parishkit.stewardship.accounts.request_models import ConfigurationChangeRequest
from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.audit.models import AuditContext, AuditEvent
from parishkit.stewardship.deployment import ServiceRole

from ..policy_factory import address
from .auth_builders import signed_in, unguarded
from .automation_builders import paired
from .campaign_builders import add_draft, change
from .leader_builders import grant_role, leader, with_leaders
from .response_builders import response_source
from .test_admin_status_cli_postgresql import admin, one  # noqa: F401
from .test_background_grants_postgresql import task_login
from .test_source_families_postgresql import publish
from .user_builders import (
    USERS,
    add_rules,
    convert,
    installed,
    post,
    token,
    users,
    web,
)

pytestmark = pytest.mark.django_db(transaction=True)

IMPORT = "/admin/users/leaders/import/"
MIGRATION = (
    Path(__file__).resolve().parents[3]
    / "src/parishkit/stewardship/schema/migrations/0054_leaders_without_user.sql"
)
# Leader Members get DUIDs from 900 and the name "Example <n>, Leader", in
# this order (leader_builders.with_leaders).
LEADERS = [
    leader("ann@example.org", 9),  # 900
    # Two Members share one address: one row, their Ministries' union, and
    # the first Member by name gives the Name and DUID.
    leader("shared@example.org", 4),  # 901
    leader(" Shared@Example.org ", 9, role="Chairperson"),  # 902
    # Already a user: never listed.
    leader("staff@example.org", 9),  # 903
    # An active Member of a parishioner Family leads, as a non-parishioner
    # (every leader above) does.
    leader("parish@example.org", 4, family="parishioner"),  # 904
    # Inactive Members, Members of an inactive Family, a role that is not a
    # leader role and a Ministry outside the campaign lead nothing.
    leader("inactive@example.org", 9, status="Inactive"),  # 905
    leader("gone@example.org", 9, family="inactive"),  # 906
    leader("member@example.org", 9, role="Member"),  # 907
    leader("outside@example.org", 5),  # 908
    # A leader with no valid address cannot sign in: only counted.
    leader("not an address", 9),  # 909
]
LISTED = {
    "ann@example.org": ("Example 0, Leader", 900, [9]),
    "shared@example.org": ("Example 1, Leader", 901, [4, 9]),
    "parish@example.org": ("Example 4, Leader", 904, [4]),
    # The response fixture's own Choir Chairperson.
    "valid@example.org": ("Example, Member", 3, [4]),
}


def leaders_source(store, leaders=LEADERS):
    """A draft campaign asking about Choir (4) and Food pantry (9), and leaders.

    Promotes the response fixture's source with the leader Members (no
    Family effects), then turns Ministry stewardship on for those two
    Ministries through the ordinary configuration path.
    """
    from parishkit.stewardship.campaigns.domain import EnabledModules
    from parishkit.stewardship.campaigns.models import Campaign
    from parishkit.stewardship.source.models import SourceCurrent, SourceMutationLease

    add_draft(store, store.active(), uuid4())
    campaign = Campaign.objects.get(
        pk=SystemConfiguration.objects.get().current_campaign_id
    )
    SourceMutationLease.objects.get_or_create(singleton=True)
    SourceCurrent.objects.get_or_create(singleton=True)
    source = response_source()
    source.ministry_types[5] = {"id": 5, "name": "Ushers"}
    source.ministry_type_memberships[5] = {"membership": []}
    publish(with_leaders(source, leaders))
    values = campaign.active_configuration.values
    result = change(
        store,
        store.active(),
        uuid4(),
        [
            {
                "operation": "update",
                "section": "campaigns",
                "id": str(campaign.pk),
                "values": {
                    "modules": EnabledModules.from_list(
                        sorted({*values["modules"], "ministry"})
                    ).to_list(),
                    "ministry_duids": [4, 9],
                },
            }
        ],
    )
    assert result.state == "applied"


@pytest.fixture
def deployment(auth_service, google):
    """A users deployment with leaders in ParishSoft; the Administrator signed in."""
    store = auth_service.store
    add_rules(store, address("staff@example.org", ("staff",)))
    convert(store)
    leaders_source(store)
    browser, login = signed_in()
    assert login.status_code == 302
    return store, browser


def live_key(email="admin@example.org"):
    """The Django session key of this address's live Admin session."""
    return (
        PortalSession.objects.filter(
            revoked_at=None, principal_id=PortalUser.objects.get(email=email).id
        )
        .latest("authenticated_at")
        .session_id
    )


def listed(email="admin@example.org"):
    """The leaders-listed audit events recorded for this address's user."""
    return AuditEvent.objects.filter(
        event_type="user_leaders_listed",
        actor_id=PortalUser.objects.get(email=email).id,
    )


def as_web(key):
    """``read_leaders`` under the real web login."""
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        return read_leaders(key)


def test_the_definer_lists_leaders_without_a_user(deployment):
    """Rows, names "Last, first", the union of Ministries, the unreachable count."""
    leaders = as_web(live_key())
    assert {
        row["email"]: (row["name"], row["member_duid"], row["ministries"])
        for row in leaders.rows
    } == LISTED
    # Name order, as the table's default.
    assert [row["email"] for row in leaders.rows] == list(LISTED)
    assert leaders.unreachable == 1
    # One audit event per read, by count alone; never an address.
    events = listed()
    assert events.count() == 1
    context = AuditContext.objects.get(event__in=events)
    assert context.context == {"outcome": "succeeded", "count": len(LISTED)}
    assert (context.actor_kind, context.schema) == ("portal_user", "action")
    assert "@" not in str(list(events.values())) + str(context.context)


def test_the_definer_agrees_with_the_leaders_definition(deployment):
    """Every address 0042 counts as a leader, and no other, less the users.

    0054 copies 0042's conditions to keep leaders with no valid address;
    this pins the copy to the original on the same data.
    """
    store, _browser = deployment
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT email, array_agg(DISTINCT ministry_duid ORDER BY ministry_duid) "
            "FROM public.stewardship_ministry_leaders_v1() GROUP BY email"
        )
        defined = {email.strip(): list(duids) for email, duids in cursor.fetchall()}
    users_now = set(users(store))
    expected = {
        email: duids for email, duids in defined.items() if email not in users_now
    }
    assert "staff@example.org" in defined and "staff@example.org" not in expected
    rows = as_web(live_key()).rows
    assert {row["email"]: row["ministries"] for row in rows} == expected


def test_no_campaign_or_source_lists_nobody(auth_service, google):
    """Without a Ministry campaign there are no leaders, and that is audited."""
    convert(auth_service.store)
    _browser, login = signed_in()
    assert login.status_code == 302
    leaders = as_web(live_key())
    assert (leaders.rows, leaders.unreachable) == ([], 0)
    assert listed().count() == 1


def test_the_definer_answers_only_a_live_administrator_session(deployment):
    """No key, a made-up key, a revoked session, or a recovery since: refused."""
    from django.utils import timezone

    key = live_key()
    for refused in (None, "", "not-a-session-key", "x" * 40):
        with pytest.raises(LeadersRefused):
            as_web(refused)
    assert listed().count() == 0
    with unguarded(), connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO stewardship_admin_revocation "
            "(id, created_at, actor_id, correlation_id, activation_id) "
            "VALUES (%s, clock_timestamp(), NULL, %s, %s)",
            [uuid4(), uuid4(), uuid4()],
        )
    with pytest.raises(LeadersRefused):
        as_web(key)
    with unguarded():
        PortalSession.objects.filter(session_id=key).update(revoked_at=timezone.now())
    with pytest.raises(LeadersRefused):
        as_web(key)
    assert listed().count() == 0


@pytest.mark.parametrize("role", ["staff", "ministry_leader"])
def test_other_roles_get_neither_the_list_nor_the_import(auth_service, google, role):
    """The definer refuses a Staff or Ministry leader session; so do the routes."""
    grant_role(auth_service.store, "reader@example.org", role)
    convert(auth_service.store)
    google[0]["email"] = "reader@example.org"
    browser, login = signed_in()
    assert login.status_code == 302
    with pytest.raises(LeadersRefused):
        as_web(live_key("reader@example.org"))
    with web():
        response = post(browser, IMPORT, {"scope": "all"})
    assert response.status_code == 403
    assert not AuditEvent.objects.filter(event_type="user_leaders_listed").exists()


def test_the_definer_is_installed_as_declared():
    """Migration 0054: a definer with a fixed path, web's alone to run."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT p.prosecdef, p.proconfig, "
            "has_function_privilege('public', p.oid, 'EXECUTE') "
            "FROM pg_proc p WHERE p.proname='stewardship_leaders_without_user_v1'"
        )
        assert cursor.fetchall() == [
            (True, ["search_path=pg_catalog, public, pg_temp"], False)
        ]
    with (
        task_login(ServiceRole.WORKER, exact=True, reconnect=True),
        pytest.raises(DatabaseError, match="permission denied"),
    ):
        read_leaders("no-session")


def test_the_check_block_refuses_a_wrongly_installed_definer():
    """0054's DO block raises once the definer is no longer a definer."""
    text = MIGRATION.read_text(encoding="utf-8")
    check = text[text.index("DO $check$") :]
    with (
        pytest.raises(DatabaseError, match="Migration 0054"),
        transaction.atomic(),
        connection.cursor() as cursor,
    ):
        cursor.execute(
            "ALTER FUNCTION public.stewardship_leaders_without_user_v1(text, uuid) "
            "SECURITY INVOKER"
        )
        cursor.execute(check)
    # The real installation passes it.
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(check)


def page(browser, path=USERS):
    """The Users page as the signed-in browser sees it, under the web role."""
    with web():
        response = browser.get(path)
    assert response.status_code == 200, response.content
    return response.content.decode()


def leaders_region(body):
    """The leaders table's region of the Users page."""
    start = body.index('<section id="leaders-table"')
    return body[start : body.index("</section>", start)]


def test_the_page_lists_the_leaders_below_the_users(deployment):
    """Selection, Name, Member DUID, Email, Ministries led, Role; sorted on the
    server."""
    _store, browser = deployment
    region = leaders_region(page(browser))
    headings = re.findall(r"<th[^>]*scope=\"col\"[^>]*>(.*?)</th>", region, flags=re.S)
    labels = [re.sub(r"<[^>]+>", " ", heading) for heading in headings]
    order = ["Name", "Member DUID", "Email", "Ministries led", "Role"]
    positions = [
        next(i for i, text in enumerate(labels) if name in text) for name in order
    ]
    assert positions == sorted(positions) and positions[0] == 1
    assert "data-select-all" in headings[0]
    for email in LISTED:
        assert f'name="email" value="{email}"' in region
    assert "staff@example.org" not in region and "inactive@example.org" not in region
    # The Ministries cell names them by name, the full list in its tooltip
    # (Ministry 9 has the leader fixture's plain name).
    assert (
        '<span class="cell-ellipsis" title="Choir, Ministry 9">Choir, Ministry 9'
        in region
    )
    assert '<td class="numeric">901</td>' in region
    assert "Ministry leader" in region
    # Leaders with no usable address are counted in a muted line.
    assert "1 ParishSoft Ministry leader has no email address" in region
    # The bulk bar: Import selected waits for a tick, its visible reason in a
    # reserved line; Import all is available.
    assert re.search(r"data-bulk-action[^>]*>Import selected", region)
    assert (
        'id="leaders-import-reason" data-selection-reason>Select at least one' in region
    )
    assert re.search(r'value="all"\s*>Import all', region)
    # Server sorting with its own prefix, and only its own tokens.
    by_email = leaders_region(page(browser, USERS + "?leaders_sort=-email"))
    assert by_email.index("shared@example.org") < by_email.index("ann@example.org")
    with web():
        assert browser.get(USERS + "?leaders_sort=role").status_code == 400
    # Each page view reads the leaders once, audited by the definer.
    assert listed().count() == 2


def test_import_selected_reviews_and_applies_one_change(deployment):
    """Two leaders become Ministry leader users in one audited change."""
    store, browser = deployment
    with web():
        response = post(
            browser,
            IMPORT,
            {"scope": "selected", "email": ["Shared@Example.org", "ann@example.org"]},
        )
    assert response.status_code == 200, response.content
    text = response.content.decode()
    assert "<h1>Import Ministry leaders</h1>" in text and "2 new users" in text
    assert "Example 1, Leader" in text and "Example 0, Leader" in text
    assert "Skipped" not in text
    # Nothing changed by reviewing.
    assert "ann@example.org" not in users(store)
    with web():
        applied = post(
            browser, USERS, {"action": "confirm", "preview": token(response)}
        )
    request = installed(store, applied)
    now = users(store)
    assert now["ann@example.org"] == ("Example 0, Leader", "ministry_leader")
    assert now["shared@example.org"] == ("Example 1, Leader", "ministry_leader")
    assert now["staff@example.org"][1] == "staff"
    assert request.request_schema == "users-patch-v10"
    # One audit entry naming the count, one per new user (by record ID).
    summary = AuditEvent.objects.get(
        event_type="user_leaders_import_requested", subject_id=request.pk
    )
    assert summary.auditcontext.context == {"outcome": "succeeded", "count": 2}
    added = {
        UUID(record["id"])
        for record in store.active().document()["sections"]["login_rules"]
        if record["values"]["email"] in {"ann@example.org", "shared@example.org"}
    }
    assert {
        event.subject_id
        for event in AuditEvent.objects.filter(event_type="user_leader_imported")
    } == added
    assert not AuditEvent.objects.filter(event_type="user_change_requested").exists()
    # Ministry leader only: no Administrator security alert.
    assert not PolicySecurityEvent.objects.filter(target="ann@example.org").exists()
    # The table now lists only the leader left.
    region = leaders_region(page(browser))
    assert 'value="parish@example.org"' in region
    assert 'value="ann@example.org"' not in region


def test_the_review_skips_users_and_leaders_no_longer_listed(deployment):
    """An existing user is never changed; an address not listed is skipped."""
    store, browser = deployment
    before = users(store)
    with web():
        response = post(
            browser,
            IMPORT,
            {
                "scope": "selected",
                "email": [
                    "staff@example.org",
                    "nobody@example.org",
                    "parish@example.org",
                ],
            },
        )
    text = response.content.decode()
    assert response.status_code == 200, text
    assert "1 new user" in text and "Example 4, Leader" in text
    assert "Already a user" in text and "No longer a ParishSoft" in text
    with web():
        applied = post(
            browser, USERS, {"action": "confirm", "preview": token(response)}
        )
    installed(store, applied)
    after = users(store)
    assert after.pop("parish@example.org") == ("Example 4, Leader", "ministry_leader")
    assert after == before


def test_import_all_and_nothing_left(deployment):
    """Import all takes every listed leader; afterwards there is nothing to import."""
    store, browser = deployment
    with web():
        response = post(browser, IMPORT, {"scope": "all", "email": ["ann@example.org"]})
    assert f"{len(LISTED)} new users" in response.content.decode()
    with web():
        applied = post(
            browser, USERS, {"action": "confirm", "preview": token(response)}
        )
    installed(store, applied)
    assert {email for email in users(store)} >= set(LISTED)
    region = leaders_region(page(browser))
    assert "No ParishSoft Ministry leader is waiting for a user." in region
    assert re.search(
        r'disabled data-server-disabled aria-describedby="leaders-import-reason"'
        r"\s*>Import all",
        region,
    )
    assert "Nothing to import" in region
    with web():
        response = post(browser, IMPORT, {"scope": "all"})
    text = response.content.decode()
    assert response.status_code == 200 and "Nobody to import" in text
    assert 'name="preview"' not in text


def test_the_import_form_is_checked(deployment):
    """CSRF, a known scope, valid addresses and at least one chosen."""
    _store, browser = deployment
    before = ConfigurationChangeRequest.objects.count()
    with web():
        assert browser.post(IMPORT, {"scope": "all"}).status_code == 403
        assert post(browser, IMPORT, {"scope": "some"}).status_code == 400
        assert post(browser, IMPORT, {"scope": "selected"}).status_code == 400
        assert (
            post(browser, IMPORT, {"scope": "selected", "email": "not an address"})
        ).status_code == 400
        assert post(browser, IMPORT, {"scope": "all", "name": "X"}).status_code == 400
        assert post(browser, IMPORT + "?scope=all", {"scope": "all"}).status_code == 400
        assert browser.get(IMPORT).status_code == 405
    assert ConfigurationChangeRequest.objects.count() == before


def test_import_leaders_command_previews_and_confirms(admin, google):  # noqa: F811
    """``users import-leaders`` lists the leaders and imports with the page's code."""
    from parishkit.stewardship.accounts.configuration_installation import (
        install_request,
    )

    store = admin.service.store
    add_rules(store, address("staff@example.org", ("staff",)))
    convert(store)
    leaders_source(store)
    google[0].update(email="admin@example.org", sub="synthetic-google-subject")
    _browser, secret, _ = paired(admin.service)
    code, document = one(admin, "users", "import-leaders", "--preview", secret=secret)
    assert code == 0 and document["ok"], document
    result = document["result"]
    assert set(result) == set(admin_cli.BY_NAME["users import-leaders"].result_fields)
    assert {row["email"]: row["member_duid"] for row in result["leaders"]} == {
        email: duid for email, (_name, duid, _ministries) in LISTED.items()
    }
    assert result["unreachable"] == 1 and result["added"] == []
    assert result["preview"] == {"token": None}
    code, document = one(
        admin,
        "users",
        "import-leaders",
        "--preview",
        "--email",
        "ann@example.org",
        "--email",
        "staff@example.org",
        secret=secret,
    )
    assert code == 0, document
    result = document["result"]
    assert [(row["email"], row["role"]) for row in result["added"]] == [
        ("ann@example.org", "ministry_leader")
    ]
    assert result["skipped"] == [{"email": "staff@example.org", "reason": "user"}]
    code, document = one(
        admin,
        "users",
        "import-leaders",
        "--confirm",
        "--token",
        result["preview"]["token"],
        secret=secret,
    )
    assert code == 0 and document["result"]["created"], document
    receipt = install_request(
        store,
        request_id=UUID(document["result"]["request"]["request_id"]),
        correlation_id=uuid4(),
    )
    assert receipt.state == "applied"
    assert users(store)["ann@example.org"] == ("Example 0, Leader", "ministry_leader")
    for event_type in (
        "admin_cmd_users_import_leaders",
        "user_leaders_import_requested",
        "user_leader_imported",
    ):
        assert AuditEvent.objects.filter(event_type=event_type).count() == 1
    # A users preview token is not an import, and the modes do not mix.
    code, document = one(
        admin, "users", "import-leaders", "--confirm", "--token", "x", secret=secret
    )
    assert code == 1 and document["error"]["code"] == "invalid", document
    code, document = one(
        admin,
        "users",
        "import-leaders",
        "--preview",
        "--all",
        "--email",
        "a@b.org",
        secret=secret,
    )
    assert code != 0 and not document["ok"], document
