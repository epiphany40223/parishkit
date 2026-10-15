"""Bulk send builds outside the work-order lock (BG-12 PR 3, #447).

On the bulk path each scheduled Family message was re-rendered, its sealed
credentials opened and sealed again, inside the send batch's lock
transaction (``begin_submission``'s ``current_content``), so the
deployment-wide work-order lock was held for that Python work too. A *send
build* does that work before the batch, outside the lock, and the batch's
commit half (``family_mail_dispatch.commit_submission``) uses the result only
if nothing it depends on has changed:

1. **Snapshot.** One REPEATABLE READ transaction reads the message, its
   occurrence, the runtime and campaign rows and the Family's source,
   renders the message (``build_render``) and computes the message's send
   fingerprint (``send_fingerprint``) from the same snapshot.
2. **Open and seal.** A second, short transaction opens the message's own
   sealed code and token reference and seals it again for the new render
   (``built_content``), holding only the credential key-set lock, shared
   and non-waiting, with its inventory check. It reads outside the
   snapshot, so it computes the fingerprint again, still under that lock,
   and drops the build if it differs from the snapshot's.
3. **Recheck under the lock.** The commit half claims the task and runs
   ``disposition``, the pause and cancellation checks and ``plan_family``
   exactly as before. Before releasing a pause hold (which moves the
   message's version), it takes the key-set lock and compares the
   fingerprint in one query (``prebuilt_matches``). If it matches, at the
   content step it resolves the link token under the claim and writes the
   build's render and sealed substitutions through the same guarded
   writes (``prebuilt_content``); if not, it builds the content under the
   lock exactly as before.

A build is dropped (``None``), and the message is then built under the lock
as before, when the task is not a claimable scheduled Family delivery, the
key-set lock is busy (a rotation in progress), a key inventory is not
current, a serialization failure (``40001``) or other database error
occurs, the source is not current, the credential generation has moved, or
decryption fails. Dropping costs time, never correctness: the commit half
and the SQL guards decide every write either way.

Production and Testing messages are both built here: sending writes no
credential (BG-12 decision 12). Builds run serially in the mail consumer's
one process, on its own connection, between its batches.

No credential changes. A build only opens the reference sealed into the
message at preparation and seals the same reference again, as dispatch
always has. It never reads a link token: the token definer answers only for
a live delivery claim (#389 L1), which only the commit half holds. A build
keeps the opened code in memory only (``repr=False``) and is never logged;
debug lines carry only an exception's type.
"""

import hashlib
import logging
from dataclasses import dataclass, field
from uuid import UUID

from django.db import connection, transaction

from parishkit.stewardship.storage import StorageInvariantError

DEBUG = logging.getLogger("parishkit.stewardship.debug")

# Scheduled Family mail only (family_mail_bulk.BULK_PURPOSES).
BUILT_PURPOSES = ("initial", "reminder")

# Everything a send build's render, opened reference and sealed
# substitutions depend on, for one message, in one statement, using only
# columns the mail-dispatch login may read (family_dispatch_grants). Under
# the lock, a build whose fingerprint differs is rebuilt there instead of
# being written. Every join is by primary key or LIMIT 1, so the planner
# expects one row (see family_mail_builds._FINGERPRINT for why that matters).
#
# - the message: its version (moves on every write), state, render (the
#   retained render the sealed reference is bound to), a digest of its
#   sealed substitutions, and its generation, epoch and rehearsal epoch;
# - the occurrence (version, state, outbox, target and revision, which names
#   the template) and the schedule's current revision;
# - the runtime row (mode, campaign, active configuration with the parish
#   values, sender, reply address and template content, all immutable per
#   configuration; restore review; the Testing recipient) and the campaign
#   row (active configuration with its name, banner and values, the
#   Production cycle, state and active token generation);
# - the Family's eligibility, deliverability, response and source
#   generation;
# - the promoted source and the population built from it, and the Family's
#   unresolved address refusals (the recipient list);
# - the hosted files' public links, and the ready branding bundles with
#   their assets' sizes (the links and banner the template names);
# - the active link-token generation, its credential epoch, the deployment's
#   current epoch, the Family's current link token row (Production) or
#   rehearsal credential (Testing), each with its digest, and both key
#   inventories' digests.
_SEND_FINGERPRINT = """
SELECT m.id, m.version, m.state, m.mode, m.purpose, m.family_id, m.campaign_id,
    m.semantic_key, m.render_id, m.token_generation_id, m.credential_epoch_id,
    m.rehearsal_epoch_id,
    encode(sha256(convert_to(m.sealed_substitutions,'UTF8')),'hex'),
    o.version, o.state, o.outbox_id, o.target, o.revision_id, d.current_revision_id,
    r.mode, r.current_campaign_id, r.active_configuration_id,
    r.restore_review_required, r.testing_recipient,
    c.active_configuration_id, c.production_cycle, c.state,
    c.active_token_generation_id,
    f.family_duid, f.active, f.email_eligible, f.email_deliverable,
    f.effective_submission_id, f.source_generation,
    k.population_dirty, k.source_snapshot_id, k.source_generation, k.go_live_gate,
    k.rehearsal_epoch_id,
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
    (SELECT string_agg(asset.id::text||'='||asset.width||'x'||asset.height, ','
            ORDER BY asset.id)
        FROM stewardship_branding_asset asset
        JOIN stewardship_branding_bundle bundle ON bundle.id=asset.bundle_id
        WHERE bundle.state='ready'),
    g.id, g.state, g.credential_epoch, deployment.family_link_epoch,
    (SELECT string_agg(token.id::text||'='||token.digest, ',' ORDER BY token.id)
        FROM stewardship_family_token token
        WHERE token.family_id=f.id AND token.campaign_id=c.id
          AND token.generation_id=m.token_generation_id
          AND token.destroyed_at IS NULL),
    (SELECT string_agg(rehearsal.id::text||'='||rehearsal.token_digest, ','
            ORDER BY rehearsal.id)
        FROM stewardship_rehearsal_credential rehearsal
        WHERE rehearsal.family_id=f.id AND rehearsal.epoch_id=m.rehearsal_epoch_id),
    (SELECT string_agg(inventory.kind||'='||inventory.inventory_digest, ','
            ORDER BY inventory.kind)
        FROM stewardship_credential_key_state inventory)
FROM stewardship_outbox_message m
JOIN stewardship_schedule_occurrence o ON o.id=m.semantic_key
JOIN stewardship_schedule_definition d ON d.id=o.definition_id
JOIN stewardship_campaign c ON c.id=m.campaign_id AND c.id=d.campaign_id
JOIN stewardship_family_campaign f ON f.id=m.family_id AND f.campaign_id=c.id
LEFT JOIN LATERAL (SELECT * FROM stewardship_system_configuration LIMIT 1) r ON true
LEFT JOIN LATERAL (SELECT * FROM stewardship_campaign_credentials credentials
    WHERE credentials.campaign_id=c.id ORDER BY credentials.id LIMIT 1) k ON true
LEFT JOIN LATERAL (SELECT * FROM stewardship_source_current current
    WHERE current.singleton LIMIT 1) s ON true
LEFT JOIN stewardship_family_token_generation g ON g.id=c.active_token_generation_id
LEFT JOIN LATERAL (SELECT family_link_epoch FROM stewardship_credential_deployment
    LIMIT 1) deployment ON true
WHERE m.id=%s
"""


def send_fingerprint(message_id):
    """The digest of everything a send build depends on (one query).

    Returns None when the message is not scheduled Family mail with its
    occurrence and Family. The caller owns the transaction: a build's
    snapshot, its sealing transaction, or the batch's lock transaction.
    """
    with connection.cursor() as cursor:
        cursor.execute(_SEND_FINGERPRINT, [message_id])
        rows = cursor.fetchall()
    if len(rows) != 1:
        return None
    return hashlib.sha256(repr(rows[0]).encode()).hexdigest()


@dataclass(frozen=True)
class SendBuild:
    """One message's content built outside the lock, ready to recheck.

    ``render`` is the redacted rendered message, ``sealed`` its sealed
    substitutions and ``reference`` the opened code and token reference
    (the plaintext code, in memory only). None of them is ever logged, and
    no link token is held: the commit half resolves it under the claim.
    """

    task_id: UUID
    message_id: UUID
    identity: object
    fingerprint: str
    render: object = field(repr=False)
    sealed: object = field(repr=False)
    reference: object = field(repr=False)


def _snapshot(task_id, *, public_origin):
    """Read, render and fingerprint one message in one REPEATABLE READ snapshot.

    Returns ``(message, identity, campaign, render, fingerprint)`` or None
    when the task is not a claimable delivery of unsent scheduled Family
    mail whose occurrence and scope are current (it is then built under the
    lock as before). Raises what the reads raise; the caller drops the build.
    """
    from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
    from parishkit.stewardship.campaigns.credential_models import FamilyCampaign
    from parishkit.stewardship.campaigns.models import Campaign
    from parishkit.stewardship.campaigns.schedule_models import ScheduleOccurrence

    from .family_mail_dispatch import TASK_TYPE
    from .family_mail_inputs import read_family_mail_source
    from .family_mail_rendering import build_render
    from .models import TaskRun
    from .outbox_models import OutboxMessage
    from .outbox_storage import _status

    with transaction.atomic():
        with connection.cursor() as cursor:
            # The first statement of the transaction, so every read below
            # sees one snapshot. Not READ ONLY: reading the source corpus
            # takes a share lock on its snapshot row (read_snapshot).
            cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        task = TaskRun.objects.filter(pk=task_id).first()
        if (
            task is None
            or task.task_type != TASK_TYPE
            or task.state not in {"queued", "retry_wait"}
            or task.domain_request_id is None
        ):
            return None
        message = (
            OutboxMessage.objects.select_related("render")
            .filter(pk=task.domain_request_id, task_id=task.root_id)
            .first()
        )
        if (
            message is None
            or message.purpose not in BUILT_PURPOSES
            or message.state not in {"pending", "retry_wait"}
            or message.sealed_substitutions is None
        ):
            return None
        row = (
            ScheduleOccurrence.objects.select_related("definition", "revision")
            .filter(pk=message.semantic_key)
            .first()
        )
        if (
            row is None
            or row.outbox_id != message.pk
            or row.target != f"family:{message.family_id}"
            or row.revision_id != row.definition.current_revision_id
            or row.definition.campaign_id != message.campaign_id
        ):
            return None
        runtime = SystemConfiguration.objects.select_related(
            "active_configuration"
        ).get()
        if (
            runtime.mode != message.mode
            or runtime.current_campaign_id != message.campaign_id
        ):
            return None
        campaign = Campaign.objects.select_related("active_configuration").get(
            pk=message.campaign_id
        )
        # The columns current_content reads, which mail dispatch may read.
        family = FamilyCampaign.objects.only(
            "id", "campaign_id", "family_duid", "source_generation"
        ).get(pk=message.family_id, campaign=campaign)
        source = read_family_mail_source(family)
        identity = _status(message).identity
        render = build_render(
            identity,
            UUID(row.revision.values["template_version"]),
            runtime,
            campaign,
            source,
            public_origin=public_origin,
        )
        digest = send_fingerprint(message.pk)
        if digest is None:
            return None
        return message, identity, campaign, render, digest


def build_submission(task_id, *, private, public_origin):
    """Build one delivery task's message content outside the lock, or None.

    ``begin_submission``'s build half: see the module docstring. Never
    raises: a busy or stale key set, a serialization failure, a database
    refusal, source not current, a moved generation, a decryption error or
    any other failure drops the build, and the message is then built under
    the lock as before.
    """
    from .family_mail_dispatch_content import built_content

    if connection.in_atomic_block:
        raise StorageInvariantError("A bulk build must own its transactions.")
    try:
        built = _snapshot(task_id, public_origin=public_origin)
        if built is None:
            return None
        message, identity, campaign, render, digest = built
        with transaction.atomic():
            # Takes the shared key-set lock (non-waiting, with its inventory
            # check) and keeps it to the end of this transaction, so the
            # fingerprint below is read under it too.
            reference, sealed = built_content(
                message, identity, campaign, render, private=private
            )
            if send_fingerprint(message.pk) != digest:
                # Something moved between the snapshot and the seal (a token
                # generation, epoch or key inventory): drop the build.
                return None
    except Exception as error:  # noqa: BLE001 - a build is only an optimization
        # Expected: a busy or stale key set (CryptographicError), a
        # serialization failure or other DatabaseError, source not current
        # or a held generation (PermissionError), a vanished row. Anything
        # else is dropped too, so one bad message can never abort the
        # drain: the batch builds it under the lock, where any real fault
        # surfaces as it always has.
        DEBUG.debug("bulk send build dropped: %s", type(error).__name__)
        return None
    return SendBuild(
        task_id=task_id,
        message_id=message.pk,
        identity=identity,
        fingerprint=digest,
        render=render,
        sealed=sealed,
        reference=reference,
    )
