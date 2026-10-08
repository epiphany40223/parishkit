"""Staff and Admin resolve By-hand census changes under the real web role (#528).

Migration 0026 (``schema/migrations/0026_census_resolution.sql``) adds the
resolution history and lets the web login apply exactly what a resolution
row records. These tests run the service and the SQL guards as installed,
and check the file's own DO block against the guard's previous body.
"""

import re
from pathlib import Path
from uuid import uuid4

import pytest
from django.db import DatabaseError, IntegrityError, connection, transaction

from parishkit.stewardship.audit.models import AuditContext, AuditEvent
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.responses.census_resolution import resolve_census_change
from parishkit.stewardship.responses.models import ProposalResolution, ProposedChange
from parishkit.stewardship.storage import StaleRecordError

from ..policy_factory import address as rule
from .campaign_builders import change
from .response_builders import activate_response_service
from .test_background_grants_postgresql import task_login
from .test_census_changes_postgresql import submit_live
from .test_ministry_responses_postgresql import start
from .test_policy_postgresql import user

pytestmark = pytest.mark.django_db(transaction=True)

ROOT = Path(__file__).resolve().parents[3]
FROZEN = ROOT / "src/parishkit/stewardship/schema/migrations/0026_census_resolution.sql"
GUARDS = ROOT / "src/parishkit/stewardship/schema/guards.sql"


def prepared(harness):
    """A live response with a By-hand address row and an automatic name row,
    plus an Administrator and a Staff member with current policy."""
    start(harness)
    harness = activate_response_service(harness)
    submit_live(harness)
    admin = user("admin@example.org")
    store = harness.service.store
    change(
        store,
        store.active(),
        admin.pk,
        [
            {"operation": "add", "section": "login_rules", **item}
            for item in (rule("staff@example.org", ("staff",)),)
        ],
    )
    staff = user("staff@example.org")
    rows = {row.field: row for row in ProposedChange.objects.all()}
    return harness, admin.pk, staff.pk, rows["home_address"], rows["first_name"]


def resolve(harness, actor, proposal, action, *, version=None, key=None, note=""):
    """Resolve through the service on the restricted web login."""
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        return resolve_census_change(
            harness.service.store,
            actor,
            proposal.pk,
            expected_version=proposal.version if version is None else version,
            request_key=key or uuid4(),
            action=action,
            note=note,
        )


def test_staff_enter_a_by_hand_change_with_history_and_audit(response_service):
    """Entered in ParishSoft makes the change final, keeps who, when and the
    note, and is audited with versions only; a repeated request is one row."""
    harness, _, staff, home, _ = prepared(response_service)
    key = uuid4()
    first = resolve(harness, staff, home, "entered", key=key, note="Typed it in")
    again = resolve(harness, staff, home, "entered", key=key, note="Typed it in")
    assert again.pk == first.pk and ProposalResolution.objects.count() == 1
    home.refresh_from_db()
    assert (
        home.execution == "resolved_external"
        and home.version == first.expected_version + 1
    )
    assert first.actor_id == staff and first.note == "Typed it in" and first.created_at
    event = AuditEvent.objects.get(event_type="census_change_updated")
    assert event.subject_id == first.pk
    assert AuditContext.objects.get(event=event).context == {
        "outcome": "changed",
        "before_version": first.expected_version,
        "after_version": first.expected_version + 1,
    }
    # Final: nothing more may be recorded on it.
    for action in ("entered", "ignored"):
        with pytest.raises(ValueError):
            resolve(harness, staff, home, action)


def test_ignore_and_an_administrators_reopen(response_service):
    """Staff may ignore; only an Administrator reopens, back to unreviewed."""
    harness, admin, staff, home, _ = prepared(response_service)
    resolve(harness, staff, home, "ignored", note="Duplicate")
    home.refresh_from_db()
    assert home.decision == "ignored" and home.execution == "pending"
    with pytest.raises(PermissionError):
        resolve(harness, staff, home, "reopened", note="Staff cannot")
    resolve(harness, admin, home, "reopened", note="Not a duplicate")
    home.refresh_from_db()
    assert home.decision == "unreviewed"
    assert [row.action for row in home.resolutions.order_by("expected_version")] == [
        "ignored",
        "reopened",
    ]


def test_service_refuses_automatic_stale_and_rebound_requests(response_service):
    """An automatic change is the Administrator's to publish, a stale version
    is refused, and a request key cannot be reused for another intent."""
    harness, admin, staff, home, name = prepared(response_service)
    with pytest.raises(ValueError):
        resolve(harness, admin, name, "entered")
    with pytest.raises(StaleRecordError):
        resolve(harness, staff, home, "entered", version=home.version + 1)
    key = uuid4()
    resolve(harness, staff, home, "ignored", key=key)
    with pytest.raises(ValueError):
        resolve(harness, staff, home, "ignored", key=key, note="changed")
    with pytest.raises(ValueError):
        resolve(harness, staff, home, "entered", note="x" * 2001)


def forge(harness, proposal, actor, action, *, version=None):
    """Insert a resolution and apply its result directly, as the web login,
    bypassing the service, inside one transaction."""
    with (
        task_login(ServiceRole.WEB, exact=True, reconnect=True),
        work_transaction(),
        connection.cursor() as cursor,
    ):
        expected = proposal.version if version is None else version
        cursor.execute(
            "INSERT INTO stewardship_proposal_resolution (id,correlation_id,"
            "actor_id,proposal_id,expected_version,request_key,action,note) "
            "VALUES (gen_random_uuid(),gen_random_uuid(),%s,%s,%s,"
            "gen_random_uuid(),%s,'')",
            [actor, proposal.pk, expected, action],
        )


@pytest.mark.parametrize("case", ["automatic", "stale", "staff_reopen", "unknown"])
def test_sql_refuses_a_forged_resolution(response_service, case):
    """The resolution guard holds even when the service is bypassed."""
    harness, admin, staff, home, name = prepared(response_service)
    target, actor, action, version = {
        "automatic": (name, admin, "entered", None),
        "stale": (home, staff, "entered", home.version + 1),
        "staff_reopen": (home, staff, "reopened", None),
        "unknown": (home, uuid4(), "ignored", None),
    }[case]
    if case == "staff_reopen":
        resolve(harness, staff, home, "ignored")
        target = ProposedChange.objects.get(pk=home.pk)
    with pytest.raises((IntegrityError, DatabaseError)):
        forge(harness, target, actor, action, version=version)


def test_sql_refuses_a_resolution_without_its_result_or_audit(response_service):
    """A resolution row alone, with no proposal change or audit, cannot commit."""
    harness, _, staff, home, _ = prepared(response_service)
    with pytest.raises((IntegrityError, DatabaseError), match="result and audit"):
        forge(harness, home, staff, "entered")
    assert ProposalResolution.objects.count() == 0


def test_web_cannot_change_a_decision_without_a_resolution(response_service):
    """The web login's new decision grant is usable only with its paired row."""
    harness, _, _, home, _ = prepared(response_service)
    with (
        pytest.raises((IntegrityError, DatabaseError)),
        task_login(ServiceRole.WEB, exact=True, reconnect=True),
        work_transaction(),
    ):
        ProposedChange.objects.filter(pk=home.pk).update(
            decision="ignored", version=home.version + 1
        )
    home.refresh_from_db()
    assert home.decision == "unreviewed"


def test_resolution_history_is_immutable(response_service):
    """A recorded resolution can never be changed or removed."""
    harness, _, staff, home, _ = prepared(response_service)
    resolution = resolve(harness, staff, home, "ignored", note="Duplicate")
    for statement in (
        "UPDATE stewardship_proposal_resolution SET note='rewritten' WHERE id=%s",
        "DELETE FROM stewardship_proposal_resolution WHERE id=%s",
    ):
        with (
            pytest.raises((IntegrityError, DatabaseError)),
            transaction.atomic(),
            connection.cursor() as cursor,
        ):
            cursor.execute(statement, [resolution.pk])


def old_guard():
    """The derived guard as it was before 0026, rebuilt from the baseline by
    removing the resolution branch this migration added."""
    text = GUARDS.read_text(encoding="utf-8")
    start_at = text.index(
        "CREATE FUNCTION public.stewardship_response_derived_guard_v1()"
    )
    body = text[start_at : text.index("$$;", start_at) + 3]
    branch = re.search(
        r"                -- A Staff or Administrator resolution \(#528\).*?"
        r"                   AND \(NEW\.decision<>OLD\.decision\n"
        r"                   OR NEW\.execution NOT IN",
        body,
        re.S,
    )
    assert branch, "the resolution branch is in the baseline"
    old = body.replace(branch[0], "                IF NEW.execution NOT IN", 1)
    old = old.replace(
        "successor.submission_id=later.id))\n                   ))\n",
        "successor.submission_id=later.id))\n                   )\n",
        1,
    )
    assert "stewardship_proposal_resolution" not in old
    return old.replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1)


class Rollback(Exception):
    """Ends a check that must leave the database as it found it."""


def test_migration_self_check_refuses_the_old_guard_and_installs_the_new():
    """The DO block fails on the previous guard body; the whole file, run on
    a database with that body and no resolution table, installs both."""
    text = FROZEN.read_text(encoding="utf-8")
    check = re.search(r"^DO \$check\$.*?\$check\$;", text, re.M | re.S)[0]
    with connection.cursor() as cursor:
        cursor.execute(check)
        with (
            pytest.raises(DatabaseError, match="lacks the resolution branch"),
            transaction.atomic(),
        ):
            cursor.execute(old_guard())
            cursor.execute(check)
        with pytest.raises(Rollback), transaction.atomic():
            cursor.execute(old_guard())
            cursor.execute("DROP TABLE stewardship_proposal_resolution")
            cursor.execute(
                "DROP FUNCTION stewardship_proposal_resolution_guard_v1(),"
                " stewardship_proposal_resolution_effect_v1()"
            )
            cursor.execute(text)
            cursor.execute(
                "SELECT prosrc LIKE '%%stewardship_proposal_resolution r%%'"
                " FROM pg_proc WHERE proname='stewardship_response_derived_guard_v1'"
            )
            assert cursor.fetchone() == (True,)
            raise Rollback


def test_the_worklist_records_a_resolution_and_answers_in_place(
    response_service, google
):
    """A row's form posts with the page's filters; the page answers with the
    result and a notice, and a stale form is told the row changed."""
    from .auth_builders import signed_in
    from .test_information_followup_postgresql import search

    harness, _, _, home, _ = prepared(response_service)
    browser, login = signed_in()
    assert login.status_code == 302
    route = f"/admin/reports/{harness.campaign.pk}/census/"
    form = {
        "status": "all",
        "resolve": "entered",
        "proposal": str(home.pk),
        "version": str(home.version),
        "request_key": str(uuid4()),
        "note": "  Typed it in  ",
    }
    response, body = search(browser, route, form)
    assert response.status_code == 200
    assert b"marked entered in ParishSoft." in body and b"Entered by hand" in body
    assert b"Typed it in" in body
    home.refresh_from_db()
    assert home.execution == "resolved_external"
    assert ProposalResolution.objects.get().note == "Typed it in"
    # The same form again (another tab, drawn before) is now stale.
    stale = form | {"request_key": str(uuid4())}
    response, body = search(browser, route, stale)
    assert response.status_code == 200 and b"updated by someone else" in body
    assert ProposalResolution.objects.count() == 1
    # A form missing a field, or naming an action that is not offered.
    for broken in ({"resolve": "entered"}, form | {"resolve": "published"}):
        assert search(browser, route, broken)[0].status_code == 400


def resubmit(harness):
    """The Family answers again with a different first name, superseding
    the earlier proposal through the existing replacement path."""
    from .test_ministry_responses_postgresql import respond, revisit
    from .test_response_http_postgresql import answers_for
    from .test_runtime_auth_grants_postgresql import web_login

    with web_login():
        form = revisit(harness)
        answers = answers_for(form)
        answers["members"]["3"]["first_name"] = "Requested again"
        return respond(harness, form, answers)


def test_the_replacement_path_cannot_change_a_decision(response_service, monkeypatch):
    """The web login's decision grant (for resolutions) is not usable on the
    older replacement path: a later response's supersession that also
    changes the decision is refused (#811 review), while the same
    resupersession unchanged is accepted."""
    from django.db.models import QuerySet

    harness, _, _, _, name = prepared(response_service)
    original = QuerySet.update

    def forged(self, **values):
        """Also flip the decision when a proposal's execution changes."""
        if self.model is ProposedChange and "execution" in values:
            values = values | {"decision": "ignored"}
        return original(self, **values)

    with monkeypatch.context() as patch:
        patch.setattr(QuerySet, "update", forged)
        with pytest.raises(DatabaseError, match="Proposal replacement requires"):
            resubmit(harness)
    name.refresh_from_db()
    assert name.execution == "pending" and name.decision == "unreviewed"
    resubmit(harness)
    name.refresh_from_db()
    assert name.execution == "superseded" and name.decision == "unreviewed"


def forge_pair(proposal, actor, action, assignments):
    """A valid resolution row, then a proposal UPDATE that differs from it,
    in one transaction as the web login."""
    with (
        task_login(ServiceRole.WEB, exact=True, reconnect=True),
        work_transaction(),
        connection.cursor() as cursor,
    ):
        cursor.execute(
            "INSERT INTO stewardship_proposal_resolution (id,correlation_id,"
            "actor_id,proposal_id,expected_version,request_key,action,note) "
            "VALUES (gen_random_uuid(),gen_random_uuid(),%s,%s,%s,"
            "gen_random_uuid(),%s,'Because')",
            [actor, proposal.pk, proposal.version, action],
        )
        cursor.execute(
            f"UPDATE stewardship_proposed_change SET {assignments},"
            " version=version+1 WHERE id=%s",
            [proposal.pk],
        )


@pytest.mark.parametrize(
    ("action", "assignments"),
    [
        ("ignored", "decision='ignored', execution='resolved_external'"),
        ("entered", "execution='resolved_external', decision='ignored'"),
        ("ignored", "decision='ignored', admin_value_set=true, admin_value='\"x\"'"),
        ("entered", "execution='resolved_external', current_value='\"x\"'"),
        ("ignored", "decision='ignored', current_available=NOT current_available"),
    ],
)
def test_sql_refuses_an_update_that_differs_from_its_resolution(
    response_service, action, assignments
):
    """A valid resolution row admits exactly its own result, nothing more."""
    harness, _, staff, home, _ = prepared(response_service)
    # Refused by the guard, or, for a column the web login may not update at
    # all, by its grants.
    with pytest.raises(DatabaseError):
        forge_pair(home, staff, action, assignments)
    home.refresh_from_db()
    assert (home.execution, home.decision) == ("pending", "unreviewed")


def test_leaders_testing_responses_and_gated_campaigns_are_refused(
    response_service, google
):
    """A Ministry-leader-only actor, a Testing response and a work-gated
    campaign are refused by the service and by SQL."""
    from parishkit.stewardship.campaigns.runtime_models import CampaignWorkGate

    from .test_ministry_exports_postgresql import leader

    harness, admin, staff, home, _ = prepared(response_service)
    _, head, _, _ = leader(harness, google)
    with pytest.raises(PermissionError):
        resolve(harness, head, home, "entered")
    with pytest.raises((IntegrityError, DatabaseError)):
        forge_pair(home, head, "entered", "execution='resolved_external'")
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(
            "ALTER TABLE stewardship_campaign_work_gate DISABLE TRIGGER USER"
        )
        CampaignWorkGate.objects.create(
            campaign=harness.campaign,
            request_id=uuid4(),
            initiated_by_id=uuid4(),
            state="preparing",
        )
        cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
        cursor.execute("ALTER TABLE stewardship_campaign_work_gate ENABLE TRIGGER USER")
    with pytest.raises(PermissionError):
        resolve(harness, staff, home, "entered")
    with pytest.raises((IntegrityError, DatabaseError)):
        forge_pair(home, staff, "entered", "execution='resolved_external'")
    assert ProposalResolution.objects.count() == 0


def test_testing_responses_take_no_resolution(response_service):
    """A Testing-mode response's proposals are refused by the service and SQL."""
    harness = response_service
    start(harness)
    submit_live(harness)
    admin = user("admin@example.org").pk
    home = ProposedChange.objects.get(field="home_address")
    assert home.submission.mode != "live"
    with pytest.raises(ProposedChange.DoesNotExist):
        resolve(harness, admin, home, "entered")
    with pytest.raises((IntegrityError, DatabaseError)):
        forge_pair(home, admin, "entered", "execution='resolved_external'")


def test_reopen_needs_a_note(response_service):
    """Undoing an Ignore says why, in the service and in SQL."""
    harness, admin, staff, home, _ = prepared(response_service)
    resolve(harness, staff, home, "ignored")
    home.refresh_from_db()
    with pytest.raises(ValueError):
        resolve(harness, admin, home, "reopened", note="   ")
    with (
        pytest.raises((IntegrityError, DatabaseError)),
        task_login(ServiceRole.WEB, exact=True, reconnect=True),
        work_transaction(),
        connection.cursor() as cursor,
    ):
        cursor.execute(
            "INSERT INTO stewardship_proposal_resolution (id,correlation_id,"
            "actor_id,proposal_id,expected_version,request_key,action,note) "
            "VALUES (gen_random_uuid(),gen_random_uuid(),%s,%s,%s,"
            "gen_random_uuid(),'reopened',E'\\t\\n ')",
            [admin, home.pk, home.version],
        )


def test_staff_and_admin_see_their_own_actions_over_http(
    response_service, google, monkeypatch
):
    """Staff get the tick and Ignore but never Reopen; an Administrator gets
    Reopen on an ignored row. A proposal from another campaign is denied, and
    a racing write answers with the row's current state, not an outage."""
    from parishkit.stewardship.reports import census_change_views

    from .auth_builders import signed_in
    from .test_information_followup_postgresql import search

    harness, _, staff, home, _ = prepared(response_service)
    route = f"/admin/reports/{harness.campaign.pk}/census/"
    google[0]["email"] = "staff@example.org"
    browser, login = signed_in()
    assert login.status_code == 302
    _, body = search(browser, route, {"status": "all"})
    assert b'value="entered"' in body and b'value="ignored"' in body
    assert b"cannot be undone" in body
    resolve(harness, staff, home, "ignored")
    _, body = search(browser, route, {"status": "all"})
    assert b'value="reopened"' not in body
    google[0]["email"] = "admin@example.org"
    admin_browser, _ = signed_in()
    _, body = search(admin_browser, route, {"status": "all"})
    assert b'value="reopened"' in body and b"Why reopen? (required)" in body
    home.refresh_from_db()
    form = {
        "status": "all",
        "resolve": "reopened",
        "proposal": str(home.pk),
        "version": str(home.version),
        "request_key": str(uuid4()),
        "note": "Not a duplicate",
    }
    # A proposal is resolved only on its own campaign's page.
    from types import SimpleNamespace

    from parishkit.stewardship.reports.census_change_views import _resolve

    with pytest.raises(PermissionError):
        _resolve(
            SimpleNamespace(store=harness.service.store),
            SimpleNamespace(identity=uuid4()),
            uuid4(),
            {
                "action": "reopened",
                "proposal_id": home.pk,
                "expected_version": home.version,
                "request_key": uuid4(),
                "note": "Elsewhere",
            },
        )

    class Unique(Exception):
        """The driver error a unique violation carries."""

        sqlstate = "23505"

    def racing(*args, **kwargs):
        """Another tab's write committed first: a unique violation."""
        try:
            raise Unique
        except Unique as cause:
            raise IntegrityError("duplicate key") from cause

    def refused(*args, **kwargs):
        """Any other database refusal (a guard or audit mismatch)."""
        raise IntegrityError("Census change resolution requires its result")

    with monkeypatch.context() as patch:
        patch.setattr(census_change_views, "resolve_census_change", racing)
        response, body = search(admin_browser, route, form)
    assert response.status_code == 200
    assert b"Home address for the Family in Family" in body
    assert b"was updated by someone else" in body
    # Any other refusal is not dressed up as the race: it fails, logged.
    with monkeypatch.context() as patch:
        patch.setattr(census_change_views, "resolve_census_change", refused)
        response, body = search(admin_browser, route, form)
    assert response.status_code == 503 and b"updated by someone else" not in body
    response, body = search(admin_browser, route, form | {"request_key": str(uuid4())})
    assert b"Home address for the Family in Family" in body and b"reopened." in body
