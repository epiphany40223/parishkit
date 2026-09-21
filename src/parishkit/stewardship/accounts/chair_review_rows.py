"""Read-only rows for suspended Chairperson-seeded assignments awaiting review.

A suspension is derived runtime state the reconciliation owners record: the
overlay says why the seed is not in force and the open review episode says
since when. This module shapes those facts with what policy grants the
address now, through the same evaluator as every other table, and decides
nothing itself. A review here is a dict with `email`, `ministry_duid`,
`ministry_name` (empty when the Ministry left the catalog), `member_duid`,
`reason`, `opened_at`, `generation`, `latest_at` and `elsewhere`, whether the
retained Member is a current Chairperson of some active Ministry now.
"""

from django.utils.translation import gettext_lazy as _

from .user_rows import ORIGIN_LABELS, _latest, role_labels

REASON_LABELS = {
    "relationship_missing": _(
        "The parish source no longer shows this Member as a current "
        "Chairperson of this Ministry."
    ),
    "missing_binding": _("No retained identity binds this seed to a Member."),
    "ministry_inactive": _("The Ministry is inactive in the applied activity."),
    "organization_changed": _(
        "The configured ParishSoft organization is not the seed's."
    ),
}


def suspended_rows(policy, reviews):
    """One row per open review, ordered by suspension time then address.

    Whether a manual assignment of the same address to the same Ministry
    already exists decides which decisions the row offers: a restore would
    duplicate it, so only removal remains.
    """
    rows = []
    for review in sorted(reviews, key=lambda item: (item["opened_at"], item["email"])):
        email = review["email"]
        granted, ministries = policy.resolve(email, None)
        manual = any(
            item["ministry_duid"] == review["ministry_duid"]
            and item["source"] == ORIGIN_LABELS["manual"]
            for item in policy.held(email)
        )
        rows.append(
            {
                "email": email,
                "ministry_duid": review["ministry_duid"],
                "ministry_name": review["ministry_name"] or "",
                "member_duid": review["member_duid"],
                "reason": REASON_LABELS.get(review["reason"], review["reason"]),
                "opened_at": review["opened_at"],
                "generation": review["generation"],
                "latest_at": review["latest_at"],
                "elsewhere": review["elsewhere"],
                "manual": manual,
                "granted": role_labels(granted),
                "leading": bool(ministries),
                "last_login": _latest(policy.identities.get(email, [])),
            }
        )
    return rows
