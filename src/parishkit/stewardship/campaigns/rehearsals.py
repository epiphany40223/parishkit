"""Mode-disjoint rehearsal epochs, non-reusable reservations and bounded cleanup."""

from uuid import UUID, uuid4

from django.contrib.sessions.models import Session
from django.db import IntegrityError, transaction
from django.db.models import F

from parishkit.stewardship.accounts.cryptography import (
    CryptographicError,
    new_code,
    new_token,
    token_digest,
)
from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.audit.models import AuditEvent
from parishkit.stewardship.storage import StorageInvariantError

from .credential_keys import key_set_lock
from .credential_models import (
    CampaignCredentialState,
    FamilyCampaign,
    FamilySession,
    RehearsalCodeFingerprint,
    RehearsalCodeReservation,
    RehearsalCredential,
    RehearsalEpoch,
)
from .family_identity import collision_query
from .lifecycle import CampaignWorkKind, campaign_work_admitted
from .models import Campaign
from .runtime import _now, campaign_facts


def code_context(identifier):
    """A testing display code cannot be transplanted to a live-code envelope."""
    return b"rehearsal-code-v1:" + identifier.bytes


def token_context(identifier):
    """Sealed testing links carry their independent record-purpose binding."""
    return b"rehearsal-token-v1:" + identifier.bytes


def prepare_rehearsals(
    *, campaign_id, family_ids, general, mac, public, purpose, admit
):
    """One bounded batch reserves every code before any caller can reference it.

    The readiness-test exception is still subject to the owning authenticated
    task admission callback, which must return exactly True. Passing a purpose
    enum alone never authorizes it. Other credential mutation ports use the same
    explicit-True contract, including invalidation and gate release below.
    """
    family_ids = tuple(family_ids)
    if (
        not callable(admit)
        or not 1 <= len(family_ids) <= 500
        or any(not isinstance(value, UUID) for value in family_ids)
        or len(set(family_ids)) != len(family_ids)
        or purpose not in {CampaignWorkKind.REHEARSAL, CampaignWorkKind.READINESS_TEST}
    ):
        raise ValueError(
            "Rehearsal preparation requires bounded identities and owning admission."
        )
    with transaction.atomic(), key_set_lock(general, mac, public):
        runtime = SystemConfiguration.objects.select_for_update().get()
        campaign = Campaign.objects.get(pk=campaign_id)
        scope = CampaignCredentialState.objects.select_for_update().get(
            campaign=campaign
        )
        if admit(campaign, purpose) is not True:
            raise PermissionError("Rehearsal preparation is not admitted.")
        if (
            scope.go_live_gate
            or scope.population_dirty
            or not campaign_work_admitted(
                campaign_facts(campaign, runtime), _now(), purpose
            )
        ):
            raise StorageInvariantError("Rehearsal credentials are not admitted.")
        families = list(
            FamilyCampaign.objects.filter(
                campaign=campaign, pk__in=family_ids, portal_eligible=True
            )
        )
        if len(families) != len(family_ids):
            raise CryptographicError("Family information is unavailable.")
        if scope.rehearsal_epoch_id is None:
            epoch = RehearsalEpoch.objects.create(campaign=campaign)
            scope.rehearsal_epoch = epoch
            scope.version += 1
            scope.save()
        else:
            epoch = RehearsalEpoch.objects.get(
                pk=scope.rehearsal_epoch_id, state="active"
            )
        existing = {
            row.family_id: row
            for row in RehearsalCredential.objects.filter(
                epoch=epoch, family_id__in=family_ids
            )
        }
        pending = [row for row in families if row.pk not in existing]
        required = set(
            RehearsalCodeReservation.objects.filter(campaign=campaign)
            .values_list("key_id", flat=True)
            .distinct()
        ) | {mac.active.id}
        if required - mac.keys.keys():
            raise CryptographicError(
                "A required rehearsal collision key is unavailable."
            )
        for _ in range(8):
            if not pending:
                return {identifier: row.pk for identifier, row in existing.items()}
            proposals, seen = [], set()
            for family in pending:
                code = new_code(testing=True)
                if code not in seen:
                    seen.add(code)
                    reservations = {
                        key: mac.digest(key, campaign.pk, code, reservation=True)
                        for key in required
                    }
                    proposals.append((family, code, reservations))
            occupied = set(
                RehearsalCodeReservation.objects.filter(campaign=campaign)
                .filter(
                    collision_query(
                        {key: [item[2][key] for item in proposals] for key in required}
                    )
                )
                .values_list("key_id", "digest")
            )
            accepted = [
                item
                for item in proposals
                if not any(pair in occupied for pair in item[2].items())
            ]
            credentials, fingerprints, reservations = [], [], []
            for family, code, digests in accepted:
                identifier, token = uuid4(), new_token(testing=True)
                credential = RehearsalCredential(
                    id=identifier,
                    epoch=epoch,
                    family=family,
                    code_ciphertext=general.encrypt(
                        code.encode(), context=code_context(identifier)
                    ),
                    token_ciphertext=public.encrypt(
                        token.encode(), context=token_context(identifier)
                    ),
                    token_digest=token_digest(token, campaign.pk),
                )
                credentials.append(credential)
                fingerprints.extend(
                    RehearsalCodeFingerprint(
                        credential=credential, epoch=epoch, key_id=key, digest=digest
                    )
                    for key, digest in mac.lookups(campaign.pk, code).items()
                )
                reservations.append(
                    RehearsalCodeReservation(
                        campaign=campaign,
                        key_id=mac.active.id,
                        digest=digests[mac.active.id],
                    )
                )
            try:
                with transaction.atomic():
                    RehearsalCredential.objects.bulk_create(credentials)
                    RehearsalCodeFingerprint.objects.bulk_create(fingerprints)
                    RehearsalCodeReservation.objects.bulk_create(reservations)
            except IntegrityError:
                continue
            existing.update({row.family_id: row for row in credentials})
            pending = [row for row in pending if row.pk not in existing]
        if pending:
            raise CryptographicError("Rehearsal allocation exhausted bounded retries.")
        return {identifier: row.pk for identifier, row in existing.items()}


def invalidate_rehearsal(*, campaign_id, admit):
    """Go-live admission clears authority before any asynchronous cleanup starts."""
    with transaction.atomic():
        campaign = Campaign.objects.get(pk=campaign_id)
        scope = CampaignCredentialState.objects.select_for_update().get(
            campaign=campaign
        )
        if admit(campaign) is not True:
            raise PermissionError("Rehearsal invalidation is not admitted.")
        epoch_id = scope.rehearsal_epoch_id
        scope.go_live_gate, scope.rehearsal_epoch_id = True, None
        scope.version += 1
        scope.save()
        if epoch_id:
            changed = RehearsalEpoch.objects.filter(pk=epoch_id, state="active").update(
                state="invalidated", invalidated_at=_now(), version=F("version") + 1
            )
            if changed != 1:
                raise StorageInvariantError(
                    "The current rehearsal epoch is not active."
                )
            AuditEvent.objects.create(
                event_type="rehearsal_invalidated", subject_id=epoch_id
            )
        return epoch_id


def release_rehearsal_gate(*, campaign_id, admit):
    """Cancellation/withdrawal can allow a new epoch, never revive the previous one."""
    with transaction.atomic():
        campaign = Campaign.objects.get(pk=campaign_id)
        scope = CampaignCredentialState.objects.select_for_update().get(
            campaign=campaign
        )
        if admit(campaign) is not True:
            raise PermissionError("Rehearsal gate release is not admitted.")
        if scope.rehearsal_epoch_id is not None:
            raise StorageInvariantError(
                "An invalidated rehearsal pointer must remain clear."
            )
        if scope.go_live_gate:
            scope.go_live_gate, scope.version = False, scope.version + 1
            scope.save()
            AuditEvent.objects.create(
                event_type="rehearsal_gate_released", subject_id=scope.pk
            )


def cleanup_rehearsal(epoch_id, *, batch_size=500):
    """Delete only sensitive invalidated-epoch detail; anonymous reservations stay."""
    if type(batch_size) is not int or not 1 <= batch_size <= 1000:
        raise ValueError("Rehearsal cleanup requires a bounded batch size.")
    with transaction.atomic():
        epoch = RehearsalEpoch.objects.select_for_update().get(pk=epoch_id)
        if (
            epoch.state != "invalidated"
            or CampaignCredentialState.objects.filter(rehearsal_epoch=epoch).exists()
        ):
            raise StorageInvariantError("An active rehearsal cannot be cleaned up.")
        sessions = list(
            FamilySession.objects.select_for_update(skip_locked=True)
            .filter(rehearsal_epoch=epoch)
            .order_by("pk")[:batch_size]
        )
        from parishkit.stewardship.accounts.sessions import revoke_family_sessions

        revoke_family_sessions(sessions, now=_now())
        FamilySession.objects.filter(pk__in=[item.pk for item in sessions]).delete()
        Session.objects.filter(pk__in=[item.session_id for item in sessions]).delete()
        ids = list(
            RehearsalCredential.objects.filter(epoch=epoch)
            .order_by("pk")
            .values_list("pk", flat=True)[:batch_size]
        )
        # Deletion is a deliberate retention service operation after invalidation,
        # not an ordinary immutable-record API; the SQL cleanup guard agrees.
        RehearsalCodeFingerprint._base_manager.filter(credential_id__in=ids).delete()
        RehearsalCredential.objects.filter(pk__in=ids).delete()
        return len(ids) + len(sessions)
