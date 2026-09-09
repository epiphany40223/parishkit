"""Strict versioned login policy and explicit grant provenance, without OAuth."""

import re
from uuid import UUID

from django.core.exceptions import ValidationError
from django.core.validators import EmailValidator

from parishkit.config import ConfigError
from parishkit.stewardship.campaigns.domain import PortalRole

ROLES = frozenset(role.value for role in PortalRole)


def invalid_policy():
    """Never put an untrusted email, field or submitted value in an exception."""
    raise ConfigError("Invalid or inconsistent login policy.")


def normalized_email(value):
    """Use case-insensitive exact-address identity, not provider-specific aliases."""
    if type(value) is not str or value != value.strip() or len(value) > 254:
        invalid_policy()
    try:
        EmailValidator()(value)
    except ValidationError:
        invalid_policy()
    return value.lower()


def normalized_domain(value):
    """Require an ASCII DNS hosted-domain name, not an arbitrary email suffix."""
    if type(value) is not str or len(value) > 253:
        invalid_policy()
    result = value.lower()
    if len(result.split(".")) < 2 or any(
        re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) is None
        for label in result.split(".")
    ):
        invalid_policy()
    return result


def _operation(value):
    """Provenance always identifies an explicit configuration or bootstrap operation."""
    try:
        if type(value) is not str or str(UUID(value)) != value:
            invalid_policy()
    except ValueError:
        invalid_policy()


def validate_policy_records(records):
    """Validate exact field sets, unique policy identities and grant-origin parity.

    Empty policy supports historical/unconfigured preparation only. Once policy
    is present an explicit-address Administrator is mandatory; later patch
    admission additionally prevents removing the entire configured policy.
    """
    seen, administrators = set(), 0
    for record in records:
        values = record["values"]
        kind = values.get("kind")
        if kind in ("domain", "address"):
            roles = values.get("roles")
            if type(roles) is not list or any(type(role) is not str for role in roles):
                invalid_policy()
            if roles != sorted(set(roles)) or set(roles) - ROLES:
                invalid_policy()
        if kind == "domain":
            if set(values) != {"kind", "domain", "roles"}:
                invalid_policy()
            domain = normalized_domain(values["domain"])
            if domain != values["domain"] or domain == "gmail.com":
                invalid_policy()
            key = (kind, domain)
        elif kind == "address":
            if set(values) != {
                "kind",
                "email",
                "roles",
                "creation_origin",
                "creation_operation",
                "grants",
            }:
                invalid_policy()
            email = normalized_email(values["email"])
            if email != values["email"] or values["creation_origin"] not in (
                "manual",
                "chair-seed",
            ):
                invalid_policy()
            _operation(values["creation_operation"])
            grants = values["grants"]
            if type(grants) is not dict or set(grants) != set(values["roles"]):
                invalid_policy()
            for role, origins in grants.items():
                if (
                    type(origins) is not dict
                    or not origins
                    or set(origins) - {"manual", "chair-seed"}
                ):
                    invalid_policy()
                if "chair-seed" in origins and role != "ministry_leader":
                    invalid_policy()
                for operation in origins.values():
                    _operation(operation)
            administrators += "administrator" in grants
            key = (kind, email)
        elif kind == "assignment":
            if set(values) != {
                "kind",
                "email",
                "ministry_duid",
                "source",
                "operation_id",
            }:
                invalid_policy()
            email = normalized_email(values["email"])
            if email != values["email"] or values["source"] not in (
                "manual",
                "chair-seed",
            ):
                invalid_policy()
            if (
                type(values["ministry_duid"]) is not int
                or not 1 <= values["ministry_duid"] <= 2**63 - 1
            ):
                invalid_policy()
            _operation(values["operation_id"])
            key = (kind, email, values["ministry_duid"], values["source"])
        else:
            invalid_policy()
        if kind != "assignment":
            roles = values["roles"]
            if type(roles) is not list or any(type(role) is not str for role in roles):
                invalid_policy()
            if roles != sorted(set(roles)) or set(roles) - ROLES:
                invalid_policy()
            if kind == "domain" and (not roles or "administrator" in roles):
                invalid_policy()
        if key in seen:
            invalid_policy()
        seen.add(key)
    if records and not administrators:
        invalid_policy()


def validate_policy_change(before, after):
    """Ordinary edits preserve origins and cannot manufacture chair-seed authority."""
    old = {record["id"]: record["values"] for record in before}
    if before and not after:
        invalid_policy()
    for record in after:
        values, previous = record["values"], old.get(record["id"])
        kind = values["kind"]
        if previous is not None:
            if previous["kind"] != kind:
                invalid_policy()
            frozen = {
                "domain": ("domain",),
                "address": ("email", "creation_origin", "creation_operation"),
                "assignment": ("email", "ministry_duid", "source", "operation_id"),
            }[kind]
            if any(values[name] != previous[name] for name in frozen):
                invalid_policy()
        if (
            kind == "assignment"
            and values["source"] == "chair-seed"
            and previous is None
        ):
            invalid_policy()
        if kind == "address":
            if previous is None and values["creation_origin"] != "manual":
                invalid_policy()
            for role, origins in values["grants"].items():
                prior = previous["grants"].get(role, {}) if previous else {}
                if "chair-seed" in origins and origins["chair-seed"] != prior.get(
                    "chair-seed"
                ):
                    invalid_policy()
                if any(
                    origins.get(origin) != operation
                    for origin, operation in prior.items()
                ):
                    invalid_policy()
