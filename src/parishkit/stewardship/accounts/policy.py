"""One capability vocabulary for future views, jobs, downloads and report columns.

Principals are point-in-time decisions, not authorization tokens. Mutations
must reload policy under their own configuration/admission serialization. Google
verification and session lifecycle are ARC-04's boundary, not trusted UUID input.
"""

from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

from parishkit.config import ConfigError

from .policy_schema import normalized_domain, normalized_email


class Capability(StrEnum):
    """Canonical operations rather than scattered role-name comparisons."""

    CONFIGURE = "configure"
    MANAGE_USERS = "manage_users"
    FAMILY_CODES = "family_codes"
    BACKGROUND_WORK = "background_work"
    REPORT_EXPORT = "report_export"
    CAMPAIGN_REPORT = "campaign_report"
    MINISTRY_REPORT = "ministry_report"
    FINANCIAL_DETAIL = "financial_detail"
    ADDITIONAL_FOLLOWUP = "additional_followup"
    MINISTRY_FOLLOWUP = "ministry_followup"
    MANUAL_CENSUS = "manual_census"
    VIEW_CENSUS = "view_census"
    PUBLISH_CENSUS = "publish_census"
    SYSTEM_LOGS = "system_logs"
    PURGE = "purge"
    FAMILY_READ = "family_read"
    FAMILY_SUBMIT = "family_submit"


@dataclass(frozen=True)
class Principal:
    """Immutable current roles and object scopes, without credentials or email."""

    identity: UUID
    roles: frozenset[str] = frozenset()
    ministries: frozenset[int] = frozenset()
    family_id: UUID | None = None

    def __post_init__(self):
        """Keep principal namespaces and scopes disjoint and immutable."""
        if (
            not isinstance(self.identity, UUID)
            or type(self.roles) is not frozenset
            or type(self.ministries) is not frozenset
        ):
            raise TypeError("Invalid principal identity or scopes.")
        if self.roles - {"administrator", "staff", "ministry_leader"} or any(
            type(item) is not int or not 1 <= item <= 2**63 - 1
            for item in self.ministries
        ):
            raise ValueError("Invalid principal roles or ministry scope.")
        if self.family_id is not None and (
            not isinstance(self.family_id, UUID) or self.roles or self.ministries
        ):
            raise ValueError("Family and administration principals are separate.")


def resolve_roles(email, hosted_domain, records, active_seeded=frozenset()):
    """Exact address replaces domain rules; seeded scope fails closed on suspension."""
    email = normalized_email(email)
    domain = email.rsplit("@", 1)[1]
    try:
        hosted = None if hosted_domain is None else normalized_domain(hosted_domain)
    except ConfigError:
        # Bad optional hosted-domain evidence grants no domain authority; it
        # must not override an otherwise valid exact-address decision.
        hosted = None
    assignments = [
        record
        for record in records
        if record["values"]["kind"] == "assignment"
        and record["values"]["email"] == email
    ]
    ministries = frozenset(
        record["values"]["ministry_duid"]
        for record in assignments
        if record["values"]["source"] == "manual" or record["id"] in active_seeded
    )
    exact = next(
        (
            record["values"]
            for record in records
            if record["values"]["kind"] == "address"
            and record["values"]["email"] == email
        ),
        None,
    )
    if exact is not None:
        roles = set(exact["roles"])
        if (
            exact["creation_origin"] == "chair-seed"
            and set(exact["grants"].get("ministry_leader", {})) == {"chair-seed"}
            and not ministries
        ):
            roles.discard("ministry_leader")
    else:
        matching = next(
            (
                record["values"]
                for record in records
                if record["values"]["kind"] == "domain"
                and record["values"]["domain"] == domain == hosted
            ),
            None,
        )
        roles = set(matching["roles"]) if matching else set()
    if "administrator" in roles:
        roles.update(("staff", "ministry_leader"))
    return frozenset(roles), ministries


def allows(principal, capability, *, ministry_id=None, family_id=None):
    """Deny unknown capabilities and enforce object scope independently of roles."""
    if not isinstance(principal, Principal) or not isinstance(capability, Capability):
        return False
    if principal.family_id is not None:
        return family_id == principal.family_id and capability in {
            Capability.FAMILY_READ,
            Capability.FAMILY_SUBMIT,
            Capability.FINANCIAL_DETAIL,
        }
    if capability in {Capability.FAMILY_READ, Capability.FAMILY_SUBMIT}:
        return False
    if "administrator" in principal.roles:
        return True
    if "staff" in principal.roles:
        return capability in {
            Capability.FAMILY_CODES,
            Capability.REPORT_EXPORT,
            Capability.CAMPAIGN_REPORT,
            Capability.MINISTRY_REPORT,
            Capability.FINANCIAL_DETAIL,
            Capability.ADDITIONAL_FOLLOWUP,
            Capability.MINISTRY_FOLLOWUP,
            Capability.MANUAL_CENSUS,
            Capability.VIEW_CENSUS,
        }
    return (
        "ministry_leader" in principal.roles
        and type(ministry_id) is int
        and 1 <= ministry_id <= 2**63 - 1
        and ministry_id in principal.ministries
        and capability
        in {
            Capability.MINISTRY_REPORT,
            Capability.MINISTRY_FOLLOWUP,
            Capability.REPORT_EXPORT,
        }
    )


def report_columns(principal, requested, *, ministry_id=None):
    """Ministry-only exports cannot leak financial fields or Family manual codes."""
    if not isinstance(principal, Principal):
        return ()
    if "administrator" in principal.roles or "staff" in principal.roles:
        return tuple(requested)
    if not allows(principal, Capability.MINISTRY_REPORT, ministry_id=ministry_id):
        return ()
    permitted = {
        "member_name",
        "member_duid",
        "gender",
        "age",
        "phones",
        "emails",
        "addresses",
        "ministry",
        "chairs",
        "year",
        "interest",
        "outcome",
        "email_date",
        "phone_date",
    }
    return tuple(column for column in requested if column in permitted)


def current_principal(store, user_id):
    """Reload verified YAML-backed policy and source overlays on every request."""
    from .configuration_installation import coherent_configuration
    from .policy_models import AssignmentOverlay, MinistryAssignment, PortalUser

    if not isinstance(user_id, UUID):
        raise TypeError("An opaque user identity is required.")
    runtime = coherent_configuration(store)
    user = PortalUser.objects.get(pk=user_id, disabled=False)
    email = normalized_email(user.email)
    # Coherence already verified this exact canonical document against every
    # normalized projection. Filter once, without a second corpus materialization.
    records = [
        record
        for record in runtime.active_configuration.canonical_document["sections"].get(
            "login_rules", []
        )
        if record["values"].get("email") == email
        or record["values"].get("domain") == email.rsplit("@", 1)[1]
    ]
    seeded = MinistryAssignment.objects.filter(
        configuration=runtime.active_configuration,
        email=email,
        source="chair-seed",
    ).values_list("record_id", flat=True)
    # A missing promoted-source overlay is not proof of a current Chairperson.
    active = set(
        AssignmentOverlay.objects.filter(
            assignment_record_id__in=seeded, active=True
        ).values_list("assignment_record_id", flat=True)
    )
    roles, ministries = resolve_roles(
        email,
        user.hosted_domain,
        records,
        frozenset(str(identifier) for identifier in active),
    )
    return Principal(user.pk, roles, ministries)
