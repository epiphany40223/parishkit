"""Outgoing mail's bulk actions: many messages, each resolved on its own (#382 M4).

After a mail outage an Administrator may face dozens of failed emails, or
of emails marked Not sure it arrived, and used to resolve them one page at
a time. Two bulk actions cover those cases:

- ``retry_failed``: retry every failed email whose own page offers **Retry
  failed delivery**;
- ``confirm_unsent``: record as not sent every Not sure it arrived email
  whose own page offers that resolution, on the Administrator's word that
  the mail service's own records show it was not sent.

Both select by the rules each message's page uses
(``delivery_reads.offered_actions``), limited to the current campaign and
optionally one email type, and preview the exact messages with one count.
The preview is a signed binding of every selected message's id and
version, bound to the Administrator and valid for ``PREVIEW_SECONDS``.
It carries only a digest of the note, never the note itself, so the
evidence is not readable from the token; Confirm and Continue post the
note again beside it and it must match.

Applying it runs the ordinary per-message ``resolve_delivery`` once per
message, each in its own command scope and transaction, with the
message's previewed version, so each message keeps its own guard,
resolution record and audit event, and none is prepared differently from
a one-at-a-time retry (no new Family code or link). Each message's
command id is derived from the preview and the message, so applying the
same preview again never repeats a resolution: already resolved messages
are counted, not redone. A message that changed since the preview, or that
its own guard now refuses, is skipped and counted, never forced. At most
``BATCH`` messages are resolved per request, which keeps one web request
well inside its time limit; the rest of the same preview is applied by
applying it again (**Continue** on the page). Continue's preview is the
same binding re-signed with the messages skipped so far, so they are
counted again without being retried and never fill a later batch, and
with the original preview's issue time, so it expires when the original
would. Every application records
one ``delivery_bulk_resolved`` audit event with the counts.
"""

import hashlib
import hmac
import time
from dataclasses import dataclass
from uuid import UUID, uuid4, uuid5

from django.core import signing
from django.db import DatabaseError, connection, transaction
from django.db.models import OuterRef, Subquery
from django.db.models.expressions import RawSQL

from parishkit.stewardship.audit.schemas import Action, ActorKind, Outcome
from parishkit.stewardship.audit.services import record_action
from parishkit.stewardship.storage import StaleRecordError

from .delivery_admin import evidence_note
from .delivery_metadata import PURPOSES, messages
from .delivery_reads import offered_actions
from .delivery_resolution import resolve_delivery
from .delivery_resolution_models import DeliveryResolution
from .outbox_models import OutboxMessage
from .storage import TaskRetryConflict

# Each bulk action, with the message state it selects from.
KINDS = {"retry_failed": "permanent_failure", "confirm_unsent": "delivery_unknown"}
# The email types each action may cover. A chosen-Family test is never
# retried (its own page offers no retry), so it is left out of retry
# rather than previewed and then refused.
KIND_PURPOSES = {
    "retry_failed": tuple(purpose for purpose in PURPOSES if purpose != "family_test"),
    "confirm_unsent": PURPOSES,
}
SALT = "parishkit.stewardship.delivery-bulk"
PREVIEW_SECONDS = 15 * 60
# Messages resolved per request. A retry re-renders and seals one email,
# typically well under a second, so a batch stays far inside the web
# request limit (runtime_budget.server_timeout_seconds).
BATCH = 100
# A refusal from the message's own guard, raised in its own transaction:
# the message changed, its task moved on, or SQL refused the intent
# (check or unique violation). Any other database error is an outage and
# stops the run.
REFUSED_SQLSTATES = {"23514", "23505"}
# A signed preview's fields.
BINDING = {
    "actor",
    "command",
    "kind",
    "purpose",
    "note_digest",
    "items",
    "skipped",
    "issued",
}


class NothingToResolve(StaleRecordError):
    """No message qualifies for the bulk action any more."""


@dataclass(frozen=True)
class BulkResult:
    """What one application of a preview did.

    ``resolved`` counts the previewed messages resolved by this preview so
    far (including by an earlier application), ``newly`` those resolved by
    this one, ``skipped`` those found changed or refused by this or an
    earlier application of it, and ``remaining`` those left for the next
    application by the batch limit. ``token`` is the signed preview that
    Continue posts while some remain (None otherwise).
    """

    kind: str
    purpose: str
    total: int
    resolved: int
    newly: int
    skipped: int
    remaining: int
    token: str | None = None


def note_digest(note):
    """The digest a preview carries in place of its note.

    Line breaks are compared as ``\n``: a browser posts a textarea's and a
    hidden field's line breaks as CRLF, but the page's HTML parser reads a
    hidden field's value back with LF, so the same note can arrive either
    way.
    """
    return hashlib.sha256(note.replace("\r\n", "\n").encode()).hexdigest()


def sign(binding):
    """Sign ``binding``'s fields, leaving out the note ``load_preview`` adds."""
    return signing.dumps(
        {key: binding[key] for key in BINDING}, salt=SALT, compress=True
    )


def candidates(kind):
    """The current campaign's messages ``kind`` would resolve now, oldest first.

    Returns dicts with ``id``, ``version`` and ``purpose``. A message is
    included only when its own page offers ``kind`` now: the campaign is not
    archived and its exports are admitted, the latest delivery task failed,
    and, for a retry, the message's resend admission holds.
    """
    from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
    from parishkit.stewardship.campaigns.models import Campaign

    from .models import TaskRun

    configuration = SystemConfiguration.objects.first()
    if configuration is None or configuration.current_campaign_id is None:
        return []
    campaign = Campaign.objects.only("state").get(pk=configuration.current_campaign_id)
    with connection.cursor() as cursor:
        cursor.execute("SELECT stewardship_export_admitted_v1(%s,true)", [campaign.pk])
        can_resolve = cursor.fetchone()[0]
    latest = (
        TaskRun.objects.filter(root_id=OuterRef("task_id"))
        .order_by("-retry_sequence")
        .values("state")[:1]
    )
    rows = messages().filter(
        campaign_id=campaign.pk, state=KINDS[kind], purpose__in=KIND_PURPOSES[kind]
    )
    rows = rows.annotate(task_state=Subquery(latest))
    if kind == "retry_failed":
        # The same per-message admission the message's page reads.
        rows = rows.annotate(
            can_retry=RawSQL(
                "stewardship_delivery_retry_admitted_v1(stewardship_outbox_message.id)",
                (),
            )
        )
    # Only metadata columns: the web login may not read a message's content.
    fields = ["id", "version", "purpose", "state", "task_state"]
    if kind == "retry_failed":
        fields.append("can_retry")
    found = []
    for row in rows.order_by("created_at", "id").values(*fields):
        offered = offered_actions(
            row["state"],
            row["task_state"],
            campaign_state=campaign.state,
            can_resolve=can_resolve,
            can_retry=row.get("can_retry", False),
        )
        if kind in offered:
            found.append(
                dict(id=row["id"], version=row["version"], purpose=row["purpose"])
            )
    return found


def overview():
    """What each bulk action could cover now, for Outgoing mail's panel.

    Returns ``{kind: {"total": n, "purposes": [(purpose, count), ...]}}``
    with only the kinds and email types that have a message, in
    ``PURPOSES`` order.
    """
    shown = {}
    for kind in KINDS:
        counts = {}
        for item in candidates(kind):
            counts[item["purpose"]] = counts.get(item["purpose"], 0) + 1
        if counts:
            shown[kind] = dict(
                total=sum(counts.values()),
                purposes=[
                    (purpose, counts[purpose])
                    for purpose in PURPOSES
                    if purpose in counts
                ],
            )
    return shown


def preview(actor_id, *, kind, purpose, note, checked):
    """Select the messages, and bind them to a signed preview.

    ``purpose`` is one email type or ``"all"``; ``note`` the required
    evidence or reason, recorded on every message; ``checked`` whether the
    Administrator confirmed checking the mail service's records, required
    for ``confirm_unsent`` and refused for a retry. Raises ``ValueError``
    for an invalid choice or note and ``NothingToResolve`` when nothing
    qualifies any more. Returns ``kind``, ``purpose``, ``count``, ``note``
    and ``token``.
    """
    if kind not in KINDS or (
        purpose != "all" and purpose not in KIND_PURPOSES.get(kind, ())
    ):
        raise ValueError("Invalid bulk resolution choice.")
    note = evidence_note(note)
    if type(checked) is not bool or checked != (kind == "confirm_unsent"):
        raise ValueError("Confirm checking the mail service's records first.")
    items = [
        [str(item["id"]), item["version"]]
        for item in candidates(kind)
        if purpose in ("all", item["purpose"])
    ]
    if not items:
        raise NothingToResolve("No email qualifies for this action any more.")
    token = sign(
        {
            "actor": str(actor_id),
            "command": str(uuid4()),
            "kind": kind,
            "purpose": purpose,
            "note_digest": note_digest(note),
            "items": items,
            "skipped": [],
            "issued": int(time.time()),
        }
    )
    return dict(kind=kind, purpose=purpose, count=len(items), note=note, token=token)


def load_preview(token, actor_id, note):
    """The binding of a signed preview made for ``actor_id``, or refuse.

    ``note`` is the note posted beside the preview; it must be the one
    previewed, and is returned in the binding as ``note``. An expired or
    altered preview, or a different note, raises ``signing.BadSignature``
    (its ``SignatureExpired`` subclass when expired); another
    Administrator's raises ``PermissionError``; a malformed one, or an
    invalid note, ``ValueError``.
    """
    if type(token) is not str or not token:
        raise ValueError("Invalid bulk resolution preview.")
    note = evidence_note(note)
    binding = signing.loads(token, salt=SALT, max_age=PREVIEW_SECONDS)
    if (
        type(binding) is not dict
        or set(binding) != BINDING
        or binding["kind"] not in KINDS
        or type(binding["items"]) is not list
        or not binding["items"]
        or type(binding["skipped"]) is not list
        or type(binding["issued"]) is not int
    ):
        raise ValueError("Invalid bulk resolution preview.")
    # Continue re-signs the preview, so its own signature is fresh; the
    # original issue time it carries is what expires.
    if time.time() - binding["issued"] > PREVIEW_SECONDS:
        raise signing.SignatureExpired("Bulk resolution preview expired.")
    if not hmac.compare_digest(binding["note_digest"], note_digest(note)):
        raise signing.BadSignature("Bulk resolution note differs.")
    if binding["actor"] != str(actor_id):
        raise PermissionError("This preview belongs to another Administrator.")
    return binding | {"note": note}


def apply_preview(
    store, actor_id, binding, *, scope, admit, preparation_inputs, limit=BATCH
):
    """Resolve each previewed message on its own; return a ``BulkResult``.

    ``scope`` makes the context manager each message is resolved in (the
    page's command scope, which re-admits the Administrator and commits);
    ``admit`` re-checks the Administrator after a message is refused for
    permission, raising ``PermissionError`` if they lost access, which
    stops the run. ``preparation_inputs`` is ``resolve_delivery``'s.

    A message an earlier application of this preview skipped (listed by
    its index in ``binding["skipped"]``) is counted as skipped again, not
    retried, so skipped messages never fill a batch and Continue always
    moves on to messages not yet attempted.
    """
    command, kind = UUID(binding["command"]), binding["kind"]
    skipped_before, skipped = set(binding["skipped"]), []
    resolved = newly = remaining = attempted = 0
    try:
        for index, (message_id, version) in enumerate(binding["items"]):
            message_id = UUID(message_id)
            # One command id per (preview, message): applying the same
            # preview again replays, never repeats, a resolution.
            command_id = uuid5(command, str(message_id))
            if DeliveryResolution.objects.filter(pk=command_id).exists():
                resolved += 1
                continue
            if index in skipped_before:
                skipped.append(index)
                continue
            if attempted >= limit:
                remaining += 1
                continue
            attempted += 1
            try:
                with scope():
                    resolve_delivery(
                        store,
                        actor_id,
                        message_id=message_id,
                        command_id=command_id,
                        expected_version=version,
                        action=kind,
                        note=binding["note"],
                        preparation_inputs=preparation_inputs,
                    )
            except (StaleRecordError, TaskRetryConflict, OutboxMessage.DoesNotExist):
                skipped.append(index)
                continue
            except PermissionError:
                # The message's own scope or campaign refused it (an
                # earlier scope, a paused campaign), unless the
                # Administrator lost access, which stops everything.
                admit()
                skipped.append(index)
                continue
            except DatabaseError as error:
                if getattr(error.__cause__, "sqlstate", None) not in REFUSED_SQLSTATES:
                    raise
                skipped.append(index)
                continue
            resolved += 1
            newly += 1
    finally:
        # One lasting record of the bulk action, even when an outage or a
        # lost sign-in stopped it part way (each resolved message also has
        # its own resolution audit event).
        if attempted:
            with transaction.atomic():
                record_action(
                    Action.DELIVERY_BULK_RESOLVED,
                    actor_kind=ActorKind.PORTAL_USER,
                    actor_id=actor_id,
                    subject_id=command,
                    context={
                        "outcome": Outcome.SUCCEEDED
                        if newly == attempted
                        else Outcome.CHANGED,
                        "count": newly,
                        "matching_count": len(binding["items"]),
                    },
                )
    return BulkResult(
        kind=kind,
        purpose=binding["purpose"],
        total=len(binding["items"]),
        resolved=resolved,
        newly=newly,
        skipped=len(skipped),
        remaining=remaining,
        # Continue's preview: the same binding, with the skipped carried.
        token=sign(binding | {"skipped": skipped}) if remaining else None,
    )
