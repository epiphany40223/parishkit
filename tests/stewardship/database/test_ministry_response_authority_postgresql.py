"""Exact-role SQL independently rejects forged and incomplete Ministry effects."""

from copy import deepcopy
from functools import partial
from uuid import UUID, uuid4

import pytest
from django.db import IntegrityError, transaction
from django.db.models import F, QuerySet

from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.responses import submission
from parishkit.stewardship.responses.models import ProposedChange, Submission
from parishkit.stewardship.source.models import SourceSnapshotPin
from parishkit.stewardship.workflows.models import MinistryRequest

from ..census_factory import member
from .test_background_grants_postgresql import task_login
from .test_ministry_responses_postgresql import configure, respond, revisit, start
from .test_response_http_postgresql import answers_for
from .test_runtime_auth_grants_postgresql import web_login

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.mark.parametrize(
    "setup_fault,faults",
    [
        (
            "common",
            (
                "current_join",
                "noncurrent_leave",
                "unknown_ministry",
                "foreign_member",
                "duplicate",
                "boolean_id",
                "string_id",
                "extra_group",
                "missing_member",
                "disabled_census",
                "proposed_without_census",
            ),
        ),
        ("inactive_ministry", ("inactive_ministry",)),
        ("unselected_ministry", ("unselected_ministry",)),
        ("proposed_leave", ("proposed_leave",)),
    ],
    ids=[
        "common-fixture",
        "inactive-ministry",
        "unselected-ministry",
        "proposed-member",
    ],
)
def test_sql_revalidates_complete_ministry_aggregate(
    response_service, monkeypatch, setup_fault, faults
):
    """Bypass only Python answer validation; SQL still owns the admitted boundary."""
    # Only the common invalid-input cases share setup. Cases with different
    # configuration/census prerequisites retain independent fixture instances.
    harness = response_service
    form = start(harness, census=setup_fault == "proposed_leave")
    if setup_fault == "unselected_ministry":
        configure(harness, census=False, selected=(4,))
        from .test_response_http_postgresql import load_form

        form = load_form(harness)
    elif setup_fault == "inactive_ministry":
        configure(
            harness,
            census=False,
            patches=[
                {
                    "operation": "add",
                    "section": "ministries",
                    "id": str(uuid4()),
                    "values": {
                        "organization_id": 12345,
                        "ministry_duid": 9,
                        "active": False,
                    },
                }
            ],
        )
        from .test_response_http_postgresql import load_form

        form = load_form(harness)
    answers = answers_for(form)
    proposed_id = str(uuid4())
    if setup_fault == "proposed_leave":
        answers["proposed_members"][proposed_id] = member()
        answers["ministries"]["proposed_members"][proposed_id] = {"join": []}
    real = submission.validate_answers

    def forge(fault, *args, **kwargs):
        """Alter a normalized aggregate, not source/configuration or SQL guards."""
        result = deepcopy(real(*args, **kwargs))
        choices = result["ministries"]["members"]["3"]
        if fault == "current_join":
            choices["join"] = [4]
        elif fault == "noncurrent_leave":
            choices["leave"] = [9]
        elif fault == "unknown_ministry":
            choices["join"] = [999]
        elif fault in {"inactive_ministry", "unselected_ministry"}:
            choices["join"] = [9]
        elif fault == "proposed_leave":
            result["ministries"]["proposed_members"][proposed_id]["leave"] = [4]
        elif fault == "foreign_member":
            result["ministries"]["members"]["999"] = choices
        elif fault == "duplicate":
            choices["join"] = [9, 9]
        elif fault == "boolean_id":
            choices["join"] = [True]
        elif fault == "string_id":
            choices["join"] = ["9"]
        elif fault == "extra_group":
            result["ministries"]["private_unexpected"] = {}
        elif fault == "missing_member":
            result["ministries"]["members"] = {}
        elif fault == "disabled_census":
            result["members"]["3"] = {"first_name": "Forbidden census write"}
        else:
            result["proposed_members"] = {"new": {"first_name": "Unscoped"}}
        return result

    with monkeypatch.context() as patch:
        for fault in faults:
            patch.setattr(submission, "validate_answers", partial(forge, fault))
            # A genuine independent transaction per attempted write preserves
            # deferred SQL checks. Only the expensive initial campaign is reused.
            try:
                with web_login(), transaction.atomic():
                    submission.submit_family(
                        harness.request,
                        harness.service,
                        baseline_id=UUID(form["baseline"]),
                        payload=answers,
                    )
            except IntegrityError:
                pass
            except Exception as error:
                error.add_note(f"Ministry aggregate fault: {fault}")
                raise
            else:
                # Stop here: a committed bad write invalidates the shared
                # baseline, so continuing would report misleading later errors.
                pytest.fail(f"{fault}: SQL accepted a forged ministry aggregate")
            assert not Submission.objects.exists(), fault
            assert not MinistryRequest.objects.exists(), fault
    # Successful unchanged submission proves a prior failed case did not poison
    # the shared baseline/session and that these were not generic admission errors.
    with web_login():
        accepted = submission.submit_family(
            harness.request,
            harness.service,
            baseline_id=UUID(form["baseline"]),
            payload=answers,
        )
    assert Submission.objects.get().pk == accepted.submission.pk


def test_missing_derived_ministry_work_rolls_back_every_final_effect(
    response_service, monkeypatch
):
    """A forgotten derivation owner cannot commit answers without follow-up work."""
    harness = response_service
    form = start(harness)
    answers = answers_for(form)
    answers["ministries"]["members"]["3"]["join"] = [9]
    monkeypatch.setattr(submission, "derive_ministry_requests", lambda *args: [])
    with web_login(), pytest.raises(IntegrityError), transaction.atomic():
        submission.submit_family(
            harness.request,
            harness.service,
            baseline_id=UUID(form["baseline"]),
            payload=answers,
        )
    assert not Submission.objects.exists() and not MinistryRequest.objects.exists()
    assert not SourceSnapshotPin.objects.filter(parent_kind="submission").exists()


def test_omitted_supersession_cannot_leave_duplicate_actionable_history(
    response_service, monkeypatch
):
    """Deferred completeness checks catch an interrupted derived update sequence."""
    harness = response_service
    form = start(harness)
    answers = answers_for(form)
    answers["ministries"]["members"]["3"]["join"] = [9]
    respond(harness, form, answers)
    form = revisit(harness)
    real = QuerySet.update

    def omit_request_update(query, **kwargs):
        """Only suppress the predecessor update; keep actual insert/commit guards."""
        if query.model is MinistryRequest:
            return 0
        return real(query, **kwargs)

    monkeypatch.setattr(QuerySet, "update", omit_request_update)
    with web_login(), pytest.raises(IntegrityError), transaction.atomic():
        submission.submit_family(
            harness.request,
            harness.service,
            baseline_id=UUID(form["baseline"]),
            payload=answers_for(form),
        )
    assert Submission.objects.count() == 1
    assert MinistryRequest.objects.get().state == "new"


def test_web_cannot_cancel_outside_new_final_submission(response_service):
    """Knowing a request UUID and taking common locks do not confer workflow rights."""
    harness = response_service
    form = start(harness)
    answers = answers_for(form)
    answers["ministries"]["members"]["3"]["join"] = [9]
    respond(harness, form, answers)
    row = MinistryRequest.objects.get()
    with web_login(), pytest.raises(IntegrityError), work_transaction():
        MinistryRequest.objects.filter(pk=row.pk).update(
            state="cancelled", version=F("version") + 1
        )
    row.refresh_from_db()
    assert row.state == "new"


@pytest.mark.parametrize("fault", ["resolved", "ministry_only", "gap_withdrawal"])
def test_sql_independently_enforces_proposed_member_cancellation_scope(
    response_service, monkeypatch, fault
):
    """Faulty derivation cannot discard unseen intent or omit an explicit removal."""
    harness = response_service
    form = start(harness)
    identifier = str(uuid4())
    answers = answers_for(form)
    answers["proposed_members"][identifier] = member()
    answers["ministries"]["proposed_members"][identifier] = {"join": [9]}
    respond(harness, form, answers)
    row = MinistryRequest.objects.get()
    if fault == "resolved":
        with work_transaction():
            ProposedChange.objects.filter(field="new_member").update(
                execution="resolved_external", version=F("version") + 1
            )
    else:
        configure(harness, census=False)
        if fault == "gap_withdrawal":
            form = revisit(harness)
            respond(harness, form, answers_for(form))
            configure(harness)
    form = revisit(harness)
    answers = answers_for(form)
    answers["proposed_members"] = {}
    answers["ministries"]["proposed_members"] = {}
    before = Submission.objects.count()

    def faulty_derivation(*args):
        """Bypass only Python scope selection, preserving actual SQL authority."""
        if fault != "gap_withdrawal":
            MinistryRequest.objects.filter(pk=row.pk).update(
                state="cancelled", version=F("version") + 1
            )
        # For a real removal after a module gap, suppress the required update;
        # the deferred guard must still discover the census anchor at commit.
        return []

    with monkeypatch.context() as patch:
        patch.setattr(submission, "derive_ministry_requests", faulty_derivation)
        with web_login(), pytest.raises(IntegrityError), transaction.atomic():
            submission.submit_family(
                harness.request,
                harness.service,
                baseline_id=UUID(form["baseline"]),
                payload=answers,
            )
    assert Submission.objects.count() == before
    row.refresh_from_db()
    assert row.state == "new"
    # Failed final effects roll back, leaving the genuine form usable. Repeating
    # the correct response also proves resolved/unpresented work stays intact.
    with web_login():
        respond(harness, form, answers)
        form = revisit(harness)
        respond(harness, form, answers_for(form))
    row.refresh_from_db()
    assert row.state == ("cancelled" if fault == "gap_withdrawal" else "new")


def test_worker_cannot_manufacture_roster_resolution_without_source_fence(
    response_service,
):
    """An existing pin is insufficient without owned current roster proof."""
    harness = response_service
    form = start(harness)
    answers = answers_for(form)
    answers["ministries"]["members"]["3"]["join"] = [9]
    respond(harness, form, answers)
    row = MinistryRequest.objects.get()
    with (
        task_login(ServiceRole.WORKER, exact=True),
        pytest.raises(IntegrityError),
        work_transaction(),
    ):
        MinistryRequest.objects.filter(pk=row.pk).update(
            state="resolved",
            outcome="joined",
            resolution_source_id=harness.snapshot.pk,
            resolved_at=row.created_at,
            version=F("version") + 1,
        )
    row.refresh_from_db()
    assert row.state == "new"
