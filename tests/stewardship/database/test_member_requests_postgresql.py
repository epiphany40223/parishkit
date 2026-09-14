"""Complete household requests under exact web/worker roles, with SQL parity."""

from copy import deepcopy
from dataclasses import replace
from uuid import uuid4

import pytest
from django.db import IntegrityError, connection
from django.db.models import F

from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.responses.models import ProposedChange, Submission

from ..census_factory import member
from .response_builders import response_source
from .test_background_grants_postgresql import task_login
from .test_family_auth_postgresql import login
from .test_response_http_postgresql import answers_for, load_form, post
from .test_response_submission_postgresql import form_and_answers, submit
from .test_runtime_auth_grants_postgresql import web_login
from .test_source_families_postgresql import prepare, promote

pytestmark = pytest.mark.django_db(transaction=True)


def send(harness, form, answers):
    """Submit only through the actual HTTP owner and assert atomic acceptance."""
    result = post(
        harness.client,
        "/family/submit",
        {"baseline": form["baseline"], "answers": answers},
    )
    assert result.status_code == 200, result.content
    return result


def revisit(harness):
    """Create a new ordinary Family session after successful Submit logs out."""
    harness.client, response = login(harness.code, harness.client)
    assert response.status_code == 302, response.content
    return load_form(harness)


@pytest.mark.parametrize(
    "kind,date_value",
    [
        ("moved_household", None),
        ("deceased_status", ""),
        ("deceased_status", "2026-01-01"),
    ],
)
def test_terminal_request_ignores_edits_and_round_trips(
    live_response_service, kind, date_value
):
    harness = live_response_service
    request = {kind: True, "confirmed": True}
    if date_value is not None:
        request["death_date"] = date_value
    with web_login():
        form = load_form(harness)
        answers = answers_for(form)
        answers["members"]["3"] = {"first_name": None, "email": "invalid"} | request
        send(harness, form, answers)
        refreshed = revisit(harness)
        assert refreshed["members"][0]["request"] == request
        send(harness, refreshed, answers_for(refreshed))
    first, second = Submission.objects.order_by("family_version")
    expected = {kind} | ({"death_date"} if date_value else set())
    proposals = list(ProposedChange.objects.filter(submission=second))
    assert {row.field for row in proposals} == expected
    assert {row.handling for row in proposals if row.field == kind} == {"manual"}
    assert all(row.handling == "api" for row in proposals if row.field == "death_date")
    assert all(row.execution == "pending" for row in proposals)
    assert "first_name" not in second.answers["members"]["3"]
    assert set(
        ProposedChange.objects.filter(submission=first).values_list(
            "execution", flat=True
        )
    ) == {"superseded"}


def test_new_member_add_edit_remove_revisit_retains_history(live_response_service):
    harness = live_response_service
    identity = str(uuid4())
    with web_login():
        form = load_form(harness)
        answers = answers_for(form)
        answers["proposed_members"][identity] = member(
            first_name="New", birth_date="unknown", home_phone="2025550123"
        )
        send(harness, form, answers)
        refreshed = revisit(harness)
        assert answers_for(refreshed) == answers
        edited = answers_for(refreshed)
        edited["proposed_members"][identity]["first_name"] = "Corrected"
        send(harness, refreshed, edited)
        refreshed = revisit(harness)
        assert answers_for(refreshed) == edited
        removed = answers_for(refreshed)
        removed["proposed_members"].clear()
        send(harness, refreshed, removed)
        assert revisit(harness)["proposed_members"] == []
    first, second = ProposedChange.objects.order_by("created_at")
    assert (first.entity_kind, first.entity_key, first.field, first.handling) == (
        "proposed_member",
        identity,
        "new_member",
        "manual",
    )
    assert first.execution == "superseded" and first.superseded_by_id == second.pk
    assert second.execution == "cancelled"
    assert first.submitted_value["first_name"] == "New"
    assert second.submitted_value["first_name"] == "Corrected"
    assert Submission.objects.count() == 3


def test_terminal_submission_cancels_prior_ordinary_edits(live_response_service):
    harness = live_response_service
    with web_login():
        form = load_form(harness)
        answers = answers_for(form)
        answers["members"]["3"]["first_name"] = "Edited"
        send(harness, form, answers)
        form = revisit(harness)
        answers = answers_for(form)
        answers["members"]["3"] = {"moved_household": True, "confirmed": True}
        send(harness, form, answers)
    assert ProposedChange.objects.get(field="first_name").execution == "cancelled"
    assert ProposedChange.objects.get(field="moved_household").execution == "pending"


def test_source_death_date_catchup_does_not_resolve_semantics(live_response_service):
    harness = live_response_service
    identity = str(uuid4())
    with web_login():
        form = load_form(harness)
        answers = answers_for(form)
        answers["members"]["3"] = {
            "deceased_status": True,
            "confirmed": True,
            "death_date": "2026-01-01",
        }
        answers["proposed_members"][identity] = member()
        send(harness, form, answers)
    data = response_source()
    data.members[3]["dateOfDeath"] = "2026-01-01"
    snapshot, claim = prepare(data)
    with task_login(ServiceRole.WORKER, exact=True):
        promote(snapshot, claim, harness.campaign, harness.rings)
    assert (
        ProposedChange.objects.get(field="death_date").execution == "resolved_upstream"
    )
    assert ProposedChange.objects.get(field="deceased_status").execution == "pending"
    assert ProposedChange.objects.get(field="new_member").execution == "pending"


def test_same_local_intent_retains_review_decision_with_phone_reformat(
    live_response_service,
):
    harness = live_response_service
    identity = str(uuid4())
    with web_login():
        form = load_form(harness)
        answers = answers_for(form)
        answers["proposed_members"][identity] = member(home_phone="2025550123")
        send(harness, form, answers)
    with work_transaction():
        ProposedChange.objects.update(decision="ignored", version=F("version") + 1)
    with web_login():
        form = revisit(harness)
        answers = answers_for(form)
        answers["proposed_members"][identity]["home_phone"] = "+1 (202) 555-0123"
        send(harness, form, answers)
    current = ProposedChange.objects.get(execution="pending")
    assert current.decision == "ignored"
    assert current.entity_key == identity


def test_terminal_withdrawal_cancels_semantics_without_erasing_history(
    live_response_service,
):
    harness = live_response_service
    with web_login():
        form = load_form(harness)
        answers = answers_for(form)
        answers["members"]["3"] = {"moved_household": True, "confirmed": True}
        send(harness, form, answers)
        form = revisit(harness)
        answers = answers_for(form)
        answers["members"]["3"] = {
            field["name"]: field["value"] for field in form["members"][0]["fields"]
        }
        send(harness, form, answers)
    assert ProposedChange.objects.get(field="moved_household").execution == "cancelled"
    assert Submission.objects.count() == 2


def test_semantic_request_remains_manual_after_source_member_deceased(
    live_response_service,
):
    harness = live_response_service
    with web_login():
        form = load_form(harness)
        answers = answers_for(form)
        answers["members"]["3"] = {
            "deceased_status": True,
            "confirmed": True,
            "death_date": "",
        }
        send(harness, form, answers)
    data = response_source()
    data.members[3]["memberStatus"] = "Deceased"
    snapshot, claim = prepare(data)
    with task_login(ServiceRole.WORKER, exact=True):
        promote(snapshot, claim, harness.campaign, harness.rings)
    assert ProposedChange.objects.get(field="deceased_status").execution == "pending"


def test_omitted_death_date_cannot_be_forged_into_clear_proposal(
    response_service, monkeypatch
):
    from parishkit.stewardship.responses import submission as owner

    harness = response_service
    data = response_source()
    data.members[3]["dateOfDeath"] = "2026-01-01"
    snapshot, claim = prepare(data)
    promote(snapshot, claim, harness.campaign, harness.rings)
    form, answers = form_and_answers(harness)
    answers["members"]["3"] = {
        "deceased_status": True,
        "confirmed": True,
        "death_date": "",
    }
    original = owner.derive_proposals

    def forged(response, validated):
        """A defective derivation cannot reinterpret absence as a clearing intent."""
        rows = original(response, validated)
        ProposedChange.objects.create(
            submission=response,
            entity_kind="member",
            entity_key="3",
            field="death_date",
            baseline_available=True,
            baseline_value="2026-01-01",
            submitted_value=None,
            current_available=True,
            current_value="2026-01-01",
            current_source_id=validated.validation_snapshot_id,
            handling="api",
            actor_id=response.family_id,
        )
        return rows

    monkeypatch.setattr(owner, "derive_proposals", forged)
    with web_login(), pytest.raises(IntegrityError, match="Omitted terminal date"):
        submit(harness, form, answers)
    assert not Submission.objects.exists() and not ProposedChange.objects.exists()


@pytest.mark.parametrize("change", ["deceased", "inactive", "moved", "removed"])
@pytest.mark.parametrize("caught_up", [False, True])
def test_date_work_survives_terminal_source_and_later_household_response(
    live_response_service, change, caught_up
):
    """Status completion neither cancels date work nor reads a foreign date."""
    harness = live_response_service
    with web_login():
        form = load_form(harness)
        answers = answers_for(form)
        answers["members"]["3"] = {
            "deceased_status": True,
            "confirmed": True,
            "death_date": "2026-01-01",
        }
        send(harness, form, answers)
    data = response_source()
    # Keep another active Member so the household remains portal-eligible after
    # Member 3 leaves the active roster. An empty household must not log in.
    data.members[4] = {**data.members[3], "memberDUID": 4, "firstName": "Survivor"}
    if caught_up:
        data.members[3]["dateOfDeath"] = "2026-01-01"
    if change == "removed":
        del data.members[3]
        data.ministry_type_memberships.clear()
        data.member_contactinfos.clear()
    elif change == "moved":
        data.members[3]["familyDUID"] = 2
    else:
        data.members[3]["memberStatus"] = (
            "Deceased" if change == "deceased" else "Inactive"
        )
    snapshot, claim = prepare(data)
    with task_login(ServiceRole.WORKER, exact=True):
        promote(snapshot, claim, harness.campaign, harness.rings)
    date_change = ProposedChange.objects.get(field="death_date")
    expected = (
        "conflict"
        if change in {"moved", "removed"}
        else "resolved_upstream"
        if caught_up
        else "pending"
    )
    assert date_change.execution == expected
    if change in {"moved", "removed"}:
        assert not date_change.current_available and date_change.current_value is None
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT stewardship_response_field_source_v1(%s,%s,'3','death_date')",
            [snapshot.pk, date_change.submission.family_id],
        )
        reconstructed = cursor.fetchone()[0]
    if change in {"moved", "removed"}:
        assert reconstructed is None
    else:
        import json

        assert json.loads(reconstructed) == {
            "available": date_change.current_available,
            "value": date_change.current_value,
        }
    original_version, original_source = (
        date_change.version,
        date_change.current_source_id,
    )
    repeated, repeated_claim = prepare(data)
    with task_login(ServiceRole.WORKER, exact=True):
        promote(repeated, repeated_claim, harness.campaign, harness.rings)
    date_change.refresh_from_db()
    assert (date_change.version, date_change.current_source_id) == (
        original_version,
        original_source,
    )
    from parishkit.stewardship.source.models import SourceSnapshotPin

    assert not SourceSnapshotPin.objects.filter(
        snapshot=repeated, parent_kind="submission", parent_id=date_change.submission_id
    ).exists()
    with web_login():
        form = revisit(harness)
        assert [value["id"] for value in form["members"]] == ["4"]
        send(harness, form, answers_for(form))
    date_change.refresh_from_db()
    assert date_change.execution == expected
    assert ProposedChange.objects.get(field="deceased_status").execution == "pending"


@pytest.mark.parametrize("kind", ["moved_household", "deceased_status"])
def test_resolved_semantic_is_not_reopened_by_untouched_revisit(
    live_response_service, kind
):
    """Hide completed status work without cancelling independent date work."""
    harness = live_response_service
    with web_login():
        form = load_form(harness)
        answers = answers_for(form)
        answers["members"]["3"] = {kind: True, "confirmed": True}
        if kind == "deceased_status":
            answers["members"]["3"]["death_date"] = "2026-01-01"
        send(harness, form, answers)
    with work_transaction():
        ProposedChange.objects.filter(field=kind).update(
            execution="resolved_external", version=F("version") + 1
        )
    with web_login():
        form = revisit(harness)
        assert form["members"][0]["request"] is None
        send(harness, form, answers_for(form))
    assert ProposedChange.objects.filter(field=kind).count() == 1
    assert ProposedChange.objects.get(field=kind).execution == "resolved_external"
    if kind == "deceased_status":
        assert ProposedChange.objects.get(field="death_date").execution == "pending"


@pytest.mark.parametrize("changed_date", [False, True])
def test_preserved_request_reappears_and_supersedes_after_member_returns(
    live_response_service, changed_date
):
    """An intervening response cannot orphan or duplicate preserved requests."""
    harness = live_response_service
    with web_login():
        form = load_form(harness)
        answers = answers_for(form)
        answers["members"]["3"] = {
            "deceased_status": True,
            "confirmed": True,
            "death_date": "2026-01-01",
        }
        send(harness, form, answers)
    original = {row.field: row for row in ProposedChange.objects.all()}
    with work_transaction():
        ProposedChange.objects.filter(field="death_date").update(
            decision="ignored", version=F("version") + 1
        )
    data = response_source()
    data.members[4] = {**data.members[3], "memberDUID": 4, "firstName": "Survivor"}
    data.members[3]["memberStatus"] = "Inactive"
    snapshot, claim = prepare(data)
    with task_login(ServiceRole.WORKER, exact=True):
        promote(snapshot, claim, harness.campaign, harness.rings)
    with web_login():
        form = revisit(harness)
        send(harness, form, answers_for(form))
        form = revisit(harness)
        send(harness, form, answers_for(form))
    data.members[3]["memberStatus"] = "Active"
    snapshot, claim = prepare(data)
    with task_login(ServiceRole.WORKER, exact=True):
        promote(snapshot, claim, harness.campaign, harness.rings)
    with web_login():
        form = revisit(harness)
        restored = next(value for value in form["members"] if value["id"] == "3")
        assert restored["request"]["death_date"] == "2026-01-01"
        answers = answers_for(form)
        if changed_date:
            answers["members"]["3"]["death_date"] = "2026-01-02"
        send(harness, form, answers)
    for old in original.values():
        old.refresh_from_db()
        assert old.execution == "superseded" and old.superseded_by_id is not None
    current = ProposedChange.objects.exclude(execution="superseded")
    assert current.count() == 2
    assert current.get(field="death_date").decision == (
        "unreviewed" if changed_date else "ignored"
    )
    with web_login():
        form = revisit(harness)
        answers = answers_for(form)
        answers["members"]["3"] = member(first_name="Member", birth_date="1960-01-01")
        send(harness, form, answers)
    assert not ProposedChange.objects.filter(
        field__in=["deceased_status", "death_date"],
        execution__in=["pending", "conflict", "queued", "failed"],
    ).exists()


def test_reselecting_completed_status_without_date_preserves_hidden_date(
    live_response_service,
):
    """A hidden date is not an informed withdrawal on a later blank request."""
    harness = live_response_service
    with web_login():
        form = load_form(harness)
        answers = answers_for(form)
        answers["members"]["3"] = {
            "deceased_status": True,
            "confirmed": True,
            "death_date": "2026-01-01",
        }
        send(harness, form, answers)
    with work_transaction():
        ProposedChange.objects.filter(field="deceased_status").update(
            execution="resolved_external", version=F("version") + 1
        )
    with web_login():
        # Two untouched responses exercise history beyond the immediate prior.
        for _ in range(2):
            form = revisit(harness)
            assert form["members"][0]["request"] is None
            send(harness, form, answers_for(form))
        form = revisit(harness)
        answers = answers_for(form)
        answers["members"]["3"] = {
            "deceased_status": True,
            "confirmed": True,
            "death_date": "",
        }
        send(harness, form, answers)
        assert revisit(harness)["members"][0]["request"]["death_date"] == "2026-01-01"
    assert ProposedChange.objects.get(field="death_date").execution == "pending"


def test_preserved_history_does_not_follow_member_into_another_family(
    live_response_service,
):
    """A reused Member DUID does not expose or inherit another Family's intent."""
    from parishkit.stewardship.campaigns.credential_models import FamilyCampaign
    from parishkit.stewardship.campaigns.family_identity import code_context

    harness = live_response_service
    request = {
        "deceased_status": True,
        "confirmed": True,
        "death_date": "2026-01-01",
    }
    with web_login():
        form = load_form(harness)
        answers = answers_for(form)
        answers["members"]["3"] = request
        send(harness, form, answers)
    with work_transaction():
        ProposedChange.objects.update(decision="ignored", version=F("version") + 1)
    data = response_source()
    data.members[4] = {**data.members[3], "memberDUID": 4, "firstName": "Survivor"}
    data.members[3]["familyDUID"] = 2
    snapshot, claim = prepare(data)
    with task_login(ServiceRole.WORKER, exact=True):
        promote(snapshot, claim, harness.campaign, harness.rings)
    other_family = FamilyCampaign.objects.get(campaign=harness.campaign, family_duid=2)
    other_code = harness.rings.general.decrypt(
        other_family.code_ciphertext, context=code_context(other_family.pk)
    ).decode()
    other = replace(harness, code=other_code)
    with web_login():
        form = revisit(other)
        assert form["members"][0]["request"] is None
        send(other, form, answers_for(form))
        form = revisit(other)
        assert form["members"][0]["request"] is None
        answers = answers_for(form)
        answers["members"]["3"] = request
        send(other, form, answers)
    assert set(
        ProposedChange.objects.filter(submission__family=other_family).values_list(
            "decision", flat=True
        )
    ) == {"unreviewed"}
    assert ProposedChange.objects.filter(
        submission__family__family_duid=1, decision="ignored", execution="pending"
    ).exists()


@pytest.mark.parametrize(
    "answer",
    [
        {"moved_household": True, "confirmed": False},
        {"moved_household": True, "confirmed": True, "first_name": "forged"},
        {"deceased_status": True, "confirmed": True},
        {"deceased_status": True, "confirmed": True, "death_date": "1959-01-01"},
        {"deceased_status": True, "confirmed": True, "death_date": "2999-01-01"},
        {"deceased_status": True, "confirmed": True, "death_date": True},
        {
            "deceased_status": True,
            "moved_household": True,
            "confirmed": True,
            "death_date": None,
        },
    ],
)
def test_sql_independently_rejects_forged_terminal_answer(
    response_service, monkeypatch, answer
):
    from parishkit.stewardship.responses import submission as owner

    original = owner.validate_answers
    form, answers = form_and_answers(response_service)

    def forged(*args, **kwargs):
        """Bypass only Python normalization, leaving the real restricted SQL owner."""
        result = deepcopy(original(*args, **kwargs))
        result["members"]["3"] = answer
        return result

    monkeypatch.setattr(owner, "validate_answers", forged)
    with web_login(), pytest.raises(IntegrityError):
        submit(response_service, form, answers)
    assert not Submission.objects.exists() and not ProposedChange.objects.exists()


@pytest.mark.parametrize(
    "corruption", ["identifier", "missing", "terminal", "required", "extra"]
)
def test_sql_independently_rejects_forged_proposed_member(
    response_service, monkeypatch, corruption
):
    from parishkit.stewardship.responses import submission as owner

    original = owner.validate_answers
    form, answers = form_and_answers(response_service)
    identity = str(uuid4())
    answers["proposed_members"][identity] = member()

    def forged(*args, **kwargs):
        """Alter one normalized structure before the guarded aggregate insertion."""
        result = deepcopy(original(*args, **kwargs))
        value = result["proposed_members"][identity]
        if corruption == "identifier":
            result["proposed_members"] = {"3": value}
        elif corruption == "missing":
            del value["birth_date"]
        elif corruption == "terminal":
            result["proposed_members"][identity] = {
                "moved_household": True,
                "confirmed": True,
            }
        elif corruption == "required":
            value["first_name"] = None
        else:
            value["private"] = "forged"
        return result

    monkeypatch.setattr(owner, "validate_answers", forged)
    with web_login(), pytest.raises(IntegrityError):
        submit(response_service, form, answers)
    assert not Submission.objects.exists() and not ProposedChange.objects.exists()
