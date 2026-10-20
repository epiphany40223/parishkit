"""The review pipeline's decisions on Ministry requests (#1684).

Ministry requests is the Ministry stream's review worklist
([review pipeline](docs/specs/stewardship/review-pipeline/spec.md)). Each
decision a row icon, the bulk bar or Add sends (``web.review_worklist``'s
``WorklistDecision``) is recorded here, as the stream's ``DecisionService``:

- Accept resolves a join as joined and a leave as leave confirmed; Ignore
  as declined (a leave's "Stayed in the Ministry").
- Edit accepts with another Ministry carried forward
  (``reviewed_ministry_duid``); the Family's stays as answered.
- Back to review returns an accepted or ignored request to In progress, or
  withdraws (cancels) a request the parish added. On a terminal stage it
  undoes only the terminal step, with a required note (#1840): Entered by
  hand or Confirmed keeps the decision (the tick, if set, is cleared, and
  the request's version moves past any refresh check), so the request is To
  enter by hand again; a request the refresh resolved goes back to To
  review (``_undo``).
- Entered by hand ticks Entered in ParishSoft (``roster``), Staff only.
- Add creates a request the Family did not ask for on that Family's latest
  live response, then accepts it.

Every decision is one revision of its request, written through
``followup.write_revision`` with its own audit event, under the shared work
order with the requests locked first; a bulk decision is all or none. What
each reader may do is the pipeline's role-action table
(``pipeline.allowed_actions``) at the request's current stage, and the SQL
guards (migrations 0131 and 0212) check it all again. The stage is the pipeline's
mapping (``ministry_placement``) of the locked row, so a decision against a
row someone else changed first is a ``StaleDecision``, never a guess.
"""

from dataclasses import replace
from types import SimpleNamespace
from uuid import UUID

from django.db import IntegrityError, connection, transaction
from django.db.models import Max, Q

from parishkit.stewardship.accounts.policy import Capability, allows, current_principal
from parishkit.stewardship.audit.schemas import Action as AuditAction
from parishkit.stewardship.audit.schemas import ActorKind, Outcome
from parishkit.stewardship.audit.services import record_action
from parishkit.stewardship.campaigns.models import Campaign
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.observability import bound_correlation, correlation
from parishkit.stewardship.pipeline import (
    ROLES,
    TERMINAL_STAGES,
    Action,
    Decision,
    Stage,
    Stream,
    allowed_actions,
    ministry_placement,
    undo_needs_note,
)
from parishkit.stewardship.reports.export_services import admit_campaign
from parishkit.stewardship.responses.models import Submission
from parishkit.stewardship.storage import StaleRecordError
from parishkit.stewardship.web.review_worklist import WORDS, StaleDecision

from . import roster
from .followup import (
    MemberNotInSource,
    _latest_notes,
    _system,
    _targets,
    authorize_ministry,
    bulk_key_for,
    write_revision,
)
from .models import (
    ACCEPTED_OUTCOMES,
    MinistryRequest,
    MinistryRosterEntry,
    MinistryWorkflowRevision,
    RefreshCheck,
)

# What Accept records for each kind of request, and what Ignore records.
ACCEPT_OUTCOME = {"join": "joined", "leave": "leave_confirmed"}
IGNORE_OUTCOME = "declined"
# What a replayed decision must ask the same (its revision's own fields);
# notes and contacts recorded since may differ.
REPLAYED = (
    "expected_version",
    "state",
    "outcome",
    "reviewed_ministry_duid",
    "decision_note",
)
MAX_NOTE = 2000


class DecisionRefusal(ValueError):
    """A decision the reader can correct, in plain words for its dialog.

    Add's checks (a DUID ParishSoft's data does not hold, a Family with no
    response, a request that already exists) raise it; the view answers 400
    with the message, which the dialog shows below its buttons.
    """

    def __init__(self, code, message):
        super().__init__(code)
        self.code, self.message = code, message


def reader_roles(principal):
    """The pipeline roles a principal holds (an Administrator holds all three)."""
    return frozenset(principal.roles) & frozenset(ROLES)


def leads(principal, ministry_duid):
    """Whether the reader may decide on a request about ``ministry_duid``."""
    return allows(principal, Capability.MINISTRY_FOLLOWUP, ministry_id=ministry_duid)


def placement(
    *,
    state,
    outcome,
    source_resolved,
    roster_entered,
    reviewed,
    added,
    confirmed=False,
):
    """A request's stage, with the decision the pipeline words it by.

    ``pipeline.ministry_placement`` knows only the stored workflow; an
    accepted request with an Edit's Ministry is Accepted (edited), and one
    the parish added is Added by the parish (spec, "Reviewed stage").
    ``confirmed`` is whether its latest refresh check Confirmed it (#1689).
    """
    found = ministry_placement(
        state="new" if state == "assigned" else state,
        outcome=outcome,
        source_resolved=source_resolved,
        roster_entered=roster_entered,
        confirmed=confirmed,
    )
    if found.decision is Decision.ACCEPTED and added:
        return replace(found, decision=Decision.ADDED)
    if found.decision is Decision.ACCEPTED and reviewed:
        return replace(found, decision=Decision.ACCEPTED_EDITED)
    return found


def _locked_placement(target):
    """The stage of a locked request, its roster tick and checks read under the lock.

    A request a refresh check Confirmed at its version (#1689) is Confirmed:
    Back to review undoes that (#1840), advancing the version past the
    check, which its revision guard reads too.
    """
    latest = (
        MinistryRosterEntry.objects.filter(request_id=target.pk)
        .order_by("-sequence")
        .first()
    )
    return placement(
        state=target.state,
        outcome=target.outcome,
        source_resolved=target.resolution_source_id is not None,
        roster_entered=bool(latest and latest.entered),
        reviewed=target.reviewed_ministry_duid is not None,
        added=target.parish_added,
        confirmed=RefreshCheck.objects.filter(
            request_id=target.pk, item_version=target.version, outcome="confirmed"
        ).exists(),
    )


def _stale(target):
    """A StaleRecordError naming ``target``, so a bulk notice names its row."""
    error = StaleRecordError("The Ministry request changed.")
    error.request_id = target.pk
    return error


def _later_revision(target):
    """Whether a later live response of the Family replaced ``target``.

    The worklist's order (``ministry_followup.sql``, ``revision``): the
    latest revision of a Member's request for a Ministry is the one from the
    Family's highest ``family_version`` (unique per Family and mode).
    """
    submission = target.submission
    return MinistryRequest.objects.filter(
        submission__family_id=submission.family_id,
        submission__campaign_id=submission.campaign_id,
        submission__mode="live",
        submission__family_version__gt=submission.family_version,
        entity_kind=target.entity_kind,
        entity_key=target.entity_key,
        ministry_duid=target.ministry_duid,
    ).exists()


def _ids(versions):
    """The posted item ids as request UUIDs (a malformed one is a 400)."""
    return {UUID(identity): version for identity, version in versions.items()}


def _ministry(fields, name, campaign_id):
    """A posted Ministry number the campaign asks about, else ValueError."""
    values = fields.get(name) or []
    if len(values) != 1 or not values[0].isascii() or not values[0].isdecimal():
        raise ValueError("Choose a Ministry.")
    duid = int(values[0])
    if duid not in campaign_ministries(campaign_id):
        raise ValueError("That Ministry is not part of this campaign.")
    return duid


def campaign_ministries(campaign_id):
    """The Ministry numbers the campaign asks about, from its configuration."""
    configuration = (
        Campaign.objects.select_related("active_configuration")
        .get(pk=campaign_id)
        .active_configuration
    )
    return frozenset(int(duid) for duid in configuration.values["ministry_duids"])


# The Ministry actions whose decision records no note: a roster tick has
# none, so its worklist dialog shows none (``web.review_worklist.build``'s
# ``silent``) and ``MinistryDecisions`` refuses one.
SILENT = frozenset({Action.ENTERED_BY_HAND})


class MinistryDecisions:
    """The Ministry stream's ``DecisionService`` for one campaign's worklist.

    ``store`` is the policy store the actor is reloaded from; ``namer``
    names one request as its row does ("Smith, Ann — Choir"), for the
    notice, or returns None when it cannot.
    """

    def __init__(self, store, campaign_id, *, namer=None):
        self.store = store
        self.campaign_id = campaign_id
        self.namer = namer or (lambda request_id: None)

    def decide(self, principal, decision):
        """Record ``decision`` for ``principal``; the notice the page shows.

        A request someone else changed first (its version, its roster tick,
        or its Member leaving ParishSoft) is a ``StaleDecision`` naming it,
        and nothing is recorded; a request outside the reader's scope is a
        PermissionError; anything the request's stage does not offer, or a
        malformed field, is a ValueError.
        """
        actor = principal.identity
        if len(decision.note) > MAX_NOTE:
            raise ValueError("A decision note is too long.")
        # A roster tick records no note (its dialog shows none, ``SILENT``).
        if decision.action in SILENT and decision.note:
            raise ValueError("Entered by hand takes no note.")
        try:
            if decision.action is Action.ADD:
                return self._add(actor, decision)
            return self._decide(actor, decision)
        except (MemberNotInSource, StaleRecordError) as error:
            request_id = getattr(error, "request_id", None)
            raise StaleDecision(
                self.namer(request_id) if request_id else None
            ) from None

    def _decide(self, actor, decision):
        """Record one decision on every chosen request, all or none."""
        versions = _ids(decision.versions)
        with correlation(bound_correlation()), work_transaction():
            principal = current_principal(self.store, actor)
            roles = reader_roles(principal)
            try:
                # The roster tick is recorded for a Member no longer in
                # ParishSoft too: the roster work may still be real.
                targets = _targets(
                    self.store,
                    actor,
                    versions,
                    gone_ok=decision.action is Action.ENTERED_BY_HAND,
                )
            except PermissionError:
                # A replay, or a post outdated by someone else's decision,
                # can find the reader no longer leads the request's current
                # Ministry (a Back to review returns an edited request to
                # the Family's): that is a change since the page, not a
                # denial. Only for a reader who could have seen each request
                # (one of its Ministries, an Edit's included), so a request
                # never in their scope still reads as unknown.
                found = list(MinistryRequest.objects.filter(pk__in=versions))
                if len(found) != len(versions) or not all(
                    _ever_led(principal, row) for row in found
                ):
                    raise
                if self._replayed(actor, decision, versions):
                    return self._notice(decision, list(versions))
                if any(versions[row.pk] != row.version for row in found):
                    raise StaleRecordError("A Ministry request changed.") from None
                raise
            if any(t.submission.campaign_id != self.campaign_id for t in targets):
                raise PermissionError("A Ministry request is unavailable.")
            system = _system()
            notes = _latest_notes(versions)
            written = 0
            for target in targets:
                key = (
                    bulk_key_for(decision.key, target.pk)
                    if decision.bulk
                    else decision.key
                )
                written += self._one(
                    principal,
                    roles,
                    target,
                    versions[target.pk],
                    key,
                    decision,
                    notes[target.pk],
                    system,
                )
            if decision.bulk and written:
                record_action(
                    AuditAction.MINISTRY_FOLLOWUP_BULK_APPLIED,
                    actor_kind=ActorKind.PORTAL_USER,
                    actor_id=actor,
                    subject_id=self.campaign_id,
                    parish_id=system.active_configuration.parish.pk,
                    campaign_id=self.campaign_id,
                    context={
                        "outcome": Outcome.CHANGED,
                        "count": written,
                        "matching_count": len(versions),
                    },
                )
        return self._notice(decision, targets)

    def _replayed(self, actor, decision, versions):
        """Whether this actor already recorded every request of ``decision``."""
        keys = [
            bulk_key_for(decision.key, request) if decision.bulk else decision.key
            for request in versions
        ]
        written = (
            MinistryRosterEntry
            if decision.action is Action.ENTERED_BY_HAND
            else MinistryWorkflowRevision
        )
        found = written.objects.filter(actor_id=actor, request_key__in=keys)
        return found.count() == len(versions)

    def _one(self, principal, roles, target, version, key, decision, notes, system):
        """Record ``decision`` on one locked request; 1 if written, 0 on replay.

        The action must be one the request's current stage offers this
        reader (the pipeline's table): the page offered it, so a request
        whose stage changed since is stale. A replayed key returns without
        writing, as long as it meant the same.
        """
        action = decision.action
        current = _locked_placement(target)
        offered = allowed_actions(
            Stream.MINISTRY,
            current.stage,
            roles,
            leads=leads(principal, target.current_ministry_duid),
        )
        if action is Action.ENTERED_BY_HAND:
            return self._entered(principal, target, version, key, current, offered)
        previous = target.revisions.filter(
            actor_id=principal.identity, request_key=key
        ).first()
        if previous is not None:
            # A replay: the revision this key wrote, compared with what the
            # post asks, before any rule about the request as it is now
            # (which that revision itself changed).
            # Notes and contacts recorded since may differ; the decision
            # itself must not.
            # A Back to review that kept the request resolved undid a
            # terminal step (#1840), so its intent keeps the decision.
            intent = self._intent(
                previous.request,
                version,
                decision,
                notes,
                kept=previous.state == "resolved",
            )
            if any(getattr(previous, name) != intent[name] for name in REPLAYED):
                raise ValueError("This request key is already bound.")
            return 0
        # A later response replaced this revision (the worklist's latest
        # False): only its roster tick is still offered, so any other
        # decision on it is a forged or outdated post.
        if (
            target.version != version
            or action not in offered
            or _later_revision(target)
        ):
            raise _stale(target)
        if (
            action is Action.BACK_TO_REVIEW
            and undo_needs_note(Stream.MINISTRY, current.stage)
            and not decision.note.strip()
        ):
            if current.stage is Stage.IGNORED:
                raise ValueError("Reopening an ignored request needs a note.")
            raise ValueError(f"Undoing {current.stage.label} needs a note.")
        if action is Action.BACK_TO_REVIEW and current.stage in TERMINAL_STAGES:
            _undo(principal.identity, target, version, key, decision.note, current)
            return 1
        intent = self._intent(target, version, decision, notes)
        if action is Action.EDIT:
            authorize_ministry(
                self.store, principal.identity, intent["reviewed_ministry_duid"]
            )
            if _current_request(target, intent["reviewed_ministry_duid"], target.pk):
                raise DecisionRefusal(
                    "edit_exists",
                    "This Member already has a request for that Ministry. Use "
                    "that request's own icons instead.",
                )
        write_revision(principal.identity, target, intent, key, system)
        return 1

    def _intent(self, target, version, decision, notes, *, kept=False):
        """The resulting workflow a decision records on ``target``.

        Notes carry forward unchanged (they are the request page's); the
        decision's own note is ``decision_note``. Back to review reopens a
        request as In progress, or withdraws one the parish added (the note
        an ignored request's reopening needs is checked by the caller, on
        the locked stage); ``kept`` is Back to review of a terminal step,
        which keeps the decision in force (``undo_intent``).
        """
        action = decision.action
        if action is Action.BACK_TO_REVIEW and kept:
            return undo_intent(target, version, decision.note, notes, kept=True)
        state, outcome, reviewed = "resolved", None, None
        if action is Action.ACCEPT:
            outcome = ACCEPT_OUTCOME[target.action]
        elif action is Action.IGNORE:
            outcome = IGNORE_OUTCOME
        elif action is Action.EDIT:
            outcome = ACCEPT_OUTCOME[target.action]
            reviewed = _ministry(decision.fields, "value", self.campaign_id)
            if reviewed == target.ministry_duid:
                raise ValueError("Choose another Ministry to edit into.")
        elif action is Action.BACK_TO_REVIEW:
            state = "cancelled" if target.parish_added else "in_progress"
        else:
            raise ValueError("Ministry requests do not take that decision.")
        return {
            "request_id": target.pk,
            "expected_version": version,
            "state": state,
            "outcome": outcome,
            "notes": notes,
            "contact_channel": None,
            "contact_at": None,
            "contact_notes": "",
            "reviewed_ministry_duid": reviewed,
            "decision_note": decision.note,
        }

    def _entered(self, principal, target, version, key, current, offered):
        """Tick one accepted request Entered in ParishSoft (Staff only).

        The row's version is the request's; the tick's own sequence is read
        under the lock, so a tick someone else set first is stale rather
        than set twice.
        """
        if not roster.can_tick(principal):
            raise PermissionError("The roster tick is unavailable.")
        previous = MinistryRosterEntry.objects.filter(
            actor_id=principal.identity, request_key=key
        ).first()
        if previous is not None:
            if previous.request_id != target.pk or not previous.entered:
                raise ValueError("This request key is already bound.")
            return 0
        if target.version != version or Action.ENTERED_BY_HAND not in offered:
            raise _stale(target)
        latest = MinistryRosterEntry.objects.filter(request_id=target.pk).aggregate(
            sequence=Max("sequence")
        )["sequence"]
        roster._record(
            principal.identity,
            target,
            sequence=(latest or 0) + 1,
            entered=True,
            request_key=key,
            system=_system(),
        )
        return 1

    def _notice(self, decision, targets):
        """What the page announces: the request's name, or how many.

        ``targets`` are the requests, or their ids for a replay.
        """
        done = WORDS[decision.action].done
        if len(targets) == 1:
            name = self.namer(getattr(targets[0], "pk", targets[0])) or "The request"
            return f"{name} {done}"
        return f"{len(targets)} requests {done}"

    def _add(self, actor, decision):
        """Add a request the Family did not ask for, accepted (Staff only).

        Fields: ``value_member`` (the Member's DUID), ``value_ministry`` (a
        campaign Ministry), ``value_action`` (join or leave) and, optionally,
        ``value_in_place_of`` (the same Member's ignored request). The
        request goes on the Family's latest live response that lists the
        Member, then its Accept is recorded as its first revision. A
        repeated key replays: nothing more is written.
        """
        fields = decision.fields
        member = _member_key(fields)
        ministry = _ministry(fields, "value_ministry", self.campaign_id)
        kind = (fields.get("value_action") or [""])[0]
        if kind not in ACCEPT_OUTCOME or len(fields.get("value_action")) != 1:
            raise ValueError("Choose Join or Leave.")
        replaced = (fields.get("value_in_place_of") or [""])[0]
        with correlation(bound_correlation()), work_transaction():
            principal = current_principal(self.store, actor)
            if not allows(principal, Capability.MINISTRY_FOLLOWUP):
                raise PermissionError("Only Staff add a Ministry request.")
            admit_campaign(self.campaign_id, mutating=True)
            previous = (
                MinistryRequest.objects.filter(
                    revisions__actor_id=actor, revisions__request_key=decision.key
                )
                .select_related("submission")
                .first()
            )
            if previous is not None:
                same = (previous.entity_key, previous.ministry_duid, previous.action)
                if same != (member, ministry, kind):
                    raise ValueError("This request key is already bound.")
                return f"{self.namer(previous.pk) or 'The request'} was already added."
            response = _latest_response(self.campaign_id, member)
            _still_listed(response, member)
            if _current_request(
                SimpleNamespace(
                    submission=response, entity_kind="member", entity_key=member
                ),
                ministry,
            ):
                raise DecisionRefusal(
                    "add_exists",
                    "This Member already has a request for that Ministry. Use "
                    "that request's own icons instead.",
                )
            in_place_of = _replaced(response, member, replaced)
            try:
                with transaction.atomic():
                    request = MinistryRequest.objects.create(
                        submission=response,
                        entity_kind="member",
                        entity_key=member,
                        ministry_duid=ministry,
                        action=kind,
                        state="new",
                        actor_id=actor,
                        parish_added=True,
                        added_in_place_of=in_place_of,
                    )
            except IntegrityError as error:
                # Only the request identity's uniqueness: a withdrawn request
                # for this Member and Ministry already sits on that response.
                # Any other refusal is the guard's, and not the reader's to fix.
                if getattr(error.__cause__, "sqlstate", None) != "23505":
                    raise
                raise DecisionRefusal(
                    "add_withdrawn",
                    "A request for this Member and Ministry was added on the "
                    "Family's latest response and withdrawn, so it cannot be "
                    "added there again. Record it in ParishSoft directly.",
                ) from None
            (target,) = _targets(self.store, actor, {request.pk: request.version})
            write_revision(
                actor,
                target,
                {
                    "request_id": target.pk,
                    "expected_version": target.version,
                    "state": "resolved",
                    "outcome": ACCEPT_OUTCOME[kind],
                    "notes": "",
                    "contact_channel": None,
                    "contact_at": None,
                    "contact_notes": "",
                    "reviewed_ministry_duid": None,
                    "decision_note": decision.note,
                },
                decision.key,
                _system(),
            )
        return f"{self.namer(request.pk) or 'The request'} added."


def undo_intent(target, version, note, notes, *, kept):
    """The revision Back to review of a terminal step records (#1840).

    It removes only the terminal step: a request a person decided
    (``kept``) stays resolved with its outcome and Edit, so the revision
    only advances its version past any refresh check of the step; one the
    refresh resolved, with no decision behind it, goes back to In progress
    (To review). The note is required, and is the revision's own.
    """
    return {
        "request_id": target.pk,
        "expected_version": version,
        "state": "resolved" if kept else "in_progress",
        "outcome": target.outcome if kept else None,
        "notes": notes,
        "contact_channel": None,
        "contact_at": None,
        "contact_notes": "",
        "reviewed_ministry_duid": target.reviewed_ministry_duid if kept else None,
        "decision_note": note,
    }


def _undo(actor_id, target, version, key, note, current):
    """Back to review of a locked request's terminal step; the roster entry or None.

    ``current`` is its locked placement (Entered by hand or Confirmed). The
    revision (``undo_intent``) is written first, then, if the request is
    ticked Entered in ParishSoft, the tick is cleared by a roster entry
    under the same request key, which the roster guard pairs with that
    revision (migration 0212); all in the caller's transaction.
    """
    system = _system()
    notes = _latest_notes([target.pk])[target.pk]
    kept = current.decision is not None
    write_revision(
        actor_id,
        target,
        undo_intent(target, version, note, notes, kept=kept),
        key,
        system,
    )
    latest = (
        MinistryRosterEntry.objects.filter(request_id=target.pk)
        .order_by("-sequence")
        .first()
    )
    if not (latest and latest.entered):
        return None
    return roster._record(
        actor_id,
        target,
        sequence=latest.sequence + 1,
        entered=False,
        request_key=key,
        system=system,
    )


def undo_entered(store, actor_id, request_id, *, sequence, note, request_key):
    """The request page's Clear Entered in ParishSoft: Back to review of the tick.

    The same undo as the worklist's Back to review on Entered by hand
    (#1840), with its required note: Staff and Administrators only, and,
    as the tick itself, for a Member no longer in ParishSoft too.
    ``sequence`` is the clearing entry's sequence as the page saw it (the
    tick's current sequence plus one), so a page drawn before someone
    else's change is stale. The same ``request_key`` again returns the
    first entry; with another intent it is refused. Returns the entry.
    """
    if (
        not isinstance(actor_id, UUID)
        or not isinstance(request_id, UUID)
        or not isinstance(request_key, UUID)
        or type(sequence) is not int
        or not 1 <= sequence < 2**63 - 1
        or not isinstance(note, str)
        or len(note) > MAX_NOTE
    ):
        raise ValueError("Invalid roster entry.")
    with correlation(bound_correlation()), work_transaction():
        # The role first: a Ministry leader is refused before the note.
        if not roster.can_tick(current_principal(store, actor_id)):
            raise PermissionError("The roster tick is unavailable.")
        if not note.strip():
            raise ValueError("Undoing Entered by hand needs a note.")
        (target,) = _targets(store, actor_id, {request_id: None}, gone_ok=True)
        previous = MinistryRosterEntry.objects.filter(
            actor_id=actor_id, request_key=request_key
        ).first()
        if previous is not None:
            if (previous.request_id, previous.sequence, previous.entered) != (
                target.pk,
                sequence,
                False,
            ):
                raise ValueError("This request key is already bound.")
            return previous
        if not roster.eligible(target):
            raise ValueError("This request does not take the roster tick.")
        latest = (
            MinistryRosterEntry.objects.filter(request_id=target.pk)
            .order_by("-sequence")
            .first()
        )
        if sequence != (latest.sequence if latest else 0) + 1:
            raise StaleRecordError("The roster tick changed.")
        if not (latest and latest.entered):
            raise ValueError("The roster tick already says so.")
        return _undo(
            actor_id,
            target,
            target.version,
            request_key,
            note,
            _locked_placement(target),
        )


def _ever_led(principal, request):
    """Whether the reader leads the request's Ministry or any Edit's it had."""
    ministries = {request.ministry_duid} | set(
        request.revisions.exclude(reviewed_ministry_duid=None).values_list(
            "reviewed_ministry_duid", flat=True
        )
    )
    return any(leads(principal, ministry) for ministry in ministries)


def _current_request(target, ministry, exclude=None):
    """Whether the Member already has a current request for ``ministry``.

    Current means not cancelled or replaced. A request counts for both the
    Family's Ministry and its Edit's (Back to review returns it to the
    first), so an Edit, an Add or a Back to review never leaves two
    requests about one Member and Ministry (#1684). The migration's guards
    check the same.
    """
    found = MinistryRequest.objects.filter(
        submission__family_id=target.submission.family_id,
        submission__campaign_id=target.submission.campaign_id,
        submission__mode="live",
        entity_kind=target.entity_kind,
        entity_key=target.entity_key,
    ).exclude(state__in=("cancelled", "superseded"))
    if exclude is not None:
        found = found.exclude(pk=exclude)
    # A request holds both the Family's Ministry and its Edit's, since Back
    # to review can return it to the first.
    return found.filter(
        Q(reviewed_ministry_duid=ministry) | Q(ministry_duid=ministry)
    ).exists()


def _still_listed(response, member):
    """Refuse an Add for a Member ParishSoft no longer lists in the Family.

    The same standing the worklist filters on
    (``stewardship_member_source_status_v1``, #1217), read from the current
    ParishSoft data, so Add never makes a request nobody may act on.
    """
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT stewardship_member_source_status_v1("
            "(SELECT snapshot_id FROM stewardship_source_current),%s,f.family_duid) "
            "FROM stewardship_family_campaign f WHERE f.id=%s",
            [member, response.family_id],
        )
        found = cursor.fetchone()
    if found is None or found[0] != "current":
        raise DecisionRefusal(
            "add_not_in_parishsoft",
            "ParishSoft's data no longer lists that Member in their Family, or "
            "lists them as inactive or deceased, so no request can be added.",
        )


def _member_key(fields):
    """The posted Member DUID as the request's entity key, else a refusal."""
    values = fields.get("value_member") or []
    text = values[0].strip() if len(values) == 1 else ""
    if not text.isascii() or not text.isdecimal() or not 0 < int(text) < 2**31:
        raise DecisionRefusal(
            "add_member", "Enter the Member's DUID, a number from ParishSoft."
        )
    return str(int(text))


def _latest_response(campaign_id, member):
    """The latest live response, of its Family, that lists Member ``member``.

    A request belongs to a Family's response, so Add needs one: the newest
    response of each Family is considered, and exactly one Family's must
    list the Member. None, or several (a Member listed by two Families),
    is a refusal that says what to do.
    """
    listing = (
        Submission.objects.filter(
            campaign_id=campaign_id,
            mode="live",
            answers__members__has_key=member,
        )
        .order_by("family_id", "-family_version")
        .distinct("family_id")
    )
    latest = []
    for response in listing:
        newest = (
            Submission.objects.filter(
                campaign_id=campaign_id, mode="live", family_id=response.family_id
            )
            .order_by("-family_version")
            .first()
        )
        if newest.pk == response.pk:
            latest.append(response)
    if not latest:
        raise DecisionRefusal(
            "add_no_response",
            "No Family response in this campaign lists that Member DUID. A "
            "request can be added only for a Member of a Family that responded.",
        )
    if len(latest) > 1:
        raise DecisionRefusal(
            "add_two_families",
            "More than one Family's response lists that Member DUID, so the "
            "request cannot be added here. Record it in ParishSoft directly.",
        )
    return latest[0]


def _replaced(response, member, value):
    """The same Member's ignored request an addition replaces, or None."""
    if not value:
        return None
    try:
        identity = UUID(value)
    except ValueError:
        raise ValueError("Invalid request to add in place of.") from None
    found = (
        MinistryRequest.objects.filter(
            pk=identity,
            submission__family_id=response.family_id,
            submission__campaign_id=response.campaign_id,
            submission__mode="live",
            entity_kind="member",
            entity_key=member,
        )
        .only("state", "outcome", "ministry_duid", "parish_added")
        .first()
    )
    ignored = found is not None and (
        found.state == "closed_no_response"
        or (found.state == "resolved" and found.outcome not in ACCEPTED_OUTCOMES)
    )
    if not ignored:
        raise DecisionRefusal(
            "add_in_place_of",
            "The request to add in place of must be an ignored request of the "
            "same Member.",
        )
    return found


def ministry_placement_of(row, marks, checks=None):
    """A follow-up selection row's placement, from its fields, tick and checks.

    ``checks`` are the rows' refresh check states (``responses.
    refresh_checks.check_states``), when the caller read them.
    """
    current = marks.get(str(row["id"]), {})
    check = (checks or {}).get(str(row["id"]))
    return placement(
        confirmed=bool(check and check.outcome == "confirmed"),
        state=row["state"],
        outcome=row["outcome"],
        source_resolved=bool(row["source_resolved"]),
        roster_entered=bool(current.get("entered")),
        reviewed=row.get("reviewed_ministry_duid") is not None,
        added=bool(row.get("parish_added")),
    )
