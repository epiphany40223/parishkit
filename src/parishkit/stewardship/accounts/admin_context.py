"""Capability-filtered Admin chrome; public/Family pages never query this context."""

from datetime import timedelta

from django.db.models import Count, Q
from django.urls import reverse

from parishkit.config import ConfigError
from parishkit.stewardship.audit.critical_events import ACKNOWLEDGE_LIMIT
from parishkit.stewardship.audit.critical_events import WINDOW as CRITICAL_WINDOW
from parishkit.stewardship.audit.critical_events import sign as critical_sign
from parishkit.stewardship.audit.critical_events import summary as critical_summary
from parishkit.stewardship.campaigns.credential_models import CampaignCredentialState
from parishkit.stewardship.campaigns.models import Campaign
from parishkit.stewardship.jobs.delivery_metadata import alert_counts
from parishkit.stewardship.jobs.models import NONTERMINAL_STATES, TaskRun
from parishkit.stewardship.observability import debug_logging_enabled

from . import admin_navigation, family_maintenance
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
    items = _navigation_items(actor, admin, campaign, configuration)
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
    return {
        "admin_chrome": {
            "admin": admin,
            "parish_name": parish.name if parish else None,
            "home_url": reverse("admin:index"),
            "home_current": bool(breadcrumbs) and len(breadcrumbs) == 1,
            "sections": sections,
            "breadcrumbs": breadcrumbs,
            # A multi-step flow's step indicator, and where "Return to" goes.
            "flow_steps": admin_navigation.steps(placed),
            "back": admin_navigation.back(match, placed, items),
            "testing": configuration.mode == "testing",
            "testing_recipient": configuration.testing_recipient if admin else None,
            "debug_in_production": _debug_in_production(configuration),
            "restored": configuration.restore_review_required,
            "paused": bool(campaign and campaign.delivery_paused),
            "delivery_pause": delivery_pause,
            "go_live": go_live,
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
            "delivery_unknown": delivery_unknown,
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


def _navigation_items(actor, admin, campaign, configuration):
    """The menu entries the actor may open: ``MenuItem`` rows in menu order.

    Each entry uses the same capability the page itself checks, so the menu
    and the pages cannot disagree; the menu is still not the security
    boundary. Only the role hides an entry: one the mode or the campaign's
    state does not allow right now has no URL and carries its reason (the
    admin-portal spec's stable menu shape). Campaign entries follow the
    current campaign. ``admin`` is the caller's CONFIGURE decision, reused.
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

    state = admin_navigation.MenuState(campaign, configuration.mode)
    return admin_navigation.menu(state, may_open)


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

    Only a viewer whose menu offers the Family directory now (Administrators
    and Staff, with a current campaign) gets the box, and only if they may
    also open the Family timeline its results link to. It reuses the menu's
    decision, so it costs the header no query. The search route rechecks
    both on every request; this only decides whether to show the box.
    """
    directory = any(item.url for item in items if item.name == "family_directory")
    if not directory or not allows(actor, Capability.CAMPAIGN_REPORT):
        return None
    return {"url": reverse("admin:find_family")}


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
    Only this web process's own environment is visible here.
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
