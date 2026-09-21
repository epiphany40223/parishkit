"""One autosaved role intent as the minimal login-rule patch, decided by pure code.

The users page autosaves each role checkbox change as a logical intent: one
target, one role, one desired value. The intent is applied over the rules the
page was drawn from, never as a toggle, so a stale page cannot flip a role
another Administrator already set: the resulting roles are the target's
configured roles with this one role granted or withdrawn, and an intent that
changes nothing is refused rather than recorded. The patch itself is the rule
editor's, so provenance, the domain-rule fences and the last-Administrator
guard are the same whichever path made the change.
"""

from .policy_schema import ROLES
from .user_rules import RuleRefused, existing, rule_patch, target


def autosave_patch(records, *, kind, identity, role, checked, operation_id):
    """The rule patch that grants or withdraws one role for one target.

    An unknown role is refused as invalid; withdrawing the last role of an
    exact address leaves an explicit deny, which policy admits, while a domain
    rule with no role is refused by the rule builder as the specification
    requires. A target with no rule yet gains one when a role is granted and
    is refused as missing when one is withdrawn: retry never recreates a
    deleted rule implicitly.
    """
    if role not in ROLES or type(checked) is not bool:
        raise RuleRefused("invalid")
    current = existing(records, kind, target(kind, identity))
    if current is None and not checked:
        raise RuleRefused("missing")
    configured = set(current["values"]["roles"]) if current is not None else set()
    roles = configured | {role} if checked else configured - {role}
    return rule_patch(
        records,
        kind=kind,
        identity=identity,
        roles=sorted(roles),
        operation="set",
        operation_id=operation_id,
    )
