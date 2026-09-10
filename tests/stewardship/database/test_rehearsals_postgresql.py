"""Rehearsal mode isolation, anonymous reservations and real MAC migration."""

import pytest
from django.db import IntegrityError, transaction
from django.db.models import F

from parishkit.stewardship.accounts.cryptography import (
    CodeMacKeyring,
    CryptographicError,
    Key,
)
from parishkit.stewardship.accounts.sessions import database_now
from parishkit.stewardship.audit.models import AuditEvent
from parishkit.stewardship.campaigns.credential_keys import add_rotation_key
from parishkit.stewardship.campaigns.credential_models import (
    CampaignCredentialState,
    FamilyCampaign,
    RehearsalCodeReservation,
    RehearsalCredential,
    RehearsalEpoch,
)
from parishkit.stewardship.campaigns.lifecycle import CampaignWorkKind
from parishkit.stewardship.campaigns.mac_rotation import (
    backfill_mac_batch,
    collision_only,
)
from parishkit.stewardship.campaigns.rehearsals import (
    cleanup_rehearsal,
    code_context,
    invalidate_rehearsal,
    prepare_rehearsals,
    release_rehearsal_gate,
    token_context,
)
from parishkit.stewardship.storage import StorageInvariantError

from .credential_builders import TestKeys, family_campaign

pytestmark = pytest.mark.django_db(transaction=True)


def prepare(campaign, ring):
    """Explicit readiness admission is synthetic here; it never enables login."""
    return prepare_rehearsals(
        campaign_id=campaign.pk,
        family_ids=list(
            FamilyCampaign.objects.filter(campaign=campaign).values_list(
                "pk", flat=True
            )
        ),
        general=ring.general,
        mac=ring.mac,
        public=ring.public,
        purpose=CampaignWorkKind.READINESS_TEST,
        admit=lambda *args: True,
    )


def invalidate(campaign):
    """Run the real storage invalidation under the synthetic owning gate admission."""
    return invalidate_rehearsal(campaign_id=campaign.pk, admit=lambda *args: True)


def test_invalid_epoch_pointer_cannot_emit_fictitious_transition(tmp_path):
    """Corrupt/stale durable epoch state fails before clearing its retained pointer."""
    _, campaign, _, ring = family_campaign(tmp_path)
    prepare(campaign, ring)
    scope = CampaignCredentialState.objects.get(campaign=campaign)
    original = scope.rehearsal_epoch_id, scope.version
    RehearsalEpoch.objects.filter(pk=scope.rehearsal_epoch_id).update(
        state="invalidated", invalidated_at=database_now(), version=F("version") + 1
    )
    with pytest.raises(StorageInvariantError, match="not active"):
        invalidate(campaign)
    scope.refresh_from_db()
    assert (scope.rehearsal_epoch_id, scope.version) == original
    assert not AuditEvent.objects.filter(event_type="rehearsal_invalidated").exists()


def test_testing_codes_and_tokens_are_disjoint_and_idempotent(tmp_path):
    _, campaign, _, ring = family_campaign(tmp_path, count=3)
    first = prepare(campaign, ring)
    assert prepare(campaign, ring) == first
    assert RehearsalCodeReservation.objects.count() == 3
    for credential in RehearsalCredential.objects.all():
        assert ring.general.decrypt(
            credential.code_ciphertext, context=code_context(credential.pk)
        ).startswith(b"I")
        assert ring.private.decrypt(
            credential.token_ciphertext, context=token_context(credential.pk)
        ).startswith(b"test.")
    fields = {field.name for field in RehearsalCodeReservation._meta.fields}
    assert fields == {"id", "campaign", "key_id", "algorithm", "digest"}


def test_invalidation_precedes_bounded_cleanup_and_never_erases_reservations(tmp_path):
    _, campaign, _, ring = family_campaign(tmp_path, count=3)
    prepare(campaign, ring)
    epoch = RehearsalCredential.objects.first().epoch_id
    with pytest.raises(StorageInvariantError, match="active"):
        cleanup_rehearsal(epoch)
    with pytest.raises(IntegrityError), transaction.atomic():
        RehearsalCredential.objects.all().delete()
    assert invalidate(campaign) == epoch
    with pytest.raises(StorageInvariantError, match="not admitted"):
        prepare(campaign, ring)
    assert cleanup_rehearsal(epoch, batch_size=2) == 2
    assert cleanup_rehearsal(epoch, batch_size=2) == 1
    assert cleanup_rehearsal(epoch) == 0
    assert RehearsalCodeReservation.objects.count() == 3
    release_rehearsal_gate(campaign_id=campaign.pk, admit=lambda *args: True)
    prepare(campaign, ring)
    assert RehearsalCredential.objects.first().epoch_id != epoch
    assert RehearsalCodeReservation.objects.count() == 6


def test_retired_code_is_not_reused_after_cleanup_and_key_demotion(
    tmp_path, monkeypatch
):
    from parishkit.stewardship.campaigns import rehearsals

    _, campaign, _, ring = family_campaign(tmp_path)
    prepare(campaign, ring)
    credential = RehearsalCredential.objects.get()
    old_code = ring.general.decrypt(
        credential.code_ciphertext, context=code_context(credential.pk)
    ).decode()
    epoch = invalidate(campaign)
    cleanup_rehearsal(epoch)
    rotating = CodeMacKeyring(
        [Key("m1", "lookup-only", b"m" * 32), Key("m2", "active", b"n" * 32)]
    )
    add_rotation_key(ring.mac, rotating)
    demoted = CodeMacKeyring(
        [Key("m1", "collision-only", b"m" * 32), Key("m2", "active", b"n" * 32)]
    )
    with pytest.raises(CryptographicError, match="incomplete"):
        collision_only(rotating, demoted, admit=lambda: True)
    assert (
        backfill_mac_batch(
            campaign_id=campaign.pk,
            general=ring.general,
            mac=rotating,
            admit=lambda: True,
        )
        == 1
    )
    assert (
        backfill_mac_batch(
            campaign_id=campaign.pk,
            general=ring.general,
            mac=rotating,
            admit=lambda: True,
        )
        == 0
    )
    collision_only(rotating, demoted, admit=lambda: True)
    assert RehearsalCodeReservation.objects.get().key_id == "m1"
    candidates = iter([old_code, "IABCDEFG"])
    monkeypatch.setattr(rehearsals, "new_code", lambda **kwargs: next(candidates))
    release_rehearsal_gate(campaign_id=campaign.pk, admit=lambda *args: True)
    prepare(campaign, TestKeys(ring.general, demoted, ring.private))
    credential = RehearsalCredential.objects.get()
    assert (
        ring.general.decrypt(
            credential.code_ciphertext, context=code_context(credential.pk)
        )
        == b"IABCDEFG"
    )
    assert set(RehearsalCodeReservation.objects.values_list("key_id", flat=True)) == {
        "m1",
        "m2",
    }


def test_missing_required_mac_inventory_fails_without_production_fallback(tmp_path):
    _, campaign, _, ring = family_campaign(tmp_path)
    prepare(campaign, ring)
    epoch = invalidate(campaign)
    cleanup_rehearsal(epoch)
    release_rehearsal_gate(campaign_id=campaign.pk, admit=lambda *args: True)
    incomplete = CodeMacKeyring([Key("m2", "active", b"n" * 32)])
    with pytest.raises(CryptographicError):
        prepare(campaign, TestKeys(ring.general, incomplete, ring.private))
    assert not RehearsalCredential.objects.exists()
