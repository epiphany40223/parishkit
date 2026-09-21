"""Administrator decisions on seeded assignments as ordinary policy patches.

Keeping a seeded role independently, restoring a suspended seed as a manual
assignment and removing a seed are ordinary policy changes: the ordinary
request schema admits a removed record, a new manual assignment and a new
manual grant origin, each bound by intake and the installer to the request
that carries it. The builders here know nothing of requests or sessions: they
are given the applied records and the decision, and return the patch or
refuse with a closed reason that never repeats what was submitted.
"""

from dataclasses import dataclass
from uuid import uuid4

SEEDED_ROLE = "ministry_leader"
DECISIONS = ("keep_role", "restore", "remove")


class ReviewRefused(ValueError):
    """A decision the builder will not make, named by a closed reason code."""

    def __init__(self, code):
        """Carry only the code; the page words it, and never with the target."""
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class ReviewChange:
    """One decision's patch and what it concerns."""

    decision: str
    email: str
    ministry_duid: int | None
    patch: list


def _exact(records, email):
    """The exact-address rule for this address, or None."""
    return next(
        (
            record
            for record in records
            if record["values"]["kind"] == "address"
            and record["values"]["email"] == email
        ),
        None,
    )


def _assignment(records, email, ministry_duid, source):
    """The assignment of this address to this Ministry from this source, or None."""
    return next(
        (
            record
            for record in records
            if record["values"]["kind"] == "assignment"
            and record["values"]["email"] == email
            and record["values"]["ministry_duid"] == ministry_duid
            and record["values"]["source"] == source
        ),
        None,
    )


def keep_role_patch(records, email, *, operation_id):
    """Record a manual origin beside the seeded Ministry leader grant.

    The role's configuration does not change; only its provenance gains the
    Administrator's own entry, so source suppression no longer applies to it.
    It creates no assignment and broadens no row scope, as the specification
    requires.
    """
    rule = _exact(records, email)
    if rule is None:
        raise ReviewRefused("missing")
    grants = {role: dict(origins) for role, origins in rule["values"]["grants"].items()}
    leader = grants.get(SEEDED_ROLE)
    if leader is None or "chair-seed" not in leader:
        raise ReviewRefused("not_seeded")
    if "manual" in leader:
        raise ReviewRefused("already")
    grants[SEEDED_ROLE] = leader | {"manual": operation_id}
    patch = [
        {
            "operation": "update",
            "section": "login_rules",
            "id": rule["id"],
            "values": {"roles": rule["values"]["roles"], "grants": grants},
        }
    ]
    return ReviewChange("keep_role", email, None, patch)


def restore_manual_patch(records, email, ministry_duid, *, operation_id):
    """Replace a seeded assignment with the Administrator's own.

    The seed is removed and a manual assignment of the same address to the
    same Ministry added, bound to this request; the source never rewrites a
    manual assignment, so the scope stays in force until an Administrator
    removes it. The rule's seeded grant is left as it is.
    """
    seed = _assignment(records, email, ministry_duid, "chair-seed")
    if seed is None:
        raise ReviewRefused("missing")
    if _assignment(records, email, ministry_duid, "manual") is not None:
        raise ReviewRefused("already")
    patch = [
        {"operation": "remove", "section": "login_rules", "id": seed["id"]},
        {
            "operation": "add",
            "section": "login_rules",
            "id": str(uuid4()),
            "values": {
                "kind": "assignment",
                "email": email,
                "ministry_duid": ministry_duid,
                "source": "manual",
                "operation_id": operation_id,
            },
        },
    ]
    return ReviewChange("restore", email, ministry_duid, patch)


def remove_seed_patch(records, email, ministry_duid):
    """Remove a seeded assignment; the rule and its roles are left as they are.

    A seeded Ministry leader role with no assignment left reads as suspended
    on the page until the rule editor removes it or another assignment is
    confirmed; nothing here infers that further change.
    """
    seed = _assignment(records, email, ministry_duid, "chair-seed")
    if seed is None:
        raise ReviewRefused("missing")
    patch = [{"operation": "remove", "section": "login_rules", "id": seed["id"]}]
    return ReviewChange("remove", email, ministry_duid, patch)
