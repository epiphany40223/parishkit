"""Capability-filtered Admin chrome; public/Family pages never query this context."""

import contextlib
import functools
import json
import logging
from datetime import timedelta

from django.db import DatabaseError, connection, transaction
from django.db.models import Count, Q
from django.urls import reverse

from parishkit.config import ConfigError
from parishkit.stewardship.audit.critical_events import ACKNOWLEDGE_LIMIT
from parishkit.stewardship.audit.critical_events import WINDOW as CRITICAL_WINDOW
from parishkit.stewardship.audit.critical_events import sign as critical_sign
from parishkit.stewardship.audit.critical_events import summary as critical_summary
from parishkit.stewardship.campaigns.credential_models import CampaignCredentialState
from parishkit.stewardship.campaigns.models import Campaign
from parishkit.stewardship.jobs.delivery_metadata import alert_counts, mode_split
from parishkit.stewardship.jobs.models import NONTERMINAL_STATES, TaskRun
from parishkit.stewardship.observability import (
    Event,
    debug_logging_enabled,
    emit_failure,
)
from parishkit.stewardship.pipeline import Stage
from parishkit.stewardship.web.error_pages import ERROR_PAGE_ATTRIBUTE
from parishkit.stewardship.web.refusals import mark_not_input, unavailable_response

from . import admin_navigation, campaign_wizard, family_maintenance, review_work
from .authentication import runtime
from .limiting import LimiterUnavailable
from .policy import Capability, Principal, allows
from .runtime_models import SystemConfiguration
from .sessions import ADMIN_IDLE, database_now


def portal_chrome(request):
    """Use the view's authenticated principal, not browser roles or session data.

    This presentation helper grants no authority and renews no session activity.
    Each owning view still rechecks current access before emitting private data.
    Missing bootstrap parish data is normal until transactional setup completes.
    """
    actor = getattr(request, "principal", None)
    session = getattr(request, "portal_session", None)
    if (
        not request.path_info.startswith("/admin/")
        or not isinstance(actor, Principal)
        or not actor.roles
        or session is None
    ):
        return {}
    configuration = getattr(request, "_stewardship_display_configuration", None)
    if configuration is None:
        configuration = SystemConfiguration.objects.select_related(
            "active_configuration__parish", "current_campaign__active_configuration"
        ).first()
    if configuration is None:
        return {}
    if _setup_pending():
        return {"admin_chrome": _setup_chrome(actor, configuration, session)}
    admin = allows(actor, Capability.CONFIGURE)
    campaign = _current_campaign(configuration)
    # One state for the menu and the pages' own links (``menu_link``), so the
    # ParishSoft download is read at most once per request, shared with the
    # access gate's reason check (``greyed_page``).
    state = menu_state(campaign, configuration, request_downloaded(request))
    items = _navigation_items(
        actor, admin, campaign, configuration, state.parishsoft_data
    )
    # Presentation only: reuse the instant the owning view read inside its own
    # read snapshot (never an earlier one from before a lock wait).
    now = getattr(request, "_stewardship_display_now", None) or database_now()
    counts = _background_counts(actor, now)
    parish = getattr(configuration.active_configuration, "parish", None)
    critical, critical_ids, delivery_unknown, critical_ended = (
        alert_counts(now - CRITICAL_WINDOW, limit=ACKNOWLEDGE_LIMIT)
        if admin
        else ({}, [], None, {})
    )
    go_live = bool(
        campaign
        and CampaignCredentialState.objects.filter(
            campaign=campaign, go_live_gate=True
        ).exists()
    )
    delivery_pause = None
    if admin and campaign and configuration.mode == "production":
        from .delivery_control_commands import inventory
        from .policy_models import PortalUser

        if campaign.delivery_paused:
            delivery_pause = {
                "inventory": inventory(campaign.pk),
                "reason": campaign.pause_reason,
                "since": campaign.paused_at,
                "actor": PortalUser.objects.filter(pk=campaign.pause_actor_id)
                .values_list("email", flat=True)
                .first(),
                "url": reverse("admin:delivery_control"),
            }
    # A view may place the page more precisely than its route can (#196).
    match = getattr(request, "resolver_match", None)
    placed = admin_navigation.placement(request)
    sections, breadcrumbs = admin_navigation.build(match, items, placed)
    # Staff and Ministry leaders land on My review work, not Home (#1690):
    # its menu entry (Review work's first, or the top entry when the viewer
    # has no Review work group; ``_home_in_review`` below), the trail's root
    # and a "Return to" Home name it so, as the page's heading does
    # (ui-conventions "One name").
    home_label = review_work.home_label(actor)
    if breadcrumbs and breadcrumbs[0]["label"] == admin_navigation.PAGES["index"].label:
        breadcrumbs[0]["label"] = home_label
    back = admin_navigation.back(match, placed, items)
    if back and back["label"] == admin_navigation.PAGES["index"].label:
        back = back | {"label": home_label}
    # The open counts leave their counts by stage on the request too.
    open_counts = _open_counts(request, actor, items, campaign)
    attach_counts(
        sections, open_counts, getattr(request, "_stewardship_open_stages", {})
    )
    home_current = bool(breadcrumbs) and len(breadcrumbs) == 1
    home_in_review = review_work.lands_here(actor) and _home_in_review(
        sections, home_label, home_current
    )
    return {
        "admin_chrome": {
            "admin": admin,
            "parish_name": parish.name if parish else None,
            "home_url": reverse("admin:index"),
            "home_label": home_label,
            "home_current": home_current,
            # My review work is Review work's first entry, not a top Home.
            "home_in_review": home_in_review,
            "sections": sections,
            "breadcrumbs": breadcrumbs,
            # The viewer's menu entries by page and the reason-check state,
            # for in-page links greyed as the menu greys them (``menu_link``).
            "entries": {item.name: item for item in items},
            "menu_state": state,
            # A multi-step flow's step indicator, and where "Return to" goes.
            "flow_steps": admin_navigation.steps(placed),
            # The Campaign setup stepper on its own pages (#142).
            "campaign_wizard": _campaign_wizard(
                request, match, campaign, configuration, admin=admin, go_live=go_live
            ),
            "back": back,
            "testing": configuration.mode == "testing",
            "testing_recipient": configuration.testing_recipient if admin else None,
            "debug_in_production": _debug_in_production(configuration),
            "restored": configuration.restore_review_required,
            "paused": bool(campaign and campaign.delivery_paused),
            "delivery_pause": delivery_pause,
            "go_live": go_live,
            # A key change in progress holds every later settings change.
            "key_changes": _key_changes() if admin else [],
            # The Family portal maintenance switch (family_maintenance.py).
            # Every Admin role sees this: chairs field the Families' calls.
            "family_closed": family_maintenance.current_state().closed,
            "family_portal_url": reverse("admin:family_portal"),
            "critical_count": sum(critical.values()),
            "critical_events": critical_summary(critical, critical_ended),
            # Every listed problem has ended (#633): the banner says so
            # instead of reading as an outage still going on.
            "critical_all_ended": bool(critical)
            and critical.keys() <= critical_ended.keys(),
            # The exact rows Acknowledge may record, signed (critical_events).
            "critical_shown": critical_sign(critical_ids) if critical_ids else "",
            # More pending than one form signs: the rest stay after Acknowledge.
            "critical_limit": ACKNOWLEDGE_LIMIT,
            # The banner's System logs link filters from this day onward, a
            # day in the browser's zone (#558). One UTC day earlier than the
            # window's start, so that local day starts before the window in
            # every zone (offsets reach at most 14 hours either way).
            "critical_since_day": (now - CRITICAL_WINDOW - timedelta(days=1))
            .date()
            .isoformat(),
            "background": counts,
            "find_family": _find_family(actor, items, campaign),
            # Emails that may not have arrived: the operating mode's count,
            # which the warning shows and links, and the other mode's,
            # linked on its own (#1576); None for a viewer who is not an
            # Administrator.
            "delivery_unknown": None
            if delivery_unknown is None
            else mode_split(delivery_unknown, configuration.mode),
            # Presence has its own passive endpoint. Do not repeat its current
            # epoch/population/session reads on every ordinary Admin page.
            "presence_count": None,
            "server_now": now,
            "absolute_deadline": session.expires_at,
            "idle_deadline": min(
                session.expires_at, session.last_activity_at + ADMIN_IDLE
            ),
        }
    }


def _campaign_wizard(request, match, campaign, configuration, *, admin, go_live):
    """The Campaign setup stepper for this page, or None (``campaign_wizard``).

    An error page never shows it: a view may fail its access recheck after
    the route resolved, and the refusal must not look like a step. Before
    any campaign exists, Create the campaign draws step 1 (#1404 PR 3).
    """
    if getattr(request, ERROR_PAGE_ATTRIBUTE, False):
        return None
    if campaign is None:
        return campaign_wizard.for_creation(match, configuration.mode, admin=admin)
    if not campaign_wizard.shown(
        campaign, configuration.mode, admin=admin, go_live=go_live
    ):
        return None
    return campaign_wizard.for_request(match, campaign, configuration)


def _key_changes():
    """Each integration whose key change holds the settings queue (#456 M4).

    Shown only to Administrators, who are the ones saving settings: a change
    saved now waits behind the key's selection, and does not apply if the
    new key is accepted (``integration_credentials.changes_in_progress``).
    """
    from .integration_credentials import changes_in_progress
    from .integration_forms import LABELS

    return [
        {
            "label": LABELS[target],
            "url": reverse("admin:integration_settings", args=[target]),
        }
        for target in changes_in_progress()
    ]


def parishsoft_downloaded():
    """Whether a ParishSoft download has been promoted to the current corpus.

    One small singleton-row read. Pages that list ParishSoft data have
    nothing to show until it is true (#1242).
    """
    from parishkit.stewardship.source.snapshot_models import SourceCurrent

    return SourceCurrent.objects.filter(snapshot__isnull=False).exists()


def request_downloaded(request):
    """``parishsoft_downloaded``, read at most once for ``request``, lazily.

    The access gate (``greyed_page``) and the chrome (``portal_chrome``)
    both check it on the same request; sharing one cached answer keeps that
    to a single query, and none when no entry's reason check asks.
    """
    found = getattr(request, "_stewardship_parishsoft_data", None)
    if found is None:
        found = functools.cache(parishsoft_downloaded)
        request._stewardship_parishsoft_data = found
    return found


def _home_in_review(sections, label, current):
    """Put My review work first in the viewer's Review work group, in place
    (after Review one at a time, #2123).

    Staff and Ministry leaders land on My review work at ``/admin/``
    (#1690); the menu lists it as the first entry of Review work, beside
    the worklists it counts, rather than as a Home entry above the groups
    (admin-portal spec, "Menu groups"; R39 finding 4). On ``/admin/`` the
    entry and its group are current, so the group stays open. Returns
    whether the entry was placed: a viewer with no Review work group keeps
    the top entry instead.
    """
    review = next((s for s in sections if s["key"] == "review"), None)
    if review is None:
        return False
    # Review one at a time stays the group's first entry (#2123).
    first = review["items"][:1]
    review["items"].insert(
        1 if first and first[0]["name"] == "review_wizard" else 0,
        {
            "name": "index",
            "url": reverse("admin:index"),
            "label": label,
            "reason": None,
            "current": "page" if current else None,
            "count": None,
        },
    )
    review["current"] = review["current"] or current
    return True


def menu_state(campaign, configuration, parishsoft_data=None):
    """The menu's reason-check state: the one place that builds it.

    ``parishsoft_data`` says whether ParishSoft data has been downloaded:
    True or False when the caller already knows (unit tests), another
    state's callable to share its answer, or None to read it on first use
    only, once, so a menu with no entry that needs it (a Ministry
    leader's) runs no query for it.
    """
    if parishsoft_data is None:
        parishsoft_data = functools.cache(parishsoft_downloaded)
    elif not callable(parishsoft_data):
        parishsoft_data = functools.partial(bool, parishsoft_data)
    return admin_navigation.MenuState(campaign, configuration.mode, parishsoft_data)


def _navigation_items(actor, admin, campaign, configuration, parishsoft_data=None):
    """The menu entries the actor may open: ``MenuItem`` rows in menu order.

    Each entry uses the same capability the page itself checks, so the menu
    and the pages cannot disagree; the menu is still not the security
    boundary. Only the role hides an entry: one the mode or the campaign's
    state does not allow right now has no URL and carries its reason (the
    admin-portal spec's stable menu shape). Campaign entries follow the
    current campaign. ``admin`` is the caller's CONFIGURE decision, reused.
    ``parishsoft_data`` is passed on to ``menu_state``.
    """

    def may_open(entry):
        """The entry's own capability; a Ministry leader's Ministries count."""
        if entry.capability is Capability.CONFIGURE:
            return admin
        return allows(actor, entry.capability) or (
            entry.scoped
            and any(
                allows(actor, entry.capability, ministry_id=duid)
                for duid in actor.ministries
            )
        )

    state = menu_state(campaign, configuration, parishsoft_data)
    return admin_navigation.menu(state, may_open)


def greyed_page(request, store):
    """The reason page for a menu page its menu greys out right now, or None.

    Opening a greyed-out entry's page directly (a bookmark, an old tab, a
    typed address) shows the menu's own reason and a way forward rather
    than whatever the page's refusal happens to say (ui-conventions,
    "Unavailable menu pages", #1128). It runs from the access gate before the
    view, for a GET or HEAD of a menu page only. POSTs keep their pages' own
    in-place refusals.

    Only a signed-in viewer whose role may open the entry gets the page. The
    session is checked read-only, as the not-found page does: it renews no
    idle time and rotates nothing. Anyone else (signed out, a session the
    owning view would rotate or refuse, a role the entry is hidden from)
    gets None, so the view runs and makes its own decision as before; a
    command session's cookie in a browser is left to the view before any
    session check, so its misuse is refused and recorded once. This
    grants nothing and shows only the menu's own words. An entry available
    now costs one query (the configuration and campaign) and no session check.
    """
    from .automation_sessions import MARKER
    from .sessions import authenticated_admin

    match = getattr(request, "resolver_match", None)
    if (
        request.method not in {"GET", "HEAD"}
        or match is None
        or match.namespace != admin_navigation.NAMESPACE
        or match.url_name not in admin_navigation.MENU_NAMES
    ):
        return None
    configuration = SystemConfiguration.objects.select_related(
        "current_campaign__active_configuration"
    ).first()
    if configuration is None:
        return None
    campaign = _current_campaign(configuration)
    state = menu_state(campaign, configuration, request_downloaded(request))
    if admin_navigation.MENU_REASONS[match.url_name](state) is None:
        return None
    # A command session's cookie used in a browser is refused (and its
    # sessions revoked) by the view's own check; refusing it here too would
    # record that refusal twice.
    session = getattr(request, "session", None)
    if session is None or session.get(MARKER) is not None:
        return None
    try:
        actor = authenticated_admin(request, store=store, read_only=True)
    except PermissionError:
        return None
    if not isinstance(actor, Principal) or not actor.roles:
        return None
    admin = allows(actor, Capability.CONFIGURE)
    # The reason check above may have read the download already; share it.
    items = _navigation_items(
        actor, admin, campaign, configuration, state.parishsoft_data
    )
    greyed = admin_navigation.greyed_entry(match.url_name, items)
    if greyed is None:
        # Hidden by role: the view authenticates and refuses on its own, so
        # leave the request as it would have found it.
        for name in ("principal", "portal_session"):
            request.__dict__.pop(name, None)
        return None
    # Opening a greyed-out page is no refusal of input (#1403).
    return mark_not_input(unavailable_response(greyed.refusal()))


def page_unavailable(actor, name):
    """The greyed reason page for non-menu page ``name`` now, or None.

    For a page in ``admin_navigation.PAGE_REASONS``, called by its view
    after its own access check: when the page's reason applies, the answer
    is the shared 409 reason page (#1128) with the menu's words and a link
    to the page that changes it, if the viewer's menu offers it (#1242).
    """
    configuration = SystemConfiguration.objects.select_related(
        "current_campaign__active_configuration"
    ).first()
    if configuration is None:
        return None
    campaign = _current_campaign(configuration)
    state = menu_state(campaign, configuration)
    reason = admin_navigation.PAGE_REASONS[name](state)
    if reason is None:
        return None
    admin = allows(actor, Capability.CONFIGURE)
    items = _navigation_items(
        actor, admin, campaign, configuration, state.parishsoft_data
    )
    greyed = admin_navigation.greyed_page(name, reason, items)
    return mark_not_input(unavailable_response(greyed.refusal()))


def first_entry_url(actor, section):
    """The first entry of menu group ``section`` the actor may open now, or None.

    A group's root URL opens this (admin-portal spec, "Menu groups"): the
    same menu the sidebar shows, so the root never offers a page the sidebar
    greys out or hides.
    """
    configuration = SystemConfiguration.objects.select_related(
        "current_campaign__active_configuration"
    ).first()
    if configuration is None:
        return None
    campaign = _current_campaign(configuration)
    admin = allows(actor, Capability.CONFIGURE)
    items = _navigation_items(actor, admin, campaign, configuration)
    return next(
        (item.url for item in items if item.section == section and item.url), None
    )


def _find_family(actor, items, campaign):
    """The header's Find a Family box (#561), or None when the viewer gets none.

    Only a viewer whose menu offers the directory now (Administrators
    and Staff, with a current campaign) gets the box, and only if they may
    also open the Timeline its results link to. It reuses the menu's
    decision, so it costs the header no query. The search route rechecks
    both on every request; this only decides whether to show the box.
    """
    directory = any(item.url for item in items if item.name == "family_directory")
    if not directory or not allows(actor, Capability.CAMPAIGN_REPORT):
        return None
    return {"url": reverse("admin:find_family")}


# Menu entries that show how many open items their queue holds (#585,
# #1590; Limitations since R39 finding 10; Pledges since #2125).
COUNTED = frozenset(
    {
        "census_changes",
        "information_queue",
        "ministry_report",
        "limitations_report",
        "pledges_to_enter",
    }
)
# The count of Ministry requests' To enter by hand Stage, by this key: Home's
# Next steps show it (home_steps), the menu never does, since Ministry
# requests' menu count is its own (R39 finding 2).
ENTRY_COUNT = "ministry_entry"
# Campaign states in which that count is read: past go-live (active, closed
# or archived). A draft or a scheduled campaign has no resolved live request
# to enter yet, so its Home pays nothing for it.
ROSTER_STATES = frozenset({"active", "closed", "archived"})
# Not yet read in this request: a view that has already read the To enter by
# hand count leaves it on ``request._stewardship_entry_count`` (None when the
# read answers no number), so the chrome's statement skips that selection.
UNREAD = object()
# Every count in one statement, each only when asked for (NULL otherwise).
# Census changes reads its rows grouped by the columns its status depends on
# (census_changes.OPEN_GROUPS); Python then places the groups
# (census_changes.group_stages) and counts those at To review or Conflict
# (census_changes.OPEN_STAGES), so the stage rules live in one place.
# ``{census}`` is that query (and ``{information}`` and ``{limitations}``
# theirs, below), filled in when the statement runs, since the report
# modules are imported lazily.
# Additional information reads its current requests grouped by the state
# its stage depends on (information_worklist.OPEN_GROUPS, #1685, #2101);
# Python places the groups (information_worklist.group_stages) and the menu
# counts the To review stage, the worklist's default. Ministry requests
# (the follow-up queue, #1133) runs the page's own selection with its
# default filters (Stage To review: the open states; the stream has no
# Conflict stage yet) and no rows, and takes its total, so the
# count applies every filter the page
# does: the Ministry module, a promoted and uncompacted source, the
# campaign's Ministries (plus removed ones with a current request), the
# viewer's scope and the latest revision. The selection derives that scope
# in SQL from the signed-in actor (stewardship_ministry_followup_v2, #389
# L3), as the page does, and the page's cross-check against Python's view of
# the same actor applies here too, so the count never exceeds what the queue
# shows. When the page would say the Ministry follow-up is turned off or
# unavailable, or the viewer has no Ministry in scope, the count is NULL and
# no number shows.
# Ministry requests' To enter by hand Stage (ENTRY_COUNT, which replaced
# Roster changes to enter's count, #1660) runs the selection its roster
# rows come from (ministry_worklist.read_rows for Staff: resolved requests,
# every revision and every Member, all rows) and counts, in SQL, the rows
# that Stage lists: the eligibility (workflows.roster.eligible: an outcome
# in ROSTER_OUTCOMES that no ParishSoft resolution source settled and no
# refresh check Confirmed, #1689) and no current Entered in ParishSoft mark
# (the newest tick row, as workflows.roster.roster_marks reads it).
# Counting here rather than through
# the page's Python keeps the chrome at one statement; a database test holds
# the count equal to the page's rows. Its Ministries get the same Python
# cross-check as Ministry requests' count.
# Limitations (R39 finding 10) runs its page's own selection with the page's
# default filters (limitation_worklist.OPEN_GROUPS: every answer, grouped
# by its decision and whether it is in force), which Python places
# (limitation_worklist.group_stages, pipeline.cannot_participate_placement);
# the menu counts the To review stage, the page's default. An unavailable
# report (no Family record or source) reads NULL and no number shows. The
# page also lists each Family's Cannot contribute financially answer at its
# Pledges stage (R39 finding 11), for a viewer Pledges admits, so To review
# adds those at To review (``contributions``), read through Pledges' own
# stage selection (stewardship_pledge_stages_v1, which places a pledge from
# its latest refresh check, so a Conflict or Confirmed one is not To
# review) and the response's cannot_give flag.
# Each stream's placed stages are kept beside the menu's counts (each
# entry's ``stages``), and My review work's cards show them, so a card and
# its menu entry are one read (#2101).
# Pledges (#2125) counts the pledges its default To review stage lists,
# through the same stage selection (stewardship_pledge_stages_v1). The page
# lists the Financial stewardship selection's rows (every Family whose
# current live response answers the financial section: the selection's
# own set), so the count is that selection's To review total. When the
# page would say Pledges are unavailable (stewardship_financial_report_v1:
# the campaign's source snapshot, the current one or an archived
# campaign's own, is not promoted and uncompacted) the count is NULL and no
# number shows; a campaign without Financial stewardship has no Pledges
# link, so it is never asked for. A current campaign's snapshot is the
# current pointer's, which only ever names a promoted snapshot that
# compaction never takes (compaction excludes the current one), so for it
# the test is whether ParishSoft has been downloaded: the request's one
# shared read (request_downloaded, passed as %(downloaded)s), which the
# access gate and the menu's reasons already use (#1242), never a second
# read of stewardship_source_current here. My review work's Pledges To
# review card shows this same count (#2101).
# Both Pledges' count and Limitations' Cannot contribute part come from
# one read of stewardship_pledge_stages_v1 (the pl LATERAL), since the
# stage selection is the costliest part of the statement and runs on
# every Admin page.
# The statement's columns, by name and in order (each aliased in the SQL).
# Read the row through these names, never by position: a caller that adds
# columns (My review work's counts, #1727, cross-joined after these) takes
# its own from len(MENU_COLUMNS) on.
MENU_COLUMNS = (
    "information",
    "ministry",
    "ministries",
    "census_groups",
    "roster",
    "roster_ministries",
    "limitations",
    "pledges",
    "contributions",
)
OPEN_COUNTS = """
SELECT
    CASE WHEN %(information)s THEN ({information}) END AS information,
    f.total AS ministry,
    f.ministries AS ministries,
    CASE WHEN %(census)s THEN ({census}) END AS census_groups,
    t.total AS roster,
    t.ministries AS roster_ministries,
    CASE WHEN %(limitations)s THEN ({limitations}) END AS limitations,
    CASE WHEN %(pledges)s AND EXISTS (
        SELECT 1 FROM stewardship_campaign c
        LEFT JOIN stewardship_campaign_credentials k ON k.campaign_id=c.id
        LEFT JOIN stewardship_source_snapshot x ON x.id=k.source_snapshot_id
        WHERE c.id=%(campaign)s AND CASE WHEN c.state='archived'
            THEN x.state='promoted' AND x.compacted_at IS NULL
            ELSE %(downloaded)s END)
    THEN pl.to_review END AS pledges,
    -- Limitations' To review adds the Cannot contribute financially answers
    -- at Pledges' To review: the one stage read (``pl``) counts them too.
    CASE WHEN %(limitations)s AND %(pledges)s THEN coalesce(pl.cannot_give, 0)
    ELSE 0 END AS contributions
FROM (SELECT NULL) one
LEFT JOIN LATERAL (
    SELECT count(*) AS to_review,
        count(*) FILTER (WHERE coalesce(
            ps.answers->'financial'->'cannot_give'='true'::jsonb, false))
            AS cannot_give
    FROM stewardship_pledge_stages_v1(%(campaign)s, NULL::uuid) p
    LEFT JOIN stewardship_submission ps ON ps.id=p.submission_id
    WHERE %(pledges)s AND p.stage='to_review'
) pl ON true
LEFT JOIN LATERAL (
    SELECT CASE WHEN (v->>'authorized')::boolean THEN (v->>'total')::bigint END
            AS total,
        ARRAY(SELECT (m->>'duid')::bigint
            FROM jsonb_array_elements(coalesce(v->'ministries','[]'::jsonb)) m)
            AS ministries
    FROM stewardship_ministry_followup_v2(
        campaign_uuid => %(campaign)s, filters => %(filters)s::jsonb,
        actor_uuid => %(viewer)s, page_limit => 0) v
    WHERE %(ministry)s
) f ON true
LEFT JOIN LATERAL (
    SELECT CASE WHEN (v->>'authorized')::boolean THEN (
            SELECT count(*) FROM jsonb_array_elements(v->'rows') r
            WHERE r->>'state'='resolved'
              AND r->>'outcome'=ANY(%(roster_outcomes)s)
              AND NOT (r->>'source_resolved')::boolean
              AND NOT EXISTS (SELECT 1 FROM stewardship_refresh_check k
                  WHERE k.request_id=(r->>'id')::uuid AND k.outcome='confirmed'
                    AND k.item_version=(r->>'version')::bigint)
              AND NOT coalesce((
                  SELECT e.entered FROM stewardship_ministry_roster_entry e
                  WHERE e.request_id=(r->>'id')::uuid
                  ORDER BY e.sequence DESC LIMIT 1), false))
        END AS total,
        ARRAY(SELECT (m->>'duid')::bigint
            FROM jsonb_array_elements(coalesce(v->'ministries','[]'::jsonb)) m)
            AS ministries
    FROM stewardship_ministry_followup_v2(
        campaign_uuid => %(campaign)s, filters => %(roster_filters)s::jsonb,
        actor_uuid => %(viewer)s) v
    WHERE %(roster)s
) t ON true
"""


def attach_counts(sections, counts, stages=None):
    """Give each menu entry its open count, in place, only while it is a link.

    Ministry requests also carries Home's To enter by hand count
    (``entry_count``, ``ENTRY_COUNT``), which the menu never shows (R39
    finding 2). ``stages`` are the same read's counts by stage, by entry
    name (``_open_counts``), which My review work's cards show (#2101):
    an entry gets its own, or None when they were not read. Test fixtures
    that draw the chrome call this too, so they attach the counts exactly
    as the chrome does.
    """
    stages = stages or {}
    for section in sections:
        for entry in section["items"]:
            entry["count"] = counts.get(entry["name"]) if entry["url"] else None
            entry["stages"] = stages.get(entry["name"]) if entry["url"] else None
            if entry["name"] == "ministry_report":
                entry["entry_count"] = counts.get(ENTRY_COUNT) if entry["url"] else None


def _open_counts(request, actor, items, campaign):
    """Open item counts for the menu's queue entries, by entry name (#585).

    Only an entry the viewer's menu offers as a link right now gets a count,
    so a viewer without any counted queue (or with all of them greyed out)
    pays no query; otherwise one statement reads them all. The result is
    kept on the request, so another render of the chrome in the same
    request reuses it.
    A count is never cached longer: a stale number misleads in a work queue.

    A view that already read the To enter by hand count for this request
    (Data to fix in ParishSoft, #2113) leaves it on
    ``request._stewardship_entry_count``; the statement then skips the
    roster selection and the menu reuses that number, so a page load runs
    each follow-up selection once.

    The counts only decorate the menu, so they must never take an Admin page
    down: a database error in their statement (for example inside the
    follow-up selection) is logged and the menu renders without numbers,
    and an open view transaction stays usable because the statement runs
    in its own savepoint there.

    The same read's counts by stage, by entry name, are left on
    ``request._stewardship_open_stages`` for the menu's entries (each
    entry's ``stages``, ``attach_counts``), which My review work's cards
    show (#2101).

    Admin Home's Review work counts (#1690) ride on the same statement: the
    view names the parts it wants in ``request._stewardship_review_flags``
    (``review_work.read_flags``), and their placed counts are left on
    ``request._stewardship_review_counts`` for ``review_work.with_counts``,
    or every flagged stream as not read when the statement fails.
    """
    cached = getattr(request, "_stewardship_open_counts", None)
    if cached is not None:
        return cached
    # Whether the menu offers Pledges also says whether the Limitations
    # count includes its Cannot contribute answers.
    offered = {item.name for item in items if item.url and item.name in COUNTED}
    review = getattr(request, "_stewardship_review_flags", None) or {}
    if not any(review.values()):
        review = None
    known = getattr(request, "_stewardship_entry_count", UNREAD)
    counts, stages = {}, {}
    if offered & COUNTED or review:
        # Inside a view's transaction the statement gets a savepoint, so a
        # failure rolls back only the count. Outside one (autocommit), the
        # statement is its own transaction and a failure leaves nothing
        # behind, so the savepoint's two extra statements are skipped.
        isolate = (
            transaction.atomic if connection.in_atomic_block else contextlib.nullcontext
        )
        try:
            with isolate():
                counts, stages, reviewed = _read_open_counts(
                    offered,
                    actor,
                    campaign,
                    review,
                    roster=known is UNREAD,
                    downloaded=request_downloaded(request),
                )
        except DatabaseError as error:
            _record_open_counts_failure(error)
            reviewed = review and review_work.unread(review)
        if review:
            request._stewardship_review_counts = reviewed
    if known is not UNREAD and known is not None and "ministry_report" in offered:
        counts[ENTRY_COUNT] = known
        # My review work's To enter by hand card shows the same number.
        stages["ministry_report"] = (
            {Stage.TO_REVIEW.value: None}
            | stages.get("ministry_report", {})
            | {Stage.TO_ENTER_BY_HAND.value: known}
        )
    request._stewardship_open_counts = counts
    request._stewardship_open_stages = stages
    return counts


def _read_open_counts(
    offered,
    actor,
    campaign,
    review=None,
    *,
    followup=True,
    roster=True,
    downloaded=None,
):
    """Run ``OPEN_COUNTS`` for the ``offered`` entries; see ``_open_counts``.

    ``followup`` and ``roster`` switch off Ministry requests' To review
    selection and its To enter by hand selection even when Ministry
    requests is offered: each runs the follow-up function, so a caller that
    needs only one of the two pays only for that one.

    With ``review`` (``review_work.read_flags``), Admin Home's
    ``review_work.COUNTS`` runs in the same statement, cross-joined to the
    menu's one row. Returns ``(counts, stages, review counts)``: the menu's
    counts, the same read's counts by stage of each counted entry, by entry
    name (see ``_open_counts``), and the review counts (None without
    ``review``).

    ``downloaded`` is the request's shared ParishSoft download check
    (``request_downloaded``), asked only when the Pledges count is read.
    """
    from parishkit.stewardship.reports import (
        census_changes,
        information_worklist,
        limitation_worklist,
    )
    from parishkit.stewardship.reports.limitations import LimitationsQuery
    from parishkit.stewardship.reports.ministry_followup import (
        FollowupQuery,
        in_scope,
        selection_filters,
    )
    from parishkit.stewardship.workflows.roster import ROSTER_OUTCOMES, can_tick

    # The menu offers Census changes only to those who may read it; checking
    # the page's own capability again keeps the count from ever reaching
    # anyone else, whatever the caller passes.
    census = "census_changes" in offered and allows(actor, Capability.VIEW_CENSUS)
    # Likewise the To enter by hand count, which only Administrators and
    # Staff see (can_tick), and only past go-live (ROSTER_STATES).
    asked = roster
    roster = (
        roster
        and "ministry_report" in offered
        and can_tick(actor)
        and getattr(campaign, "state", None) in ROSTER_STATES
    )
    # Pledges' own admission (FINANCIAL_DETAIL), checked again, for its own
    # count and Limitations' Cannot contribute part.
    pledges = "pledges_to_enter" in offered and allows(
        actor, Capability.FINANCIAL_DETAIL
    )
    sql = (
        OPEN_COUNTS.replace("{census}", census_changes.OPEN_GROUPS)
        .replace("{information}", information_worklist.OPEN_GROUPS)
        .replace("{limitations}", limitation_worklist.OPEN_GROUPS)
    )
    if review:
        # Both statements are one row; their shared parameters (campaign,
        # viewer, roster_filters) carry the same values.
        sql = (
            f"SELECT * FROM ({sql}) menu CROSS JOIN ({review_work.statement()}) review"
        )
    with connection.cursor() as cursor:
        cursor.execute(
            sql,
            {
                "census": census,
                "information": "information_queue" in offered,
                "ministry": followup and "ministry_report" in offered,
                "roster": roster,
                # The page admits CAMPAIGN_REPORT; checked again here, as
                # for Census changes.
                "limitations": "limitations_report" in offered
                and allows(actor, Capability.CAMPAIGN_REPORT),
                "limitation_filters": json.dumps(LimitationsQuery().form_values()),
                "pledges": pledges,
                "downloaded": bool(pledges and downloaded and downloaded()),
                "campaign": campaign.pk,
                "filters": selection_filters(FollowupQuery()),
                # The roster rows' own selection (read_rows for Staff).
                "roster_filters": selection_filters(
                    FollowupQuery(state="resolved", history="all"), every_member=True
                ),
                "roster_outcomes": sorted(ROSTER_OUTCOMES),
                "viewer": actor.identity,
            }
            | review_work.parameters(review or {}),
        )
        values = cursor.fetchone()
    # The menu's columns by name (MENU_COLUMNS); My review work's own
    # (review_work.COUNTS, cross-joined after them) follow.
    row = dict(zip(MENU_COLUMNS, values[: len(MENU_COLUMNS)], strict=True))
    ministry, ministries = row["ministry"], row["ministries"]
    roster_count, roster_ministries = row["roster"], row["roster_ministries"]
    reviewed = (
        review_work.placed(review, values[len(MENU_COLUMNS) :]) if review else None
    )
    # A count naming a Ministry outside Python's view of the actor would be
    # refused by the page (in_scope), so it gets no count either.
    if ministry is not None and not in_scope(actor, True, ministries):
        ministry = None
    if roster_count is not None and not in_scope(actor, True, roster_ministries):
        roster_count = None
    # Before go-live no request has been resolved live, so the To enter by
    # hand count is not read and nothing is there (ROSTER_STATES). A caller
    # that switched the selection off (``roster``) supplies its own count.
    entry = roster_count
    if entry is None and asked and not roster and "ministry_report" in offered:
        entry = 0 if can_tick(actor) else None
    # Each counted entry's stream by stage: each stream's stored state
    # placed by the shared stage mapping, from the groups its statement
    # read (NULL, and so None, when the entry was not asked for or its
    # report is unavailable).
    stages = {
        "census_changes": _decoded(row["census_groups"], census_changes.group_stages),
        "information_queue": _decoded(
            row["information"], information_worklist.group_stages
        ),
        "limitations_report": _decoded(
            row["limitations"],
            lambda groups: limitation_worklist.group_stages(
                groups, row["contributions"]
            ),
        ),
    }
    if ministry is not None or entry is not None:
        stages["ministry_report"] = {
            Stage.TO_REVIEW.value: ministry,
            Stage.TO_ENTER_BY_HAND.value: entry,
        }
    # Pledges' To review card is the menu's Pledges count (#2125, #2101):
    # one read of the pledge stages. The cards for its other stages come
    # from review_work's own read; no number (NULL) means unavailable.
    if row["pledges"] is not None:
        stages["pledges_to_enter"] = {Stage.TO_REVIEW.value: row["pledges"]}
    stages = {name: found for name, found in stages.items() if found is not None}
    # The menu counts each stream's open stages: Census changes its To
    # review and Conflict for everyone who may read it (Administrator
    # decision, 2026-10-18), the others To review, each worklist's default.
    census = stages.get("census_changes")
    information = stages.get("information_queue")
    limitations = stages.get("limitations_report")
    # An entry not asked for, or a Ministry follow-up the page would show as
    # off or unavailable, reads NULL and gets no count.
    menu = {
        name: count
        for name, count in (
            ("census_changes", _open(census, census_changes.OPEN_STAGES)),
            ("information_queue", _open(information, (Stage.TO_REVIEW,))),
            ("ministry_report", ministry),
            (ENTRY_COUNT, roster_count),
            ("limitations_report", _open(limitations, (Stage.TO_REVIEW,))),
            ("pledges_to_enter", row["pledges"]),
        )
        if count is not None
    }
    return menu, stages, reviewed


def _decoded(value, group_stages):
    """A JSON groups column (text or decoded) placed by stage, or None for NULL.

    A stored state the stage mapping does not place is logged and reads as
    not read (None), so it never takes an Admin page down.
    """
    if value is None:
        return None
    try:
        return group_stages(json.loads(value) if isinstance(value, str) else value)
    except ValueError as error:
        _record_open_counts_failure(error)
        return None


def _open(stages, open_stages):
    """The menu's count of a stream: its ``open_stages``' total, or None unread."""
    if stages is None:
        return None
    return sum(stages.get(stage.value, 0) for stage in open_stages)


def roster_entry_count(actor, campaign):
    """Ministry requests' To enter by hand count (``ENTRY_COUNT``), or None.

    The same ``OPEN_COUNTS`` read the menu runs for Home's Next steps and My
    review work, asked for Ministry requests alone and for its roster
    selection only (the To review selection is not run), so Data to fix in
    ParishSoft (#2113) shows the number those cards show. None when the
    actor may not enter roster changes, the campaign is not past go-live,
    or the selection refuses (see ``_read_open_counts``).
    """
    counts = _read_open_counts({"ministry_report"}, actor, campaign, followup=False)[0]
    return counts.get(ENTRY_COUNT)


def _record_open_counts_failure(error):
    """Log that the menu's open counts could not be read (#585).

    A WARNING process-log line naming only the failure's closed kind, under
    the reviewed ``report_shaping_failed`` event (a display-only step
    failed), as the other report pages log a failed display step
    (admin_report_reads). No durable System logs entry: the web login may
    write only its reviewed operational entries (#389 L2), and a failed
    count must never take the page down with it.
    """
    emit_failure(
        error,
        event=Event.REPORT_SHAPING_FAILED,
        level=logging.WARNING,
        name_class=True,
    )


def _current_campaign(configuration):
    """The current campaign with its active configuration, in at most one query.

    The menu's reason checks read the campaign's modules. The configuration
    an owning view or the branding helper lends the chrome may carry neither
    relation, and reading them lazily would take two queries, one more than
    before for Staff and Ministry leaders (the Administrator's menu read the
    modules already). Reuse the relations when both are cached.
    """
    if configuration.current_campaign_id is None:
        return None
    if SystemConfiguration._meta.get_field("current_campaign").is_cached(configuration):
        campaign = configuration.current_campaign
        if Campaign._meta.get_field("active_configuration").is_cached(campaign):
            return campaign
    return (
        Campaign.objects.select_related("active_configuration")
        .filter(pk=configuration.current_campaign_id)
        .first()
    )


def _debug_in_production(configuration):
    """Whether this process logs debug detail while the deployment is in Production.

    Debug logs can hold personal data, so they are for disposable pre-launch
    data only. Nothing refuses to start with the switch on; every Admin page
    warns instead, so whoever sees it can ask the operator to turn it off.
    Only this web process's own effective state is visible here, which the
    debug-off switch already turns off (``debug_switch``). The banner ends
    with it on purpose (ADM-13 PR 6c, #1207): reading every service's status
    record would add a query to every Admin page, and System health's
    problems (and Home's) already name each other service still logging in
    debug form.
    """
    return configuration.mode == "production" and debug_logging_enabled()


def _setup_pending():
    """Whether initial setup is incomplete; an unreadable marker counts as pending.

    The access gate already routes every other Admin page to the wizard until
    the completion marker exists. This only keeps the chrome from offering
    links that would bounce back to the wizard.
    """
    try:
        return not runtime().configured()
    except (ConfigError, LimiterUnavailable):
        return True


def _background_counts(actor, now):
    """Nonterminal task counts for the header, when the actor may see them."""
    if not allows(actor, Capability.BACKGROUND_WORK):
        return None
    return TaskRun.objects.filter(state__in=NONTERMINAL_STATES).aggregate(
        total=Count("id"),
        running=Count("id", filter=Q(state="running", lease_expires_at__gt=now)),
    )


def _setup_chrome(actor, configuration, session):
    """Offer only the setup wizard until initial setup completes.

    The background-work indicator stays: its read-only pages remain open
    during setup so an Administrator can watch the setup's own data load.
    Other operational indicators (delivery and presence counts, critical
    alerts, the mode-configuration link) point at pages that are unavailable
    before setup, so ``admin`` is False here: it is a presentation flag only
    and grants or removes no authority.
    """
    now = database_now()
    return {
        "admin": False,
        "setup_pending": True,
        "parish_name": None,
        "setup_url": reverse("admin:setup"),
        "sections": [],
        "breadcrumbs": [],
        "flow_steps": [],
        "back": None,
        "testing": configuration.mode == "testing",
        "testing_recipient": None,
        "debug_in_production": _debug_in_production(configuration),
        "restored": configuration.restore_review_required,
        "paused": False,
        "delivery_pause": None,
        "go_live": False,
        "key_changes": [],
        "critical_count": 0,
        "critical_events": [],
        "critical_shown": "",
        "critical_limit": ACKNOWLEDGE_LIMIT,
        "critical_since_day": None,
        "background": _background_counts(actor, now),
        "delivery_unknown": None,
        "presence_count": None,
        "server_now": now,
        "absolute_deadline": session.expires_at,
        "idle_deadline": min(session.expires_at, session.last_activity_at + ADMIN_IDLE),
    }
