"""Read-only Chairperson suggestion rows for the Portal users page.

Pure shaping over the current source relationships and the applied policy.
A suggestion is a fact about the parish source: this active Member is a
current Chairperson of this active Ministry and uses this valid address. It
grants nothing. The page shows beside it what policy already gives the
address and whether an assignment for that Ministry already exists, so the
Administrator can see what a confirmation would change; the confirmation
itself is a later increment.

A relationship here is a dict with `member_duid`, `member_name`,
`ministry_duid`, `ministry_name`, `email`, `publish_email` and
`address_members`, the active Members whose valid address this is.
"""

from django.utils.translation import gettext_lazy as _

from .user_rows import ORIGIN_LABELS, role_labels

RULE_LABELS = {
    "address": _("Exact-address rule"),
    "domain": _("Hosted-domain rule"),
    None: _("No login rule"),
}


def _rule(policy, email):
    """The rule the evaluator would use for this address, and what it grants.

    An exact rule's roles are what the evaluator grants the address now, so a
    seeded Ministry leader role the source no longer confirms is shown as
    suspended rather than as a role the address has, and an empty configured
    role set is an explicit denial rather than nothing. A hosted-domain rule
    grants its roles only to a Google account presenting the matching claim,
    so its roles are shown as conditional on that claim, never as held.
    """
    exact = policy.addresses.get(email)
    if exact is not None:
        configured = exact["values"]["roles"]
        granted, _ = policy.resolve(email, None)
        return {
            "kind": "address",
            "roles": role_labels(granted),
            "deny": not configured,
            "suspended": "ministry_leader" in configured
            and "ministry_leader" not in granted,
        }
    domain = policy.domains.get(email.rsplit("@", 1)[1])
    if domain is not None:
        return {
            "kind": "domain",
            "roles": role_labels(domain["values"]["roles"]),
            "deny": False,
            "suspended": False,
            "domain": domain["values"]["domain"],
        }
    return {"kind": None, "roles": [], "deny": False, "suspended": False}


def _assignment(policy, email, ministry_duid):
    """The configured assignment of this address to this Ministry, if any."""
    return next(
        (item for item in policy.held(email) if item["ministry_duid"] == ministry_duid),
        None,
    )


def suggestion_rows(policy, relationships, *, active):
    """One row per address and Ministry, retaining every Member rather than picking.

    Only Ministries the applied activity keeps active are suggested; a
    Chairperson of a locally inactive Ministry is not. Several roster rows for
    one Member yield one candidate. A row is ambiguous when the address is
    used by more than one active Member, or by a Member other than the
    Chairperson, since a confirmation must then select the Member explicitly.
    """
    groups = {}
    for item in relationships:
        if item["ministry_duid"] not in active:
            continue
        group = groups.setdefault(
            (item["email"], item["ministry_duid"]),
            # A Ministry payload without a name is valid source; it sorts and
            # shows as an empty name, exactly as the pure source suggestions do.
            {
                "ministry_name": item["ministry_name"] or "",
                "candidates": {},
                "owners": (),
            },
        )
        group["candidates"][item["member_duid"]] = {
            "duid": item["member_duid"],
            "name": item["member_name"],
            "publishable": item["publish_email"],
        }
        group["owners"] = tuple(item["address_members"] or ())
    rows = []
    for (email, ministry_duid), group in sorted(
        groups.items(),
        key=lambda pair: (pair[1]["ministry_name"].casefold(), pair[0][1], pair[0][0]),
    ):
        candidates = sorted(
            group["candidates"].values(),
            key=lambda row: (row["name"].casefold(), row["duid"]),
        )
        owners = sorted(set(group["owners"]) | {row["duid"] for row in candidates})
        assignment = _assignment(policy, email, ministry_duid)
        rows.append(
            {
                "email": email,
                "ministry_duid": ministry_duid,
                "ministry_name": group["ministry_name"],
                "candidates": candidates,
                "owners": len(owners),
                "ambiguous": len(owners) != 1,
                "rule": _rule(policy, email),
                "assignment": assignment,
                "source": ORIGIN_LABELS["chair-seed"],
            }
        )
    return rows
