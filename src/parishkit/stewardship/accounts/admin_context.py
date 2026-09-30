"""Capability-filtered Admin chrome; public/Family pages never query this context."""

from django.db.models import Count, Q
from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from parishkit.config import ConfigError
from parishkit.stewardship.audit.critical_events import WINDOW as CRITICAL_WINDOW
from parishkit.stewardship.audit.critical_events import summary as critical_summary
from parishkit.stewardship.campaigns.credential_models import CampaignCredentialState
from parishkit.stewardship.campaigns.domain import CampaignState
from parishkit.stewardship.campaigns.lifecycle import structural_edit_admitted
from parishkit.stewardship.jobs.delivery_metadata import alert_counts
from parishkit.stewardship.jobs.models import NONTERMINAL_STATES, TaskRun

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
    campaign = configuration.current_campaign
    items = _navigation_items(actor, admin, campaign, configuration)
    # Presentation only: reuse the instant the owning view read inside its own
    # read snapshot (never an earlier one from before a lock wait).
    now = getattr(request, "_stewardship_display_now", None) or database_now()
    counts = _background_counts(actor, now)
    parish = getattr(configuration.active_configuration, "parish", None)
    critical, delivery_unknown = (
        alert_counts(now - CRITICAL_WINDOW) if admin else ({}, None)
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

        items.append(
            (
                "campaign",
                "delivery_control",
                _("Delivery controls"),
                reverse("admin:delivery_control", args=[campaign.pk]),
            )
        )
        if campaign.delivery_paused:
            delivery_pause = {
                "inventory": inventory(campaign.pk),
                "reason": campaign.pause_reason,
                "since": campaign.paused_at,
                "actor": PortalUser.objects.filter(pk=campaign.pause_actor_id)
                .values_list("email", flat=True)
                .first(),
                "url": reverse("admin:delivery_control", args=[campaign.pk]),
            }
    sections, breadcrumbs = admin_navigation.build(
        getattr(request, "resolver_match", None), items
    )
    return {
        "admin_chrome": {
            "admin": admin,
            "parish_name": parish.name if parish else None,
            "home_url": reverse("admin:index"),
            "home_current": bool(breadcrumbs) and len(breadcrumbs) == 1,
            "sections": sections,
            "breadcrumbs": breadcrumbs,
            "testing": configuration.mode == "testing",
            "testing_recipient": configuration.testing_recipient if admin else None,
            "restored": configuration.restore_review_required,
            "paused": bool(campaign and campaign.delivery_paused),
            "delivery_pause": delivery_pause,
            "go_live": go_live,
            # The Family portal maintenance switch (family_maintenance.py).
            # Every Admin role sees this: chairs field the Families' calls.
            "family_closed": family_maintenance.current_state().closed,
            "family_portal_url": reverse("admin:family_portal"),
            "critical_count": sum(critical.values()),
            "critical_events": critical_summary(critical),
            # The banner's System logs link filters from this UTC day onward.
            "critical_since_day": (now - CRITICAL_WINDOW).date().isoformat(),
            "background": counts,
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
    """Sidebar entries ``(section, url_name, label, url)`` the actor may open.

    Each entry uses the same capability the page itself checks, so the menu
    and the pages cannot disagree; the menu is still not the security
    boundary. Campaign entries follow the current campaign.
    """
    items = []

    def add(section, name, label, *args):
        """Append one entry, reversing its Admin URL."""
        items.append((section, name, label, reverse(f"admin:{name}", args=args)))

    if admin:
        if campaign:
            values = campaign.active_configuration.values or {}
            add("campaign", "campaign_settings", _("Campaign settings"), campaign.pk)
            # Theme artwork (#248) stays editable while the campaign runs.
            if campaign.state != "archived":
                add("campaign", "artwork_settings", _("Campaign images"), campaign.pk)
            if campaign.state != "archived":
                add("campaign", "content_catalog", _("Pages and emails"), campaign.pk)
            add("campaign", "schedule_settings", _("Mail schedules"), campaign.pk)
            # Share options are editable only on an unlocked Testing draft; the
            # page refuses anything else, so do not offer a link that fails.
            if (
                "financial" in values.get("modules", ())
                and configuration.mode == "testing"
                and structural_edit_admitted(
                    CampaignState(campaign.state),
                    ever_active=campaign.ever_active,
                    locked=campaign.structural_locked,
                )
            ):
                add("campaign", "share_settings", _("Share options"), campaign.pk)
            if (
                "ministry" in values.get("modules", ())
                and configuration.mode == "testing"
                and structural_edit_admitted(
                    CampaignState(campaign.state),
                    ever_active=campaign.ever_active,
                    locked=campaign.structural_locked,
                )
            ):
                add("campaign", "talent_settings", _("Member talents"), campaign.pk)
            if campaign.state == "draft":
                add("campaign", "go_live", _("Go-live readiness"), campaign.pk)
        else:
            add("campaign", "campaign_new", _("New campaign"))
    if allows(actor, Capability.CAMPAIGN_REPORT):
        add("reports", "reports", _("Campaign reports"))
    if allows(actor, Capability.MINISTRY_REPORT) or any(
        allows(actor, Capability.MINISTRY_REPORT, ministry_id=duid)
        for duid in actor.ministries
    ):
        add("reports", "ministry_reports", _("Ministry reports"))
    if campaign and allows(actor, Capability.FAMILY_CODES):
        add("reports", "family_directory", _("Family directory"), campaign.pk)
    if admin and campaign:
        add(
            "reports",
            "weekly_digest_manual",
            _("Manual information report"),
            campaign.pk,
        )
    if admin:
        add("parish", "parish_settings", _("Parish settings"))
        add("parish", "branding_settings", _("Parish logos"))
        add("parish", "hosted_files", _("Hosted files"))
        add("parish", "integrations", _("Integrations"))
        add("parish", "ministries", _("Ministry activity"))
    if allows(actor, Capability.MANAGE_USERS):
        add("users", "users", _("Portal users"))
    if admin:
        add("system", "background", _("Background work"))
        add("system", "deliveries", _("Outgoing mail"))
        add("system", "presence", _("Families on the form now"))
        add("system", "family_portal", _("Family portal availability"))
    if allows(actor, Capability.SYSTEM_LOGS):
        add("system", "logs", _("System logs"))
    return items


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
        "testing": configuration.mode == "testing",
        "testing_recipient": None,
        "restored": configuration.restore_review_required,
        "paused": False,
        "delivery_pause": None,
        "go_live": False,
        "critical_count": 0,
        "critical_events": [],
        "critical_since_day": None,
        "background": _background_counts(actor, now),
        "delivery_unknown": None,
        "presence_count": None,
        "server_now": now,
        "absolute_deadline": session.expires_at,
        "idle_deadline": min(session.expires_at, session.last_activity_at + ADMIN_IDLE),
    }
