"""Idempotent display-code MAC backfill and verified collision-only demotion.

The operational owner must establish its authorization locks and return exactly
True from admit inside the transaction; no omitted or truthy callback is consent.
"""

from django.db import transaction
from django.db.models import Exists, F, OuterRef

from parishkit.stewardship.accounts.cryptography import (
    CodeMacKeyring,
    CryptographicError,
)
from parishkit.stewardship.audit.models import AuditEvent

from .credential_keys import inventory_digest, key_set_lock
from .credential_models import (
    CampaignCredentialState,
    CredentialKeyState,
    FamilyCampaign,
    FamilyCodeFingerprint,
    RehearsalCodeFingerprint,
    RehearsalCredential,
)
from .family_identity import code_context
from .rehearsals import code_context as rehearsal_context


def backfill_mac_batch(*, campaign_id, general, mac, admit, batch_size=500):
    """Rekey retained plaintext-capable rows; anonymous reservations are untouched."""
    if type(batch_size) is not int or not 1 <= batch_size <= 1000:
        raise ValueError("MAC backfill requires a bounded batch size.")
    with transaction.atomic(), key_set_lock(general, mac):
        if admit() is not True:
            raise PermissionError("MAC migration is not admitted.")
        CampaignCredentialState.objects.select_for_update().get(campaign_id=campaign_id)
        existing = FamilyCodeFingerprint.objects.filter(
            family_id=OuterRef("pk"), key_id=mac.active.id
        )
        families = list(
            FamilyCampaign.objects.filter(
                campaign_id=campaign_id, code_ciphertext__isnull=False
            )
            .filter(~Exists(existing))
            .order_by("pk")[:batch_size]
        )
        fingerprints = []
        for family in families:
            code = general.decrypt(
                family.code_ciphertext, context=code_context(family.pk)
            ).decode("ascii")
            fingerprints.append(
                FamilyCodeFingerprint(
                    family=family,
                    campaign_id=campaign_id,
                    key_id=mac.active.id,
                    digest=mac.digest(mac.active.id, campaign_id, code),
                )
            )
        FamilyCodeFingerprint.objects.bulk_create(fingerprints)
        existing = RehearsalCodeFingerprint.objects.filter(
            credential_id=OuterRef("pk"), key_id=mac.active.id
        )
        rehearsals = list(
            RehearsalCredential.objects.filter(epoch__campaign_id=campaign_id)
            .filter(~Exists(existing))
            .order_by("pk")[:batch_size]
        )
        rows = []
        for credential in rehearsals:
            code = general.decrypt(
                credential.code_ciphertext, context=rehearsal_context(credential.pk)
            ).decode("ascii")
            rows.append(
                RehearsalCodeFingerprint(
                    credential=credential,
                    epoch_id=credential.epoch_id,
                    key_id=mac.active.id,
                    digest=mac.digest(mac.active.id, campaign_id, code),
                )
            )
        RehearsalCodeFingerprint.objects.bulk_create(rows)
        if families or rehearsals:
            from parishkit.stewardship.audit.schemas import Action, ActorKind
            from parishkit.stewardship.audit.services import record_action

            record_action(
                Action.FAMILY_MAC_BACKFILLED,
                actor_kind=ActorKind.SYSTEM,
                subject_id=campaign_id,
                context={
                    "count": len(families) + len(rehearsals),
                    "source_fingerprint": mac.active.fingerprint,
                },
            )
        return len(families) + len(rehearsals)


def collision_only(previous, replacement, *, admit):
    """Remove old-key login acceptance after every retained code is backfilled.

    This does not retire any key: required reservation and backup keys remain
    operational. Actual retirement has separate, additional evidence.
    """
    if not isinstance(previous, CodeMacKeyring) or not isinstance(
        replacement, CodeMacKeyring
    ):
        raise TypeError("MAC migration requires MAC keyrings.")
    if (
        previous.active != replacement.active
        or previous.keys.keys() != replacement.keys.keys()
    ):
        raise CryptographicError(
            "MAC demotion cannot change keys or the active writer."
        )
    for old in previous.keys.values():
        new = replacement.keys[old.id]
        if old.material != new.material or (
            old.usage != new.usage
            and (old.usage, new.usage) != ("lookup-only", "collision-only")
        ):
            raise CryptographicError(
                "Only lookup-only to collision-only demotion is allowed."
            )
    if all(
        old.usage == replacement.keys[old.id].usage for old in previous.keys.values()
    ):
        raise CryptographicError("MAC demotion requires a changed key usage.")
    with transaction.atomic(), key_set_lock(previous, exclusive=True):
        if admit() is not True:
            raise PermissionError("MAC demotion is not admitted.")
        family_mac = FamilyCodeFingerprint.objects.filter(
            family_id=OuterRef("pk"), key_id=previous.active.id
        )
        testing_mac = RehearsalCodeFingerprint.objects.filter(
            credential_id=OuterRef("pk"), key_id=previous.active.id
        )
        if (
            FamilyCampaign.objects.filter(code_ciphertext__isnull=False)
            .filter(~Exists(family_mac))
            .exists()
            or RehearsalCredential.objects.filter(~Exists(testing_mac)).exists()
        ):
            raise CryptographicError(
                "Active-key authentication backfill is incomplete."
            )
        row = CredentialKeyState.objects.get(kind=previous.kind)
        CredentialKeyState.objects.filter(pk=row.pk).update(
            inventory=list(replacement.inventory()),
            inventory_digest=inventory_digest(replacement),
            version=F("version") + 1,
        )
        AuditEvent.objects.create(
            event_type="family_mac_migration_verified", subject_id=row.pk
        )
