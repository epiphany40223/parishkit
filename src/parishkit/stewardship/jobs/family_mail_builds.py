"""Bulk preparation builds outside the work-order lock (BG-12, #447).

On the bulk path each Production Family message used to be read, rendered and
sealed while its preparation batch held the deployment-wide work-order lock,
so on the validation host every batch held a single Family. A *build* does
that work before the batch, outside the lock, and the batch writes the
result under the lock only if nothing it depends on has changed:

1. **Snapshot.** One REPEATABLE READ transaction reads the ticket's
   occurrence, the Family and its source inputs, the template and the
   integration, renders the message (``build_render``) and computes the
   item's fingerprint (``fingerprint``) from the same snapshot.
2. **Seal.** A second, short transaction seals the credential substitutions
   (``seal_built_credentials``) while holding only the credential key-set
   lock, shared and non-waiting, with its inventory check. It reads outside
   the snapshot, so it computes the fingerprint again, still under that
   lock, and drops the build if it differs from the snapshot's.
3. **Recheck under the lock.** The batch item claims the ticket and runs
   ``disposition`` and ``plan_family`` exactly as before, then compares the
   fingerprint in one query (``prepare_occurrence``). If it matches, the
   prebuilt render and sealed credentials are written through the same
   ``create_message`` and SQL guards; if not, the item is rebuilt under the
   lock exactly as before.

A build is dropped (``None``), and the item then prepares under the lock as
before, when the ticket is not a pending Production one, the key-set lock is
busy (a rotation in progress), a key inventory is not current, a
serialization failure (``40001``) or other database error occurs, the
source is not current, or decryption fails. Dropping costs time, never
correctness: the guards decide every write either way.

Builds run serially, in the worker's one process, on its own connection,
between batches: no threads and no extra connections. Testing items are
never built here, because Testing preparation writes the Family's rehearsal
credential under its ticket, which needs the lock (#555).

No credential changes. A build only reads the Family's code ciphertext and
its current link token row, as preparation always has, and seals a
reference to them; codes and links already emailed stay valid. A build holds
the sealed substitutions, never a plaintext code or link, and nothing here
is logged except at DEBUG, without identifiers or content.
"""

import hashlib
import logging
from dataclasses import dataclass, field
from uuid import UUID

from django.db import connection, transaction

from parishkit.stewardship.storage import StorageInvariantError

DEBUG = logging.getLogger("parishkit.stewardship.debug")

# Everything a Production preparation render and seal depend on, for one
# occurrence and Family, in one statement. Under the lock, a build whose
# fingerprint differs is rebuilt there instead of being written. Every join
# is by primary key or LIMIT 1, so the planner expects one row: a cross join
# of the singleton tables once made it expect thousands, and PostgreSQL then
# spent about 0.4 s compiling the statement (JIT) on every call.
#
# - the occurrence row (its version moves on every write, and its revision
#   names the template) and the schedule's current revision;
# - the runtime and campaign rows the render reads: the system's active
#   configuration (parish values, the email integration's sender and reply
#   address, the template content, all immutable per configuration) and the
#   campaign's active configuration (its name, banner and values,
#   immutable per configuration), the mode and the Production cycle;
# - the Family's eligibility, deliverability, response and source
#   generation, and a digest of its code ciphertext (what is sealed);
# - the promoted source and the population built from it, and the Family's
#   unresolved address refusals (the recipient list);
# - the hosted files' public links and the ready branding bundles (the
#   links and banner the template names);
# - the active link-token generation, its credential epoch, the
#   deployment's current epoch and the Family's current token row (the
#   sealed reference), and both key inventories' digests.
_FINGERPRINT = """
SELECT o.id, o.version, o.state, o.outbox_id, o.revision_id, o.due_at,
    d.current_revision_id,
    r.mode, r.current_campaign_id, r.active_configuration_id,
    r.restore_review_required,
    c.active_configuration_id, c.production_cycle, c.state,
    c.active_token_generation_id,
    f.active, f.email_eligible, f.email_deliverable, f.effective_submission_id,
    f.source_generation, encode(sha256(convert_to(f.code_ciphertext,'UTF8')),'hex'),
    k.population_dirty, k.source_snapshot_id, k.source_generation, k.go_live_gate,
    s.snapshot_id, s.generation,
    (SELECT string_agg(refusal.address, ',' ORDER BY refusal.address COLLATE "C")
        FROM stewardship_recipient_refusal refusal
        WHERE refusal.organization_id=s.organization_id
          AND refusal.family_duid=f.family_duid
          AND NOT EXISTS (SELECT 1 FROM stewardship_recipient_resolution resolution
              WHERE resolution.refusal_id=refusal.id)),
    (SELECT string_agg(hosted.slug||'='||hosted.token, ','
            ORDER BY hosted.slug COLLATE "C")
        FROM stewardship_hosted_file hosted),
    (SELECT string_agg(bundle.id::text, ',' ORDER BY bundle.id)
        FROM stewardship_branding_bundle bundle WHERE bundle.state='ready'),
    g.id, g.state, g.credential_epoch, deployment.family_link_epoch,
    (SELECT string_agg(token.id::text, ',' ORDER BY token.id)
        FROM stewardship_family_token token
        WHERE token.family_id=f.id AND token.campaign_id=c.id
          AND token.generation_id=g.id AND token.destroyed_at IS NULL),
    (SELECT string_agg(inventory.kind||'='||inventory.inventory_digest, ','
            ORDER BY inventory.kind)
        FROM stewardship_credential_key_state inventory)
FROM stewardship_schedule_occurrence o
JOIN stewardship_schedule_definition d ON d.id=o.definition_id
JOIN stewardship_campaign c ON c.id=d.campaign_id
JOIN stewardship_family_campaign f ON f.id=%s AND f.campaign_id=c.id
LEFT JOIN LATERAL (SELECT * FROM stewardship_system_configuration LIMIT 1) r ON true
LEFT JOIN LATERAL (SELECT * FROM stewardship_campaign_credentials credentials
    WHERE credentials.campaign_id=c.id ORDER BY credentials.id LIMIT 1) k ON true
LEFT JOIN LATERAL (SELECT * FROM stewardship_source_current current
    WHERE current.singleton LIMIT 1) s ON true
LEFT JOIN stewardship_family_token_generation g ON g.id=c.active_token_generation_id
LEFT JOIN LATERAL (SELECT family_link_epoch FROM stewardship_credential_deployment
    LIMIT 1) deployment ON true
WHERE o.id=%s
"""


def fingerprint(occurrence_id, family_id):
    """The digest of everything a Production build depends on (one query).

    Returns None when the occurrence and Family do not belong together.
    The caller owns the transaction: a build's snapshot, its sealing
    transaction, or the batch's lock transaction.
    """
    with connection.cursor() as cursor:
        cursor.execute(_FINGERPRINT, [family_id, occurrence_id])
        rows = cursor.fetchall()
    if len(rows) != 1:
        return None
    return hashlib.sha256(repr(rows[0]).encode()).hexdigest()


@dataclass(frozen=True)
class PreparationBuild:
    """One Production item prepared outside the lock, ready to recheck.

    ``render`` is the redacted rendered message and ``sealed`` its sealed
    substitutions (no plaintext code or link). Neither is ever logged.
    """

    task_id: UUID
    occurrence_id: UUID
    identity: object
    fingerprint: str
    render: object = field(repr=False)
    sealed: object = field(repr=False)


def _snapshot(task_id, *, public_origin):
    """Read, render and fingerprint one item in one REPEATABLE READ snapshot.

    Returns ``(identity, family, campaign, render, fingerprint)`` or None when
    the item is not a pending Production preparation (it then prepares
    under the lock as before). Raises what the reads raise; the caller
    drops the build.
    """
    from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
    from parishkit.stewardship.campaigns.credential_models import FamilyCampaign
    from parishkit.stewardship.campaigns.models import Campaign

    from .family_mail_inputs import read_family_mail_source
    from .family_mail_models import FamilyMailPreparation
    from .family_mail_rendering import build_render
    from .family_mail_tasks import _row
    from .models import TaskRun
    from .outbox_validation import DeliveryIdentity

    with transaction.atomic():
        with connection.cursor() as cursor:
            # The first statement of the transaction, so every read below
            # sees one snapshot. Not READ ONLY: reading the source corpus
            # takes a share lock on its snapshot row (read_snapshot).
            cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        task = TaskRun.objects.filter(pk=task_id).first()
        if (
            task is None
            or task.task_type != "family_mail_prepare"
            or task.domain_request_id is None
        ):
            return None
        ticket = FamilyMailPreparation.objects.filter(
            pk=task.domain_request_id, task_id=task.root_id
        ).first()
        if ticket is None or ticket.mode != "production":
            return None
        row = _row(ticket.occurrence_id)
        if (
            row is None
            or row.mode != "production"
            or row.state != "pending"
            or row.outbox_id is not None
            or row.revision_id != row.definition.current_revision_id
        ):
            return None
        family_id = UUID(row.target.removeprefix("family:"))
        if row.target != f"family:{family_id}":
            return None
        runtime = SystemConfiguration.objects.select_related(
            "active_configuration"
        ).get()
        if (
            runtime.mode != "production"
            or runtime.current_campaign_id != row.definition.campaign_id
        ):
            return None
        campaign = Campaign.objects.select_related("active_configuration").get(
            pk=row.definition.campaign_id
        )
        family = FamilyCampaign.objects.get(pk=family_id, campaign=campaign)
        source = read_family_mail_source(family)
        if not source.recipients.status.email_deliverable:
            return None
        identity = DeliveryIdentity(
            scope_id=campaign.pk,
            campaign_id=campaign.pk,
            semantic_key=row.pk,
            family_id=family.pk,
            mode="production",
            routing=row.routing,
            purpose=row.definition.kind,
            credential_namespace="production",
            rehearsal_epoch_id=None,
        )
        render = build_render(
            identity,
            UUID(row.revision.values["template_version"]),
            runtime,
            campaign,
            source,
            public_origin=public_origin,
        )
        digest = fingerprint(row.pk, family.pk)
        if digest is None:
            return None
        return identity, family, campaign, render, digest


def build_preparation(task_id, *, general, public, public_origin):
    """Build one preparation task's message outside the lock, or None.

    See the module docstring. Never raises: a busy or stale key set, a
    serialization failure, a database refusal, source not current, a
    decryption error or any other failure drops the build, and the item is
    then prepared under the lock as before.
    """
    from .family_mail_credentials import seal_built_credentials

    if connection.in_atomic_block:
        raise StorageInvariantError("A bulk build must own its transactions.")
    try:
        built = _snapshot(task_id, public_origin=public_origin)
        if built is None:
            return None
        identity, family, campaign, render, digest = built
        with transaction.atomic():
            # Takes the shared key-set lock (non-waiting, with its inventory
            # check) and keeps it to the end of this transaction, so the
            # fingerprint below is read under it too.
            sealed = seal_built_credentials(
                identity=identity,
                render=render,
                campaign=campaign,
                family=family,
                general=general,
                public=public,
            )
            if fingerprint(identity.semantic_key, family.pk) != digest:
                # Something moved between the snapshot and the seal (a token
                # generation, epoch or key inventory): drop the build.
                return None
    except Exception as error:  # noqa: BLE001 - a build is only an optimization
        # Expected: a busy or stale key set (CryptographicError), a
        # serialization failure or other DatabaseError, source not current
        # (PermissionError), a vanished row. Anything else is dropped too,
        # so one bad item can never abort the drain: the batch prepares it
        # under the lock, where any real fault surfaces as it always has.
        DEBUG.debug("bulk build dropped: %s", type(error).__name__)
        return None
    return PreparationBuild(
        task_id=task_id,
        occurrence_id=identity.semantic_key,
        identity=identity,
        fingerprint=digest,
        render=render,
        sealed=sealed,
    )
