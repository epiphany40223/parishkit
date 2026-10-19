"""Additional information and Limitations review decisions (#1685, migration 0134).

The worklists' services record each decision as the stream's own immutable
row under the real web login; SQL admits only what the services may record,
and an Additional information revision saved before 0134 reads by its
follow-up flags.
"""

import json
from uuid import uuid4

import pytest
from django.db import DatabaseError, connection, transaction

from parishkit.stewardship.accounts.models import PortalUser
from parishkit.stewardship.audit.models import AuditEvent
from parishkit.stewardship.campaigns.models import FamilyCampaign
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.pipeline import Action, Stage
from parishkit.stewardship.reports.limitations import (
    LimitationsQuery,
    limitations_report,
)
from parishkit.stewardship.responses.census_review import KeyRebound
from parishkit.stewardship.responses.information import (
    ReopenNeedsNote,
    StaleItem,
    decide_information,
    information_stage,
    latest_revision,
    update_information,
)
from parishkit.stewardship.responses.limitation_review import (
    answer_id,
    decide_limitations,
    limitation_stage,
)
from parishkit.stewardship.responses.models import (
    AdditionalInformationItem,
    AdditionalInformationRevision,
    LimitationReview,
)

from .auth_builders import signed_in
from .response_builders import activate_response_service
from .test_background_grants_postgresql import task_login
from .test_limitations_report_postgresql import STAFF, submit_live
from .test_ministry_responses_postgresql import respond, revisit, start
from .test_response_http_postgresql import answers_for
from .test_runtime_auth_grants_postgresql import web_login
from .test_weekly_observation_postgresql import respond as respond_text

pytestmark = pytest.mark.django_db(transaction=True)


def staff_actor():
    """The signed-in fixture Administrator's portal user id."""
    _, login = signed_in()
    assert login.status_code == 302
    return PortalUser.objects.get(email="admin@example.org").pk


def stage_of(item_id):
    """One request's current stage, read as the worklist reads it."""
    item = AdditionalInformationItem.objects.get(pk=item_id)
    return information_stage(item, latest_revision(item_id)).stage


def decide(harness, actor, action, item, *, note="", key=None, bulk=False):
    """One worklist decision on one request, under the real web login."""
    item.refresh_from_db()
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        return decide_information(
            harness.service.store,
            actor,
            harness.campaign.pk,
            action,
            {item.pk: item.version},
            note=note,
            request_key=key or uuid4(),
            bulk=bulk,
        )


def test_information_decisions_follow_the_pipeline(live_response_service, google):
    """Accept, Handled, Ignore and Back to review, replays, races and the guard."""
    harness = live_response_service
    actor = staff_actor()
    item = AdditionalInformationItem.objects.get(
        submission=respond_text(harness, "Please call us about the choir")
    )
    assert stage_of(item.pk) is Stage.TO_REVIEW
    key = uuid4()
    (accepted,) = decide(harness, actor, Action.ACCEPT, item, note="Will call", key=key)
    assert accepted.review_decision == "accepted" and accepted.notes == "Will call"
    assert stage_of(item.pk) is Stage.TO_HANDLE
    # The same post again replays its revision; the key with another choice
    # is rebound.
    item.refresh_from_db()
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        replay = decide_information(
            harness.service.store,
            actor,
            harness.campaign.pk,
            Action.ACCEPT,
            {item.pk: item.version - 1},
            note="Will call",
            request_key=key,
        )
    assert [row.pk for row in replay] == [accepted.pk]
    with pytest.raises(KeyRebound):
        decide(harness, actor, Action.IGNORE, item, key=key)
    # A stage that no longer offers the action is a race, never a write.
    with pytest.raises(StaleItem):
        decide(harness, actor, Action.ACCEPT, item)
    (handled,) = decide(harness, actor, Action.HANDLED, item)
    assert handled.followed_up and handled.followed_up_by_id == actor
    # A decision with no note keeps the request's Staff notes.
    assert handled.notes == "Will call"
    assert stage_of(item.pk) is Stage.HANDLED
    # Handled is final.
    for action in (Action.BACK_TO_REVIEW, Action.IGNORE):
        with pytest.raises(StaleItem):
            decide(harness, actor, action, item)
    events = AuditEvent.objects.filter(
        event_type="information_updated", subject_id__in=[accepted.pk, handled.pk]
    )
    assert events.count() == 2


def test_ignore_needs_a_note_to_reopen(live_response_service, google):
    """Ignored goes back to review only with a note, in Python and in SQL."""
    harness = live_response_service
    actor = staff_actor()
    item = AdditionalInformationItem.objects.get(
        submission=respond_text(harness, "Just letting you know")
    )
    (ignored,) = decide(harness, actor, Action.IGNORE, item)
    assert ignored.review_decision == "ignored"
    assert stage_of(item.pk) is Stage.IGNORED
    with pytest.raises(ValueError):
        decide(harness, actor, Action.BACK_TO_REVIEW, item)
    # The guard refuses the same reopening written directly.
    item.refresh_from_db()
    with (
        task_login(ServiceRole.WEB, exact=True, reconnect=True),
        pytest.raises(DatabaseError, match="Reopening an ignored request"),
        work_transaction(),
    ):
        AdditionalInformationRevision.objects.create(
            actor_id=actor,
            item_id=item.pk,
            expected_version=item.version,
            request_key=uuid4(),
            follow_up_needed=False,
            followed_up=False,
            notes="",
            review_decision="none",
        )
    (back,) = decide(harness, actor, Action.BACK_TO_REVIEW, item, note="Misread it")
    assert back.review_decision == "none"
    assert stage_of(item.pk) is Stage.TO_REVIEW


def test_new_revisions_state_a_decision_and_old_ones_read_by_flags(
    live_response_service, google
):
    """The guard needs a decision; a revision saved before 0134 reads as Ignored.

    The older revision is written with the triggers off, as only a database
    from before migration 0134 holds one; its follow-up was left not needed,
    so it is Ignored, while a request no Staff member saved is To review.
    """
    harness = live_response_service
    actor = staff_actor()
    item = AdditionalInformationItem.objects.get(
        submission=respond_text(harness, "We moved last spring")
    )
    with (
        task_login(ServiceRole.WEB, exact=True, reconnect=True),
        pytest.raises(DatabaseError),
        work_transaction(),
    ):
        AdditionalInformationRevision.objects.create(
            actor_id=actor,
            item_id=item.pk,
            expected_version=item.version,
            request_key=uuid4(),
            follow_up_needed=False,
            followed_up=False,
            notes="",
        )
    # The CHECK ties a decision to the flags.
    with (
        pytest.raises(DatabaseError),
        transaction.atomic(),
        connection.cursor() as cursor,
    ):
        cursor.execute("SET LOCAL session_replication_role = replica")
        AdditionalInformationRevision.objects.create(
            actor_id=actor,
            item_id=item.pk,
            expected_version=item.version,
            request_key=uuid4(),
            follow_up_needed=True,
            followed_up=False,
            notes="",
            review_decision="ignored",
        )
    assert stage_of(item.pk) is Stage.TO_REVIEW
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute("SET LOCAL session_replication_role = replica")
        AdditionalInformationRevision.objects.create(
            actor_id=actor,
            item_id=item.pk,
            expected_version=item.version,
            request_key=uuid4(),
            follow_up_needed=False,
            followed_up=False,
            notes="Nothing to do",
        )
        AdditionalInformationItem.objects.filter(pk=item.pk).update(
            version=item.version + 1
        )
    assert latest_revision(item.pk).review_decision is None
    assert stage_of(item.pk) is Stage.IGNORED
    # pk-admin's flags Save keeps that Ignored on a notes-only change and
    # sends it to To handle when follow-up is needed.
    item.refresh_from_db()
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        kept = update_information(
            harness.service.store,
            actor,
            item.pk,
            expected_version=item.version,
            request_key=uuid4(),
            follow_up_needed=False,
            followed_up=False,
            confirm_clear=False,
            notes="Still nothing",
        )
    assert kept.review_decision == "ignored" and stage_of(item.pk) is Stage.IGNORED
    # Out of Ignored only with new notes: the notes it already has are not a
    # reason, in Python and in SQL.
    item.refresh_from_db()
    with (
        task_login(ServiceRole.WEB, exact=True, reconnect=True),
        pytest.raises(ReopenNeedsNote),
    ):
        update_information(
            harness.service.store,
            actor,
            item.pk,
            expected_version=item.version,
            request_key=uuid4(),
            follow_up_needed=True,
            followed_up=False,
            confirm_clear=False,
            notes="Still nothing",
        )
    with (
        task_login(ServiceRole.WEB, exact=True, reconnect=True),
        pytest.raises(DatabaseError, match="Reopening an ignored request"),
        work_transaction(),
    ):
        AdditionalInformationRevision.objects.create(
            actor_id=actor,
            item_id=item.pk,
            expected_version=item.version,
            request_key=uuid4(),
            follow_up_needed=True,
            followed_up=False,
            notes="Still nothing",
            review_decision="accepted",
        )
    item.refresh_from_db()
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        needed = update_information(
            harness.service.store,
            actor,
            item.pk,
            expected_version=item.version,
            request_key=uuid4(),
            follow_up_needed=True,
            followed_up=False,
            confirm_clear=False,
            notes="Call them",
        )
    assert needed.review_decision == "accepted"
    assert stage_of(item.pk) is Stage.TO_HANDLE
    # The guard ignores only a request To review: a To handle one goes back
    # to review first.
    item.refresh_from_db()
    with (
        task_login(ServiceRole.WEB, exact=True, reconnect=True),
        pytest.raises(DatabaseError, match="Only a request to review"),
        work_transaction(),
    ):
        AdditionalInformationRevision.objects.create(
            actor_id=actor,
            item_id=item.pk,
            expected_version=item.version,
            request_key=uuid4(),
            follow_up_needed=False,
            followed_up=False,
            notes="",
            review_decision="ignored",
        )
    item.refresh_from_db()
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        cleared = update_information(
            harness.service.store,
            actor,
            item.pk,
            expected_version=item.version,
            request_key=uuid4(),
            follow_up_needed=False,
            followed_up=False,
            confirm_clear=False,
            notes="",
        )
    assert cleared.review_decision == "none"
    assert stage_of(item.pk) is Stage.TO_REVIEW


def test_bulk_information_decisions_are_all_or_none(live_response_service, google):
    """A bulk decision records every request or, after a race, none."""
    harness = live_response_service
    actor = staff_actor()
    item = AdditionalInformationItem.objects.get(
        submission=respond_text(harness, "Bulk one")
    )
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        with pytest.raises(StaleItem):
            decide_information(
                harness.service.store,
                actor,
                harness.campaign.pk,
                Action.ACCEPT,
                {item.pk: item.version + 1},
                note="",
                request_key=uuid4(),
                bulk=True,
            )
        with pytest.raises(PermissionError):
            decide_information(
                harness.service.store,
                actor,
                harness.campaign.pk,
                Action.ACCEPT,
                {uuid4(): 1},
                note="",
                request_key=uuid4(),
                bulk=True,
            )
        bulk = uuid4()
        first = decide_information(
            harness.service.store,
            actor,
            harness.campaign.pk,
            Action.ACCEPT,
            {item.pk: item.version},
            note="All of these",
            request_key=bulk,
            bulk=True,
        )
        again = decide_information(
            harness.service.store,
            actor,
            harness.campaign.pk,
            Action.ACCEPT,
            {item.pk: item.version},
            note="All of these",
            request_key=bulk,
            bulk=True,
        )
    assert [row.pk for row in again] == [row.pk for row in first]
    assert first[0].request_key != bulk


def limitation_rows(harness):
    """The Limitations report's rows, read under the restricted web login."""
    with task_login(ServiceRole.WEB, exact=True, reconnect=True), transaction.atomic():
        return limitations_report(
            harness.campaign.pk, LimitationsQuery.parse({}), STAFF
        )


def decide_answer(harness, actor, action, item, version, *, note="", key=None):
    """One worklist decision on one answer, under the real web login."""
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        return decide_limitations(
            harness.service.store,
            actor,
            harness.campaign.pk,
            action,
            {item: version},
            note=note,
            request_key=key or uuid4(),
        )


def resubmit(harness, *, cannot_serve, cannot_attend=True):
    """The Family answers again, giving or clearing its two limitations."""
    with web_login():
        form = revisit(harness)
        answers = answers_for(form)
        answers["cannot_attend"] = cannot_attend
        answers["service"] = {
            "members": {"3": {"cannot_serve": cannot_serve}},
            "proposed_members": {},
        }
        return respond(harness, form, answers)


def test_limitation_decisions_follow_the_resubmission_rule(response_service, google):
    """A decision holds while the Family keeps its answer, and only then.

    Answering again unchanged keeps it; clearing the answer removes the row,
    and giving it again later is a new To review row. The guard admits only
    the steps the decision in force allows, at the current version, on the
    Family's effective response.
    """
    harness = response_service
    start(harness)
    harness = activate_response_service(harness)
    first = submit_live(harness, cannot_serve=True)
    actor = staff_actor()
    family = FamilyCampaign.objects.get(pk=first.family_id)
    member = answer_id(family.family_duid, "member", "3")
    whole = answer_id(family.family_duid, "family")
    rows = limitation_rows(harness)
    assert [row["version"] for row in rows["members"] + rows["families"]] == [0, 0]
    (accepted,) = decide_answer(harness, actor, Action.ACCEPT, member, 0)
    assert accepted.decision == "accepted" and accepted.submission_id == first.pk
    assert limitation_stage(accepted).stage is Stage.TO_HANDLE
    assert AuditEvent.objects.filter(
        event_type="limitation_reviewed", subject_id=accepted.pk
    ).exists()
    (ignored,) = decide_answer(harness, actor, Action.IGNORE, whole, 0)
    with pytest.raises(StaleItem):
        decide_answer(harness, actor, Action.IGNORE, member, 0)
    with pytest.raises(ValueError):
        decide_answer(harness, actor, Action.BACK_TO_REVIEW, whole, 1)
    # Answering again unchanged keeps both decisions in force.
    resubmit(harness, cannot_serve=True)
    rows = limitation_rows(harness)
    assert [
        (row["decision"], row["in_force"], row["version"]) for row in rows["members"]
    ] == [("accepted", True, 1)]
    assert [(row["decision"], row["in_force"]) for row in rows["families"]] == [
        ("ignored", True)
    ]
    (handled,) = decide_answer(harness, actor, Action.HANDLED, member, 1)
    assert limitation_stage(handled).stage is Stage.HANDLED
    # The guard refuses a step the decision in force does not allow, and an
    # earlier response, written directly.
    current = FamilyCampaign.objects.get(pk=family.pk).effective_submission_id
    for decision, version, submission in (
        ("none", 2, current),
        ("accepted", 1, current),
        ("handled", 2, first.pk),
    ):
        with (
            task_login(ServiceRole.WEB, exact=True, reconnect=True),
            pytest.raises(DatabaseError, match="Limitation review lacks"),
            work_transaction(),
        ):
            LimitationReview.objects.create(
                actor_id=actor,
                family_id=family.pk,
                submission_id=submission,
                kind="member",
                member_key="3",
                expected_version=version,
                request_key=uuid4(),
                decision=decision,
            )
    # Clearing the answer withdraws the Member's row; giving it again is a
    # new To review row, whatever the earlier decision was.
    resubmit(harness, cannot_serve=False)
    assert limitation_rows(harness)["members"] == []
    resubmit(harness, cannot_serve=True)
    (row,) = limitation_rows(harness)["members"]
    assert (row["decision"], row["in_force"], row["version"]) == ("handled", False, 2)
    (again,) = decide_answer(harness, actor, Action.ACCEPT, member, 2)
    assert limitation_stage(again).stage is Stage.TO_HANDLE
    # Reviews are history: never changed or removed.
    for statement in (
        "UPDATE stewardship_limitation_review SET note='changed' WHERE id=%s",
        "DELETE FROM stewardship_limitation_review WHERE id=%s",
    ):
        with (
            pytest.raises(DatabaseError, match="immutable"),
            transaction.atomic(),
            connection.cursor() as cursor,
        ):
            cursor.execute(statement, [again.pk])


def submit_everyone(harness):
    """A live response in which Member 3, a Member the Family adds on the
    form and the Family itself each give their limitation."""
    from ..census_factory import member
    from .test_response_http_postgresql import load_form

    with web_login():
        form = load_form(harness)
        answers = answers_for(form)
        added = str(uuid4())
        answers["proposed_members"][added] = member(
            first_name="New", last_name="Person"
        )
        answers["ministries"]["proposed_members"][added] = {"join": []}
        answers["cannot_attend"] = True
        answers["service"] = {
            "members": {"3": {"cannot_serve": True}},
            "proposed_members": {added: {"cannot_serve": True}},
        }
        # A Member who cannot participate leaves every Ministry, as
        # submit_live's does.
        answers["ministries"]["members"]["3"] = {"join": [], "leave": [4]}
        return respond(harness, form, answers), added


def test_limitation_bulk_proposed_members_and_the_sql_checks(response_service, google):
    """Bulk decisions cover every kind of answer, a proposed Member's too,
    all or none; replays and rebound keys behave as on the page; a Ministry
    leader may not decide; and SQL alone refuses a reopening with no note, a
    row without its audit event, and a change of history."""
    from .test_ministry_reports_postgresql import actor as policy_actor

    harness = response_service
    start(harness)
    harness = activate_response_service(harness)
    response, added = submit_everyone(harness)
    actor = staff_actor()
    duid = FamilyCampaign.objects.get(pk=response.family_id).family_duid
    items = [
        answer_id(duid, "member", "3"),
        answer_id(duid, "proposed_member", added),
        answer_id(duid, "family"),
    ]
    rows = limitation_rows(harness)
    assert {row["kind"] for row in rows["members"] + rows["families"]} == {
        "member",
        "proposed_member",
        "family",
    }

    def bulk(action, versions, key, note=""):
        """One bulk decision under the real web login."""
        with task_login(ServiceRole.WEB, exact=True, reconnect=True):
            return decide_limitations(
                harness.service.store,
                actor,
                harness.campaign.pk,
                action,
                versions,
                note=note,
                request_key=key,
                bulk=True,
            )

    # One stale answer and nothing is recorded.
    with pytest.raises(StaleItem):
        bulk(Action.ACCEPT, dict(zip(items, (0, 0, 5), strict=True)), uuid4())
    assert not LimitationReview.objects.exists()
    key = uuid4()
    first = bulk(Action.ACCEPT, dict.fromkeys(items, 0), key)
    assert len(first) == 3 and all(row.decision == "accepted" for row in first)
    again = bulk(Action.ACCEPT, dict.fromkeys(items, 0), key)
    assert [row.pk for row in again] == [row.pk for row in first]
    with pytest.raises(KeyRebound):
        bulk(Action.IGNORE, dict.fromkeys(items, 0), key)
    proposed = next(row for row in first if row.kind == "proposed_member")
    assert proposed.member_key == added
    assert limitation_stage(proposed).stage is Stage.TO_HANDLE
    # A Ministry leader may not decide.
    leader = policy_actor(harness, ("ministry_leader",), (4,))
    with (
        task_login(ServiceRole.WEB, exact=True, reconnect=True),
        pytest.raises(PermissionError),
    ):
        decide_limitations(
            harness.service.store,
            leader.identity,
            harness.campaign.pk,
            Action.HANDLED,
            {items[0]: 1},
            note="",
            request_key=uuid4(),
        )
    # Back to review, then Ignore, on the Family's answer; reopening it with
    # no note is refused by the guard alone.
    family = FamilyCampaign.objects.get(pk=response.family_id)
    decide_answer(harness, actor, Action.BACK_TO_REVIEW, items[2], 1)
    decide_answer(harness, actor, Action.IGNORE, items[2], 2)
    with (
        task_login(ServiceRole.WEB, exact=True, reconnect=True),
        pytest.raises(DatabaseError, match="Limitation review lacks"),
        work_transaction(),
    ):
        LimitationReview.objects.create(
            actor_id=actor,
            family_id=family.pk,
            submission_id=response.pk,
            kind="family",
            member_key="",
            expected_version=3,
            request_key=uuid4(),
            decision="none",
        )
    # A row the guard admits still needs its audit event to commit.
    with (
        task_login(ServiceRole.WEB, exact=True, reconnect=True),
        pytest.raises(DatabaseError, match="requires its audit"),
        work_transaction(),
    ):
        LimitationReview.objects.create(
            actor_id=actor,
            family_id=family.pk,
            submission_id=response.pk,
            kind="member",
            member_key="3",
            expected_version=1,
            request_key=uuid4(),
            decision="handled",
        )
    assert LimitationReview.objects.count() == 5


def test_an_answer_the_form_left_out_keeps_its_decision():
    """Only an answer stated the other way ends a decision (rule 5): one a
    later form left out is not stated at all."""
    cases = (
        ({"service": {"members": {}}}, "member", "3", False),
        ({"service": {"members": {"3": {"cannot_serve": False}}}}, "member", "3", True),
        ({"service": {}}, "proposed_member", str(uuid4()), False),
        ({"cannot_attend": False}, "family", "", True),
        ({}, "family", "", False),
    )
    with connection.cursor() as cursor:
        for answers, kind, key, stated in cases:
            cursor.execute(
                "SELECT stewardship_limitation_stated_v1(%s::jsonb,%s,%s),"
                "stewardship_limitation_answer_v1(%s::jsonb,%s,%s)",
                [json.dumps(answers), kind, key] * 2,
            )
            assert cursor.fetchone() == (stated, False), (answers, kind)


def test_the_menu_counts_the_answers_to_review(response_service, google):
    """The menu's Limitations count is the worklist's To review count (R39
    finding 10): a decision moves an answer out of it, Back to review brings
    it back, and a viewer the page refuses gets no number."""
    from types import SimpleNamespace

    from parishkit.stewardship.accounts import admin_context
    from parishkit.stewardship.accounts.policy import Principal

    harness = response_service
    start(harness)
    harness = activate_response_service(harness)
    first = submit_live(harness, cannot_serve=True)
    actor = staff_actor()
    staff = Principal(actor, frozenset({"staff"}), frozenset())
    items = [
        SimpleNamespace(name="limitations_report", url="/admin/reports/limitations/")
    ]
    family = FamilyCampaign.objects.get(pk=first.family_id)
    member = answer_id(family.family_duid, "member", "3")

    def count(principal=staff):
        """The menu's count, read as the web role reads it."""
        with task_login(ServiceRole.WEB, exact=True, reconnect=True):
            return admin_context._open_counts(
                SimpleNamespace(), principal, items, harness.campaign
            ).get("limitations_report")

    # The Member's answer and the Family's cannot-attend answer.
    assert count() == 2
    decide_answer(harness, actor, Action.ACCEPT, member, 0)
    assert count() == 1
    decide_answer(harness, actor, Action.BACK_TO_REVIEW, member, 1)
    assert count() == 2
    leader = Principal(actor, frozenset({"ministry_leader"}), frozenset({1}))
    assert count(leader) is None
