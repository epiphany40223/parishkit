"""Login-rule edits as minimal configuration patches, decided by pure code.

Every change an Administrator can make to who may sign in is one patch against
the applied policy: set the roles of one exact address or hosted domain,
creating the rule when it is new, or remove one rule. The builder knows nothing
of requests or sessions. It is given the applied records, the submitted target,
the roles asked for and the operation identity the request will carry, and it
returns the patch with its before and after roles, or refuses with a closed
reason that never repeats what was submitted.
"""

from dataclasses import dataclass
from uuid import uuid4

from parishkit.config import ConfigError

from .policy_schema import ROLES, normalized_domain, normalized_email
from .user_rows import ROLE_LABELS

# The one presentation order, fixed by the labels, is also the form's order.
ROLE_ORDER = tuple(ROLE_LABELS)
# A consumer mail domain is never a hosted domain; the specification names the
# one that is tried most, and the sign-in itself would refuse the claim anyway.
CONSUMER_DOMAINS = frozenset({"gmail.com", "googlemail.com"})


class RuleRefused(ValueError):
    """A change the builder will not make, named by a closed reason code."""

    def __init__(self, code):
        """Carry only the code; the page words it, and never with the target."""
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class RuleChange:
    """One minimal patch and what it does to the target's configured roles."""

    kind: str
    identity: str
    patch: list
    before: list | None
    after: list | None

    @property
    def expansion(self):
        """Which high-impact expansion this is, if any, by the specification.

        Adding Administrator to an exact address, creating any domain rule and
        adding Staff to an existing domain rule are the changes that must alert
        every other Administrator; the rest are ordinary audited changes.
        """
        gained = set(self.after or ()) - set(self.before or ())
        if self.kind == "address" and "administrator" in gained:
            return "administrator"
        if self.kind == "domain" and self.before is None and self.after is not None:
            return "domain_rule"
        if self.kind == "domain" and self.before is not None and "staff" in gained:
            return "domain_staff"
        return None


def target(kind, identity):
    """Normalize the rule target the way policy stores it, or refuse."""
    try:
        if kind == "address":
            return normalized_email(identity)
        if kind == "domain":
            domain = normalized_domain(identity)
            if domain in CONSUMER_DOMAINS:
                raise RuleRefused("consumer")
            return domain
    except ConfigError:
        raise RuleRefused("target") from None
    raise RuleRefused("invalid")


def existing(records, kind, identity):
    """The applied rule for this normalized target, or None."""
    field = "email" if kind == "address" else "domain"
    return next(
        (
            record
            for record in records
            if record["values"]["kind"] == kind and record["values"][field] == identity
        ),
        None,
    )


def rule_patch(records, *, kind, identity, roles, operation, operation_id):
    """Build the one patch that sets or removes a rule, preserving provenance.

    A retained role keeps every origin it had, so a Chairperson-seeded grant is
    never rewritten as a manual one; a newly granted role is manual and bound
    to this request's operation identity, which the installer verifies. A role
    that is removed loses all of its origins, as the specification requires,
    and removing a rule removes nothing else: an assignment for the address
    stays, shown by the page as relying on a domain rule.
    """
    identity = target(kind, identity)
    roles = sorted(set(roles))
    if set(roles) - ROLES or operation not in {"set", "remove"}:
        raise RuleRefused("invalid")
    current = existing(records, kind, identity)
    if operation == "remove":
        if current is None:
            raise RuleRefused("missing")
        return RuleChange(
            kind,
            identity,
            [{"operation": "remove", "section": "login_rules", "id": current["id"]}],
            current["values"]["roles"],
            None,
        )
    if kind == "domain" and (not roles or "administrator" in roles):
        raise RuleRefused("domain_roles")
    if current is not None:
        if current["values"]["roles"] == roles:
            raise RuleRefused("unchanged")
        values = {"roles": roles}
        if kind == "address":
            previous = current["values"]["grants"]
            values["grants"] = {
                role: previous.get(role) or {"manual": operation_id} for role in roles
            }
        return RuleChange(
            kind,
            identity,
            [
                {
                    "operation": "update",
                    "section": "login_rules",
                    "id": current["id"],
                    "values": values,
                }
            ],
            current["values"]["roles"],
            roles,
        )
    if kind == "domain":
        values = {"kind": "domain", "domain": identity, "roles": roles}
    else:
        values = {
            "kind": "address",
            "email": identity,
            "roles": roles,
            "creation_origin": "manual",
            "creation_operation": operation_id,
            "grants": {role: {"manual": operation_id} for role in roles},
        }
    return RuleChange(
        kind,
        identity,
        [
            {
                "operation": "add",
                "section": "login_rules",
                "id": str(uuid4()),
                "values": values,
            }
        ],
        None,
        roles,
    )
