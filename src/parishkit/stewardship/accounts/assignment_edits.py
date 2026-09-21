"""Manual Ministry assignments as ordinary policy patches, decided by pure code.

An Administrator adds or removes a manual assignment of an address to a
Ministry; each is an ordinary policy change the request schema admits and
binds to the request that carries it. Chairperson-seeded assignments are
never touched here: they are confirmed by the suggestion path and decided by
the review. The builders know nothing of requests, sessions or the source
catalog; the page judges the Ministry's activity before calling them, and
they refuse with a closed reason that never repeats what was submitted.
"""

from dataclasses import dataclass
from uuid import uuid4

from .policy_schema import normalized_email


class AssignmentRefused(ValueError):
    """An edit the builder will not make, named by a closed reason code."""

    def __init__(self, code):
        """Carry only the code; the page words it, and never with the target."""
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class AssignmentChange:
    """One edit's patch and what it concerns."""

    operation: str
    email: str
    ministry_duid: int
    patch: list


def _assignments(records, email, ministry_duid):
    """The assignments of this address to this Ministry, by source."""
    return {
        record["values"]["source"]: record
        for record in records
        if record["values"]["kind"] == "assignment"
        and record["values"]["email"] == email
        and record["values"]["ministry_duid"] == ministry_duid
    }


def add_assignment_patch(records, email, ministry_duid, *, operation_id):
    """Assign an address to a Ministry by the Administrator's own entry.

    A seeded assignment of the same pair may exist beside it, as policy
    admits; a second manual one is refused. The address needs no rule yet: an
    assignment takes effect only through a rule granting Ministry leader,
    which the page states rather than infers.
    """
    email = normalized_email(email)
    if "manual" in _assignments(records, email, ministry_duid):
        raise AssignmentRefused("already")
    patch = [
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
        }
    ]
    return AssignmentChange("add", email, ministry_duid, patch)


def remove_assignment_patch(records, email, ministry_duid):
    """Remove the Administrator's assignment; a seed is decided elsewhere."""
    email = normalized_email(email)
    existing = _assignments(records, email, ministry_duid)
    if "manual" not in existing:
        raise AssignmentRefused("seeded" if "chair-seed" in existing else "missing")
    patch = [
        {
            "operation": "remove",
            "section": "login_rules",
            "id": existing["manual"]["id"],
        }
    ]
    return AssignmentChange("remove", email, ministry_duid, patch)
