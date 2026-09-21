"""Confirmed Chairperson suggestions as configuration patches, decided by pure code.

An Administrator confirms one or more suggestions; the result is one patch
against the applied policy that creates or widens the exact-address rule for
each confirmed address with a Chairperson-seeded Ministry leader grant and
adds a seeded assignment for each confirmed Ministry. The builder knows nothing
of requests or sessions: it is given the applied records, the confirmed
address/Ministry pairs and the operation identity the request will carry, and
it returns the patch with what changes, or refuses with a closed reason that
never repeats what was submitted.

The validator is the confirmation request schema's rule: every difference
between the base and the candidate policy must be exactly what a confirmation
may do, bound to the request's own operation identity, so no other path can
manufacture Chairperson-seeded authority and a confirmation cannot smuggle
any other edit.
"""

from dataclasses import dataclass
from uuid import uuid4

from .policy_schema import invalid_policy

REQUEST_SCHEMA = "chair-seed-patch-v9"
SEEDED_ROLE = "ministry_leader"


class ConfirmationRefused(ValueError):
    """A confirmation the builder will not make, named by a closed reason code."""

    def __init__(self, code):
        """Carry only the code; the page words it, and never with the target."""
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class SeedChange:
    """What one confirmed address gains: its rule and the Ministries assigned."""

    email: str
    created: bool
    before: list
    after: list
    ministries: tuple
    assignment_ids: tuple


def _rules(records):
    """Index the applied policy by kind and identity."""
    addresses, domains, assignments = {}, {}, {}
    for record in records:
        values = record["values"]
        if values["kind"] == "address":
            addresses[values["email"]] = record
        elif values["kind"] == "domain":
            domains[values["domain"]] = record
        else:
            assignments.setdefault(values["email"], []).append(record)
    return addresses, domains, assignments


def seed_patch(records, selections, *, operation_id):
    """Build the one patch that confirms these address/Ministry pairs.

    A new exact rule replaces the address's domain rule, so the roles that
    rule inherited are preselected as manual grants beside the seeded Ministry
    leader grant, as the specification requires; an existing rule keeps every
    grant it has and gains only the seeded origin. A Ministry already assigned
    to the address by a seed is refused rather than duplicated; a manual
    assignment for it is left alone and a seeded one added beside it, as
    policy admits both.
    """
    addresses, domains, assignments = _rules(records)
    wanted = {}
    for email, ministry_duid in selections:
        wanted.setdefault(email, set()).add(ministry_duid)
    if not wanted:
        raise ConfirmationRefused("empty")
    patch, changes = [], []
    for email, ministries in sorted(wanted.items()):
        seeded = {
            record["values"]["ministry_duid"]
            for record in assignments.get(email, [])
            if record["values"]["source"] == "chair-seed"
        }
        if seeded & ministries:
            raise ConfirmationRefused("already")
        current = addresses.get(email)
        if current is None:
            domain = domains.get(email.rsplit("@", 1)[1])
            inherited = list(domain["values"]["roles"]) if domain else []
            roles = sorted(set(inherited) | {SEEDED_ROLE})
            grants = {role: {"manual": operation_id} for role in inherited}
            grants[SEEDED_ROLE] = grants.get(SEEDED_ROLE, {}) | {
                "chair-seed": operation_id
            }
            patch.append(
                {
                    "operation": "add",
                    "section": "login_rules",
                    "id": str(uuid4()),
                    "values": {
                        "kind": "address",
                        "email": email,
                        "roles": roles,
                        "creation_origin": "chair-seed",
                        "creation_operation": operation_id,
                        "grants": grants,
                    },
                }
            )
            before, after, created = inherited, roles, True
        else:
            values = current["values"]
            grants = {role: dict(origins) for role, origins in values["grants"].items()}
            leader = grants.setdefault(SEEDED_ROLE, {})
            roles = sorted(set(values["roles"]) | {SEEDED_ROLE})
            if "chair-seed" not in leader:
                leader["chair-seed"] = operation_id
                patch.append(
                    {
                        "operation": "update",
                        "section": "login_rules",
                        "id": current["id"],
                        "values": {"roles": roles, "grants": grants},
                    }
                )
            before, after, created = values["roles"], roles, False
        identifiers = []
        for ministry_duid in sorted(ministries):
            identifiers.append(str(uuid4()))
            patch.append(
                {
                    "operation": "add",
                    "section": "login_rules",
                    "id": identifiers[-1],
                    "values": {
                        "kind": "assignment",
                        "email": email,
                        "ministry_duid": ministry_duid,
                        "source": "chair-seed",
                        "operation_id": operation_id,
                    },
                }
            )
        changes.append(
            SeedChange(
                email,
                created,
                before,
                after,
                tuple(sorted(ministries)),
                tuple(identifiers),
            )
        )
    return patch, changes


def validate_seed_change(before, after, operation_id):
    """Admit exactly a confirmation's effects, each bound to this operation.

    Compared with an ordinary policy change, a confirmation may create an
    address rule whose origin is the Chairperson seed, add a seeded Ministry
    leader grant origin and add seeded assignments; it may do nothing else,
    and every seeded origin it adds names this request. Each touched address
    must gain at least one seeded assignment, and each seeded assignment must
    belong to an address whose rule carries the seeded grant, so the two can
    never be confirmed apart.
    """
    old = {record["id"]: record["values"] for record in before}
    new = {record["id"]: record["values"] for record in after}
    if set(old) - set(new) or not before:
        invalid_policy()
    old_addresses, old_domains, _ = _rules(before)
    new_addresses, _, new_assignments = _rules(after)
    touched, assigned = set(), set()
    for identifier, values in new.items():
        previous = old.get(identifier)
        kind = values["kind"]
        if previous is not None and previous == values:
            continue
        if kind == "assignment":
            if previous is not None or values["source"] != "chair-seed":
                invalid_policy()
            if values["operation_id"] != operation_id:
                invalid_policy()
            assigned.add(values["email"])
            continue
        if kind != "address":
            invalid_policy()
        email = values["email"]
        leader = values["grants"].get(SEEDED_ROLE, {})
        if leader.get("chair-seed") != operation_id:
            invalid_policy()
        if previous is None:
            if old_addresses.get(email) is not None:
                invalid_policy()
            if (
                values["creation_origin"] != "chair-seed"
                or values["creation_operation"] != operation_id
            ):
                invalid_policy()
            domain = old_domains.get(email.rsplit("@", 1)[1])
            inherited = set(domain["values"]["roles"]) if domain else set()
            if set(values["roles"]) != inherited | {SEEDED_ROLE}:
                invalid_policy()
            for role, origins in values["grants"].items():
                expected = {"manual": operation_id} if role in inherited else {}
                if role == SEEDED_ROLE:
                    expected = expected | {"chair-seed": operation_id}
                if origins != expected:
                    invalid_policy()
        else:
            if previous["kind"] != "address" or set(previous["roles"]) - set(
                values["roles"]
            ):
                invalid_policy()
            if set(values["roles"]) - set(previous["roles"]) - {SEEDED_ROLE}:
                invalid_policy()
            frozen = ("email", "creation_origin", "creation_operation")
            if any(values[name] != previous[name] for name in frozen):
                invalid_policy()
            for role, origins in values["grants"].items():
                prior = previous["grants"].get(role, {})
                expected = prior | (
                    {"chair-seed": operation_id} if role == SEEDED_ROLE else {}
                )
                if origins != expected or (
                    "chair-seed" in prior and role == SEEDED_ROLE
                ):
                    invalid_policy()
        touched.add(email)
    if touched - assigned:
        invalid_policy()
    for email in assigned:
        rule = new_addresses.get(email)
        if rule is None or "chair-seed" not in rule["values"]["grants"].get(
            SEEDED_ROLE, {}
        ):
            invalid_policy()
        # A confirmation never assigns the same Ministry twice by seed.
        seeded = [
            record["values"]["ministry_duid"]
            for record in new_assignments.get(email, [])
            if record["values"]["source"] == "chair-seed"
        ]
        if len(seeded) != len(set(seeded)):
            invalid_policy()


def seeded_additions(patch):
    """The seeded assignment record ids a confirmation patch adds, in patch order."""
    return [
        item["id"]
        for item in patch
        if item["operation"] == "add"
        and item["section"] == "login_rules"
        and item["values"].get("kind") == "assignment"
        and item["values"].get("source") == "chair-seed"
    ]
