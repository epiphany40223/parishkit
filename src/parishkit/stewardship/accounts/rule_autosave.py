"""One autosaved role intent as the minimal login-rule patch, decided by pure code.

The users page autosaves each role checkbox change as a logical intent: one
target, one role, one desired value. The intent is applied over the rules the
page was drawn from, never as a toggle, so a stale page cannot flip a role
another Administrator already set: the resulting roles are the target's
configured roles with this one role granted or withdrawn, and an intent that
changes nothing is refused rather than recorded. Only a rule the page shows
autosaves; a new rule is made by the page's reviewed add forms. The patch
itself is the rule editor's, so provenance, the domain-rule fences and the
last-Administrator guard are the same whichever path made the change.
"""

from .policy_schema import ROLES
from .user_rules import RuleRefused, existing, rule_patch, target


def autosave_patch(records, *, kind, identity, role, checked, operation_id):
    """The rule patch that grants or withdraws one role of an existing rule.

    A target another Administrator has since removed is refused as missing
    whichever way the tick went, so a retry never recreates a deleted rule
    implicitly; creating one is the add forms' explicit, reviewed action. An
    unknown role is refused as invalid. Withdrawing the last role of an exact
    address removes its rule (#939): an empty role set is an explicit deny,
    which also blocks ParishSoft Ministry leadership (#922), so a cleared
    checkbox must mean "no rule, default access", never a deny. A deliberate
    deny is made only through the reviewed add form. A domain rule with no
    role is refused by the rule builder as the specification requires. The
    patch names the existing record, so identical intents build identical
    patches and a resubmitted key binds to the same request.
    """
    if role not in ROLES or type(checked) is not bool:
        raise RuleRefused("invalid")
    current = existing(records, kind, target(kind, identity))
    if current is None:
        raise RuleRefused("missing")
    configured = set(current["values"]["roles"])
    roles = configured | {role} if checked else configured - {role}
    # Only a withdrawal that empties a non-empty set removes the rule; an
    # unchanged intent (the role was not there) is still refused as such.
    removes = kind == "address" and not roles and bool(configured) and not checked
    return rule_patch(
        records,
        kind=kind,
        identity=identity,
        roles=sorted(roles),
        operation="remove" if removes else "set",
        operation_id=operation_id,
    )
