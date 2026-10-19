"""``report pledges``, ``export pledges`` and ``pledges decide`` (#1733).

Pledges (``admin:pledges_to_enter``,
``reports.pledge_review_views``) is the financial stream's review worklist
and its manual handling report (#1686). The commands use the page's own
pieces, so the two cannot drift:

- ``report pledges`` counts the list: the page's admission
  (``pledge_review_views._principal``: Administrators and Staff, never a
  Ministry leader), the shared report read (``admin_report_reads._report``:
  the current campaign, ``admit_report_read`` and the campaign read guard),
  its read (``pledge_rows``) and its Stage choices. Each row names a Family
  and its pledge, so the document holds counts only, and the page's
  ``pledges_to_enter_viewed`` records a ``count`` of 0.
- ``export pledges`` is the page's download: the same read, the page's
  writer (``export_body``) for CSV, XLSX and PDF and its
  ``pledges_to_enter_exported`` with the rows in the file. ``jsonl`` is the
  command's own format, as ``export census``'s: the files' columns under
  fixed keys plus each row's ``item_id`` and ``version`` (what ``pledges
  decide`` takes) and the ``actions`` the reader may take on it now.
- ``pledges decide`` is a row's action or the bulk bar: a
  ``WorklistDecision`` recorded by the page's own decision service
  (``pledge_review.PledgeDecisions``), so the role, version, stage, Edit
  checks and the SQL guard of frozen migration 0127 are the page's. It runs
  in the page's command scope and records the service's
  ``pledge_review_updated`` and the command's ``admin_cmd_pledges_decide``
  in the same transaction, once per request key.

No command takes text that names a Family (the page's search stays on the
page). The host-only command line adds no rate limit or hold beyond the
page's.
"""

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from .admin_reads import NotAvailable, ReadModel, _recheck

# The page's Stage choices (``pledge_review_views.STAGES`` and ``EVERY``),
# spelled out because the parser is built before Django is set up; a test
# keeps them equal.
STAGE_CHOICES = ("to_review", "to_enter_by_hand", "entered_by_hand", "ignored", "all")
DEFAULT_STAGE = "to_review"
# The decisions ``pledges decide`` takes (``pledge_review.RECORDED``), the
# same way.
DECIDE_ACTIONS = ("accept", "edit", "ignore", "back_to_review", "entered_by_hand")
# The page's formats, and the command's JSON Lines.
CONTENT_TYPES = {
    "csv": "text/csv",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "pdf": "application/pdf",
    "jsonl": "application/x-ndjson",
}
# The fixed keys of a JSON Lines record, one per column of the page's files
# (``pledge_review_views.HEADINGS``), in the same order; a test keeps them
# aligned.
JSONL_KEYS = (
    "family",
    "family_duid",
    "parishsoft_status",
    "annual_pledge",
    "frequency",
    "share_methods",
    "reviewer_annual_pledge",
    "reviewer_frequency",
    "annual_pledge_to_enter",
    "frequency_to_enter",
    "stage",
    "decision",
    "entered_by",
    "entered_at",
    "submitted",
    "last_decision",
    "last_decision_at",
    "note",
)
# The stored actions a repeated key may have recorded for each decision: an
# Edit equal to the Family's answer is recorded as an Accept.
REPEATS = {
    "accept": {"accept"},
    "edit": {"edit", "accept"},
    "ignore": {"ignore"},
    "back_to_review": {"back_to_review"},
    "entered_by_hand": {"entered_by_hand"},
}


@dataclass(frozen=True)
class PledgeReport(ReadModel):
    """Pledges of the current campaign, as counts.

    ``stage`` is the page's Stage choice (``all`` for Every stage) and
    ``matching`` the rows it keeps; ``stages`` counts every pledge by stage
    (the Stage choices' counts, zeros included) and ``total`` is their sum.
    """

    campaign_id: UUID
    stage: str
    matching: int
    total: int
    stages: dict


@dataclass(frozen=True)
class PledgeExport(ReadModel):
    """The file ``export pledges`` wrote to standard output, described.

    ``size`` and ``sha256`` are of exactly the bytes written; ``count`` is
    how many pledges it lists (the rows at the chosen Stage).
    """

    campaign_id: UUID
    stage: str
    file_name: str
    content_type: str
    format: str
    timezone: str
    size: int
    sha256: str
    count: int


@dataclass(frozen=True)
class PledgeDecide(ReadModel):
    """The rows a ``pledges decide`` recorded, or found for its key.

    ``created`` is false for a repeat; ``decisions`` lists each chosen
    item's recorded row (``id``, ``item_id``, ``sequence``, ``action``,
    ``decision``, ``entered``, ``created_at``), in item id order: never an
    amount, a note or the Family.
    """

    created: bool
    request_key: UUID
    decisions: list


def _rows(campaign_id, actor):
    """Every pledge of the campaign, as the page reads it without a search.

    The campaign must include Financial stewardship: the menu greys the
    page out otherwise, and the command says ``not_available``.
    """
    from .admin_report_reads import _require_module
    from .reports.pledge_review_views import pledge_rows

    _require_module(campaign_id, "financial")
    rows, _metadata = pledge_rows(campaign_id, actor)
    return rows


def pledge_model(campaign_id, stage, rows):
    """``pledge_rows``'s rows as ``report pledges`` prints them: counts only."""
    from .reports.pledge_review_views import STAGES, at_stage

    counts = {choice.value: 0 for choice in STAGES}
    for row in rows:
        counts[row["stage"]] += 1
    return PledgeReport(
        campaign_id=campaign_id,
        stage=stage,
        matching=len(at_stage(rows, stage)),
        total=len(rows),
        stages=counts,
    )


def _stage(caller, service, actor, stage):
    """Refuse a Stage the page does not offer (after the session recheck)."""
    if stage not in STAGE_CHOICES:
        _recheck(caller, service.store, actor)
        raise ValueError("Invalid pledge stage.")


def read_pledges(caller, service, *, stage):
    """``report pledges``: the Pledges list, counted.

    Admission, the campaign check and the guarded read are the shared
    report read's (``_report``), so no current campaign, or one without
    Financial stewardship, is ``not_available`` and a Ministry leader is
    ``denied``. Records ``pledges_to_enter_viewed`` (succeeded or failed,
    with ``count`` 0) as the page records its view.
    """
    from .admin_report_reads import _report
    from .audit.schemas import Action
    from .reports.pledge_review_views import _audit, _principal

    def admit(actor, campaign_id):
        """The page's Stage check, before anything is recorded."""
        _stage(caller, service, actor, stage)

    def read(guard, actor, campaign_id):
        """The page's read, reduced to its counts."""
        return pledge_model(campaign_id, stage, _rows(campaign_id, actor)), None

    def audit(actor, campaign_id, outcome, values):
        """The page's view event: the command displayed no row."""
        _audit(Action.PLEDGES_TO_ENTER_VIEWED, actor, campaign_id, outcome, 0)

    return _report(
        caller, service, principal=_principal, read=read, audit=audit, admit=admit
    )


def _mutable(campaign_id):
    """Whether a decision may be recorded now: the page's own check.

    Other campaign work holding the campaign makes the page offer no row
    actions, so the JSON Lines then list none.
    """
    from .reports.export_services import admit_campaign

    try:
        admit_campaign(campaign_id, mutating=True)
    except PermissionError:
        return False
    return True


def row_actions(row, roles):
    """The decisions ``roles`` may record on ``row`` now, as its row offers them."""
    from .pipeline import Stream, allowed_actions
    from .web.review_worklist import ROW_ORDER

    allowed = allowed_actions(Stream.FINANCIAL, row["placement"].stage, roles)
    return [action.value for action in ROW_ORDER if action in allowed]


def pledges_jsonl(rows, zone, actor, *, mutable=True):
    """The rows as JSON Lines: the files' cells, ids, versions and actions.

    Each cell is the CSV's text (``export_rows``), so the formats never
    disagree about a value; ``item_id``, ``version`` and ``actions`` come
    from the row the cells were made from. ``mutable`` is the page's check
    that no other campaign work holds the campaign.
    """
    from .reports.pledge_review import reader_roles
    from .reports.pledge_review_views import export_rows

    roles = reader_roles(actor) if mutable else frozenset()
    lines = []
    for row, cells in zip(rows, export_rows(rows, zone), strict=True):
        record = dict(zip(JSONL_KEYS, cells, strict=True))
        record |= {
            "item_id": str(row["item"].submission_id),
            "version": row["item"].sequence,
            "actions": row_actions(row, roles),
        }
        lines.append(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    return "".join(lines).encode("utf-8")


def choice(fmt, zone_name):
    """The file's format and display zone, as ``(format, ZoneInfo)``.

    The page's choices (``export_choice``) and the command's ``jsonl``;
    anything else is a ``ValueError`` (``invalid``).
    """
    from zoneinfo import ZoneInfo

    from .reports.pledge_review_views import export_choice

    if fmt == "jsonl":
        export_choice("csv", zone_name)
        return fmt, ZoneInfo(zone_name)
    chosen = export_choice(fmt, zone_name)
    return fmt, chosen["zone"]


def export_pledges(caller, service, *, stage, fmt, zone_name, context):
    """``export pledges``: the Pledges download, as bytes.

    ``stage`` is the page's Stage choice, ``fmt`` and ``zone_name`` the
    file's format and the zone of its times. Admits passively with the
    page's own admission first; then the choices are checked (after the
    session recheck, so an ended session is exit 5) and activity is
    recorded as the download's post does. Records the page's
    ``pledges_to_enter_exported`` (succeeded or failed) with the rows in
    the file.
    """
    from .admin_report_reads import _report
    from .audit.schemas import Action
    from .reports.pledge_review_views import (
        _audit,
        _principal,
        at_stage,
        export_body,
        export_file_name,
    )

    holder = {}

    def admit(actor, campaign_id):
        """The page's checks of its choices, then the download post's activity."""
        _stage(caller, service, actor, stage)
        try:
            holder["choice"] = choice(fmt, zone_name)
        except ValueError:
            _recheck(caller, service.store, actor)
            raise
        _principal(caller, service.store)

    def read(guard, actor, campaign_id):
        """The page's rows at the Stage, rendered as the chosen file."""
        chosen, zone = holder["choice"]
        rows = at_stage(_rows(campaign_id, actor), stage)
        if chosen == "jsonl":
            body = pledges_jsonl(rows, zone, actor, mutable=_mutable(campaign_id))
        else:
            body = export_body(campaign_id, rows, chosen, zone)
        holder["body"] = body
        model = PledgeExport(
            campaign_id=campaign_id,
            stage=stage,
            file_name=export_file_name(chosen, datetime.now(UTC)),
            content_type=CONTENT_TYPES[chosen],
            format=chosen,
            timezone=zone_name,
            size=len(body),
            sha256=hashlib.sha256(body).hexdigest(),
            count=len(rows),
        )
        return model, len(rows)

    def audit(actor, campaign_id, outcome, values):
        """The page's download event: the rows in the file (0 if it failed)."""
        _audit(
            Action.PLEDGES_TO_ENTER_EXPORTED, actor, campaign_id, outcome, values or 0
        )

    model = _report(
        caller,
        service,
        principal=_principal,
        read=read,
        audit=audit,
        admit=admit,
        shaping=True,
    )
    context["stream"] = holder["body"]
    return model


def _admit_decide(caller, store, *, final=False):
    """The page's admission of a decision post: Administrators and Staff."""
    from .admin_roster_tick import admit_with
    from .reports.pledge_review_views import _principal

    return admit_with(_principal, caller, store, final=final)


def admit_decide(caller, service):
    """The page's admission, passive: ``pledges decide``'s role-first check.

    A Ministry leader is ``denied`` before any input or prompt. Records no
    activity.
    """
    return _admit_decide(caller, service.store, final=True)


def check_choice(versions, action, *, amount=None, frequency=None, note=""):
    """Refuse a decision the page could not post, before any prompt.

    At most the bulk bar's limit; Edit names one pledge and only bulk
    actions take several (the page's bulk bar has no Edit); a note no
    longer than the page's; and an Edit's value checked as the Family form
    checks it (at ``amount`` or ``frequency``). Returns the editor's fields
    and the checked value (None but for an Edit), which ``decide_pledges``
    posts and compares a repeat with.
    """
    from .admin_followup import InvalidFollowup, problem
    from .reports.pledge_review import MAX_NOTE, EditRefusal, edited_value
    from .web.review_worklist import BULK, MAX_BULK

    if len(versions) > MAX_BULK:
        raise InvalidFollowup(
            [problem("item", f"Choose at most {MAX_BULK} pledges at once.")]
        )
    if len(versions) > 1 and action not in BULK:
        raise InvalidFollowup([problem("item", "Edit one pledge at a time.")])
    if len(note) > MAX_NOTE:
        raise InvalidFollowup(
            [problem("note", f"Enter a note of at most {MAX_NOTE:,} characters.")]
        )
    fields = _edit_fields(action, amount, frequency)
    if not fields:
        return fields, None
    try:
        return fields, edited_value(fields)
    except EditRefusal as error:
        # The service's static reason, at the option that sets the field;
        # never the value.
        options = {"value_amount": "amount", "value_frequency": "frequency"}
        raise InvalidFollowup(
            [problem(options[name], error.message) for name in error.fields]
        ) from None


def _edit_fields(action, amount, frequency):
    """An Edit's editor fields, as the page posts them; none for the rest.

    An amount or frequency with any other decision is a usage mistake the
    page could not post (``invalid``).
    """
    from .admin_followup import InvalidFollowup, problem

    if action != "edit":
        if amount is not None or frequency is not None:
            raise InvalidFollowup(
                [problem("amount", "Only an Edit takes --amount and --frequency.")]
            )
        return {}
    if amount is None:
        raise InvalidFollowup([problem("amount", "An Edit needs --amount.")])
    return {"value_amount": [amount], "value_frequency": [frequency or ""]}


def _keys(versions, request_key):
    """Each chosen item's request key, as the service derives them."""
    from .reports.pledge_review import bulk_key_for

    if len(versions) == 1:
        return {item: request_key for item in versions}
    return {item: bulk_key_for(request_key, item) for item in versions}


def _decision_row(review):
    """One recorded row as the document prints it: never an amount or note."""
    return {
        "id": review.pk,
        "item_id": review.submission_id,
        "sequence": review.sequence,
        "action": review.action,
        "decision": review.decision,
        "entered": review.entered,
        "created_at": review.created_at,
    }


def _same_value(row, value):
    """Whether an Edit's ``value`` is what ``row`` recorded for it.

    An Edit is stored with its value; one equal to the Family's answer is
    stored as an Accept, whose value is the answer of the response it was
    recorded on.
    """
    from .reports.pledge_review import family_value

    if row.action == "edit":
        return (row.reviewed_annual_pledge, row.reviewed_frequency) == value
    return family_value(row.submission) == value


def _repeat(actor, campaign_id, request_key, keys, action, value):
    """The rows an earlier run recorded with ``request_key``, or None for none.

    A key is recorded as itself (one pledge) or as each pledge's key
    derived from it (``bulk_key_for``, several), so every row of the actor
    in the campaign is matched against both: a key reused for other
    pledges, a different number of them, another decision or another Edit
    value is ``invalid``, never a second decision under the same key. A
    repeat with the same intent returns the rows it recorded.
    """
    from .admin_followup import InvalidFollowup, problem
    from .reports.pledge_models import PledgeReview
    from .reports.pledge_review import bulk_key_for

    # One small read per run: an actor's decisions in one campaign are at
    # most a few per Family.
    matched = [
        pk
        for pk, submission_id, key in PledgeReview.objects.filter(
            actor_id=actor.identity, submission__campaign_id=campaign_id
        ).values_list("pk", "submission_id", "request_key")
        if key in (request_key, bulk_key_for(request_key, submission_id))
    ]
    if not matched:
        return None
    found = list(
        PledgeReview.objects.filter(pk__in=matched).select_related("submission")
    )
    if (
        {row.submission_id: row.request_key for row in found} != keys
        or len(found) != len(keys)
        or any(row.action not in REPEATS[action] for row in found)
        or (value is not None and not all(_same_value(row, value) for row in found))
    ):
        raise InvalidFollowup(
            [problem("request_key", "This request key was used for another decision.")]
        )
    return found


def decide_pledges(
    caller, service, versions, *, action, amount, frequency, note, request_key, context
):
    """``pledges decide``: one decision on one or several pledges, all or none.

    ``versions`` maps each chosen item (a Family's current response id) to
    the version its row shows (``export pledges --format jsonl``'s
    ``version``). In the page's command scope the current campaign is
    checked as the page does (one without Financial stewardship is
    ``not_available``), every item must be a Live response of it
    (``not_available`` otherwise), and the page's ``PledgeDecisions``
    records the decision: someone else's decision or a newer answer from
    the Family first is ``stale_version``; an action the stage does not
    offer, a missing note to reopen an ignored pledge, or an Edit value the
    Family form would refuse (at ``amount`` or ``frequency``) is
    ``invalid``. A new key records ``admin_cmd_pledges_decide`` once.
    """
    from .admin_followup import _record_command
    from .admin_report_reads import _current, _refusal, _refusals, _require_module
    from .admin_roster_tick import scoped_write
    from .audit.schemas import Action as AuditAction
    from .campaigns.work_locks import require_work_order
    from .pipeline import Action
    from .reports.pledge_models import PledgeReview
    from .reports.pledge_review import PledgeDecisions
    from .reports.read_admission import admit_report_read
    from .responses.models import Submission
    from .storage import StaleRecordError
    from .web.review_worklist import StaleDecision, WorklistDecision

    fields, value = check_choice(
        versions, action, amount=amount, frequency=frequency, note=note
    )
    actor = _admit_decide(caller, service.store)
    context["request_id"] = str(request_key)
    keys = _keys(versions, request_key)
    decision = WorklistDecision(
        Action(action),
        {str(item): version for item, version in versions.items()},
        note,
        request_key,
        bulk=len(versions) > 1,
        fields=fields,
    )

    def step(actor, written):
        """The page's decision, with the command's event for a new key."""
        campaign_id = _current()
        try:
            admit_report_read(campaign_id)
            _require_module(campaign_id, "financial")
        except _refusals() as error:
            _refusal(caller, service, actor, error)
        live = Submission.objects.filter(
            pk__in=list(versions), campaign_id=campaign_id, mode="live"
        ).count()
        if live != len(versions):
            raise NotAvailable("No such pledge.")
        # The scope's work order serializes repeats of one key (#1222).
        require_work_order()
        found = _repeat(actor, campaign_id, request_key, keys, action, value)
        if found is None:
            try:
                PledgeDecisions(service.store, campaign_id).decide(actor, decision)
            except StaleDecision:
                raise StaleRecordError("The pledge was changed first.") from None
            _record_command(
                AuditAction.ADMIN_CMD_PLEDGES_DECIDE, actor, caller, written
            )
            found = PledgeReview.objects.filter(
                actor_id=actor.identity, request_key__in=list(keys.values())
            )
        return PledgeDecide(
            created=bool(written),
            request_key=request_key,
            decisions=[
                _decision_row(row)
                for row in sorted(found, key=lambda row: str(row.submission_id))
            ],
        )

    return scoped_write(caller, service, _admit_decide, actor, context, step)
