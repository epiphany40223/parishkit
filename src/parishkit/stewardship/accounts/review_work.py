"""My review work: the landing page of Staff and Ministry leaders (#1690).

Staff and Ministry leaders sign in to review what Families submitted
(review pipeline spec, "Role views"; decision 8), so for them Home is
replaced by one panel per stream they may review, each a row of link
cards (ui-conventions "Overview pages") with the item count at each stage
of their work. Administrators keep the full Home, which shows the same
panels as its Review work, with Ready to update.

Every count is read from the stream's own stored state and placed in a
stage by ``parishkit.stewardship.pipeline``, the one shared mapping, so the
landing and each stream's worklist agree on what "To review" means:

- Census changes: every proposal of the campaign's live responses, grouped
  by its stored decision, execution and handling, then
  ``census_placement``. The census page's own selection (``ROWS`` in
  ``reports.census_changes``) reads the same proposals.
- Ministry requests: To review is the follow-up queue's own count
  (``admin_context._open_counts``, the menu's number), whose default
  Unresolved filter is exactly the open states ``ministry_placement`` puts
  at To review; a Ministry leader gets one card per Ministry they lead
  (``ministry_followup.my_ministries``), read by the same scoped selection.
  To enter by hand reads the resolved requests through that selection, as
  Roster changes to enter does, with each one's latest Entered tick.
- Additional information: ``information_placement`` of each current item.
- Financial and Cannot participate: every current answer is To review
  until their slices store a decision (#1686, #1685).

The selection derives a leader's scope in SQL from the signed-in actor, so a
leader is never shown another Ministry's count; the other streams are read
only for roles that may open them. Every other count rides on one statement
(``COUNTS``), run only when the viewer may see at least one of them.

Until a stream moves to the shared review worklist, its cards link to that
stream's current page, which does not read a stage from its address; a
stream in ``STAGE_LINKED`` gets ``?stage=<value>`` on each card instead
(the slices that move each page add it there).
"""

import contextlib
import json
import logging
from collections import Counter
from dataclasses import dataclass

from django.db import DatabaseError, connection, transaction
from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from parishkit.stewardship.observability import Event, emit_failure
from parishkit.stewardship.pipeline import (
    Stage,
    Stream,
    cannot_participate_placement,
    census_placement,
    financial_placement,
    information_placement,
    ministry_placement,
)

from .policy import Capability, allows

# Streams whose page reads ``?stage=<value>`` and filters by it. Empty until
# Census changes (#1683) and Ministry requests (#1684) move to the review
# worklist and accept a stage by GET; each adds its stream here.
STAGE_LINKED = frozenset()

# One statement for every count the viewer may see; each part runs only when
# its flag is set (NULL otherwise), as the menu's own OPEN_COUNTS does.
# Census: the census page's proposals (live responses, report-only rows
# left to the financial and Ministry streams), grouped by the state
# census_placement reads. Additional information: current items by the
# follow-up state information_placement reads (superseded and withdrawn
# items are history). Financial: the Families whose current live response
# answers the financial questions, the rows of the Financial stewardship
# report. Cannot participate: the Members and Families the Limitations
# report lists, from its own selection. Roster: the resolved requests of the
# Ministry follow-up selection, with every Member and every revision as
# Roster changes to enter reads them, grouped by the state
# ministry_placement reads and the latest Entered tick.
COUNTS = """
SELECT
    CASE WHEN %(census)s THEN (
        SELECT coalesce(jsonb_agg(jsonb_build_array(
            g.decision, g.execution, g.handling, g.entity_kind,
            g.admin_value_set, g.n)), '[]'::jsonb)
        FROM (
            SELECT p.decision, p.execution, p.handling, p.entity_kind,
                p.admin_value_set, count(*) AS n
            FROM stewardship_submission s
            JOIN stewardship_proposed_change p ON p.submission_id=s.id
            WHERE s.campaign_id=%(campaign)s AND s.mode='live'
                AND p.handling<>'report-only'
            GROUP BY 1, 2, 3, 4, 5) g)
    END,
    CASE WHEN %(information)s THEN (
        SELECT coalesce(jsonb_agg(jsonb_build_array(
            g.follow_up_needed, g.followed_up, g.n)), '[]'::jsonb)
        FROM (
            SELECT i.follow_up_needed, i.followed_up_at IS NOT NULL AS followed_up,
                count(*) AS n
            FROM stewardship_additional_information i
            JOIN stewardship_submission s ON s.id=i.submission_id
            WHERE s.campaign_id=%(campaign)s AND s.mode='live'
                AND i.disposition='current_actionable'
            GROUP BY 1, 2) g)
    END,
    CASE WHEN %(financial)s THEN (
        SELECT count(*)
        FROM stewardship_family_campaign f
        JOIN stewardship_submission s ON s.id=f.effective_submission_id
            AND s.mode='live' AND s.campaign_id=f.campaign_id
            AND s.answers ? 'financial'
        WHERE f.campaign_id=%(campaign)s)
    END,
    CASE WHEN %(limitations)s THEN (
        SELECT CASE WHEN v ? 'unavailable' THEN -1
            ELSE jsonb_array_length(coalesce(v->'members', '[]'::jsonb))
                + jsonb_array_length(coalesce(v->'families', '[]'::jsonb)) END
        FROM stewardship_limitations_report_v1(
            %(campaign)s, '{"search": "", "show": "any"}'::jsonb) v)
    END,
    CASE WHEN %(roster)s THEN (
        SELECT coalesce(jsonb_agg(jsonb_build_array(
            g.state, g.outcome, g.source_resolved, g.entered, g.n)), '[]'::jsonb)
        FROM (
            SELECT r->>'state' AS state, r->>'outcome' AS outcome,
                (r->>'source_resolved')::boolean AS source_resolved,
                coalesce(e.entered, false) AS entered, count(*) AS n
            FROM stewardship_ministry_followup_v2(
                campaign_uuid => %(campaign)s, filters => %(roster_filters)s::jsonb,
                actor_uuid => %(viewer)s, page_limit => NULL) v
            CROSS JOIN LATERAL jsonb_array_elements(
                CASE WHEN (v->>'authorized')::boolean THEN v->'rows'
                    ELSE '[]'::jsonb END) r
            LEFT JOIN LATERAL (
                SELECT x.entered FROM stewardship_ministry_roster_entry x
                WHERE x.request_id=(r->>'id')::uuid
                ORDER BY x.sequence DESC LIMIT 1) e ON true
            -- Only roster outcomes become a roster change; the others
            -- (declined, no response, ...) are never entered by hand.
            WHERE r->>'outcome' IN ('joined', 'leave_confirmed')
            GROUP BY 1, 2, 3, 4) g)
    END
"""
# Each COUNTS flag and the stream it counts.
FLAG_STREAMS = {
    "census": Stream.CENSUS,
    "information": Stream.ADDITIONAL_INFORMATION,
    "financial": Stream.FINANCIAL,
    "limitations": Stream.CANNOT_PARTICIPATE,
    "roster": Stream.MINISTRY,
}
# The Limitations count when its report says its inputs are unavailable.
UNAVAILABLE = -1


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
    """Each stream's item count by stage value, for the streams this person reviews.

    Returns ``{stream value: {stage value: count}}``, plus ``"leader"``: a
    Ministry leader's per-Ministry To review counts (``my_ministries``), or
    None for everyone else. Ministry To review for Staff is not read here:
    the menu already reads it (see the module docstring). A stream whose
    count could not be read maps to None, and its cards show no number.

    The counts only decorate the landing, so a failure must never take the
    page (or Admin Home) down, as the menu's own counts never do: a database
    error, or a stored state the pipeline does not place, is logged and
    every stream the person may open is shown without numbers. Inside the
    view's read transaction the statement gets a savepoint, so only the
    count is rolled back.
    """
    from parishkit.stewardship.workflows.roster import can_tick

    from .admin_dashboard import leads_ministries

    flags = {
        "census": allows(principal, Capability.VIEW_CENSUS),
        "information": allows(principal, Capability.ADDITIONAL_FOLLOWUP),
        "financial": allows(principal, Capability.FINANCIAL_DETAIL),
        "limitations": allows(principal, Capability.CAMPAIGN_REPORT),
        # Roster work is Staff's: a leader never sees the update phase.
        "roster": can_tick(principal),
    }
    counts = {}
    if any(flags.values()):
        isolate = (
            transaction.atomic if connection.in_atomic_block else contextlib.nullcontext
        )
        try:
            with isolate():
                counts = _read(flags, principal, campaign_id)
        except (DatabaseError, ValueError) as error:
            # A WARNING process-log line naming only the failure's kind,
            # under the event the menu's counts use for the same case.
            emit_failure(
                error,
                event=Event.REPORT_SHAPING_FAILED,
                level=logging.WARNING,
                name_class=True,
            )
            counts = {FLAG_STREAMS[flag]: None for flag, on in flags.items() if on}
    leader = None
    if leads_ministries(principal):
        from parishkit.stewardship.reports.ministry_followup import my_ministries

        leader = my_ministries(campaign_id, principal)
    return {**counts, "leader": leader}


def _read(flags, principal, campaign_id):
    """Run ``COUNTS`` and place every group in its stage; see ``read_counts``."""
    from parishkit.stewardship.reports.ministry_followup import (
        FollowupQuery,
        selection_filters,
    )

    with connection.cursor() as cursor:
        cursor.execute(
            COUNTS,
            flags
            | {
                "campaign": campaign_id,
                "viewer": principal.identity,
                "roster_filters": selection_filters(
                    FollowupQuery(state="resolved", history="all"), every_member=True
                ),
            },
        )
        census, information, financial, limitations, roster = (
            _json(value) for value in cursor.fetchone()
        )
    counts = {}
    if census is not None:
        counts[Stream.CENSUS] = _tally(
            (
                census_placement(
                    decision=decision,
                    execution=execution,
                    handling=handling,
                    entity_kind=kind,
                    admin_value_set=edited,
                ),
                n,
            )
            for decision, execution, handling, kind, edited, n in census
        )
    if information is not None:
        counts[Stream.ADDITIONAL_INFORMATION] = _tally(
            (
                information_placement(
                    disposition="current_actionable",
                    follow_up_needed=needed,
                    followed_up=done,
                ),
                n,
            )
            for needed, done, n in information
        )
    if financial is not None:
        counts[Stream.FINANCIAL] = _tally([(financial_placement(), financial)])
    if limitations == UNAVAILABLE:
        counts[Stream.CANNOT_PARTICIPATE] = None
    elif limitations is not None:
        counts[Stream.CANNOT_PARTICIPATE] = _tally(
            [(cannot_participate_placement(), limitations)]
        )
    if roster is not None:
        counts[Stream.MINISTRY] = _tally(
            (
                ministry_placement(
                    state=state,
                    outcome=outcome,
                    source_resolved=resolved,
                    roster_entered=entered,
                ),
                n,
            )
            for state, outcome, resolved, entered, n in roster
        )
    return counts


def _json(value):
    """A JSON column as Python: the driver may hand jsonb back as text."""
    return json.loads(value) if isinstance(value, str) else value


def _tally(groups):
    """Sum ``(placement, count)`` groups by stage value.

    A census report-only change places as None (it belongs to another
    stream) and is skipped, as is an empty group.
    """
    counts = Counter()
    for placement, count in groups:
        if placement is not None and count:
            counts[placement.stage.value] += count
    return dict(counts)


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
        return config, {"campaign": campaign, "counts": counts}, database_now()


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
    """How one stream is shown: its menu page, its words and its stages."""

    stream: Stream
    entry: str
    title: str
    # What one item is called after its count ("4 changes").
    items: str
    stages: tuple


# The streams in the order the panels are shown, with the stages a Staff
# member works on in each (review pipeline spec, "Role views"), plus Ready
# to update, which only an Administrator acts on (``ADMIN_STAGES``). A stage a
# stream never reaches is not shown: Ministry requests have no Conflict
# (rosters are never written, so nothing changes under a request), and
# Financial and Cannot participate have only To review until their slices
# store decisions. Entered incorrectly and Not seen in ParishSoft are added
# when the refresh re-evaluation (#1689) can place an item there.
ADMIN_STAGES = frozenset({Stage.READY_TO_UPDATE})
STREAMS = (
    StreamView(
        Stream.CENSUS,
        "census_changes",
        _("Census changes"),
        _("changes"),
        (
            Stage.TO_REVIEW,
            Stage.CONFLICT,
            Stage.READY_TO_UPDATE,
            Stage.TO_ENTER_BY_HAND,
        ),
    ),
    StreamView(
        Stream.MINISTRY,
        "ministry_report",
        _("Ministry requests"),
        _("requests"),
        (Stage.TO_REVIEW, Stage.TO_ENTER_BY_HAND),
    ),
    StreamView(
        Stream.CANNOT_PARTICIPATE,
        "limitations_report",
        _("Cannot participate"),
        _("answers"),
        (Stage.TO_REVIEW,),
    ),
    StreamView(
        Stream.FINANCIAL,
        "financial_report",
        _("Financial"),
        _("pledges"),
        (Stage.TO_REVIEW,),
    ),
    StreamView(
        Stream.ADDITIONAL_INFORMATION,
        "information_queue",
        _("Additional information"),
        _("items"),
        (Stage.TO_REVIEW, Stage.TO_HANDLE),
    ),
)


def panels(chrome, review):
    """My review work's panels, from the chrome's menu and the view's counts.

    ``chrome`` is the Admin chrome (``admin_context``): a stream is shown
    only when the viewer's menu has its page, linked while the entry is and
    greyed out with the menu's reason while it is not, so a card never
    leads to a page its viewer may not open. ``review`` is ``observe``'s
    value (or Admin Home's, which has the same shape). An Administrator
    (``chrome["admin"]``) also gets Ready to update. A Ministry leader's
    Ministry panel has one card per Ministry they lead, linking the queue
    filtered to it, as My Ministries did.
    """
    entries = chrome.get("entries") or {}
    # The menu's own open counts (#585), by entry name.
    menu_counts = {
        entry["name"]: entry.get("count")
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
        elif view.stream is Stream.MINISTRY:
            # To review is the menu's count (None if it could not be read);
            # To enter by hand only when Staff's roster count was read.
            stream_counts = {Stage.TO_REVIEW.value: menu_counts.get(view.entry)}
            if view.stream in counts:
                roster = Stage.TO_ENTER_BY_HAND.value
                read = counts[view.stream]
                stream_counts[roster] = None if read is None else read.get(roster, 0)
            cards = _stage_cards(view, item, stream_counts)
        elif view.stream in counts:
            # A counted stream's stage with no items reads as zero; one that
            # could not be read (None) has no number.
            read = counts[view.stream]
            stream_counts = {
                stage.value: None if read is None else read.get(stage.value, 0)
                for stage in view.stages
                if stage not in hidden
            }
            cards = _stage_cards(view, item, stream_counts)
        else:
            continue
        if cards:
            shown.append(Panel(view.stream.value, view.title, tuple(cards)))
    return shown


def _stage_cards(view, item, counts):
    """A Staff member's cards for one stream: one per stage in ``counts``.

    Ministry To enter by hand is Roster changes to enter, the Staff list
    that is opened from Ministry requests, so it follows that entry, and
    census Ready to update is Publish to ParishSoft, the update run's page,
    which is opened from Census changes. A stream in ``STAGE_LINKED`` links
    its page filtered to each stage.
    """
    cards = []
    for stage in view.stages:
        if stage.value not in counts:
            continue
        url = item.url
        if url and view.stream is Stream.MINISTRY and stage is Stage.TO_ENTER_BY_HAND:
            url = reverse("admin:ministry_roster")
        elif url and stage is Stage.READY_TO_UPDATE:
            url = reverse("admin:census_publication")
        elif url and view.stream in STAGE_LINKED:
            url = f"{url}?stage={stage.value}"
        cards.append(
            Card(stage.label, url, item.reason, counts[stage.value], view.items)
        )
    return cards


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
