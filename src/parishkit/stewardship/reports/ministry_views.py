"""The Ministry report's admission and read audit, for ``report ministry``.

The Ministry requests page itself is the follow-up queue (#1133,
``ministry_followup_views``); its separate summary page was retired. The
command line's ``report ministry`` still reads the Ministry summary, with
this module's principal check and ``ministry_report_viewed`` audit.
"""

from django.db import transaction

from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.accounts.sessions import authenticated_admin
from parishkit.stewardship.audit.schemas import Action, ActorKind
from parishkit.stewardship.audit.services import record_action

from .ministries import can_report, unscoped


def _principal(request, store, *, read_only=False):
    """Authenticate native Google sessions and require a current reporting scope."""
    actor = authenticated_admin(
        request, store=store, activity=not read_only, read_only=read_only
    )
    if not can_report(actor):
        raise unscoped(actor, "Ministry report access is unavailable.")
    return actor


def _audit(actor, campaign_id, query, outcome, count=0, total=0, scope=()):
    """Record each displayed Ministry DUID, never names, contacts or search text."""
    # An audit append takes no row locks, so it need not join the
    # writers' work order; waiting there stalled report pages behind
    # every source promotion and installer.
    with transaction.atomic():
        system = SystemConfiguration.objects.select_related(
            "active_configuration__parish"
        ).get()
        for ministry in scope or (None,):
            context = {
                "outcome": outcome,
                "count": count,
                "matching_count": total,
                "page": query.page,
                "search_used": bool(query.search),
            }
            if ministry is not None:
                context["ministry_duid"] = ministry
            record_action(
                Action.MINISTRY_REPORT_VIEWED,
                actor_kind=ActorKind.PORTAL_USER,
                actor_id=actor.identity,
                subject_id=campaign_id,
                parish_id=system.active_configuration.parish.pk,
                campaign_id=campaign_id,
                context=context,
            )
