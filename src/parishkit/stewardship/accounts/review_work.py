"""My review work: the landing page of Staff and Ministry leaders (#1690).

Staff and Ministry leaders sign in to review what Families submitted
(review pipeline spec, "Role views"; decision 8), so for them Home is
replaced by one panel per stream they may review, each a row of link
cards (ui-conventions "Overview pages") with the item count at each stage
of their work. Administrators keep the full Home, which shows the same
panels as its Review work, with Ready to update.

Each card's count is the count its stream's worklist shows for that Stage
choice (#2101), placed in its stage by ``parishkit.stewardship.pipeline``,
the one shared mapping, from the stream's own stored state:

- The stages the menu counts too take the menu's own read
  (``admin_context._open_counts``), whose statement places each stream by
  stage and leaves those counts on each menu entry (``stages``): Census
  changes before a change's step (To review, Conflict, Ready to update,
  To enter by hand), Ministry requests To review (the follow-up queue's
  own count; a Ministry leader gets one card per Ministry they lead,
  ``ministry_followup.my_ministries``) and To enter by hand, Cannot
  participate and Additional information To review and To handle. So a
  card and its menu entry are one read and can never disagree.
- The other counts are this module's own statement (``COUNTS``): the
  census changes the parish added (To enter by hand), Financial's every
  stage (Pledges' own stage selection), and the items Entered by hand
  whose refresh checks (#1689) may make them Entered incorrectly or flag
  them Not seen in ParishSoft, which each stream's check read then places
  (``settle``) as its worklist does.

The selection derives a leader's scope in SQL from the signed-in actor, so a
leader is never shown another Ministry's count; the other streams are read
only for roles that may open them. ``COUNTS`` runs only when the viewer may
see at least one of its parts; on Admin Home it runs inside the menu's own
open-count statement (``with_counts``), so Home's panel adds no statement
of its own beyond the refresh-check reads of items Entered by hand.

Each card of a stream in ``STAGE_LINKED`` links that stream's review
worklist at its stage (``?stage=<value>``, #1818, #2101), so the page opens
on the items counted, except census Ready to update, which opens Update
ParishSoft.
"""

import contextlib
import json
import logging
from dataclasses import dataclass

from django.db import DatabaseError, connection, transaction
from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from parishkit.stewardship.observability import Event, emit_failure
from parishkit.stewardship.pipeline import TERMS, Stage, Stream
from parishkit.stewardship.web.review_worklist import NOT_SEEN

from .admin_navigation import MENU, PAGES
from .policy import Capability, allows

# Streams whose page reads ``?stage=<value>`` and filters by it, so each
# card links its stage (Administrator decision, #1818; Cannot participate
# since #2101). Financial's page is Pledges (#1686).
STAGE_LINKED = frozenset(
    {
        Stream.CENSUS,
        Stream.MINISTRY,
        Stream.CANNOT_PARTICIPATE,
        Stream.ADDITIONAL_INFORMATION,
        Stream.FINANCIAL,
    }
)

# One statement for every count of this module's own the viewer may see;
# each part runs only when its flag is set (NULL otherwise), as the menu's
# own OPEN_COUNTS does. Its flags are named ``review_<flag>`` so that Admin
# Home can run it inside the menu's statement (``admin_context.
# _open_counts``), whose own flags share the plain names; ``campaign``,
# ``viewer``, ``roster_filters`` and ``roster_outcomes`` mean the same in
# both. ``statement`` fills in the census reads, whose module is imported
# lazily.
# Census: the parish's additions by their latest step
# (census_changes.ADDITION_STEPS) and the entered changes no later
# response replaced (census_changes.ENTERED). Financial: Pledges' own stage
# selection (stewardship_pledge_stages_v1, which places each pledge from
# its decision in force and latest counting refresh check, #1686, #1747),
# by stage, with the decision rows of those Entered by hand. Roster: the
# Ministry requests a person resolved as a roster change (the follow-up
# selection with every Member and every revision, as Ministry requests
# reads them for Staff, and workflows.roster.eligible) whose latest tick
# is Entered, with their versions.
COUNTS = """
SELECT
    CASE WHEN %(review_census)s THEN ({additions}) END,
    CASE WHEN %(review_census)s THEN ({entered}) END,
    CASE WHEN %(review_financial)s THEN (
        SELECT coalesce(jsonb_agg(jsonb_build_array(g.stage, g.n, g.entered)),
            '[]'::jsonb)
        FROM (
            SELECT stage, count(*) AS n,
                coalesce(jsonb_agg(review_id::text)
                    FILTER (WHERE stage='entered_by_hand'), '[]'::jsonb) AS entered
            FROM stewardship_pledge_stages_v1(%(campaign)s, NULL::uuid)
            GROUP BY 1) g)
    END,
    CASE WHEN %(review_roster)s THEN (
        SELECT coalesce(jsonb_agg(jsonb_build_array(
            r->>'id', (r->>'version')::bigint)), '[]'::jsonb)
        FROM stewardship_ministry_followup_v2(
            campaign_uuid => %(campaign)s, filters => %(roster_filters)s::jsonb,
            actor_uuid => %(viewer)s, page_limit => NULL) v
        CROSS JOIN LATERAL jsonb_array_elements(
            CASE WHEN (v->>'authorized')::boolean THEN v->'rows'
                ELSE '[]'::jsonb END) r
        WHERE r->>'state'='resolved' AND r->>'outcome'=ANY(%(roster_outcomes)s)
            AND NOT (r->>'source_resolved')::boolean
            AND coalesce((
                SELECT x.entered FROM stewardship_ministry_roster_entry x
                WHERE x.request_id=(r->>'id')::uuid
                ORDER BY x.sequence DESC LIMIT 1), false))
    END
"""
# Each flag (``read_flags``) and the stream it shows. Only ``COUNTED``'s
# flags have a part in ``COUNTS``; the others' counts are all the menu's.
FLAG_STREAMS = {
    "census": Stream.CENSUS,
    "information": Stream.ADDITIONAL_INFORMATION,
    "financial": Stream.FINANCIAL,
    "limitations": Stream.CANNOT_PARTICIPATE,
    "roster": Stream.MINISTRY,
}
COUNTED = ("census", "financial", "roster")
# Where ``place`` leaves the items whose refresh checks ``settle`` reads,
# by stream, until it has.
CHECKS = "checks"


def statement():
    """``COUNTS`` with the census reads filled in."""
    from parishkit.stewardship.reports.census_changes import ADDITION_STEPS, ENTERED

    return COUNTS.replace("{additions}", ADDITION_STEPS).replace("{entered}", ENTERED)


def lands_here(principal):
    """Whether this person signs in to My review work rather than Home.

    Staff and Ministry leaders do (decision 8); an Administrator, who holds
    both roles too, keeps the full Home.
    """
    roles = principal.roles
    return "administrator" not in roles and bool(roles & {"staff", "ministry_leader"})


def home_label(principal):
    """The name of ``/admin/`` for this person: My review work or Home."""
    return _("My review work") if lands_here(principal) else _("Home")


def read_counts(principal, campaign_id):
    """This module's counts by stage value, for the streams this person reviews.

    Returns ``{stream: {stage value: count}}`` (Not seen in ParishSoft under
    ``NOT_SEEN``) for each stream the person may open, plus ``"leader"``: a
    Ministry leader's per-Ministry To review counts (``my_ministries``), or
    None for everyone else. A stream whose counts are all the menu's
    (``FLAG_STREAMS`` outside ``COUNTED``) maps to an empty dict; ``panels``
    adds the menu's (see the module docstring). A stream whose count could
    not be read maps to None, and its cards show no number for it.

    The counts only decorate the landing, so a failure must never take the
    page (or Admin Home) down, as the menu's own counts never do: a database
    error, or a stored state the pipeline does not place, is logged and
    every stream the person may open is shown without these numbers. Inside
    the view's read transaction the reads get a savepoint, so only the
    count is rolled back.
    """
    from .admin_dashboard import leads_ministries

    flags = read_flags(principal)
    counts = {}
    if any(flags.values()):
        counts = _guarded(flags, lambda: settle(_read(flags, principal, campaign_id)))
    leader = None
    if leads_ministries(principal):
        from parishkit.stewardship.reports.ministry_followup import my_ministries

        leader = my_ministries(campaign_id, principal)
    return {**counts, "leader": leader}


def read_flags(principal):
    """Which streams this person may see counted, by flag (``FLAG_STREAMS``)."""
    from parishkit.stewardship.workflows.roster import can_tick

    return {
        "census": allows(principal, Capability.VIEW_CENSUS),
        "information": allows(principal, Capability.ADDITIONAL_FOLLOWUP),
        "financial": allows(principal, Capability.FINANCIAL_DETAIL),
        "limitations": allows(principal, Capability.CAMPAIGN_REPORT),
        # Roster work is Staff's: a leader never sees the update phase.
        "roster": can_tick(principal),
    }


def unread(flags):
    """Every stream ``flags`` would count, as not read (its cards show no number)."""
    return {FLAG_STREAMS[flag]: None for flag, on in flags.items() if on}


def _record_failure(error):
    """Log a count that could not be read or placed, as the menu's counts do.

    A WARNING process-log line naming only the failure's kind, under the
    event the menu's counts use for the same case.
    """
    emit_failure(
        error, event=Event.REPORT_SHAPING_FAILED, level=logging.WARNING, name_class=True
    )


def _guarded(flags, read):
    """``read()``, or every flagged stream as not read when it fails.

    Inside a view's transaction the reads get a savepoint, so a failure
    rolls back only the count; outside one (autocommit) each statement is
    its own transaction and the savepoint is skipped.
    """
    isolate = (
        transaction.atomic if connection.in_atomic_block else contextlib.nullcontext
    )
    try:
        with isolate():
            return read()
    except (DatabaseError, ValueError) as error:
        _record_failure(error)
        return unread(flags)


def parameters(flags):
    """``COUNTS``' own flag parameters (``review_<flag>``) for ``flags``.

    ``campaign``, ``viewer``, ``roster_filters`` and ``roster_outcomes`` are
    the caller's: the menu's statement passes the same values under those
    names.
    """
    return {f"review_{flag}": bool(flags.get(flag)) for flag in COUNTED}


def _read(flags, principal, campaign_id):
    """Run ``COUNTS`` and place every group in its stage; see ``read_counts``."""
    from parishkit.stewardship.reports.ministry_followup import (
        FollowupQuery,
        selection_filters,
    )
    from parishkit.stewardship.workflows.roster import ROSTER_OUTCOMES

    with connection.cursor() as cursor:
        cursor.execute(
            statement(),
            parameters(flags)
            | {
                "campaign": campaign_id,
                "viewer": principal.identity,
                "roster_filters": selection_filters(
                    FollowupQuery(state="resolved", history="all"), every_member=True
                ),
                "roster_outcomes": sorted(ROSTER_OUTCOMES),
            },
        )
        return place(flags, cursor.fetchone())


def placed(flags, row):
    """``place`` for the menu's statement: a state the pipeline does not place
    is logged and every flagged stream is shown without numbers."""
    try:
        return place(flags, row)
    except ValueError as error:
        _record_failure(error)
        return unread(flags)


def with_counts(request, review):
    """Admin Home's ``review`` value with the counts the menu's statement read.

    Home does not run ``COUNTS`` itself: the view asks for it with
    ``request._stewardship_review_flags`` and the menu's open counts
    (``admin_context._open_counts``) run it in their own statement, leaving
    the placed counts on ``request._stewardship_review_counts``; their
    refresh checks are read here (``settle``). A value that already has its
    counts (My review work's ``observe``) is returned as is; one whose
    counts were never read shows its cards without these numbers.
    """
    if not review or "counts" in review:
        return review
    flags = review["flags"]
    counts = getattr(request, "_stewardship_review_counts", None)
    if counts is None:
        counts = unread(flags)
    elif CHECKS in counts:
        counts = _guarded(flags, lambda: settle(counts))
        # A second render of the panel in this request reads nothing again.
        request._stewardship_review_counts = counts
    return review | {"counts": {**counts, "leader": review.get("leader")}}


def place(flags, row):
    """``COUNTS``' row, each group placed in its stage; see ``read_counts``.

    The items Entered by hand are left under ``CHECKS`` for ``settle``,
    which reads their refresh checks.
    """
    from parishkit.stewardship.reports.census_changes import addition_stages

    additions, entered, financial, roster = (_json(value) for value in row)
    # Every stream the person may open; the menu's counts fill those whose
    # counts are all its own.
    counts = {FLAG_STREAMS[flag]: {} for flag, on in flags.items() if on}
    checks = {}
    if additions is not None:
        counts[Stream.CENSUS] = addition_stages(additions)
        checks[Stream.CENSUS] = entered or []
    if financial is not None:
        # Pledges' own selection places each pledge item (#1686, #1747).
        counts[Stream.FINANCIAL] = {
            Stage(stage).value: count for stage, count, _ in financial
        }
        checks[Stream.FINANCIAL] = [
            review for _, _, reviews in financial for review in reviews
        ]
    if roster is not None:
        counts[Stream.MINISTRY] = {}
        checks[Stream.MINISTRY] = roster
    return counts | {CHECKS: checks}


def settle(counts):
    """``place``'s counts with the refresh checks of its items Entered by hand.

    Each stream's own check read places them as its worklist does: Census
    changes' Entered incorrectly and Not seen in ParishSoft
    (``census_changes.entered_stages``), and the Not seen flag of Ministry
    requests and pledges (``ministry_worklist.not_seen_count``,
    ``pledge_refresh.not_seen_count``). Each read runs only when its stream
    has such an item.
    """
    from parishkit.stewardship.reports.census_changes import entered_stages
    from parishkit.stewardship.reports.ministry_worklist import (
        not_seen_count as requests_not_seen,
    )
    from parishkit.stewardship.reports.pledge_refresh import (
        not_seen_count as pledges_not_seen,
    )

    checks = counts.get(CHECKS) or {}
    found = {key: value for key, value in counts.items() if key != CHECKS}
    for stream, items in checks.items():
        if not items or found.get(stream) is None:
            continue
        if stream is Stream.CENSUS:
            extra = entered_stages(items)
        elif stream is Stream.MINISTRY:
            extra = {NOT_SEEN: requests_not_seen(items)}
        else:
            extra = {NOT_SEEN: pledges_not_seen(items)}
        found[stream] = found[stream] | extra
    return found


def _json(value):
    """A JSON column as Python: the driver may hand jsonb back as text."""
    return json.loads(value) if isinstance(value, str) else value


def observe(principal, store):
    """Read My review work in one read-only snapshot, as Home's ``observe`` does.

    Returns ``(configuration, review, now)``, or None while a restore is
    under review. ``review`` holds the current campaign (or None) and its
    counts (``read_counts``); there is nothing to count without a campaign.
    """
    from parishkit.stewardship.campaigns.models import Campaign
    from parishkit.stewardship.campaigns.work_locks import read_transaction

    from .configuration_installation import coherent_configuration
    from .sessions import database_now

    with read_transaction():
        config = coherent_configuration(store)
        if config.restore_review_required:
            return None
        campaign = None
        if config.current_campaign_id is not None:
            campaign = Campaign.objects.select_related("active_configuration").get(
                pk=config.current_campaign_id
            )
            # The chrome reads the current campaign too; give it this row.
            config.current_campaign = campaign
        counts = read_counts(principal, campaign.pk) if campaign else {}
        review = {
            "campaign": campaign,
            "counts": counts,
            "runs": runs(principal, campaign),
        }
        return config, review, database_now()


def runs(principal, campaign):
    """The reader's unfinished review wizard runs, for the wizard's card (#2123).

    ``review_wizard_views.card_runs``' rows, or none without a campaign.
    """
    if campaign is None:
        return []
    from parishkit.stewardship.reports.review_wizard_views import card_runs

    return card_runs(principal, campaign.pk)


def wizard_cards(chrome, review):
    """The Review one at a time entry on My review work and Home (#2123).

    A one-card list, drawn as the One at a time pane's button beside the
    stream groups (review-work-split.html, R52 Part W). Shown while the
    viewer's menu has the wizard's entry (some stream is offered), with the
    menu's reason instead while the menu greys it out. It opens the start
    page; with exactly one unfinished run it reads "Continue: *stream*,
    *progress*" and opens that run's current page. It has no count: the
    waiting set differs per stream, so a total would add unlike things.
    """
    item = (chrome.get("entries") or {}).get("review_wizard")
    if item is None or not (review or {}).get("campaign"):
        return []
    found = (review or {}).get("runs") or []
    if item.url and len(found) == 1 and found[0]:
        words, url = found[0]
        label = _("Continue: %(run)s") % {"run": words}
        return [Card(label, url)]
    return [Card(item.label, item.url, item.reason)]


@dataclass(frozen=True, slots=True)
class Card:
    """One link card (``components/link-cards.html``): a count and where it leads.

    ``url`` is None while the menu greys the stream's page out, and
    ``reason`` is then the menu's reason; ``count`` is None when it could not
    be read (the card then shows no number).
    """

    label: str
    url: str | None
    reason: str | None = None
    count: int | None = None
    note: str | None = None
    other: None = None


@dataclass(frozen=True, slots=True)
class Panel:
    """One stream's panel: its heading, its page and its cards."""

    stream: str
    title: str
    cards: tuple


@dataclass(frozen=True, slots=True)
class StreamView:
    """How one stream is shown: its menu page, its words and its stages.

    Its title is its menu entry's label (``title``), so a panel is named as
    the Review work menu names its page (Administrator, #2108).

    ``stages`` are the cards' Stage choice values, in order (Not seen in
    ParishSoft is ``NOT_SEEN``, a flag counted beside its stage). ``menu``
    are those whose count is the menu entry's (``admin_context.
    _open_counts``) and ``own`` those ``read_counts`` reads; a stage in
    both adds the two (Census changes' To enter by hand: the Families'
    changes and the parish's additions).
    """

    stream: Stream
    entry: str
    # What one item is called after its count ("4 changes").
    items: str
    stages: tuple
    menu: frozenset = frozenset()
    own: frozenset = frozenset()

    @property
    def title(self):
        """The panel's heading: the menu entry's own label."""
        return PAGES[self.entry].label


def _values(*stages):
    """The Stage choice values of ``stages`` (a Stage, or ``NOT_SEEN``)."""
    return tuple(getattr(stage, "value", stage) for stage in stages)


# The streams, with the stages a Staff member works on in each (review
# pipeline spec, "Role views"), plus Ready to update, which only an
# Administrator acts on (``ADMIN_STAGES``). A stage a stream never reaches is
# not shown: Ministry requests have no Conflict and are never Entered
# incorrectly (rosters are never written, so nothing changes under a
# request), and Cannot participate and Additional information hold nothing
# ParishSoft has. ``STREAMS`` puts them in the Review work menu's order
# (``MENU``), so the panels and the menu never disagree (#2108).
ADMIN_STAGES = frozenset({Stage.READY_TO_UPDATE.value})
_STREAMS = (
    StreamView(
        Stream.CENSUS,
        "census_changes",
        _("changes"),
        _values(
            Stage.TO_REVIEW,
            Stage.CONFLICT,
            Stage.READY_TO_UPDATE,
            Stage.TO_ENTER_BY_HAND,
            Stage.ENTERED_INCORRECTLY,
            NOT_SEEN,
        ),
        menu=frozenset(
            _values(
                Stage.TO_REVIEW,
                Stage.CONFLICT,
                Stage.READY_TO_UPDATE,
                Stage.TO_ENTER_BY_HAND,
            )
        ),
        own=frozenset(
            _values(Stage.TO_ENTER_BY_HAND, Stage.ENTERED_INCORRECTLY, NOT_SEEN)
        ),
    ),
    StreamView(
        Stream.MINISTRY,
        "ministry_report",
        _("requests"),
        _values(Stage.TO_REVIEW, Stage.TO_ENTER_BY_HAND, NOT_SEEN),
        menu=frozenset(_values(Stage.TO_REVIEW, Stage.TO_ENTER_BY_HAND)),
        own=frozenset({NOT_SEEN}),
    ),
    StreamView(
        Stream.CANNOT_PARTICIPATE,
        "limitations_report",
        _("answers"),
        _values(Stage.TO_REVIEW, Stage.TO_HANDLE),
        menu=frozenset(_values(Stage.TO_REVIEW, Stage.TO_HANDLE)),
    ),
    StreamView(
        Stream.FINANCIAL,
        "pledges_to_enter",
        _("pledges"),
        _values(
            Stage.TO_REVIEW,
            Stage.CONFLICT,
            Stage.TO_ENTER_BY_HAND,
            Stage.ENTERED_INCORRECTLY,
            NOT_SEEN,
        ),
        # To review is the menu's Pledges count (#2125, #2101); the other
        # stages are read here.
        menu=frozenset(_values(Stage.TO_REVIEW)),
        own=frozenset(
            _values(
                Stage.CONFLICT,
                Stage.TO_ENTER_BY_HAND,
                Stage.ENTERED_INCORRECTLY,
                NOT_SEEN,
            )
        ),
    ),
    StreamView(
        Stream.ADDITIONAL_INFORMATION,
        "information_queue",
        _("items"),
        _values(Stage.TO_REVIEW, Stage.TO_HANDLE),
        menu=frozenset(_values(Stage.TO_REVIEW, Stage.TO_HANDLE)),
    ),
)
_MENU_POSITION = {entry.name: position for position, entry in enumerate(MENU)}
STREAMS = tuple(sorted(_STREAMS, key=lambda view: _MENU_POSITION[view.entry]))


def panels(chrome, review):
    """My review work's panels, from the chrome's menu and the view's counts.

    ``chrome`` is the Admin chrome (``admin_context``): a stream is shown
    only when the viewer's menu has its page, linked while the entry is and
    greyed out with the menu's reason while it is not, so a card never
    leads to a page its viewer may not open. ``review`` is ``observe``'s
    value (or Admin Home's, which has the same shape). An Administrator
    (``chrome["admin"]``) also gets Ready to update. A Ministry leader's
    Ministry panel has one card per Ministry they lead, linking the queue
    filtered to it, as My Ministries did. Staff's Ministry To enter by hand
    and Not seen in ParishSoft cards come only with their roster work
    (``read_flags``' ``roster``). A Not seen in ParishSoft card is shown
    only while some item carries the flag, as each worklist offers that
    Stage choice.
    """
    entries = chrome.get("entries") or {}
    # The menu's own read by stage (#2101), by entry name: None while the
    # entry is greyed or its count could not be read.
    menu_stages = {
        entry["name"]: entry.get("stages")
        for section in chrome.get("sections") or ()
        for entry in section["items"]
    }
    counts = review.get("counts") or {}
    hidden = frozenset() if chrome.get("admin") else ADMIN_STAGES
    shown = []
    for view in STREAMS:
        item = entries.get(view.entry)
        if item is None:
            continue
        if view.stream is Stream.MINISTRY and counts.get("leader") is not None:
            cards = _leader_cards(item, counts["leader"])
        elif view.stream in counts or view.stream is Stream.MINISTRY:
            stages = [stage for stage in view.stages if stage not in hidden]
            if view.stream not in counts:
                # Ministry To review alone (no roster work to count).
                stages = [Stage.TO_REVIEW.value]
            stream_counts = {
                stage: _count(
                    stage, view, menu_stages.get(view.entry), counts.get(view.stream)
                )
                for stage in stages
            }
            # Not seen in ParishSoft is a Stage choice only while some item
            # carries the flag, so its card is too (#2101).
            if not stream_counts.get(NOT_SEEN):
                stream_counts.pop(NOT_SEEN, None)
            cards = _stage_cards(view, item, stream_counts)
        else:
            continue
        if cards:
            shown.append(Panel(view.stream.value, view.title, tuple(cards)))
    return shown


def _count(stage, view, menu, own):
    """One card's count: the menu's read, this module's, or both added.

    ``menu`` and ``own`` are the stream's counts by stage from each read
    (None when not read). A stage either read holds that was not read has no
    number (None); a stage with no items reads zero.
    """
    total = 0
    for wanted, found in ((stage in view.menu, menu), (stage in view.own, own)):
        if not wanted:
            continue
        if found is None or found.get(stage, 0) is None:
            return None
        total += found.get(stage, 0)
    return total


def _stage_cards(view, item, counts):
    """A Staff member's cards for one stream: one per stage in ``counts``.

    A stream in ``STAGE_LINKED`` links its page filtered to each stage
    (Ministry To enter by hand is Ministry requests at that Stage, where
    Roster changes to enter was folded, R39 finding 2). Census Ready to
    update is Update ParishSoft instead, the update run's page, which
    is opened from Census changes.
    """
    cards = []
    for stage in view.stages:
        if stage not in counts:
            continue
        url = item.url
        if url and stage == Stage.READY_TO_UPDATE.value:
            url = reverse("admin:update_parishsoft")
        elif url and view.stream in STAGE_LINKED:
            url = f"{url}?stage={stage}"
        cards.append(Card(_label(stage), url, item.reason, counts[stage], view.items))
    return cards


def _label(stage):
    """A card's label: the stage's shared word, or Not seen in ParishSoft's."""
    return TERMS["not_seen"] if stage == NOT_SEEN else Stage(stage).label


def _leader_cards(item, mine):
    """A Ministry leader's cards: To review in each Ministry they lead.

    ``mine`` is ``my_ministries``' value: each Ministry of the campaign in
    the leader's scope, with its open join and leave requests, plus any
    Ministry since removed from the campaign that still has some (marked as
    the queue marks it). Each links the queue filtered to that Ministry.
    """
    cards = []
    for ministry in mine["ministries"]:
        name = ministry["name"]
        if not ministry["in_campaign"]:
            name = _("%(name)s (no longer in this campaign)") % {"name": name}
        url = item.url and f"{item.url}?ministry={ministry['duid']}"
        cards.append(
            Card(
                name,
                url,
                item.reason,
                ministry["join"] + ministry["leave"],
                _("to review"),
            )
        )
    return cards
