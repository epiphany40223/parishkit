"""The reviewed Family form (#1705): what a live form shows, recorded and read back.

A live Family form shows each item's **shown value** (review pipeline spec,
"Reviewed Family form"): the Family's own answer, the reviewer's value or
the current baseline, by the item's stage (``pipeline.shown_value``). When
the form is issued, its baseline records one **shown item** per census,
Ministry or pledge item in force (``shown_items``), and the submit judges
each item against the value the form showed, read back from the immutable
rows the entry names, never recomputed from the item's state at submit:

- a census entry names the ``ProposedChange`` and, for a reviewer's value,
  the ``ProposalReview`` that set it;
- a Ministry entry names the ``MinistryRequest`` and, for an Edit, the
  ``MinistryWorkflowRevision`` that carried another Ministry forward;
- the pledge entry names the Family's current live response and, for an
  edited pledge, the ``PledgeReview`` that recorded the reviewer's value.

No value is copied into an entry. ``shown_items`` builds the entries when
a form is issued and ``carry_over`` keeps an untouched item's earlier entry
when a refresh replaces an open form; the ``*_draw`` functions draw the
form from the entries, and the ``*_shown`` functions read a shown value
back for the submit's comparison, so the form and the submit read the same
value. A Testing form is never drawn this way: Testing responses never
enter the pipeline (decision 3).
"""

from parishkit.stewardship.pipeline import (
    Shown,
    Stream,
    census_placement,
    shown_value,
)

from .census import FAMILY_FIELDS
from .comparison import ValueKind, canonical_value
from .effective import CLOSED_EXECUTIONS
from .member_census import MEMBER_FIELDS
from .member_requests import REQUEST_FIELDS
from .merge import EffectiveValue, KnownValue, MergeState
from .models import CensusAddition, ProposalReview, ProposedChange

CENSUS = Stream.CENSUS.value
MINISTRY = Stream.MINISTRY.value
FINANCIAL = Stream.FINANCIAL.value
# Executions that leave an item in history: it has no version in force.
HISTORY_EXECUTIONS = frozenset({"cancelled", "superseded"})
MINISTRY_HISTORY = frozenset({"cancelled", "superseded"})
# Each census field's comparison kind, by (entity, field).
KINDS = {
    **{("family", field.name): field.kind for field in FAMILY_FIELDS},
    **{
        ("member", field.name): field.kind
        for field in (*MEMBER_FIELDS, *REQUEST_FIELDS)
    },
    ("proposed_member", "new_member"): ValueKind.MEMBER,
}
# The fields a census change the parish added (#1710) is drawn in on the
# reviewed form (#2008): a Member's ordinary fields and the Family's
# addresses, every field census Add offers but a death date, which the form
# presents only inside a deceased request.
PARISH_FIELDS = frozenset(
    {("member", field.name) for field in MEMBER_FIELDS}
    | {("family", "home_address"), ("family", "mailing_address")}
)
# A census answer the response does not give (a field it does not present,
# a proposed Member it no longer lists), told apart from a known null.
ABSENT = object()


def census_key(entry):
    """A census entry's item identity: (entity, entity key, field)."""
    return (entry["entity"], entry["key"], entry["field"])


def ministry_key(entry):
    """A Ministry entry's item identity: (entity, entity key, the Family's Ministry)."""
    return (entry["entity"], entry["key"], entry["ministry"])


def entries_of(shown_items, stream):
    """The entries of one stream in a baseline's or response's ``shown_items``."""
    return [entry for entry in shown_items or () if entry["stream"] == stream]


def same(kind, left, right):
    """Whether two census values are the same by the field's canonical comparison."""
    return canonical_value(kind, left) == canonical_value(kind, right)


def census_answer(answers, entity, key, field):
    """A response's answer for one census item, or ``ABSENT`` when it gives none."""
    if entity == "family":
        return answers["family"].get(field, ABSENT)
    if entity == "proposed_member":
        return answers["proposed_members"].get(key, ABSENT)
    return (answers["members"].get(key) or {}).get(field, ABSENT)


# --- Census ----------------------------------------------------------------


def edit_review(row):
    """The review row whose Edit set ``row``'s reviewer's value, or None.

    Every review row records the change's whole resulting review state, so
    the latest one on the change holds its current reviewer's value. A
    same-answer successor made before the reviewed form carried its
    predecessor's value without a review of its own, so the search walks
    back along the replaced changes. Only an edited change is searched; it
    is rare, and each step is one query.
    """
    current = row
    while current is not None:
        review = (
            ProposalReview.objects.filter(proposal=current)
            .order_by("-expected_version")
            .first()
        )
        if review is not None:
            matches = review.admin_value_set and review.admin_value == row.admin_value
            return review if matches else None
        current = ProposedChange.objects.filter(superseded_by=current).first()
    return None


def census_entries(inputs, prior, family=None):
    """One shown item per census item in force for the Family's form.

    ``prior`` is the Family's latest live response. An item is a change of
    ``effective.proposal_index``'s selection that is not history; Confirmed
    and report-only changes record none (the form shows the plain baseline).
    A refresh check places a final change, as the Census changes page does.
    An Ignored item shows the baseline where the form shows it as an answer
    (a Member's field, request or New Member), else the Family's own answer.
    With ``family`` (the ``FamilyCampaign``), a field with a standing change
    the parish added has one more entry (``parish_entries``).
    """
    if prior is None or "census" not in inputs.modules:
        return []
    from .effective import proposal_index
    from .refresh_checks import FINAL_EXECUTIONS, check_states

    sources = {
        (field.entity, str(field.identity), field.field): field.source
        for field in inputs.fields
    }
    # A New Member entered in ParishSoft is not shown: ParishSoft lists the
    # Member itself (``AddedMemberLink``), so the form would show the person
    # twice; the Family's response leaves it out, as before the reviewed
    # form, and its refresh check judges it.
    rows = [
        row
        for row in proposal_index(prior).values()
        if row.execution not in HISTORY_EXECUTIONS
        and not (
            row.entity_kind == "proposed_member"
            and row.execution in ("published", "resolved_external")
        )
    ]
    final = [row for row in rows if row.execution in FINAL_EXECUTIONS]
    checks = check_states(
        proposals=[row.pk for row in final],
        versions={str(row.pk): row.version for row in final},
    )
    entries = []
    # Each field's change that is not Ignored, whatever its stage (a
    # Confirmed one records no shown item): a change the parish added is
    # not shown over one newer than it or still open (``parish_entries``).
    answered = {
        (row.entity_kind, row.entity_key, row.field): row
        for row in rows
        if row.decision != "ignored"
    }
    for row in rows:
        check = checks.get(str(row.pk))
        placement = census_placement(
            decision=row.decision,
            execution=row.execution,
            handling=row.handling,
            entity_kind=row.entity_kind,
            admin_value_set=row.admin_value_set,
            check=check.outcome if check else None,
        )
        if placement is None:
            continue
        source = sources.get(
            (row.entity_kind, row.entity_key, row.field), KnownValue(False)
        )
        showable = row.entity_kind == "proposed_member" or (
            row.entity_kind == "member" and source.available
        )
        found = shown_value(Stream.CENSUS, placement, baseline_shown=showable)
        if found is None:
            continue
        shown, decision = found.shown, None
        if shown is Shown.REVIEWER:
            review = edit_review(row)
            # A reviewer's value with no review row behind it cannot be read
            # back; the form shows the Family's answer rather than guess.
            if review is None:
                shown = Shown.FAMILY
            else:
                decision = str(review.pk)
        entries.append(
            {
                "stream": CENSUS,
                "entity": row.entity_kind,
                "key": row.entity_key,
                "field": row.field,
                "item": str(row.pk),
                "version": row.version,
                "decision": decision,
                "shown": shown.value,
            }
        )
    if family is not None:
        entries.extend(parish_entries(inputs, family, answered))
    return entries


def parish_entries(inputs, family, answered):
    """The shown items of a Family's standing census changes the parish added.

    One per field (#2008, review pipeline spec, "What the reviewed form
    shows"), naming the addition, for a field of ``PARISH_FIELDS`` the form
    presents: its open addition, else its newest Entered by hand one
    (``models.standing_additions``), which stands only while ParishSoft
    still has the value it had when the change was added (once that value
    changes, the form shows ParishSoft's; a Family address, which the form
    cannot read, keeps it). A field whose change in ``answered`` (each
    field's change that is not Ignored, whatever its stage) is newer than
    the addition, or still open, shows the Family's answer instead: the
    later one, or one reopened under an addition before census Back to
    review refused that (``census_resolution.covered_by_addition``), whose
    shown item an unchanged submit would otherwise lose. ``version`` and
    ``decision`` are null: an addition has no version a Family response
    keeps or replaces.
    """
    from .models import standing_additions

    sources = {
        (field.entity, str(field.identity), field.field): field
        for field in inputs.fields
        if (field.entity, field.field) in PARISH_FIELDS
    }
    standing = {}
    for addition in standing_additions(family):
        standing[(addition.entity_kind, addition.entity_key, addition.field)] = addition
    result = []
    for key, addition in standing.items():
        field = sources.get(key)
        change = answered.get(key)
        if field is None or (
            change is not None
            and (
                change.created_at > addition.created_at
                or change.execution not in CLOSED_EXECUTIONS
            )
        ):
            continue
        if not addition.is_open and not _unchanged_since(field, addition.baseline):
            continue
        result.append(
            {
                "stream": CENSUS,
                "entity": addition.entity_kind,
                "key": addition.entity_key,
                "field": addition.field,
                "item": str(addition.pk),
                "version": None,
                "decision": None,
                "shown": Shown.PARISH.value,
            }
        )
    return result


def _unchanged_since(field, recorded):
    """Whether ``field``'s ParishSoft value is still ``recorded``.

    ``recorded`` is ParishSoft's value of the field when a change was added
    to it (an addition's ``baseline``, ``{"available", "value"}``, or None
    where the source had no such field), compared by the field's canonical
    comparison; an unavailable value equals only an unavailable one.
    """
    before = KnownValue(
        bool(recorded and recorded["available"]),
        recorded["value"] if recorded else None,
    )
    now = field.source
    if not (before.available and now.available):
        return before.available == now.available
    return same(field.kind, before.value, now.value)


def census_rows(entries):
    """The changes and edit reviews the census ``entries`` name, by id text.

    An entry naming a change the parish added (``parish``) is left out:
    ``census_additions`` reads those.
    """
    entries = [entry for entry in entries if entry["shown"] != Shown.PARISH]
    rows = ProposedChange.objects.in_bulk([entry["item"] for entry in entries])
    reviews = ProposalReview.objects.in_bulk(
        [entry["decision"] for entry in entries if entry["decision"]]
    )
    return {str(key): row for key, row in rows.items()}, {
        str(key): review for key, review in reviews.items()
    }


def census_additions(entries):
    """The additions the ``parish`` census ``entries`` name, by id text.

    A withdrawn addition is left out: the form never draws one, and its
    entry is never left as shown (a form that shows one is stale,
    ``withdrawn_shown``). One query, none when no entry names one.
    """
    ids = [entry["item"] for entry in entries if entry["shown"] == Shown.PARISH]
    if not ids:
        return {}
    rows = CensusAddition.objects.filter(pk__in=ids).exclude(
        resolutions__action="withdrawn"
    )
    return {str(row.pk): row for row in rows}


def withdrawn_shown(shown_items):
    """Whether a form's ``shown_items`` name a change the parish added since withdrawn.

    Its submit is then a stale-form review (review pipeline spec, "Reviewed
    form concurrency"): the Family never sends, as if it were still the
    parish's, a value Staff took back while the form was open.
    """
    entries = entries_of(shown_items, CENSUS)
    named = {entry["item"] for entry in entries if entry["shown"] == Shown.PARISH}
    return bool(named) and named != set(census_additions(entries))


def parish_left_as_shown(entry, addition, answer):
    """Whether ``answer`` leaves a ``parish`` entry's added value as shown (rule 1).

    ``addition`` is the change the entry names (None when it is withdrawn).
    The answer then makes no Family change, and the addition and any earlier
    change of the field stay as they are; the submission check (frozen file
    0170) admits the same.
    """
    kind = KINDS[entry["entity"], entry["field"]]
    return (
        addition is not None
        and answer is not ABSENT
        and same(kind, answer, addition.value)
    )


def census_shown(entry, row, review, source):
    """The value a census entry showed, read back; ``ABSENT`` when it showed none.

    ``row`` is the change the entry names and ``review`` its edit review
    (or None); ``source`` is the field's baseline in the form's own
    snapshot. A New Member the form left out (Ignored) and a request the
    form did not show (an Ignored move or death: the Member as ParishSoft
    has them) are ``ABSENT``: the response gives no such answer.
    """
    shown = entry["shown"]
    if shown == Shown.FAMILY:
        return row.submitted_value
    if shown == Shown.REVIEWER:
        return review.admin_value
    if entry["entity"] == "proposed_member" or entry["field"] in {
        "moved_household",
        "deceased_status",
        "death_date",
    }:
        return ABSENT
    return source.value


def left_as_shown(entry, row, review, source, answer):
    """Whether a response's ``answer`` leaves a census entry's item as shown (rule 1).

    An item the form did not show (``census_shown`` ``ABSENT``) is left as
    shown by an absent answer, or, for a Member's move or death request
    shown as ParishSoft has the Member, by the baseline's own answer.
    """
    shown = census_shown(entry, row, review, source)
    kind = KINDS[census_key(entry)[0], entry["field"]]
    if shown is ABSENT:
        return answer is ABSENT or (
            entry["entity"] == "member"
            and source.available
            and same(kind, answer, source.value)
        )
    return answer is not ABSENT and same(kind, answer, shown)


def census_draw(field, entry, row, review):
    """The effective value a reviewed form draws for one census field.

    With no entry the form shows the plain baseline. The Family's own
    answer is drawn by the Effective-value merge, as before the reviewed
    form, so its change and conflict markers are today's (none when
    Ignored); a reviewer's value, and a change the parish added (``row`` is
    then the ``CensusAddition``), is marked changed when it differs from
    the baseline; a baseline is never marked.
    """
    from .merge import PriorChange, merge_value

    if entry is None:
        return merge_value(field.kind, field.source)
    if entry["shown"] == Shown.FAMILY:
        # The Family's own answer, merged without the row's reviewer's value:
        # the entry showed the Family's answer, so the form never draws an
        # Edit recorded since (``effective_field`` would, once entered).
        drawn = merge_value(
            field.kind,
            field.source,
            PriorChange(
                KnownValue(row.baseline_available, row.baseline_value),
                row.submitted_value,
            ),
        )
        if row.decision == "ignored":
            # An Ignored item shown as the Family's own answer (a Family
            # address, the email opt-out) is never marked.
            return EffectiveValue(drawn.value, MergeState.CURRENT, False, False)
        return drawn
    if entry["shown"] in (Shown.REVIEWER, Shown.PARISH):
        value = review.admin_value if entry["shown"] == Shown.REVIEWER else row.value
        changed = not (
            field.source.available and same(field.kind, value, field.source.value)
        )
        return EffectiveValue(
            KnownValue(True, value),
            MergeState.FAMILY_CHANGED if changed else MergeState.CURRENT,
            changed,
            False,
        )
    return merge_value(field.kind, field.source)


def drawn_entries(entries, additions):
    """Each census field's entry the reviewed form draws, by item identity.

    A field's ``parish`` entry wins over the change entry beside it (the
    Ignored change the addition was added in place of, or an older one),
    unless its addition was withdrawn (not in ``additions``, from
    ``census_additions``).
    """
    keyed = {}
    for entry in entries:
        key = census_key(entry)
        if entry["shown"] == Shown.PARISH:
            if entry["item"] in additions:
                keyed[key] = entry
        elif keyed.get(key, {}).get("shown") != Shown.PARISH:
            keyed[key] = entry
    return keyed


def census_fields(inputs, entries, additions=None):
    """The effective census fields a reviewed form draws, in ``inputs`` order.

    ``additions`` are ``census_additions(entries)`` when the caller already
    read them.
    """
    from .effective import EffectiveField

    if additions is None:
        additions = census_additions(entries)
    keyed = drawn_entries(entries, additions)
    rows, reviews = census_rows(entries)
    result = []
    for field in inputs.fields:
        entry = keyed.get((field.entity, str(field.identity), field.field))
        row = (
            (additions if entry["shown"] == Shown.PARISH else rows).get(entry["item"])
            if entry
            else None
        )
        review = reviews.get(entry["decision"] or "") if entry else None
        result.append(
            EffectiveField(
                field.entity,
                field.identity,
                field.field,
                census_draw(field, entry, row, review),
            )
        )
    return tuple(result)


def proposed_draw(entries):
    """The New Members a reviewed form shows, with their carried-forward answers.

    A New Member shows until Confirmed, Entered by hand and Updated
    included; an Ignored one is left out (its absence from the submit is
    rule 1). A New Member takes no Edit, so its answer is the Family's.
    """
    shown = [
        entry
        for entry in entries
        if entry["entity"] == "proposed_member" and entry["shown"] != Shown.BASELINE
    ]
    rows, _ = census_rows(shown)
    return sorted(
        ((entry["key"], rows[entry["item"]]) for entry in shown),
        key=lambda pair: pair[0],
    )


# --- Ministry --------------------------------------------------------------


def _entered(request_ids):
    """The ids (text) of the requests whose latest roster tick says entered."""
    from django.db.models import OuterRef, Subquery

    from parishkit.stewardship.workflows.models import MinistryRosterEntry

    latest = MinistryRosterEntry.objects.filter(request_id=OuterRef("request_id"))
    return {
        str(request)
        for request, entered in MinistryRosterEntry.objects.filter(
            request_id__in=list(request_ids),
            sequence=Subquery(latest.order_by("-sequence").values("sequence")[:1]),
        ).values_list("request_id", "entered")
        if entered
    }


def edit_revision(request):
    """The revision whose Edit carried ``request``'s Ministry forward, or None."""
    from parishkit.stewardship.workflows.models import MinistryWorkflowRevision

    return (
        MinistryWorkflowRevision.objects.filter(
            request=request, reviewed_ministry_duid=request.reviewed_ministry_duid
        )
        .order_by("-expected_version")
        .first()
    )


def drawable(action, ministry, roster):
    """Whether a choice can be drawn: a join of a Ministry the Member is not in,
    or a leave of one they are in."""
    return (action == "join") != (ministry in roster)


def ministry_entries(inputs, prior):
    """One shown item per Ministry request in force for the Family's form.

    An item is a request of ``ministry_requests.request_index``'s selection
    that is not history or Confirmed. Its shown choice is the request's
    action on the Ministry it carries forward (the Family's, an Edit's, or
    the parish's for an added one); an Ignored request shows "not selected"
    or "Continue in this ministry" (the baseline). A request the roster
    makes impossible to draw (a join of a Ministry the Member is in, a leave
    of one they are not in), or whose Ministry the campaign no longer
    offers, records none and is not presented (rule 5).
    """
    if prior is None or inputs.ministries is None:
        return []
    from parishkit.stewardship.workflows.review import placement as ministry_stage

    from .ministry_requests import request_index
    from .refresh_checks import check_states

    rows = [
        row
        for row in request_index(prior).values()
        if row.state not in MINISTRY_HISTORY
    ]
    entered = _entered(row.pk for row in rows)
    checks = check_states(
        requests=[row.pk for row in rows],
        versions={str(row.pk): row.version for row in rows},
    )
    offered = frozenset(option.duid for option in inputs.ministries.options)
    rosters = {
        str(member): frozenset(values)
        for member, values in inputs.ministries.memberships
    }
    entries = []
    for row in rows:
        if row.entity_kind == "member" and row.entity_key not in rosters:
            continue
        check = checks.get(str(row.pk))
        placement = ministry_stage(
            state=row.state,
            outcome=row.outcome,
            source_resolved=row.resolution_source_id is not None,
            roster_entered=str(row.pk) in entered,
            reviewed=row.reviewed_ministry_duid is not None,
            added=row.parish_added,
            confirmed=bool(check and check.outcome == "confirmed"),
        )
        found = shown_value(Stream.MINISTRY, placement)
        if found is None:
            continue
        roster = rosters.get(row.entity_key, frozenset())
        ministry = (
            row.current_ministry_duid
            if found.shown is Shown.REVIEWER
            else row.ministry_duid
        )
        if ministry not in offered or not drawable(row.action, ministry, roster):
            continue
        decision = None
        if found.shown is Shown.REVIEWER and row.reviewed_ministry_duid is not None:
            revision = edit_revision(row)
            decision = str(revision.pk) if revision else None
            if revision is None:
                # No Edit to read back: shown as the request's own Ministry
                # is impossible to tell from the Family's, so none is drawn.
                continue
        entries.append(
            {
                "stream": MINISTRY,
                "entity": row.entity_kind,
                "key": row.entity_key,
                "ministry": row.ministry_duid,
                "action": row.action,
                "item": str(row.pk),
                "version": row.version,
                "decision": decision,
                "shown": found.shown.value,
            }
        )
    return entries


def ministry_rows(entries):
    """The requests and Edit revisions the Ministry ``entries`` name, by id text."""
    from parishkit.stewardship.workflows.models import (
        MinistryRequest,
        MinistryWorkflowRevision,
    )

    rows = MinistryRequest.objects.in_bulk([entry["item"] for entry in entries])
    revisions = MinistryWorkflowRevision.objects.in_bulk(
        [entry["decision"] for entry in entries if entry["decision"]]
    )
    return {str(key): row for key, row in rows.items()}, {
        str(key): revision for key, revision in revisions.items()
    }


def ministry_shown(entry, request, revision):
    """The Ministry a Ministry entry showed chosen, read back; None for the baseline.

    The Family's request shows its own Ministry; an edited one the Ministry
    its Edit carried forward (``revision``); one the parish added its own.
    An Ignored request shows the baseline: not selected, or Continue.
    """
    if entry["shown"] == Shown.BASELINE:
        return None
    if entry["shown"] == Shown.REVIEWER and revision is not None:
        return revision.reviewed_ministry_duid
    return request.ministry_duid


def ministry_draw(entries, rows, revisions):
    """The choices a reviewed form draws, by (entity, key): ``{action: [duid]}``."""
    choices = {}
    for entry in entries:
        request = rows[entry["item"]]
        shown = ministry_shown(entry, request, revisions.get(entry["decision"] or ""))
        if shown is None:
            continue
        member = choices.setdefault((entry["entity"], entry["key"]), {})
        member.setdefault(entry["action"], []).append(shown)
    return choices


# --- Financial -------------------------------------------------------------


def financial_entries(family, prior):
    """The pledge's one shown item: the Family's current live pledge answer.

    The pledge is one item per Family that stays the Family's answer after
    Confirmed, so it always has an entry while the Family's latest live
    response answers the financial questions. Its stage is the pledge
    selection's (``reports.pledge_review``); an edited pledge shows the
    reviewer's amount and frequency from the decision in force.
    """
    if prior is None or prior.mode != "live" or "financial" not in prior.answers:
        return []
    from parishkit.stewardship.reports.pledge_review import (
        placement,
        read_items,
        version_of,
    )

    item = read_items(prior.campaign_id, family.pk).get(prior.pk)
    if item is None:
        return []
    found = shown_value(Stream.FINANCIAL, placement(item))
    review = item.review if item.in_force else None
    shown = found.shown
    if shown is Shown.REVIEWER and (
        review is None or review.reviewed_annual_pledge is None
    ):
        shown = Shown.FAMILY
    return [
        {
            "stream": FINANCIAL,
            "item": str(item.submission_id),
            "version": version_of(item),
            "decision": str(review.pk) if review else None,
            "shown": shown.value,
        }
    ]


def pledge_shown(entry, answer, review):
    """The pledge answer a pledge entry showed, read back.

    ``answer`` is the financial answer of the response the entry names (the
    Family's own) and ``review`` the decision row it names. An edited pledge
    shows the reviewer's amount and frequency with the Family's own share
    methods and Cannot contribute answer; an edited zero pledge hides and
    clears the frequency and share methods as the Family form does for a
    zero pledge, and an edited pledge above zero is not Cannot contribute.
    The pledge stage selection compares a later response with the same
    value in SQL (0155's ``stewardship_pledge_review_states_v1``).
    """
    if entry["shown"] != Shown.REVIEWER:
        return dict(answer)
    from parishkit.stewardship.reports.money import MoneyAmount

    amount = MoneyAmount(int(review.reviewed_annual_pledge * 100))
    positive = amount.cents > 0
    return {
        "annual_pledge": amount.canonical,
        "frequency": (review.reviewed_frequency or "") if positive else "",
        "shares": dict(answer["shares"]) if positive else {},
        "cannot_give": False if positive else bool(answer.get("cannot_give")),
    }


def financial_draw(entries, prior):
    """The pledge answers a reviewed form draws, or None to draw the Family's own."""
    if not entries or prior is None or "financial" not in prior.answers:
        return None
    from parishkit.stewardship.reports.pledge_models import PledgeReview

    entry = entries[0]
    review = (
        PledgeReview.objects.get(pk=entry["decision"])
        if entry["shown"] == Shown.REVIEWER
        else None
    )
    return pledge_shown(entry, prior.answers["financial"], review)


# --- Recording and carrying over -------------------------------------------


def shown_items(inputs, prior, family):
    """Every shown item a live reviewed form records, in a stable order."""
    entries = [
        *census_entries(inputs, prior, family),
        *ministry_entries(inputs, prior),
        *financial_entries(family, prior),
    ]
    return sorted(entries, key=identity)


def identity(entry):
    """An entry's stream and item identity, as a sortable tuple of text."""
    if entry["stream"] == CENSUS:
        parts = census_key(entry)
    elif entry["stream"] == MINISTRY:
        parts = (*ministry_key(entry)[:2], str(entry["ministry"]), entry["action"])
    else:
        parts = ()
    return (entry["stream"], *map(str, parts))


def _relevant(stream_entry, inputs):
    """The baseline inputs one entry's shown value depends on, comparable."""
    stream = stream_entry["stream"]
    if stream == CENSUS:
        key = census_key(stream_entry)
        for field in inputs.fields:
            if (field.entity, str(field.identity), field.field) == key:
                return field.comparison()
        return None
    if stream == MINISTRY:
        if inputs.ministries is None:
            return None
        rosters = dict(inputs.ministries.memberships)
        offered = tuple(option.duid for option in inputs.ministries.options)
        roster = rosters.get(
            int(stream_entry["key"]) if stream_entry["entity"] == "member" else None
        )
        return (offered, roster)
    return inputs.financial.comparison() if inputs.financial is not None else None


def carry_over(earlier, fresh, earlier_inputs, inputs):
    """The shown items of a form that replaces an open one after a refresh.

    The tab keeps its unsaved edits and tracks them against the values it
    was drawn with, so an item whose relevant baseline inputs did not
    change keeps the earlier form's entry: the form still shows the value
    the Family saw, even if a decision was recorded meanwhile, and rule 1
    then keeps that newer decision. An item the refresh changed (its
    baseline value, its Ministry's roster or offer, the pledge's inputs)
    takes its fresh entry, as do items new to the form. ``earlier`` is the
    replaced baseline's entries and ``earlier_inputs`` its inputs.
    """
    kept = {(identity(entry), entry["item"]): entry for entry in earlier or ()}
    result = []
    for entry in fresh:
        old = kept.get((identity(entry), entry["item"]))
        if old is not None and _relevant(old, earlier_inputs) == _relevant(
            entry, inputs
        ):
            result.append(old)
        else:
            result.append(entry)
    return result
