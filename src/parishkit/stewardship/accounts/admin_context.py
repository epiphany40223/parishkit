"""Capability-filtered Admin chrome; public/Family pages never query this context."""

from django.db.models import Count, Q
from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from parishkit.config import ConfigError
from parishkit.stewardship.audit.critical_events import WINDOW as CRITICAL_WINDOW
from parishkit.stewardship.audit.critical_events import summary as critical_summary
from parishkit.stewardship.campaigns.credential_models import CampaignCredentialState
from parishkit.stewardship.jobs.delivery_metadata import alert_counts
from parishkit.stewardship.jobs.models import NONTERMINAL_STATES, TaskRun

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
    navigation = [(reverse("admin:index"), _("Home"))]
    if allows(actor, Capability.CAMPAIGN_REPORT):
        navigation.append((reverse("admin:reports"), _("Campaign reports")))
    if allows(actor, Capability.MINISTRY_REPORT) or any(
        allows(actor, Capability.MINISTRY_REPORT, ministry_id=duid)
        for duid in actor.ministries
    ):
        navigation.append((reverse("admin:ministry_reports"), _("Ministry reports")))
    if admin:
        navigation.extend(
            [
                (reverse("admin:parish_settings"), _("Parish settings")),
                (reverse("admin:branding_settings"), _("Parish logos")),
                (reverse("admin:integrations"), _("Integrations")),
                (reverse("admin:ministries"), _("Ministry activity")),
                (reverse("admin:background"), _("Background work")),
                (reverse("admin:deliveries"), _("Outgoing mail")),
            ]
        )
    # The same capabilities the pages themselves check, so they cannot disagree.
    if allows(actor, Capability.MANAGE_USERS):
        navigation.append((reverse("admin:users"), _("Portal users")))
    if allows(actor, Capability.SYSTEM_LOGS):
        navigation.append((reverse("admin:logs"), _("System logs")))
    if campaign and allows(actor, Capability.FAMILY_CODES):
        navigation.extend(
            [
                (
                    reverse("admin:family_directory", args=[campaign.pk]),
                    _("Family codes"),
                ),
                (
                    reverse("admin:postal_directory", args=[campaign.pk]),
                    _("Postal outreach"),
                ),
            ]
        )
    if admin:
        navigation.append(
            (
                reverse("admin:campaign_settings", args=[campaign.pk])
                if campaign
                else reverse("admin:campaign_new"),
                _("Campaign settings") if campaign else _("New campaign"),
            )
        )
        if campaign:
            navigation.append(
                (
                    reverse("admin:weekly_digest_manual", args=[campaign.pk]),
                    _("Manual information report"),
                )
            )
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

        navigation.append(
            (
                reverse("admin:delivery_control", args=[campaign.pk]),
                _("Delivery controls"),
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
    return {
        "admin_chrome": {
            "admin": admin,
            "parish_name": parish.name if parish else None,
            "navigation": [{"url": url, "label": label} for url, label in navigation],
            "testing": configuration.mode == "testing",
            "testing_recipient": configuration.testing_recipient if admin else None,
            "restored": configuration.restore_review_required,
            "paused": bool(campaign and campaign.delivery_paused),
            "delivery_pause": delivery_pause,
            "go_live": go_live,
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
        "navigation": [{"url": reverse("admin:setup"), "label": _("Initial setup")}],
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
