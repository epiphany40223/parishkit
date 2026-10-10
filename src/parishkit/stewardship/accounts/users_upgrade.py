"""The one offline conversion of sign-in rules into users (#952).

The operations specification's "Users upgrade from sign-in rules" section is
the contract. The conversion follows its read-only preview exactly:

- every address rule that grants a role becomes a user with its highest
  role, keeping its address;
- every recorded Google identity that a hosted-domain rule admits today and
  that has signed in before becomes a user with that rule's highest role
  (Staff or Ministry leader): enabled, its email suffix and recorded
  hosted-domain claim matching the rule, and no address rule for its
  address;
- empty-role (deny) rules are dropped: with domain rules gone, an address
  with no user is already denied.

Disabled identities and a domain rule's people who never signed in are not
converted; the preview lists them. A user's Name is the first ParishSoft
Member name that lists its address, or the address's local part.

Every address that holds Administrator before keeps an Administrator user
after, and the operator must confirm that exact list first. The request
builder and SQL's activation guard each check it again, so the conversion
fails closed rather than lose an Administrator. It runs only offline, as
``pk-stewardship upgrade-users`` under the admin-recovery profile, through
the configuration installer's ordinary checkpoint protocol; a repeat run
with the same operation ID resumes, and a configuration already in the users
form is left unchanged.

``plan_upgrade`` decides everything from plain data, so the rules are unit
tested without a database; ``upgrade_users`` binds it to the deployment.
"""

import json
from dataclasses import dataclass
from uuid import UUID, uuid4

from django.db import connection, transaction

from parishkit.config import ConfigError

from .configuration_installation import _install_request, coherent_configuration
from .configuration_requests import _own_transaction
from .operator_recovery import _text
from .policy_schema import normalized_email
from .request_admission import intake_base
from .request_models import ConfigurationChangeRequest
from .request_patch import UPGRADE_BASES, build_candidate
from .runtime_models import SystemConfiguration
from .users_schema import (
    ROLE_LEVELS,
    SCHEMA,
    UPGRADE_REQUEST_SCHEMA,
    uses_users,
    valid_name,
)


@dataclass(frozen=True)
class Identity:
    """One recorded Google identity, as the conversion needs to see it."""

    email: str
    hosted_domain: str | None
    disabled: bool
    signed_in: bool


def highest_role(roles):
    """The highest level among ``roles``, or None for an empty (deny) rule."""
    held = [role for role in ROLE_LEVELS if role in roles]
    return held[-1] if held else None


def default_name(email, names):
    """The first usable ParishSoft name for the address, else its local part.

    ``names`` is one address's ordered list from ``member_names``. A
    name the users schema would refuse (too long, or with control
    characters) is skipped rather than failing the conversion.
    """
    for name in names:
        try:
            return valid_name(name)
        except ConfigError:
            continue
    return email.rsplit("@", 1)[0]


def plan_upgrade(records, identities, names):
    """Decide the users and the preview from the current rules, without I/O.

    ``records`` are the active configuration's ``login_rules``; ``identities``
    are ``Identity`` values; ``names`` maps an address to its ordered
    ParishSoft names (``member_names``'s mapping; an absent address has
    none). Returns the preview dict, whose ``users`` list (sorted by address)
    is what the conversion writes.
    """
    rules = [record["values"] for record in records]
    addresses = {rule["email"]: rule for rule in rules if rule["kind"] == "address"}
    domains = {rule["domain"]: rule for rule in rules if rule["kind"] == "domain"}
    users = {}
    for email, rule in addresses.items():
        role = highest_role(rule["roles"])
        if role is not None:
            users[email] = role
    # Several identities may share one address (each Google subject is its
    # own identity); the address converts when any of them qualifies.
    admitted, disabled, never, overridden = {}, set(), set(), set()
    for identity in identities:
        # A stored address the policy validator refuses can match no rule
        # reliably, so the whole conversion fails closed rather than guess.
        # The message never repeats the untrusted address.
        # The SQL definer already lowercases it.
        try:
            normalized_email(identity.email)
        except ConfigError:
            raise ConfigError(
                "A recorded Google identity has an invalid email address; "
                "the users upgrade changed nothing."
            ) from None
        domain = identity.email.rsplit("@", 1)[1]
        rule = domains.get(domain)
        if rule is None or identity.hosted_domain != domain:
            continue
        if identity.email in addresses:
            # An address rule decides this address; an empty one is a deny
            # that overrides the domain rule today.
            if not addresses[identity.email]["roles"]:
                overridden.add(identity.email)
            continue
        if identity.disabled:
            disabled.add(identity.email)
        elif not identity.signed_in:
            never.add(identity.email)
        else:
            admitted[identity.email] = highest_role(rule["roles"])
    users.update(admitted)
    # An address converts if any of its identities qualified.
    disabled -= set(admitted)
    never -= set(admitted)
    return {
        "administrators": sorted(
            email for email, role in users.items() if role == "administrator"
        ),
        "address_rules": [
            {"email": email, "roles": sorted(rule["roles"]), "role": users[email]}
            for email, rule in sorted(addresses.items())
            if email in users
        ],
        "domain_rules": [
            {
                "domain": domain,
                "roles": sorted(rule["roles"]),
                "role": highest_role(rule["roles"]),
                "users": sorted(
                    email for email in admitted if email.rsplit("@", 1)[1] == domain
                ),
            }
            for domain, rule in sorted(domains.items())
        ],
        # Each deny rule is dropped. When it overrides a recorded domain
        # identity of its address today, that address stays denied after the
        # upgrade only because it has no user; the preview names the person.
        "dropped_deny_rules": [
            {
                "email": email,
                "overrides_identity": email in overridden,
                "name": default_name(email, names.get(email, [])),
            }
            for email, rule in sorted(addresses.items())
            if not rule["roles"]
        ],
        "not_converted": {
            "disabled": sorted(disabled),
            "never_signed_in": sorted(never),
        },
        "users": [
            {
                "email": email,
                "name": default_name(email, names.get(email, [])),
                "role": role,
            }
            for email, role in sorted(users.items())
        ],
    }


def upgrade_patch(records, plan):
    """The one request patch: remove every sign-in rule, add every user.

    Each user gets a new opaque ID: the users form binds an ID to its address
    for good, and an old rule's ID stays bound to its old shape.
    """
    return [
        {"operation": "remove", "section": "login_rules", "id": record["id"]}
        for record in records
    ] + [
        {
            "operation": "add",
            "section": "login_rules",
            "id": str(uuid4()),
            "values": {"kind": "user", **user},
        }
        for user in plan["users"]
    ]


def recorded_identities():
    """Every recorded Google identity, read through its narrow SQL definer."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT email, hosted_domain, disabled, signed_in "
            "FROM public.stewardship_portal_identities_v1()"
        )
        return [Identity(*row) for row in cursor.fetchall()]


def member_names():
    """Map each address the upgrade names to its ordered ParishSoft names.

    One call for every address: the definer looks up only the active
    configuration's address rules and the recorded Google identities, and
    returns a row only for an address some Member lists.
    """
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT address, names FROM public.stewardship_member_names_v1()"
        )
        rows = cursor.fetchall()
    # Django's database connection returns jsonb as its text.
    return {
        address: json.loads(names) if isinstance(names, str) else names
        for address, names in rows
    }


def upgrade_preview(version):
    """The read-only preview of converting this configuration version."""
    sections = version.document()["sections"]
    if uses_users(version.document()):
        return {"already_users": True}
    plan = plan_upgrade(
        sections.get("login_rules", []), recorded_identities(), member_names()
    )
    return {"already_users": False} | plan


def _confirmed(addresses):
    """Normalize the operator's confirmed Administrator list, refusing repeats."""
    if not isinstance(addresses, (list, tuple)) or not addresses:
        raise ConfigError("Confirm the Administrators the upgrade keeps.")
    confirmed = sorted(normalized_email(address) for address in addresses)
    if len(set(confirmed)) != len(confirmed):
        raise ConfigError("Confirm each Administrator once.")
    return confirmed


def upgrade_users(
    store,
    *,
    operation_id,
    operator_name,
    reason,
    deployment_id,
    confirmed_administrators,
    correlation_id,
    offline_interlock,
    before_apply=None,
):
    """Convert the active sign-in rules into users once, offline, or refuse.

    The operator's ``confirmed_administrators`` must equal the preview's
    Administrator list exactly, never confirmed by default. Interlock
    acquisition is repeated even for a completed replay, and a stored
    operation's operator, reason and deployment must match. Returns the
    request status, or None when the configuration is already in the users
    form and no operation of this ID exists (a no-op). Only state "applied"
    is success; a refused or failed receipt is terminal for its operation.
    """
    if any(
        not isinstance(value, UUID)
        for value in (operation_id, deployment_id, correlation_id)
    ):
        raise TypeError(
            "Explicit operation, deployment and correlation UUIDs are required."
        )
    _text(operator_name, 254)
    _text(reason, 1024)
    confirmed = _confirmed(confirmed_administrators)
    if not callable(offline_interlock):
        raise ConfigError("An offline interlock is required.")
    if before_apply is not None and not callable(before_apply):
        raise TypeError("Upgrade preview output requires a callable.")
    _own_transaction()
    with offline_interlock():
        try:
            runtime = SystemConfiguration.objects.get()
        except (
            SystemConfiguration.DoesNotExist,
            SystemConfiguration.MultipleObjectsReturned,
        ):
            raise ConfigError("The upgrade needs one initialized deployment.") from None
        if runtime.pk != deployment_id:
            raise ConfigError("The confirmed deployment does not match.")
        request = ConfigurationChangeRequest.objects.filter(
            authority="operator_upgrade", request_key=operation_id
        ).first()
        if request is not None:
            if (
                request.operator_name,
                request.operator_reason,
                request.confirmed_deployment_id,
            ) != (operator_name, reason, deployment_id):
                raise ConfigError("Upgrade operation is bound to another intent.")
        else:
            runtime = coherent_configuration(store)
            base, version = intake_base(runtime.active_configuration.digest)
            if base.validation_schema == SCHEMA:
                return None
            if base.validation_schema not in UPGRADE_BASES:
                raise ConfigError("Finish setup before converting sign-in rules.")
            records = version.document()["sections"].get("login_rules", [])
            plan = plan_upgrade(records, recorded_identities(), member_names())
            if plan["administrators"] != confirmed:
                raise ConfigError(
                    "The confirmed Administrators do not match the preview."
                )
            if before_apply is not None:
                before_apply({"already_users": False} | plan)
            intent = build_candidate(
                version,
                upgrade_patch(records, plan),
                candidate_id=uuid4(),
                request_schema=UPGRADE_REQUEST_SCHEMA,
            )
            with transaction.atomic(durable=True):
                request = ConfigurationChangeRequest.objects.create(
                    base=base,
                    patch=intent.patch(),
                    actor_id=None,
                    request_key=operation_id,
                    request_schema=UPGRADE_REQUEST_SCHEMA,
                    correlation_id=correlation_id,
                    payload_fingerprint=intent.payload_fingerprint,
                    candidate_version_id=intent.candidate.version_id,
                    candidate_digest=intent.candidate.digest,
                    authority="operator_upgrade",
                    operator_name=operator_name,
                    operator_reason=reason,
                    confirmed_deployment_id=deployment_id,
                )
        result = _install_request(store, request=request, correlation_id=correlation_id)
        if result.state == "applied":
            coherent_configuration(store)
        return result
