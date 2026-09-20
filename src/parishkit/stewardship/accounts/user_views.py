"""Read-only Administrator review of who may sign in to the portal and why."""

from django.db import DatabaseError
from django.db.models import Max, Q
from django.db.models.functions import Lower
from django.shortcuts import render
from django.views.decorators.http import require_safe

from parishkit.config import ConfigError
from parishkit.stewardship.audit.models import AuditEvent
from parishkit.stewardship.audit.schemas import Action, ActorKind, Outcome
from parishkit.stewardship.audit.services import record_action
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.storage import StaleRecordError
from parishkit.stewardship.web.contracts import filters

from .admin_editing import editable_configuration, error_response
from .authentication import runtime
from .limiting import LimiterUnavailable
from .policy import Capability, allows, confirmed_seeded
from .policy_models import PortalUser
from .sessions import authenticated_admin
from .user_rows import (
    Policy,
    address_rows,
    disclosed,
    domain_assignment_rows,
    domain_rows,
)


def _principal(request, service, *, read_only=False):
    """Only an Administrator may see who else holds access."""
    actor = authenticated_admin(
        request, store=service.store, activity=not read_only, read_only=read_only
    )
    if not allows(actor, Capability.MANAGE_USERS):
        raise PermissionError("Portal user management requires an Administrator.")
    return actor


def _identities(records):
    """Only the Google identities this policy names, with successful sign-ins.

    Every verified Google attempt records an identity and refreshes its
    verification time, including a stranger's attempt that policy then denies.
    So the read is bounded by policy, not by attempts, and a sign-in means the
    durable login event written only when a session was actually issued.
    """
    emails = {
        record["values"]["email"]
        for record in records
        if record["values"]["kind"] != "domain"
    }
    domains = {
        record["values"]["domain"]
        for record in records
        if record["values"]["kind"] == "domain"
    }
    rows = list(
        PortalUser.objects.annotate(address=Lower("email"))
        .filter(Q(address__in=emails) | Q(hosted_domain__in=domains))
        .values("id", "email", "hosted_domain", "disabled")
    )
    logins = dict(
        AuditEvent.objects.filter(
            event_type="admin_login", actor_id__in=[row["id"] for row in rows]
        )
        .values_list("actor_id")
        .annotate(latest=Max("created_at"))
    )
    return [row | {"last_login": logins.get(row["id"])} for row in rows]


@require_safe
def users(request):
    """List the applied login rules, what policy grants now, and provenance.

    The work lock keeps the applied policy, the source overlays and the Google
    identities one coherent observation; separate READ COMMITTED statements could
    pair a newly activated rule with an older overlay. That lock also serializes
    the whole system's admissions, so only the observation, the access recheck
    and the audit run inside it; shaping and rendering happen after release.
    Editing arrives later through configuration requests, never these reads.

    Like the Admin editors it sits beside, the page is unavailable before setup
    completes and during a restore review, when the applied configuration is not
    yet trusted as the parish's own.
    """
    try:
        service = runtime()
        actor = _principal(request, service)
        # The page takes no parameters, so an address never reaches a URL or log.
        filters(request.GET, allowed=set())
        with work_transaction():
            configuration = editable_configuration(service)
            records = configuration.active_configuration.canonical_document[
                "sections"
            ].get("login_rules", [])
            identities = _identities(records)
            # The same definition of a confirmed Chairperson that sign-in uses.
            active = confirmed_seeded(configuration.active_configuration)
            # A demotion between admission and this observation must not
            # disclose the list, so current access is rechecked before anything
            # is audited as viewed or rendered.
            _principal(request, service, read_only=True)
            record_action(
                Action.USERS_VIEWED,
                actor_kind=ActorKind.PORTAL_USER,
                actor_id=actor.identity,
                # A count only: an audit row never carries an address or a role.
                context={"outcome": Outcome.SUCCEEDED, "count": disclosed(records)},
            )
        policy = Policy(records, identities, active)
        response = render(
            request,
            "stewardship/users.html",
            {
                "domains": domain_rows(policy),
                "addresses": address_rows(policy),
                "domain_assignments": domain_assignment_rows(policy),
            },
        )
        response["Cache-Control"] = "no-store"
        return response
    except (
        ConfigError,
        DatabaseError,
        LimiterUnavailable,
        PermissionError,
        ValueError,
        StaleRecordError,
    ) as error:
        return error_response(error)
