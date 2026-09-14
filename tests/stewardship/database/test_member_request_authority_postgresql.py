"""Fault-inject derivation while retaining exact web SQL authority boundaries."""

from contextlib import contextmanager

import pytest
from django.db import IntegrityError, connection, transaction
from django.db.models import F, QuerySet

from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.responses import submission as owner
from parishkit.stewardship.responses.effective import proposal_index
from parishkit.stewardship.responses.models import ProposedChange, Submission

from .test_member_requests_postgresql import revisit, send
from .test_response_http_postgresql import answers_for, load_form
from .test_runtime_auth_grants_postgresql import web_login

pytestmark = pytest.mark.django_db(transaction=True)


def request_answer():
    """A manual semantic request with no companion date row."""
    return {"deceased_status": True, "confirmed": True, "death_date": ""}


def first_request(harness, *, ordinary=False):
    """Create actual immutable answers and a separately reviewed pending row."""
    with web_login():
        form = load_form(harness)
        answers = answers_for(form)
        if ordinary:
            answers["members"]["3"]["first_name"] = "Requested"
        else:
            answers["members"]["3"] = request_answer()
        send(harness, form, answers)
    row = ProposedChange.objects.get()
    with work_transaction():
        ProposedChange.objects.filter(pk=row.pk).update(
            decision="ignored", version=F("version") + 1
        )
    return row


def response_without_derivation(harness, monkeypatch):
    """Emulate incomplete derivation, not an alternate production write owner."""
    with incomplete_history_fixture(), monkeypatch.context() as patch, web_login():
        patch.setattr(owner, "derive_proposals", lambda *args: [])
        form = revisit(harness)
        send(harness, form, answers_for(form))
    return Submission.objects.latest("family_version")


@contextmanager
def incomplete_history_fixture():
    """Fabricate corruption only as the disposable schema owner, then restore guards.

    The production completeness guard now rejects these historical no-op bugs.
    Retain independent child-authority tests by explicitly injecting the corrupt
    starting state. Every tested web mutation runs after this guard is restored.
    """
    with connection.cursor() as cursor:
        cursor.execute(
            "ALTER TABLE stewardship_submission DISABLE TRIGGER "
            "stewardship_submission_effects"
        )
    try:
        yield
    finally:
        with connection.cursor() as cursor:
            cursor.execute(
                "ALTER TABLE stewardship_submission ENABLE TRIGGER "
                "stewardship_submission_effects"
            )
            cursor.execute(
                "SELECT tgenabled FROM pg_trigger "
                "WHERE tgname='stewardship_submission_effects'"
            )
            assert cursor.fetchone() == ("O",)


def restart_rehearsal(harness):
    """Retain invalidated history while issuing a genuinely new rehearsal epoch."""
    from parishkit.stewardship.campaigns.credential_models import RehearsalCredential
    from parishkit.stewardship.campaigns.lifecycle import CampaignWorkKind
    from parishkit.stewardship.campaigns.rehearsals import (
        code_context,
        invalidate_rehearsal,
        prepare_rehearsals,
        release_rehearsal_gate,
    )

    prior = Submission.objects.latest("family_version")
    invalidate_rehearsal(campaign_id=harness.campaign.pk, admit=lambda *args: True)
    release_rehearsal_gate(campaign_id=harness.campaign.pk, admit=lambda *args: True)
    credentials = prepare_rehearsals(
        campaign_id=harness.campaign.pk,
        family_ids=[prior.family_id],
        general=harness.rings.general,
        mac=harness.rings.mac,
        public=harness.rings.public,
        purpose=CampaignWorkKind.REHEARSAL,
        admit=lambda *args: True,
    )
    credential = RehearsalCredential.objects.get(pk=credentials[prior.family_id])
    harness.code = harness.rings.general.decrypt(
        credential.code_ciphertext, context=code_context(credential.pk)
    ).decode()


def foreign_parent_fixture(row, scope):
    """Seed malformed historical provenance only in the disposable test database.

    The schema owner temporarily disables the immutable-row trigger to model
    a foreign namespace without granting that ability to the tested web role.
    This is fault injection, not an upgrade, restore or production mutation.
    """
    from parishkit.stewardship.campaigns.credential_models import FamilyCampaign

    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(
            "ALTER TABLE stewardship_submission DISABLE TRIGGER "
            "stewardship_submission_guard"
        )
        if scope == "family":
            other = FamilyCampaign.objects.get(family_duid=2)
            cursor.execute(
                "UPDATE stewardship_submission SET family_id=%s WHERE id=%s",
                [other.pk, row.submission_id],
            )
        else:
            cursor.execute(
                "UPDATE stewardship_submission SET mode='live', "
                "rehearsal_epoch_id=NULL WHERE id=%s",
                [row.submission_id],
            )
        # Finish deferred FK checks before changing the trigger definition.
        cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
        cursor.execute(
            "ALTER TABLE stewardship_submission ENABLE TRIGGER "
            "stewardship_submission_guard"
        )


@pytest.mark.parametrize("scope", ["family", "mode", "epoch", "ordinary_history"])
def test_sql_rejects_review_state_from_unrelated_history(
    response_service, monkeypatch, scope
):
    """Bypass Python selection: only SQL can reject the forged carried decision."""
    harness = response_service
    old = first_request(harness, ordinary=scope == "ordinary_history")
    if scope == "epoch":
        restart_rehearsal(harness)
    prior = response_without_derivation(harness, monkeypatch)
    if scope in {"family", "mode"}:
        foreign_parent_fixture(old, scope)
    with work_transaction():
        indexed = proposal_index(prior)
        assert (old.entity_kind, old.entity_key, old.field) not in indexed
    with web_login():
        form = revisit(harness)
        answers = answers_for(form)
        if scope == "ordinary_history":
            answers["members"]["3"]["first_name"] = "Requested"
        else:
            assert form["members"][0]["request"] is None
            answers["members"]["3"] = request_answer()
        original = ProposedChange.objects.create

        def forged(**values):
            """Inject the foreign decision immediately before the real INSERT."""
            return original(**(values | {"decision": "ignored"}))

        monkeypatch.setattr(ProposedChange.objects, "create", forged)
        with pytest.raises(IntegrityError, match="review state must be unreviewed"):
            send(harness, form, answers)
    assert Submission.objects.count() == 2


def duplicate_pending_fixture(harness, monkeypatch):
    """Emulate a faulty prior derivation that omitted its predecessor transition."""
    original_update = QuerySet.update

    def omit_supersession(query, **values):
        """Keep only the old pending row; all actual inserts remain guarded."""
        if query.model is ProposedChange and values.get("execution") == "superseded":
            return 0
        return original_update(query, **values)

    with incomplete_history_fixture(), monkeypatch.context() as patch, web_login():
        patch.setattr(QuerySet, "update", omit_supersession)
        form = revisit(harness)
        send(harness, form, answers_for(form))


@pytest.mark.parametrize("target", ["stale", "family", "closed_successor"])
def test_web_replacement_rejects_unrelated_or_stale_request(
    response_service, monkeypatch, target
):
    """An in-flight response cannot authorize arbitrary historical mutations."""
    harness = response_service
    old = first_request(harness)
    if target in {"stale", "closed_successor"}:
        duplicate_pending_fixture(harness, monkeypatch)
    else:
        response_without_derivation(harness, monkeypatch)
        foreign_parent_fixture(old, "family")
    latest = ProposedChange.objects.order_by("-submission__family_version").first()
    with web_login():
        form = revisit(harness)
        answers = answers_for(form)

        def forged(*args):
            """Try a stale/foreign mutation while the new baseline is still open."""
            values = {"execution": "cancelled", "version": F("version") + 1}
            victim = old
            if target == "closed_successor":
                victim = latest
                values.update(execution="superseded", superseded_by_id=old.pk)
            ProposedChange.objects.filter(pk=victim.pk).update(**values)

        monkeypatch.setattr(owner, "derive_proposals", forged)
        with pytest.raises(IntegrityError, match="new final Family response"):
            send(harness, form, answers)
    old.refresh_from_db()
    assert old.execution == "pending"


def test_current_history_version_does_not_expose_future_request(response_service):
    """A retained earlier baseline cannot select a later request for the same key."""
    harness = response_service
    old = first_request(harness)
    prior = Submission.objects.get(pk=old.submission_id)
    with web_login():
        form = revisit(harness)
        answers = answers_for(form)
        answers["members"]["3"] = {"moved_household": True, "confirmed": True}
        send(harness, form, answers)
    with work_transaction():
        assert ("member", "3", "moved_household") not in proposal_index(prior)
