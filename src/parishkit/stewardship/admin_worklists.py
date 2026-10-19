"""Additional information and Limitations decisions on the command line (#1735).

The two review worklists of #1685 (``admin:information_queue``,
``reports.information_views``; ``admin:limitations_report``,
``reports.limitation_views``) record Staff decisions (Accept, Ignore, Back
to review, Handled) through each stream's ``DecisionService``. These
commands use the pages' own pieces, so the two cannot drift:

- ``information list`` is the Additional information worklist's rows as
  identifiers only (each request's id, version, stage, decision and the
  actions the reader may take), as ``followup information-show`` prints one
  request: the page's admission (``information_views._principal``), the
  shared report read (``admin_report_reads._report``) and the page's
  placed read (``information_worklist.requests``). It records the page's
  ``information_viewed``, started and then succeeded or failed.
- ``information decide`` and ``limitations decide`` are a row's action or
  the bulk bar: a ``WorklistDecision`` recorded by the page's own service
  (``information_worklist.InformationDecisions``,
  ``limitation_worklist.LimitationDecisions``), so the role, version, stage,
  note rule and the SQL guard of frozen migration 0126 are the page's. Each
  runs in the page's command scope and records the service's events and
  the command's ``admin_cmd_<stream>_decide`` in the same transaction, once
  per request key.

``report limitations --stage``, ``report information --stage``, ``export
limitations --stage --format jsonl`` and ``export information --stage``
extend the existing commands in their own modules. No command takes text
that names a Family. The host-only command line adds no rate limit or hold
beyond the pages'.
"""

from dataclasses import dataclass
from uuid import UUID

from .admin_reads import NotAvailable, ReadModel

# Each stream's Stage choices (``information_worklist.STAGE_CHOICES`` and
# ``limitations.STAGES`` with Every stage), spelled out because the parser
# is built before Django is set up; a test keeps them equal.
INFORMATION_STAGES = ("to_review", "to_handle", "ignored", "handled", "history", "all")
LIMITATION_STAGES = ("to_review", "to_handle", "ignored", "handled", "all")
# The decisions both streams take (``responses.information.DECISIONS`` and
# ``responses.limitation_review.DECISIONS``), the same way.
DECIDE_ACTIONS = ("accept", "ignore", "back_to_review", "handled")
# The longest item id the page draws (``review_worklist._item``).
MAX_ITEM = 64


@dataclass(frozen=True)
class InformationList(ReadModel):
    """The Additional information worklist's requests, as identifiers only.

    ``stage`` is the page's Stage choice (``all`` for Every stage, which
    leaves History out) and ``requests`` each request at it: ``item_id``
    and ``version`` (what ``information decide`` takes), ``stage``,
    ``decision`` (in force, or None), ``disposition``, ``submitted_at``,
    ``decided_at`` and ``actions``. Never the Family, its words or the
    Staff notes.
    """

    campaign_id: UUID
    stage: str
    filters: dict
    requests: list


@dataclass(frozen=True)
class WorklistDecide(ReadModel):
    """The rows a ``<stream> decide`` recorded, or found for its key.

    ``created`` is false for a repeat; ``decisions`` lists each chosen
    item's recorded row in the order the items were given, never a note.
    """

    created: bool
    request_key: UUID
    decisions: list


def row_actions(stream, placement, mutable):
    """The decisions a reader may record on a row now, as its icons offer them.

    The pages offer every reader Staff's actions (Administrators and Staff
    decide alike), and none while other campaign work holds the campaign.
    """
    from .pipeline import allowed_actions
    from .web.review_worklist import ROW_ORDER

    if not mutable:
        return []
    allowed = allowed_actions(stream, placement.stage, frozenset({"staff"}))
    return [action.value for action in ROW_ORDER if action in allowed]


def mutable(campaign_id):
    """Whether a decision may be recorded now: the pages' own check."""
    from .reports.export_services import admit_campaign

    try:
        admit_campaign(campaign_id, mutating=True)
    except PermissionError:
        return False
    return True


# ------------------------------------------------------ information list


def information_query(stage, dates):
    """The worklist's query for ``stage`` and the page's date filters.

    ``dates`` holds ``start``, ``end`` and ``zone`` (None when not given);
    the page's own parser refuses a bad value (``invalid``).
    """
    from .reports.information_worklist import WorklistQuery

    values = {key: value for key, value in dates.items() if value is not None}
    return WorklistQuery.parse({"stage": stage, **values})


def information_filters(query):
    """The filters a document prints: the Stage and dates, never a search."""
    return {key: value for key, value in query.form_values().items() if key != "search"}


def _information_row(row, allowed):
    """One placed request as ``information list`` prints it: no content."""
    placement = row["placement"]
    return {
        "item_id": str(row["id"]),
        "version": row["version"],
        "stage": placement.stage.value,
        "decision": placement.decision.value if placement.decision else None,
        "disposition": row["disposition"],
        "submitted_at": row["submitted_at"],
        "decided_at": row["decided_at"],
        "actions": allowed(placement),
    }


def list_information(caller, service, *, stage, dates):
    """``information list``: the worklist's requests at ``stage``, as ids.

    Admits passively with the page's own admission (Staff and
    Administrators), reads through the shared report read and the page's
    ``requests``, and records the page's ``information_viewed`` (started,
    then succeeded or failed).
    """
    from .admin_report_reads import _report
    from .pipeline import Stream
    from .reports.information_views import _audit, _principal
    from .reports.information_worklist import requests

    query = information_query(stage, dates)

    def read(guard, actor, campaign_id):
        """The page's placed rows at the Stage, each as identifiers."""
        _metadata, rows, _counts = requests(campaign_id, query)
        writable = mutable(campaign_id)

        def allowed(placement):
            """The row's offered decisions."""
            return row_actions(Stream.ADDITIONAL_INFORMATION, placement, writable)

        model = InformationList(
            campaign_id=campaign_id,
            stage=query.stage,
            filters=information_filters(query),
            requests=[_information_row(row, allowed) for row in rows],
        )
        return model, None

    return _report(
        caller,
        service,
        principal=_principal,
        read=read,
        audit=lambda actor, campaign_id, outcome, values: _audit(
            actor, campaign_id, outcome
        ),
        started=True,
    )


# ------------------------------------------------------------- decisions


@dataclass(frozen=True)
class _Stream:
    """What differs between the two streams' ``decide`` commands.

    ``name`` is the command's area, ``items`` the plural the messages use
    and ``event`` the name of the command's own audit action.
    """

    name: str
    items: str
    event: str


INFORMATION = _Stream("information", "requests", "ADMIN_CMD_INFORMATION_DECIDE")
LIMITATIONS = _Stream("limitations", "answers", "ADMIN_CMD_LIMITATIONS_DECIDE")


def _principal(stream):
    """The stream's page admission, imported once Django is set up."""
    if stream is INFORMATION:
        from .reports.information_views import _principal as admission
    else:
        from .reports.limitation_views import _principal as admission
    return admission


def _admitter(stream):
    """The page's admission of a decision post, as ``scoped_write`` calls it."""
    from .admin_roster_tick import admit_with

    def admit(caller, store, *, final=False):
        """Administrators and Staff, recording activity as the post does."""
        return admit_with(_principal(stream), caller, store, final=final)

    return admit


def admit_decide(stream, caller, service):
    """The page's admission, passive: a ``decide`` command's role-first check.

    A Ministry leader is ``denied`` before any input or prompt. Records no
    activity.
    """
    return _admitter(stream)(caller, service.store, final=True)


def check_choice(stream, versions, note):
    """Refuse a decision the page could not post, before any prompt.

    At most the bulk bar's limit, each Limitations item an answer id the
    page draws, and a note no longer than the page's that the page's
    bounded text check takes. Returns ``versions`` keyed as the service
    takes them (request UUIDs, or each answer's canonical id).
    """
    from .admin_followup import InvalidFollowup, problem
    from .web.content import bounded_text
    from .web.review_worklist import MAX_BULK, MAX_NOTE

    if len(versions) > MAX_BULK:
        raise InvalidFollowup(
            [problem("item", f"Choose at most {MAX_BULK} {stream.items} at once.")]
        )
    if len(note) > MAX_NOTE:
        raise InvalidFollowup(
            [problem("note", f"Enter a note of at most {MAX_NOTE:,} characters.")]
        )
    try:
        bounded_text(note)
    except ValueError:
        raise InvalidFollowup(
            [problem("note", "The note is not valid text.")]
        ) from None
    if stream is INFORMATION:
        return dict(versions)
    from .responses.limitation_review import parse_answer

    try:
        return {parse_answer(item).id: version for item, version in versions.items()}
    except ValueError:
        raise InvalidFollowup(
            [problem("item", "Each item is an answer's item_id, as the export lists.")]
        ) from None


def _keys(versions, request_key):
    """Each chosen item's request key, as the services derive them."""
    from .responses.census_review import bulk_key_for

    if len(versions) == 1:
        return {item: request_key for item in versions}
    return {item: bulk_key_for(request_key, item) for item in versions}


def _information_rows(actor, campaign_id):
    """The actor's revisions in the campaign, as (pk, item id, key) triples."""
    from .responses.models import AdditionalInformationRevision

    return AdditionalInformationRevision.objects.filter(
        actor_id=actor.identity, item__submission__campaign_id=campaign_id
    ).values_list("pk", "item_id", "request_key")


def _limitation_rows(actor, campaign_id):
    """The actor's reviews in the campaign, as (pk, answer id, key) triples."""
    from .responses.limitation_review import answer_id
    from .responses.models import LimitationReview

    return [
        (pk, answer_id(duid, kind, member_key), key)
        for pk, duid, kind, member_key, key in LimitationReview.objects.filter(
            actor_id=actor.identity, family__campaign_id=campaign_id
        ).values_list("pk", "family__family_duid", "kind", "member_key", "request_key")
    ]


def _recorded(stream, action):
    """What a row of ``action`` stores: the fields a repeat must match."""
    from .pipeline import Action

    if stream is INFORMATION:
        from .responses.information import DECISIONS

        needed, completed, decision = DECISIONS[Action(action)][0]
        return {
            "review_decision": decision,
            "follow_up_needed": needed,
            "followed_up": completed,
        }
    from .responses.limitation_review import DECISIONS

    return {"decision": DECISIONS[Action(action)][0]}


def _model(stream):
    """The stream's decision row model."""
    from .responses.models import AdditionalInformationRevision, LimitationReview

    return AdditionalInformationRevision if stream is INFORMATION else LimitationReview


def _repeat(stream, actor, campaign_id, request_key, keys, action):
    """The rows an earlier run recorded with ``request_key``, or None for none.

    A key is recorded as itself (one item) or as each item's key derived
    from it (``bulk_key_for``, several), so every row of the actor in the
    campaign is matched against both: a key reused for other items, a
    different number of them or another decision is ``invalid``, never a
    second decision under the same key. A repeat with the same intent
    returns the rows it recorded, keyed by item.
    """
    from .admin_followup import InvalidFollowup, problem
    from .responses.census_review import bulk_key_for

    found_rows = (_information_rows if stream is INFORMATION else _limitation_rows)(
        actor, campaign_id
    )
    # One small read per run: an actor's decisions in one campaign are at
    # most a few per Family.
    matched = {
        pk: item
        for pk, item, key in found_rows
        if key in (request_key, bulk_key_for(request_key, item))
    }
    if not matched:
        return None
    found = {
        matched[row.pk]: row for row in _model(stream).objects.filter(pk__in=matched)
    }
    expected = _recorded(stream, action)
    if (
        {item: row.request_key for item, row in found.items()} != keys
        or len(matched) != len(keys)
        or any(
            getattr(row, field) != value
            for row in found.values()
            for field, value in expected.items()
        )
    ):
        raise InvalidFollowup(
            [problem("request_key", "This request key was used for another decision.")]
        )
    return found


def _decision_row(stream, row):
    """One recorded row as the document prints it: never a note.

    A Limitations row leaves out its item id, which names the Family's DUID;
    the rows follow the order the items were given.
    """
    if stream is INFORMATION:
        return {
            "id": row.pk,
            "item_id": row.item_id,
            "expected_version": row.expected_version,
            "decision": row.review_decision,
            "follow_up_needed": row.follow_up_needed,
            "followed_up": row.followed_up,
            "created_at": row.created_at,
        }
    return {
        "id": row.pk,
        "kind": row.kind,
        "expected_version": row.expected_version,
        "decision": row.decision,
        "created_at": row.created_at,
    }


def _found_items(stream, campaign_id, versions):
    """Refuse an item that is not the current campaign's (``not_available``).

    A request must be a Live one of the campaign; an answer's Family must
    be one of its Families, as each service checks under its locks.
    """
    from .campaigns.models import FamilyCampaign
    from .responses.models import AdditionalInformationItem

    if stream is INFORMATION:
        found = AdditionalInformationItem.objects.filter(
            pk__in=list(versions),
            submission__campaign_id=campaign_id,
            submission__mode="live",
        ).count()
        if found != len(versions):
            raise NotAvailable("No such information request.")
        return
    from .responses.limitation_review import parse_answer

    duids = {parse_answer(item).family_duid for item in versions}
    found = FamilyCampaign.objects.filter(
        campaign_id=campaign_id, family_duid__in=duids
    ).count()
    if found != len(duids):
        raise NotAvailable("No such Limitations answer.")


def _service(stream, store, campaign_id):
    """The page's own decision service for the stream."""
    if stream is INFORMATION:
        from .reports.information_worklist import InformationDecisions

        return InformationDecisions(store, campaign_id)
    from .reports.limitation_worklist import LimitationDecisions

    return LimitationDecisions(store, campaign_id)


def _decide(stream, service, actor, decision, note):
    """Record ``decision`` through the page's service, as the command reports it.

    Someone else's decision first is ``stale_version``; a reopening with no
    new note is ``invalid`` at ``note``, as the page's dialog requires one;
    a key the service finds bound to another decision ``invalid`` at
    ``request_key``.
    """
    from .admin_followup import InvalidFollowup, problem
    from .responses.census_review import KeyRebound
    from .responses.information import ReopenNeedsNote
    from .storage import StaleRecordError
    from .web.review_worklist import StaleDecision

    reopen = "This item is Ignored; give a new --note saying why it is reopened."
    try:
        service.decide(actor, decision)
    except StaleDecision:
        raise StaleRecordError(f"The {stream.items} were changed first.") from None
    except KeyRebound:
        raise InvalidFollowup(
            [problem("request_key", "This request key was used for another decision.")]
        ) from None
    except ReopenNeedsNote:
        raise InvalidFollowup([problem("note", reopen)]) from None
    except ValueError:
        # Limitations refuses a reopening with no note as a plain
        # ValueError; every other input was checked before, so only an
        # empty note can be refused here.
        if stream is LIMITATIONS and not note:
            raise InvalidFollowup([problem("note", reopen)]) from None
        raise


def decide(stream, caller, service, versions, *, action, note, request_key, context):
    """``<stream> decide``: one decision on one or several items, all or none.

    ``versions`` maps each chosen item (a request's id, or an answer's item
    id) to the version its row shows (``information list``'s or ``export
    limitations --format jsonl``'s ``version``). In the page's command
    scope the current campaign is checked as the page does, every item must
    be the campaign's (``not_available`` otherwise), and the page's service
    records the decision. A new key records ``admin_cmd_<stream>_decide``
    once; a repeat returns the rows the key recorded.
    """
    from .admin_followup import _record_command
    from .admin_report_reads import _current, _refusal, _refusals
    from .admin_roster_tick import scoped_write
    from .audit.schemas import Action as AuditAction
    from .campaigns.work_locks import require_work_order
    from .pipeline import Action
    from .reports.read_admission import admit_report_read
    from .web.review_worklist import WorklistDecision

    versions = check_choice(stream, versions, note)
    admit = _admitter(stream)
    actor = admit(caller, service.store)
    context["request_id"] = str(request_key)
    keys = _keys(versions, request_key)
    decision = WorklistDecision(
        Action(action),
        {str(item): version for item, version in versions.items()},
        note,
        request_key,
        bulk=len(versions) > 1,
    )

    def step(actor, written):
        """The page's decision, with the command's event for a new key."""
        campaign_id = _current()
        try:
            admit_report_read(campaign_id)
        except _refusals() as error:
            _refusal(caller, service, actor, error)
        _found_items(stream, campaign_id, versions)
        # The scope's work order serializes repeats of one key (#1222).
        require_work_order()
        found = _repeat(stream, actor, campaign_id, request_key, keys, action)
        if found is None:
            _decide(
                stream,
                _service(stream, service.store, campaign_id),
                actor,
                decision,
                note,
            )
            _record_command(getattr(AuditAction, stream.event), actor, caller, written)
            rows = _model(stream).objects.filter(
                actor_id=actor.identity, request_key__in=list(keys.values())
            )
            by_key = {row.request_key: row for row in rows}
            found = {item: by_key[key] for item, key in keys.items()}
        return WorklistDecide(
            created=bool(written),
            request_key=request_key,
            decisions=[_decision_row(stream, found[item]) for item in versions],
        )

    return scoped_write(caller, service, admit, actor, context, step)
