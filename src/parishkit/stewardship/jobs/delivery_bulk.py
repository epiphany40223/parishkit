"""Sent mail's bulk actions: many messages, each resolved on its own (#382 M4).

After a mail outage an Administrator may face dozens of failed emails, or
of emails marked Not sure it arrived, and used to resolve them one page at
a time. Sent mail's table selects them by row (#2296) for two bulk
actions:

- ``retry_failed`` (**Send again**): retry each failed email whose own
  page offers **Retry failed delivery**;
- ``confirm_unsent`` (**Record as not sent**): record as not sent each Not
  sure it arrived email whose own page offers that resolution, on the
  Administrator's word that the mail service's own records show it was
  not sent.

A row is selectable only by the rules each message's page uses
(``delivery_reads.offered_actions``), limited to the current campaign
(``selectable``), and the selection is either the ticked rows of one page
or every selectable email the page's filters match (``matching_ids``).
The preview checks that every selected message still qualifies for the
action and binds them all: a signed binding of every message's id and
version, bound to the Administrator and valid for ``PREVIEW_SECONDS``.
The note is typed in the review dialog after the preview, so a first
application takes it as given (the binding's ``note_digest`` is None);
Continue's preview carries only a digest of it, never the note itself, so
the evidence is not readable from the token, and Continue posts the note
again beside it and it must match.

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
same binding re-signed with the messages skipped so far and the note's
digest, so they are counted again without being retried and never fill a
later batch, and with the original preview's issue time, so it expires
when the original would. Every application records one
``delivery_bulk_resolved`` audit event with the counts.
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
# The email types each action may cover. A chosen-Family test and a report
# email test (#2036) are never retried (their own pages offer no retry), so
# they are left out of retry rather than previewed and then refused.
NEVER_RETRIED = frozenset({"family_test", "admin_report_test"})
KIND_PURPOSES = {
    "retry_failed": tuple(
        purpose for purpose in PURPOSES if purpose not in NEVER_RETRIED
    ),
    "confirm_unsent": PURPOSES,
}
SALT = "parishkit.stewardship.delivery-bulk"
PREVIEW_SECONDS = 15 * 60
# The most rows one page selection may post: Sent mail's largest page
# (``web.tables.WINDOW_SIZES``). Select all N matching has no such limit;
# its emails are read again from the filters.
SELECTION_LIMIT = 100
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
    "note_digest",
    "items",
    "skipped",
    "issued",
}


class NothingToResolve(StaleRecordError):
    """No message qualifies for the bulk action any more."""


class SelectionChanged(StaleRecordError):
    """A selected message no longer qualifies for the bulk action.

    It changed since the list was drawn (or the selection was built by
    hand), so the whole selection is refused rather than shrunk: the
    review only ever covers exactly what the Administrator chose.
    """


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


def selectable():
    """Every message a bulk action covers now, by id, in ``candidates`` order.

    Returns ``{id: {"id", "version", "purpose", "kind"}}``: one entry per
    current-campaign message whose own page offers ``retry_failed`` or
    ``confirm_unsent`` now. A message has at most one kind, since the two
    select from different states. Sent mail gives exactly these rows a
    selection box (#2296).
    """
    found = {}
    for kind in KINDS:
        for item in candidates(kind):
            found[item["id"]] = item | {"kind": kind}
    return found


def matching_ids(values, *, snapshot, actor, among):
    """The ids in ``among`` that Sent mail's applied filters list (#2296).

    ``values`` are ``read_listing``'s validated filters (its ``values``),
    ``snapshot`` and ``actor`` what it read them with; ``among`` the
    ``selectable`` messages. Select all N matching covers these: the
    emails with a selection box on every page of the filtered list, read
    by primary key, so the query never reads the whole log.
    """
    from .delivery_metadata import filtered
    from .delivery_reads import day_bounds
    from .send_history import SendKey

    if not among:
        return []
    since, until = day_bounds(values)
    listed = set(
        filtered(
            state=values["state"],
            query=values["recipient"],
            send=SendKey.parse(values["send"]) if values.get("send") else None,
            snapshot=snapshot,
            purpose=values["purpose"],
            mode=values["mode"],
            since=since,
            until=until,
            actor=actor,
        )
        .filter(pk__in=list(among))
        .values_list("pk", flat=True)
    )
    return [message_id for message_id in among if message_id in listed]


def preview(actor_id, *, kind, ids, available):
    """Bind the selected messages to a signed preview.

    ``ids`` are the selected message ids (a page's ticked rows, or every
    one ``matching_ids`` found); ``available`` is ``selectable()``, read
    in the same transaction. Every selected message must qualify for
    ``kind`` now, or ``SelectionChanged`` refuses the whole selection.
    Raises ``ValueError`` for an invalid action or an empty or repeated
    selection, and ``NothingToResolve`` when nothing qualifies any more.
    Returns ``kind``, ``count``, ``purposes`` (``(purpose, count)`` pairs
    in ``PURPOSES`` order) and ``token``. The binding has no note yet:
    the review dialog asks for it, and Apply posts it
    (``load_preview``).
    """
    if kind not in KINDS:
        raise ValueError("Invalid bulk resolution choice.")
    if len(set(ids)) != len(ids):
        raise ValueError("Each email may be selected only once.")
    if not ids:
        raise NothingToResolve("No email qualifies for this action any more.")
    if any(available.get(message_id, {}).get("kind") != kind for message_id in ids):
        raise SelectionChanged("A selected email no longer qualifies.")
    chosen = set(ids)
    # The candidates' order (oldest first), whatever order the rows posted.
    items = [item for message_id, item in available.items() if message_id in chosen]
    counts = {}
    for item in items:
        counts[item["purpose"]] = counts.get(item["purpose"], 0) + 1
    token = sign(
        {
            "actor": str(actor_id),
            "command": str(uuid4()),
            "kind": kind,
            "note_digest": None,
            "items": [[str(item["id"]), item["version"]] for item in items],
            "skipped": [],
            "issued": int(time.time()),
        }
    )
    return dict(
        kind=kind,
        count=len(items),
        purposes=[
            (purpose, counts[purpose]) for purpose in PURPOSES if purpose in counts
        ],
        token=token,
    )


def load_preview(token, actor_id, note, *, checked=False):
    """The binding of a signed preview made for ``actor_id``, or refuse.

    ``note`` is the note posted beside the preview, returned in the binding
    as ``note``. A first application (a binding without a note digest)
    takes the note typed in the review dialog, and, to record emails as not
    sent, needs ``checked``: the Administrator confirmed checking the mail
    service's records (refused for a retry). Continue's binding carries the
    note's digest, and the note posted must be that one. Once any of a
    preview's messages is resolved, only Continue's binding goes on: the
    first one, which binds no note, would otherwise apply the rest with a
    different note. An expired or altered preview, a different note or a
    first binding already applied raises ``signing.BadSignature`` (its
    ``SignatureExpired`` subclass when expired); another
    Administrator's raises ``PermissionError``; a malformed one, an invalid
    note or a missing check, ``ValueError``.
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
        or not (binding["note_digest"] is None or type(binding["note_digest"]) is str)
    ):
        raise ValueError("Invalid bulk resolution preview.")
    # Continue re-signs the preview, so its own signature is fresh; the
    # original issue time it carries is what expires.
    if time.time() - binding["issued"] > PREVIEW_SECONDS:
        raise signing.SignatureExpired("Bulk resolution preview expired.")
    if binding["note_digest"] is None:
        if type(checked) is not bool or checked != (
            binding["kind"] == "confirm_unsent"
        ):
            raise ValueError("Confirm checking the mail service's records first.")
    elif not hmac.compare_digest(binding["note_digest"], note_digest(note)):
        raise signing.BadSignature("Bulk resolution note differs.")
    if binding["actor"] != str(actor_id):
        raise PermissionError("This preview belongs to another Administrator.")
    if binding["note_digest"] is None and _started(binding):
        raise signing.BadSignature("Bulk resolution preview already applied.")
    return binding | {"note": note}


def _started(binding):
    """Whether any message of the preview ``binding`` is already resolved.

    Each resolution's id is ``uuid5(command, message id)``, as
    ``apply_preview`` derives it. Raises ``ValueError`` for a malformed id.
    """
    command = UUID(binding["command"])
    commands = [
        uuid5(command, str(UUID(message_id)))
        for message_id, _version in binding["items"]
    ]
    return DeliveryResolution.objects.filter(pk__in=commands).exists()


def left(binding):
    """How many of a preview's messages are neither resolved nor skipped yet.

    Continue's review counts these: the messages its Apply would still
    attempt, at most ``BATCH`` of them per request.
    """
    command, skipped = UUID(binding["command"]), set(binding["skipped"])
    commands = [
        uuid5(command, message_id)
        for index, (message_id, _version) in enumerate(binding["items"])
        if index not in skipped
    ]
    return len(commands) - DeliveryResolution.objects.filter(pk__in=commands).count()


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
        total=len(binding["items"]),
        resolved=resolved,
        newly=newly,
        skipped=len(skipped),
        remaining=remaining,
        # Continue's preview: the same binding, with the skipped carried
        # and the note applied so far bound by its digest.
        token=sign(
            binding | {"skipped": skipped, "note_digest": note_digest(binding["note"])}
        )
        if remaining
        else None,
    )
