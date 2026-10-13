"""Send a delivered Family email again to the addresses refused only for now (#806).

One email goes to all of a Family's addresses at once. When the provider
accepts it but refuses some addresses temporarily (an SMTP 4xx at RCPT), the
email is delivered and is never sent again. ``finish_submission`` then opens
a follow-up (``open_follow_up``) in the same transaction: a row of
``stewardship_outbox_address_retry`` with its own ``family_address_retry``
task, created by a SECURITY DEFINER function under the delivery's live claim
(frozen migration 0077).

The follow-up's task, run by mail dispatch, sends the very same email (the
original rendering, Family code and link, read through the copied sealed
reference) to the addresses still refused, and only to them:

- Nothing is rendered or prepared again and no credential is written: the
  code and link are only read, and the follow-up ends ("cancelled") rather
  than send a link whose token generation or epoch has changed or whose
  token is gone.
- It sends only in the scope the message itself would need (see
  ``retry_disposition``; the SQL guard checks it again): it waits out a
  restore review, pause, go-live or purge gate or source reconciliation,
  and it ends, unsent, when the campaign, mode or production cycle changed,
  the Family is no longer eligible or deliverable at that address, a later
  invitation or reminder to the Family was accepted, or it was restored
  from a backup (it may have been sent after that backup was taken).
- Every envelope is the row's ``remaining`` positions, which only shrink: an
  address that accepted the email, in the original attempt or a follow-up,
  is never sent it again. "submitting" commits before any SMTP, and an
  uncertain outcome (or a crash past the provider deadline) ends the row as
  "unknown", never retried.
- It retries on the message's per-address throttle schedule
  (RECIPIENT_RETRY_SECONDS: 15 minutes after the acceptance, then 1 hour, 4
  hours and 12 hours), MAX_ATTEMPTS counted attempts at most (the accepted
  attempt being these addresses' first) and never past the row's expiry (7
  days after the acceptance). Sending limits and provider outages hold it
  without spending an attempt, and so do the mailbox's pause, stop and daily
  cap.
- A permanent refusal seen here is recorded on the row (the Mail message
  page shows it) and, in the same transaction, as a RecipientRefusal of the
  accepted attempt's event (``record_refusals``), so the address is set
  aside for the Family as the message's own permanent refusal would be.
  That refusal's trigger may update the Family's deliverability: the one
  FamilyCampaign write, after the follow-up's own rows under the
  work-order lock, the same order the message's settle uses. Nothing else
  locks or writes a FamilyCampaign row.
"""

import logging
from datetime import timedelta
from functools import partial
from html import escape
from pathlib import Path
from random import randint
from uuid import uuid4

from django.db import connection, transaction

from parishkit.config import ConfigError
from parishkit.stewardship.accounts.configuration_models import AppliedIntegration
from parishkit.stewardship.accounts.cryptography import token_digest
from parishkit.stewardship.accounts.integration_selection import switching
from parishkit.stewardship.accounts.key_files import file_fingerprint, read_private
from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.campaigns.credential_keys import key_set_lock
from parishkit.stewardship.campaigns.credential_models import (
    CampaignCredentialState,
    DeploymentCredentialState,
    FamilyAccessTokenGeneration,
    FamilyCampaign,
)
from parishkit.stewardship.campaigns.link_tokens import token_context
from parishkit.stewardship.campaigns.runtime_models import CampaignWorkGate
from parishkit.stewardship.campaigns.schedule_models import ScheduleOccurrence
from parishkit.stewardship.campaigns.work_locks import (
    require_work_order,
    work_transaction,
)
from parishkit.stewardship.family_delivery import (
    FamilyDeliveryMail,
    FamilyDeliveryResult,
    FamilyDeliveryStatus,
    ProviderHealth,
)
from parishkit.stewardship.family_delivery_process import submit_family
from parishkit.stewardship.provider_checks import ProviderCheckDrainFailure
from parishkit.stewardship.runtime_background import mail_authority
from parishkit.stewardship.sender_name import configured_sender_name
from parishkit.stewardship.source.snapshot_models import SourceCurrent

from .address_retry_models import AddressRetry
from .admission import _scope
from .connection_reuse import drop_unusable
from .dispatch import Handler, RecoveryPlan
from .family_mail_content import (
    CODE_PLACEHOLDER,
    LINK_PLACEHOLDER,
    open_family_credentials,
)
from .family_mail_dispatch import (
    CAPPED_RETRY_SECONDS,
    LIMIT_RETRY_SECONDS,
    PROVIDER_SECONDS,
    RECIPIENT_RETRY_SECONDS,
    FamilyDeliveryHeld,
    over_daily_limit,
    retry_delay,
)
from .models import TaskRun
from .outbox_models import OutboxEvent, OutboxMessage
from .outbox_storage import _status as delivery_status
from .outbox_validation import RenderInput, SealedSubstitutions
from .ownership import database_now, lock_task_claim
from .phases import TaskPhase
from .queues import WorkQueue
from .storage import TaskStatus, _status

LOG = logging.getLogger(__name__)
TASK_TYPE = "family_address_retry"
# Only invitations and reminders carry the Family's code and link; receipts
# and Testing mail are not followed up.
FOLLOWED_PURPOSES = frozenset({"initial", "reminder"})
# Counted follow-up attempts (limit and outage holds excluded) before it gives
# up. The accepted attempt was these addresses' first, so with it they get
# the message's own five attempts, on its per-address throttle schedule.
MAX_ATTEMPTS = 4
# How soon an abandoned, still-unsent follow-up may be claimed again.
RECOVERY_RETRY_SECONDS = 30
# The task transition each ended state settles with.
TERMINAL_TRANSITIONS = {
    "finished": "complete",
    "cancelled": "safe_cancel",
    "failed": "permanent_failure",
    "unknown": "permanent_failure",
}


def follows_up(message, result):
    """Whether an accepted attempt of ``message`` opens a follow-up.

    Only a delivered Production invitation or reminder whose result refused
    at least one address for now; the SQL definer checks the same again.
    """
    return (
        result.status is FamilyDeliveryStatus.ACCEPTED
        and bool(result.transient)
        and message.mode == "production"
        and message.purpose in FOLLOWED_PURPOSES
        and message.family_id is not None
    )


def open_follow_up(*, event_id, claim, sealed, sealed_key):
    """Open a follow-up for one accepted attempt; its id, or None if refused.

    Called by ``finish_submission`` in the transaction that recorded the
    acceptance, with the message's sealed reference as it was before the
    acceptance scrubbed it. It runs in its own savepoint: a refusal (or any
    database error) is logged at ERROR and leaves the follow-up unopened, and
    never undoes the recorded acceptance. The first try waits the throttle
    schedule's first step, as the message's own next attempt would.
    """
    try:
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute(
                "SELECT public.stewardship_address_retry_open_v1(%s,%s,%s,%s,%s,%s,%s)",
                [
                    event_id,
                    claim.run_id,
                    claim.fence,
                    claim.worker_id,
                    sealed,
                    sealed_key,
                    RECIPIENT_RETRY_SECONDS[0],
                ],
            )
            return cursor.fetchone()[0]
    except Exception:
        LOG.error(
            "An email accepted for some addresses could not schedule another "
            "try for the addresses refused for now (#806).",
            exc_info=True,
        )
        return None


def bound_retry(status):
    """The follow-up a Task names, only against its exact stored task row."""
    require_work_order()
    if (
        not isinstance(status, TaskStatus)
        or status.task_type != TASK_TYPE
        or not TaskRun.objects.filter(
            pk=status.run_id,
            root_id=status.root_id,
            task_type=TASK_TYPE,
            domain_request_id=status.domain_request_id,
            state=status.state,
            version=status.version,
            fence=status.fence,
            worker_id=status.worker_id,
        ).exists()
    ):
        raise PermissionError("Address follow-up Task binding differs.")
    return AddressRetry.objects.select_related("message", "render", "source_event").get(
        pk=status.domain_request_id, task_id=status.root_id
    )


def recovery_plan(status):
    """Decide an abandoned follow-up's disposition from durable state; write nothing.

    A submission whose provider deadline has passed is recorded as unknown
    (see ``recover_retry``) and never sent again.
    """
    row = bound_retry(status)
    if status.state != "abandoned":
        raise PermissionError("Address follow-up recovery requires an abandoned Task.")
    if row.state == "submitting":
        if row.provider_deadline > database_now():
            return None
        return RecoveryPlan("recovery_fail")
    if row.state == "waiting":
        return RecoveryPlan("recovery_retry", retry_seconds=RECOVERY_RETRY_SECONDS)
    return RecoveryPlan(
        {
            "finished": "recovery_complete",
            "cancelled": "recovery_cancel",
        }.get(row.state, "recovery_fail")
    )


def recover_retry(status):
    """Apply the recovery plan's effect under the abandoned Task, then return it."""
    plan = recovery_plan(status)
    if plan is None:
        return None
    row = bound_retry(status)
    if row.state == "submitting":
        _change(
            row,
            actor_id=uuid4(),
            correlation_id=status.run_id,
            state="unknown",
            reason="recovery_unknown",
        )
    return plan


def admit(action, status):
    """Admission for the mail consumer: the follow-up's state decides transitions."""
    row = bound_retry(status)
    if action in {"lease_expired", "heartbeat", "progress", "hint", "claim", "effect"}:
        return True
    if action == "recovery_hint" and status.state == "running":
        return True
    if action in {
        "recovery_hint",
        "recovery_complete",
        "recovery_fail",
        "recovery_cancel",
        "recovery_retry",
    }:
        if row.state == "submitting":
            return action == "recovery_hint" and row.provider_deadline <= database_now()
        plan = recovery_plan(status)
        return plan is not None and action in {"recovery_hint", plan.action}
    if action == "retryable_failure":
        return row.state == "waiting"
    return TERMINAL_TRANSITIONS.get(row.state) == action


def admit_hint(action, status):
    """The scheduler only hints; the mail consumer decides under its claim."""
    return action in {"hint", "recovery_hint"} and status.task_type == TASK_TYPE


def _change(row, *, actor_id, correlation_id, **values):
    """Write one guarded move of the follow-up (frozen migration 0077's guard).

    An ended row's sealed reference is scrubbed here; the guard checks the
    rest of the move against the claim, the positions and the outcome.
    """
    if values.get("state", row.state) not in {"waiting", "submitting"}:
        values.update(sealed_substitutions=None, sealed_key_id=None)
        values.setdefault("finished_at", database_now())
    updated = AddressRetry.objects.filter(pk=row.pk, version=row.version).update(
        version=row.version + 1,
        actor_id=actor_id,
        correlation_id=correlation_id,
        **values,
    )
    if updated != 1:
        raise PermissionError("Address follow-up changed concurrently.")
    row.refresh_from_db()
    return row


def retry_disposition(row):
    """Why the follow-up must not send now: an ending reason, "held", or None.

    It is held to the scope the message itself would need to be sent
    (family_dispatch.sql's live check), and the SQL guard checks the same
    again when it commits "submitting".

    Ending reasons, each final (the follow-up is cancelled, or failed when
    expired): "expired" (past its 7 days); "scope_replaced" (not the current
    Production campaign or production cycle any more); "restored" (it was
    open when the backup this database was restored from was taken, so it
    may have been sent since: never send it again); "campaign_closed";
    "credentials_changed" (the code and link it holds are no longer the
    Family's current ones); "superseded" (a later invitation or reminder to
    the Family has been accepted since); and "recipients_changed" (the
    Family is no longer eligible or deliverable, or an address still to
    send is no longer one of its current, unrefused addresses; the message
    itself would not be sent then either).

    "held" waits, as the message does: a restore review, delivery pause,
    go-live gate, purge (campaign work gate), source reconciliation, or
    another email to the Family whose provider outcome is unresolved.
    """
    message = row.message
    now = database_now()
    if now >= row.expires_at:
        return "expired"
    runtime = SystemConfiguration.objects.get()
    if (
        runtime.mode != "production"
        or runtime.current_campaign_id != message.campaign_id
    ):
        return "scope_replaced"
    if (
        runtime.restore_activated_at is not None
        and row.created_at <= runtime.restore_activated_at
    ):
        return "restored"
    if runtime.restore_review_required:
        return "held"
    scope = _scope(message.campaign_id, share=True)
    if (
        scope.instant >= scope.campaign.active_configuration.ends_at
        or scope.instant < scope.campaign.active_configuration.starts_at
        or scope.campaign.state not in {"scheduled", "active"}
    ):
        return "campaign_closed"
    occurrence = ScheduleOccurrence.objects.only("production_cycle").get(
        pk=message.semantic_key, outbox_id=message.pk
    )
    if occurrence.production_cycle != scope.campaign.production_cycle:
        return "scope_replaced"
    deployment = DeploymentCredentialState.objects.get()
    if (
        row.token_generation_id != scope.campaign.active_token_generation_id
        or row.credential_epoch_id != deployment.family_link_epoch
        or not FamilyAccessTokenGeneration.objects.filter(
            pk=row.token_generation_id,
            campaign_id=message.campaign_id,
            state="active",
            credential_epoch=deployment.family_link_epoch,
        ).exists()
    ):
        return "credentials_changed"
    if (
        OutboxEvent.objects.filter(
            message__family_id=message.family_id,
            message__campaign_id=message.campaign_id,
            message__mode=message.mode,
            message__purpose__in=FOLLOWED_PURPOSES,
            previous_state="submitting",
            state="delivered",
            created_at__gt=row.source_event.created_at,
        )
        .exclude(message_id=message.pk)
        .exists()
    ):
        return "superseded"
    # Another email to the Family whose provider outcome is unresolved: wait,
    # as the message itself would ("awaits an unresolved provider outcome").
    if (
        OutboxMessage.objects.filter(
            family_id=message.family_id,
            campaign_id=message.campaign_id,
            mode=message.mode,
            state__in=("submitting", "delivery_unknown"),
        )
        .exclude(pk=message.pk)
        .exists()
    ):
        return "held"
    population = CampaignCredentialState.objects.get(campaign_id=message.campaign_id)
    current = SourceCurrent.objects.get(singleton=True)
    if (
        scope.campaign.delivery_paused
        or population.go_live_gate
        or population.population_dirty
        or population.source_snapshot_id != current.snapshot_id
        or population.source_generation != current.generation
        or CampaignWorkGate.objects.filter(campaign_id=message.campaign_id)
        .exclude(state="released")
        .exists()
    ):
        return "held"
    family = FamilyCampaign.objects.only(
        "id", "active", "email_eligible", "email_deliverable", "source_generation"
    ).get(pk=message.family_id)
    if family.source_generation != current.generation:
        return "held"
    with connection.cursor() as cursor:
        # The Family's current, unrefused addresses, as preparation reads them.
        cursor.execute(
            "SELECT jsonb_array_elements_text("
            "public.stewardship_family_mail_recipients_v1(%s))",
            [family.pk],
        )
        addresses = {address for (address,) in cursor.fetchall()}
    routed = row.render.routed_recipients
    if (
        not family.active
        or not family.email_eligible
        or not family.email_deliverable
        or any(routed[index] not in addresses for index in row.remaining)
    ):
        return "recipients_changed"
    return None


class CredentialsChanged(PermissionError):
    """The follow-up's link token is gone or no longer matches: never send it."""


def retry_mail(row, claim, *, private, public_origin):
    """The original email, for just the addresses still to send; memory only.

    The sealed reference is opened against the message's identity and the
    original rendering it was sealed with, and the link token is read
    through the claim-bound definer; nothing is sealed, written or rotated.
    The To: header names only the addresses this envelope sends to.
    """
    message, render = row.message, row.render
    retained = RenderInput(
        **{field: getattr(render, field) for field in RenderInput.__dataclass_fields__}
    )
    with key_set_lock(private, private.public()):
        reference = open_family_credentials(
            identity=delivery_status(message).identity,
            render=retained,
            private=private,
            sealed=SealedSubstitutions(
                row.sealed_substitutions,
                row.token_generation_id,
                row.credential_epoch_id,
            ),
        )
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT ciphertext, digest"
                " FROM public.stewardship_address_retry_token_v1(%s,%s,%s,%s,%s)",
                [
                    row.pk,
                    reference.token_id,
                    claim.run_id,
                    claim.fence,
                    claim.worker_id,
                ],
            )
            found = cursor.fetchone()
        if found is None:
            raise CredentialsChanged("Address follow-up link token is unavailable.")
        token = private.decrypt(found[0], context=token_context(reference.token_id))
        token = token.decode("ascii")
        if token_digest(token, message.campaign_id) != found[1]:
            raise CredentialsChanged("Address follow-up token fingerprint differs.")
    url = public_origin + "/access/" + token
    return FamilyDeliveryMail(
        message.semantic_key,
        render.sender,
        render.reply_to,
        tuple(render.routed_recipients[index] for index in row.remaining),
        render.subject,
        render.html.replace(CODE_PLACEHOLDER, escape(reference.code)).replace(
            LINK_PLACEHOLDER, escape(url, quote=True)
        ),
        render.text.replace(CODE_PLACEHOLDER, reference.code).replace(
            LINK_PLACEHOLDER, url
        ),
        banner_origin=public_origin,
    )


def spared(result):
    """Whether an outcome is a sending limit or outage, not the address's fault."""
    return result.status in {
        FamilyDeliveryStatus.TRANSIENT,
        FamilyDeliveryStatus.UNAVAILABLE,
    } and (result.limit is not None or result.health is ProviderHealth.UNAVAILABLE)


def retry_seconds(result, counted):
    """How long to wait before the next try after ``result``.

    A sending limit waits for the limit and an outage backs off briefly;
    otherwise the message's per-address throttle schedule, counting the
    accepted attempt as these addresses' first: it opened the follow-up 15
    minutes out (the first step), so after ``counted`` follow-up attempts
    the wait is the next step (1 hour, 4 hours, then 12 hours).
    """
    if result.limit is not None:
        return LIMIT_RETRY_SECONDS[result.limit]
    if result.health is ProviderHealth.UNAVAILABLE:
        return retry_delay(max(counted, 1))
    step = min(max(counted, 1) + 1, len(RECIPIENT_RETRY_SECONDS))
    return RECIPIENT_RETRY_SECONDS[step - 1]


def settled(row, result, now):
    """The follow-up's values after ``result``; pure, for the guarded write.

    ``result`` indexes the envelope just sent (``row.remaining``); the
    stored outcome and lists use the rendering's positions. Accepted
    positions leave ``remaining``; refused-permanently ones move to
    ``refused``; refused-for-now ones stay. An uncertain outcome ends the
    follow-up as "unknown"; a whole-message permanent or systemic refusal,
    the attempt budget or the expiry ends it as "failed".
    """
    envelope = list(row.remaining)
    if result.recipient_count != len(envelope):
        raise PermissionError("Address follow-up outcome does not match its envelope.")
    permanent = sorted(envelope[index] for index in result.permanent)
    transient = sorted(envelope[index] for index in result.transient)
    status = result.status.value
    refused = sorted(set(row.refused) | set(permanent))
    if result.status is FamilyDeliveryStatus.ACCEPTED:
        remaining = transient
    else:
        remaining = [position for position in envelope if position not in permanent]
    values = {
        "outcome": {"status": status, "permanent": permanent, "transient": transient},
        "remaining": remaining,
        "refused": refused,
        "spared": row.spared + int(spared(result)),
        "reason": "",
    }
    counted = row.attempt - values["spared"]
    wait = timedelta(seconds=retry_seconds(result, counted))
    if result.status is FamilyDeliveryStatus.UNKNOWN:
        values.update(state="unknown", reason="delivery_unknown")
    elif not remaining:
        values.update(state="finished")
    elif result.status in {
        FamilyDeliveryStatus.PERMANENT,
        FamilyDeliveryStatus.SYSTEMIC,
    }:
        values.update(state="failed", reason="provider_refused")
    elif counted >= MAX_ATTEMPTS:
        values.update(state="failed", reason="attempts")
    elif now + wait >= row.expires_at:
        values.update(state="failed", reason="expired")
    else:
        values.update(state="waiting", not_before=now + wait)
    return values, wait


def record_refusals(row, claim):
    """Set aside each address the follow-up's latest outcome refused permanently.

    Called right after the guarded write that recorded the outcome, in its
    transaction, so the refusal's evidence (migration 0079's guard) is that
    outcome under the same worker and run. Like ``open_follow_up``, it runs
    in a savepoint: a failure is logged at ERROR and never loses the recorded
    outcome; the address is then left to the next email's own refusal, as
    before #806.
    """
    from .recipient_suppressions import record_retry_refusal

    permanent = (row.outcome or {}).get("permanent", [])
    if not permanent:
        return
    try:
        with transaction.atomic():
            for position in permanent:
                record_retry_refusal(
                    retry=row,
                    address=row.render.routed_recipients[position],
                    actor_id=claim.worker_id,
                    correlation_id=claim.run_id,
                    fence=claim.fence,
                )
    except Exception:
        LOG.error(
            "An address follow-up could not record an address refused "
            "permanently as a recipient refusal (#806).",
            exc_info=True,
        )


def _defer(execution, seconds):
    """Hold the follow-up's task without spending anything, then try again."""
    execution.progress(0, 0, phase=TaskPhase.RECONCILING)
    execution.transition("retryable_failure", retry_seconds=seconds)


def _end_unsent(execution, reason):
    """End a waiting follow-up that must not send (see ``retry_disposition``)."""
    with execution.control.lock, work_transaction():
        row = bound_retry(_status(lock_task_claim(execution.claim)))
        if row.state != "waiting":
            return TERMINAL_TRANSITIONS.get(row.state)
        state = "failed" if reason == "expired" else "cancelled"
        _change(
            row,
            actor_id=execution.claim.worker_id,
            correlation_id=execution.claim.run_id,
            state=state,
            reason=reason,
        )
    return TERMINAL_TRANSITIONS[state]


def _execute(execution, *, store, private, public_origin, credential_path, circuit):
    """Recheck scope, commit "submitting", send once, then settle the outcome.

    Mirrors the Family message's own path (family_mail_delivery_tasks): no
    provider work inside a transaction, and an outcome after launch that
    cannot be read is recorded as unknown, never retried.
    """
    from .family_mail_delivery_tasks import _inflight_check, observe_outcome

    if connection.in_atomic_block or not execution.control.active:
        raise RuntimeError("Address follow-ups require maintained worker lifetime.")
    claim = execution.claim
    with execution.effect():
        row = bound_retry(_status(lock_task_claim(claim)))
        transition = TERMINAL_TRANSITIONS.get(row.state)
        reason = None if transition else retry_disposition(row)
        if transition is None and reason is None:
            try:
                mail_authority(store)
            except ConfigError:
                reason = "held"
        if transition is None and reason is None:
            configuration_id = SystemConfiguration.objects.get().active_configuration_id
            workspace = AppliedIntegration.objects.get(
                configuration_id=configuration_id, kind="google_workspace"
            )
            sender_name = configured_sender_name(configuration_id)
            capped = over_daily_limit(
                row.message.purpose, circuit.daily_sends(), len(row.remaining)
            )
    if transition is not None:
        execution.transition(transition)
        return
    if reason == "held":
        _defer(execution, 60 + randint(0, 300))
        return
    if reason is not None:
        execution.transition(_end_unsent(execution, reason))
        return
    wait = circuit.limit_remaining()
    if capped or wait or circuit.blocks_new_send():
        _defer(
            execution,
            (max(60, int(wait)) if wait else CAPPED_RETRY_SECONDS) + randint(0, 300),
        )
        return
    candidate = read_private(credential_path)
    if file_fingerprint(candidate) != workspace.credential_fingerprint:
        # Mid-switch to a new key: hold. Any other mismatch is an error.
        if switching("google_workspace", file_fingerprint(candidate)):
            _defer(execution, 60)
            return
        raise PermissionError("Installed Workspace credential differs.")
    execution.check()
    try:
        with execution.control.lock, work_transaction():
            row = bound_retry(_status(lock_task_claim(claim)))
            if row.state != "waiting" or retry_disposition(row) is not None:
                raise FamilyDeliveryHeld("Address follow-up scope changed.")
            mail = retry_mail(row, claim, private=private, public_origin=public_origin)
            now = database_now()
            row = _change(
                row,
                actor_id=claim.worker_id,
                correlation_id=claim.run_id,
                state="submitting",
                attempt=row.attempt + 1,
                run_id=claim.run_id,
                task_fence=claim.fence,
                worker_id=claim.worker_id,
                submitted_at=now,
                provider_deadline=now + timedelta(seconds=PROVIDER_SECONDS),
            )
    except FamilyDeliveryHeld:
        # Scope moved since the first check: look again shortly; the next
        # pass ends or holds it for its reason.
        _defer(execution, 60)
        return
    except CredentialsChanged:
        # The link it holds no longer works: end it rather than retry.
        execution.transition(_end_unsent(execution, "credentials_changed"))
        return
    except Exception:
        # Nothing was sent. Try again later; the expiry bounds the retries.
        LOG.warning(
            "An address follow-up could not be prepared; it is tried again later.",
            exc_info=True,
        )
        _defer(execution, 600)
        return
    settings = workspace.settings | {
        "sender": mail.sender,
        "reply_to": mail.reply_to,
        "sender_name": sender_name,
    }
    with transaction.atomic():
        remaining = (row.provider_deadline - database_now()).total_seconds()
    # No helper has started yet, so an elapsed budget is definitive non-acceptance.
    result = FamilyDeliveryResult(
        FamilyDeliveryStatus.TRANSIENT,
        len(mail.recipients),
        health=ProviderHealth.UNOBSERVED,
    )
    if remaining > 0:
        try:
            result = submit_family(
                candidate,
                settings,
                mail,
                seconds=min(30, remaining),
                check=_inflight_check(execution),
            )
        except ProviderCheckDrainFailure:
            raise
        except Exception:
            result = FamilyDeliveryResult(
                FamilyDeliveryStatus.UNKNOWN,
                len(mail.recipients),
                health=ProviderHealth.UNAVAILABLE,
            )
    # A kept connection may have ended during SMTP (a PostgreSQL restart):
    # replace it now, so recording the outcome does not fail and leave the
    # follow-up to recovery as unknown (#365).
    drop_unusable()
    with execution.control.lock, work_transaction():
        row = bound_retry(_status(lock_task_claim(claim)))
        if (row.state, row.run_id, row.task_fence) != (
            "submitting",
            claim.run_id,
            claim.fence,
        ):
            raise PermissionError("Address follow-up does not own its attempt.")
        values, wait = settled(row, result, database_now())
        row = _change(
            row, actor_id=claim.worker_id, correlation_id=claim.run_id, **values
        )
        record_refusals(row, claim)
    observe_outcome(circuit, result)
    if row.state == "waiting":
        execution.transition(
            "retryable_failure", retry_seconds=int(wait.total_seconds())
        )
    else:
        execution.transition(TERMINAL_TRANSITIONS[row.state])


def _unavailable(execution):
    """A scheduler registration never sends."""
    raise PermissionError("Schedulers cannot send address follow-ups.")


def retry_handler(
    store,
    *,
    private=None,
    public_origin=None,
    credential_path=None,
    circuit=None,
    scheduler=False,
):
    """The follow-up's handler: the scheduler only hints; mail dispatch sends.

    ``circuit`` is the mail consumer's Family DeliveryCircuit, so a
    follow-up honors the same pause, stop, sending limits and daily cap.
    """
    if scheduler:
        return Handler(
            queue=WorkQueue.MAIL,
            admit=admit_hint,
            execute=_unavailable,
            scope=work_transaction,
        )
    if not isinstance(credential_path, Path) or circuit is None:
        raise TypeError("Address follow-ups require the mail consumer's setup.")
    return Handler(
        queue=WorkQueue.MAIL,
        admit=admit,
        recover=recover_retry,
        execute=partial(
            _execute,
            store=store,
            private=private,
            public_origin=public_origin,
            credential_path=credential_path,
            circuit=circuit,
        ),
        scope=work_transaction,
    )
