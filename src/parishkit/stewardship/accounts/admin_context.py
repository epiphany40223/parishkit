"""Capability-filtered Admin chrome; public/Family pages never query this context."""

from datetime import timedelta

from django.db.models import Count, Q
from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from parishkit.stewardship.audit.models import OperationalLog
from parishkit.stewardship.campaigns.credential_models import CampaignCredentialState
from parishkit.stewardship.jobs.models import NONTERMINAL_STATES, TaskRun

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
    admin = allows(actor, Capability.CONFIGURE)
    campaign = configuration.current_campaign
    navigation = [(reverse("admin:index"), _("Home"))]
    if admin:
        navigation.extend(
            [
                (reverse("admin:parish_settings"), _("Parish settings")),
                (reverse("admin:branding_settings"), _("Parish logos")),
                (reverse("admin:integrations"), _("Integrations")),
                (reverse("admin:ministries"), _("Ministry activity")),
                (reverse("admin:background"), _("Background work")),
            ]
        )
    if campaign and allows(actor, Capability.FAMILY_CODES):
        navigation.append(
            (reverse("admin:family_codes", args=[campaign.pk]), _("Family codes"))
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
    now = database_now()
    counts = None
    if allows(actor, Capability.BACKGROUND_WORK):
        counts = TaskRun.objects.filter(state__in=NONTERMINAL_STATES).aggregate(
            total=Count("id"),
            running=Count("id", filter=Q(state="running", lease_expires_at__gt=now)),
        )
    parish = getattr(configuration.active_configuration, "parish", None)
    critical_count = (
        OperationalLog.objects.filter(
            level="CRITICAL", created_at__gte=now - timedelta(hours=24)
        ).count()
        if admin
        else 0
    )
    go_live = bool(
        campaign
        and CampaignCredentialState.objects.filter(
            campaign=campaign, go_live_gate=True
        ).exists()
    )
    return {
        "admin_chrome": {
            "admin": admin,
            "parish_name": parish.name if parish else None,
            "navigation": [{"url": url, "label": label} for url, label in navigation],
            "testing": configuration.mode == "testing",
            "testing_recipient": configuration.testing_recipient if admin else None,
            "restored": configuration.restore_review_required,
            "paused": bool(campaign and campaign.delivery_paused),
            "go_live": go_live,
            "critical_count": critical_count,
            "background": counts,
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
