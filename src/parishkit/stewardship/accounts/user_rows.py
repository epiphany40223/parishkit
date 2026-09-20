"""Read-only review rows for the applied login rules and Ministry assignments.

Pure shaping over the applied canonical policy records. It decides nothing:
effective roles come from the one policy evaluator, so this page can never show
an authority that a sign-in would not actually receive.
"""

from django.utils.translation import gettext_lazy as _

from .policy import resolve_roles

ROLE_LABELS = {
    "administrator": _("Administrator"),
    "staff": _("Staff"),
    "ministry_leader": _("Ministry leader"),
}
ORIGIN_LABELS = {
    "manual": _("Added by an Administrator"),
    "chair-seed": _("Chairperson"),
}


def _labels(roles):
    """Present roles in one fixed order rather than a set's arbitrary one."""
    return [ROLE_LABELS[role] for role in ROLE_LABELS if role in roles]


def _signins(users):
    """Index the mutable Google identities by their normalized address."""
    return {user["email"].lower(): user for user in users}


def domain_rows(records, users):
    """Hosted-domain rules, with whether a matching signed claim was ever seen.

    An email suffix alone never matches a domain rule, so the evidence that a
    rule is usable is a sign-in that actually presented that hosted domain.
    """
    rows = []
    for record in records:
        values = record["values"]
        if values["kind"] != "domain":
            continue
        claims = [user for user in users if user["hosted_domain"] == values["domain"]]
        warnings = []
        if not values["roles"]:
            warnings.append(_("This rule grants no role."))
        if not claims:
            warnings.append(
                _("No sign-in has presented this Google hosted-domain claim yet.")
            )
        rows.append(
            {
                "id": record["id"],
                "domain": values["domain"],
                "roles": _labels(values["roles"]),
                "claims": len(claims),
                "last_login": max(
                    (user["verified_at"] for user in claims), default=None
                ),
                "warnings": warnings,
            }
        )
    return sorted(rows, key=lambda row: row["domain"])


def address_rows(records, users, active_seeded=frozenset()):
    """Exact-address rules with effective roles, provenance and assignments.

    `active_seeded` holds the Chairperson-seeded assignment record identities the
    promoted source currently confirms; any other seeded assignment is suspended.
    """
    signins = _signins(users)
    domains = {
        record["values"]["domain"]
        for record in records
        if record["values"]["kind"] == "domain"
    }
    assignments = {}
    for record in records:
        values = record["values"]
        if values["kind"] == "assignment":
            assignments.setdefault(values["email"], []).append(
                {
                    "ministry_duid": values["ministry_duid"],
                    "source": ORIGIN_LABELS[values["source"]],
                    "active": values["source"] == "manual"
                    or record["id"] in active_seeded,
                }
            )
    rows = []
    for record in records:
        values = record["values"]
        if values["kind"] != "address":
            continue
        email = values["email"]
        user = signins.get(email)
        effective, ministries = resolve_roles(
            email, user and user["hosted_domain"], records, active_seeded
        )
        held = sorted(
            assignments.get(email, []), key=lambda item: item["ministry_duid"]
        )
        warnings = []
        if "ministry_leader" in values["roles"] and "ministry_leader" not in effective:
            warnings.append(
                _(
                    "The Ministry leader role is suspended: no Chairperson "
                    "assignment is currently confirmed by the parish source."
                )
            )
        elif (
            "ministry_leader" in effective
            and not ministries
            # An Administrator is a leader of everything and needs no assignment.
            and "administrator" not in effective
        ):
            warnings.append(_("Ministry leader with no active Ministry assignment."))
        if held and "ministry_leader" not in effective:
            warnings.append(
                _(
                    "Ministry assignments have no effect without the Ministry "
                    "leader role."
                )
            )
        if email.rsplit("@", 1)[1] in domains:
            warnings.append(
                _("This exact address replaces its domain rule for this person.")
            )
        if user and user["disabled"]:
            warnings.append(_("This Google identity is disabled."))
        rows.append(
            {
                "id": record["id"],
                "email": email,
                # An empty role set is a deliberate denial, never an accident.
                "deny": not values["roles"],
                "roles": _labels(values["roles"]),
                "effective": _labels(effective),
                "origin": ORIGIN_LABELS[values["creation_origin"]],
                "grants": [
                    {
                        "role": ROLE_LABELS[role],
                        "origins": [
                            ORIGIN_LABELS[origin]
                            for origin in ORIGIN_LABELS
                            if origin in values["grants"].get(role, {})
                        ],
                    }
                    for role in ROLE_LABELS
                    if role in values["roles"]
                ],
                "assignments": held,
                "last_login": user["verified_at"] if user else None,
                "warnings": warnings,
            }
        )
    return sorted(rows, key=lambda row: row["email"])


def domain_assignment_rows(records, active_seeded=frozenset()):
    """Assignments for people who have no exact rule and rely on a domain rule.

    These are valid when the person's hosted-domain rule grants Ministry leader.
    Otherwise nothing can give that person the role, so the assignment is inert.
    """
    addressed = {
        record["values"]["email"]
        for record in records
        if record["values"]["kind"] == "address"
    }
    leading = {
        record["values"]["domain"]
        for record in records
        if record["values"]["kind"] == "domain"
        and "ministry_leader" in record["values"]["roles"]
    }
    grouped = {}
    for record in records:
        values = record["values"]
        if values["kind"] == "assignment" and values["email"] not in addressed:
            grouped.setdefault(values["email"], []).append(
                {
                    "ministry_duid": values["ministry_duid"],
                    "source": ORIGIN_LABELS[values["source"]],
                    "active": values["source"] == "manual"
                    or record["id"] in active_seeded,
                }
            )
    return [
        {
            "email": email,
            "assignments": sorted(held, key=lambda item: item["ministry_duid"]),
            "warnings": []
            if email.rsplit("@", 1)[1] in leading
            else [_("No login rule gives this person the Ministry leader role.")],
        }
        for email, held in sorted(grouped.items())
    ]
