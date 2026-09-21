"""Read-only Administrator review of who may sign in to the portal and why."""

from django.db import DatabaseError, connection, transaction
from django.db.models import Max, Q
from django.shortcuts import render
from django.views.decorators.http import require_safe

from parishkit.config import ConfigError
from parishkit.stewardship.audit.models import AuditEvent
from parishkit.stewardship.audit.schemas import Action, ActorKind, Outcome
from parishkit.stewardship.audit.services import record_action
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.source.snapshot_models import SourceCurrent
from parishkit.stewardship.storage import StaleRecordError
from parishkit.stewardship.web.contracts import filters

from .admin_editing import editable_configuration, error_response, principal
from .authentication import runtime
from .chair_rows import suggestion_rows
from .limiting import LimiterUnavailable
from .ministry_activity import active_ministries
from .policy import Capability, confirmed_seeded
from .policy_models import PortalUser
from .user_rows import (
    ROLE_LABELS,
    AppliedPolicy,
    address_rows,
    domain_assignment_rows,
    domain_rows,
)
from .user_rules import ROLE_ORDER


def policy_identities(records):
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
    # Addresses are stored normalized, so the stored column is compared as is.
    # An identity at a configured domain is history for that domain's row even
    # when no rule names its address and it presented no hosted claim, such as
    # a consumer account whose exact rule was since removed; the evaluator's
    # stricter claim test still decides whom the rule authorizes.
    named = Q(email__in=emails)
    for domain in domains:
        named |= Q(email__endswith=f"@{domain}")
    rows = list(
        PortalUser.objects.filter(named).values(
            "id", "email", "hosted_domain", "disabled"
        )
    )
    logins = dict(
        AuditEvent.objects.filter(
            event_type="admin_login", actor_id__in=[row["id"] for row in rows]
        )
        .values_list("actor_id")
        .annotate(latest=Max("created_at"))
    )
    return [row | {"last_login": logins.get(row["id"])} for row in rows]


SUGGESTION_COLUMNS = (
    "member_duid",
    "member_name",
    "ministry_duid",
    "ministry_name",
    "email",
    "publish_email",
    "address_members",
)


def chair_relationships(document):
    """The current source's Chairperson relationships and the active Ministries.

    Read from the schema-owned projection under the observation's lock, for
    the promoted snapshot only, so a relationship is never paired with another
    generation's names. Which of those Ministries the applied activity keeps
    active is decided by the same rule the reconciliation owner applies. With
    no promoted source there is nothing to suggest.
    """
    current = SourceCurrent.objects.filter(singleton=True).first()
    if current is None or current.snapshot_id is None:
        return [], frozenset()
    with connection.cursor() as cursor:
        cursor.execute(
            f"SELECT {','.join(SUGGESTION_COLUMNS)} FROM stewardship_chair_suggestion"
            " WHERE snapshot_id=%s AND organization_id=%s"
            " ORDER BY ministry_duid,email,member_duid,roster_key",
            [current.snapshot_id, current.organization_id],
        )
        relationships = [
            dict(zip(SUGGESTION_COLUMNS, row, strict=True)) for row in cursor
        ]
    ministries = active_ministries(
        document,
        organization_id=current.organization_id,
        catalog_duids=frozenset(item["ministry_duid"] for item in relationships),
    )
    return relationships, ministries


@require_safe
def users(request):
    """Observe under the lock, render outside it, then recheck and audit.

    The work lock keeps the applied policy, the source overlays and the Google
    identities one coherent observation; separate READ COMMITTED statements could
    pair a newly activated rule with an older overlay. That lock also serializes
    the whole system's admissions, so only the observation runs inside it.
    Shaping and rendering happen after release, and only then does a short
    transaction recheck current access and record the view: a response that
    failed to render, or whose reader was revoked meanwhile, never leaves a
    successful disclosure on record. Editing goes through previewed
    configuration requests on its own route, never these reads.

    Like the Admin editors it sits beside, the page is unavailable before setup
    completes and during a restore review, when the applied configuration is not
    yet trusted as the parish's own.
    """
    try:
        service = runtime()
        actor = principal(request, service, capability=Capability.MANAGE_USERS)
        # The page takes no parameters, so an address never reaches a URL or log.
        filters(request.GET, allowed=set())
        with work_transaction():
            configuration = editable_configuration(service)
            records = configuration.active_configuration.canonical_document[
                "sections"
            ].get("login_rules", [])
            identities = policy_identities(records)
            # The same definition of a confirmed Chairperson that sign-in uses.
            active = confirmed_seeded(configuration.active_configuration)
            relationships, ministries = chair_relationships(
                configuration.active_configuration.canonical_document
            )
            # The chrome presents this verified observation, never a newer one.
            request._stewardship_display_configuration = configuration
        policy = AppliedPolicy(records, identities, active)
        tables = {
            "domains": domain_rows(policy),
            "addresses": address_rows(policy),
            "domain_assignments": domain_assignment_rows(policy),
            "suggestions": suggestion_rows(policy, relationships, active=ministries),
        }
        response = render(
            request,
            "stewardship/users.html",
            tables
            | {
                # Every edit form carries the digest it was drawn from, so a
                # change proposed against an older policy is refused as stale.
                "base_digest": configuration.active_configuration.digest,
                "roles": [(role, ROLE_LABELS[role]) for role in ROLE_ORDER],
            },
        )
        with transaction.atomic():
            current = principal(
                request, service, read_only=True, capability=Capability.MANAGE_USERS
            )
            if current.identity != actor.identity:
                raise PermissionError("Portal users reader changed.")
            record_action(
                Action.USERS_VIEWED,
                actor_kind=ActorKind.PORTAL_USER,
                actor_id=current.identity,
                # The rows actually rendered, counted; never an address or role.
                context={
                    "outcome": Outcome.SUCCEEDED,
                    "count": sum(len(rows) for rows in tables.values()),
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
