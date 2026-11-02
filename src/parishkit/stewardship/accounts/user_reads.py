"""Portal users reads shared by the page and ``pk-admin users`` (#952).

Nothing here takes a request (the Admin automation specification's "Read
models"), so the Portal users page and the ``users list`` and
``users preview-upgrade`` commands read through the same functions and
cannot drift:

- ``policy_identities`` reads the recorded Google identities a policy
  names, each with its latest successful sign-in, including a Google
  account matched to a user's address through another of its verified
  addresses (#2343);
- ``user_list`` shapes the users ``users list`` prints and the Users table
  shows, in either policy form, each with the recorded Google identities
  of its address.
"""

from django.db.models import Max, Q

from parishkit.stewardship.audit.models import AuditEvent
from parishkit.stewardship.web.tables import Sorting

from .policy_models import AddressMatch, PortalUser
from .users_schema import ROLE_LEVELS
from .users_upgrade import highest_role


def policy_identities(records):
    """Only the Google identities this policy names, with successful sign-ins.

    Every verified Google attempt records an identity and refreshes its
    verification time, including a stranger's attempt that policy then denies.
    So the read is bounded by policy, not by attempts, and a sign-in means the
    durable login event written only when a session was actually issued.

    A Google account with a standing verified-address match of a named
    address (#2343) is that address's identity whatever address it acts as
    at the moment, so its ``email`` here is the matched address and its
    ``match`` the match (``match_id``, the account ``id``, ``sign_in_email``,
    ``matched_at`` and ``confirmed_at``); every other identity's ``match``
    is None.
    """
    emails = {
        record["values"]["email"]
        for record in records
        if record["values"]["kind"] != "domain"
    }
    domains = {
        record["values"]["domain"]
        for record in records
        if record["values"]["kind"] == "domain"
    }
    # Addresses are stored normalized, so the stored column is compared as is.
    # An identity at a configured domain is history for that domain's row even
    # when no rule names its address and it presented no hosted claim, such as
    # a consumer account whose exact rule was since removed; the evaluator's
    # stricter claim test still decides whom the rule authorizes.
    named = Q(email__in=emails)
    for domain in domains:
        named |= Q(email__endswith=f"@{domain}")
    matches = {
        row["portal_user_id"]: row
        for row in AddressMatch.objects.filter(
            email__in=emails, ended_at__isnull=True
        ).values(
            "id",
            "portal_user_id",
            "email",
            "sign_in_email",
            "matched_at",
            "confirmed_at",
        )
    }
    rows = []
    for row in PortalUser.objects.filter(named | Q(pk__in=list(matches))).values(
        "id", "email", "hosted_domain", "disabled"
    ):
        match = matches.get(row["id"])
        rows.append(
            row
            | {
                "email": match["email"] if match else row["email"],
                "match": None
                if match is None
                else {
                    "match_id": str(match["id"]),
                    "id": str(row["id"]),
                    "sign_in_email": match["sign_in_email"],
                    "matched_at": match["matched_at"],
                    "confirmed_at": match["confirmed_at"],
                },
            }
        )
    logins = dict(
        AuditEvent.objects.filter(
            event_type="admin_login", actor_id__in=[row["id"] for row in rows]
        )
        .values_list("actor_id")
        .annotate(latest=Max("created_at"))
    )
    return [row | {"last_login": logins.get(row["id"])} for row in rows]


def _level(row):
    """A role's place in the ladder, so Administrator sorts above Staff."""
    return ROLE_LEVELS.index(row["role"])


# The Users table's sort (admin-portal "Users table"): Email by default, Role
# by level with Administrator first on the first choice, and the newest
# sign-in first, users who never signed in last either way.
USER_SORTING = Sorting.by_column(
    {
        "email": lambda row: row["email"],
        "name": lambda row: None if row["name"] is None else row["name"].casefold(),
        "role": _level,
        "last_login": lambda row: row["last_login"],
    },
    default="email",
    descending_first={"role", "last_login"},
)


def user_list(records, identities):
    """Each user's ID, email, Name, role and newest successful sign-in.

    In the users form every ``user`` record is a user. In the older
    sign-in-rules form, before the users upgrade, an exact-address rule that
    grants a role is the nearest thing to a user: it is listed with its
    highest role and no Name. Deny rules, hosted-domain rules and Ministry
    assignments are not users; the upgrade preview lists a domain rule's
    people. Rows come in address order; the caller sorts them. ``id`` is the
    user's opaque record ID, which Edit user's address and ``users preview``
    name. ``identities`` lists the recorded Google identities of the
    address in ID order, each ``{"id", "disabled"}``, which ``users list``
    prints, and ``address_match`` is the standing verified-address match of
    the address (#2343), or None.
    """
    latest, known, matched = {}, {}, {}
    for identity in identities:
        if identity.get("match"):
            matched[identity["email"]] = identity["match"]
        known.setdefault(identity["email"], []).append(
            {"id": str(identity["id"]), "disabled": identity["disabled"]}
        )
        moment = identity["last_login"]
        if moment is not None:
            latest[identity["email"]] = max(
                latest.get(identity["email"], moment), moment
            )
    rows = []
    for record in records:
        values = record["values"]
        if values["kind"] == "user":
            name, role = values["name"], values["role"]
        elif values["kind"] == "address" and values["roles"]:
            name, role = None, highest_role(values["roles"])
        else:
            continue
        rows.append(
            {
                "id": record["id"],
                "email": values["email"],
                "name": name,
                "role": role,
                "last_login": latest.get(values["email"]),
                "identities": sorted(
                    known.get(values["email"], []), key=lambda item: item["id"]
                ),
                "address_match": matched.get(values["email"]),
            }
        )
    return sorted(rows, key=lambda row: row["email"])
