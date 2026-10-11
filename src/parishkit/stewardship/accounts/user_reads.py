"""Portal users reads shared by the page and ``pk-admin users`` (#952).

Nothing here takes a request (the Admin automation specification's "Read
models"), so the Portal users page and the ``users list`` and
``users preview-upgrade`` commands read through the same functions and
cannot drift:

- ``policy_identities`` reads the recorded Google identities a policy
  names, each with its latest successful sign-in;
- ``user_list`` shapes the users ``users list`` prints, in either policy
  form;
- ``upgrade_rows`` and ``upgrade_panel`` shape the read-only users upgrade
  preview (``users_upgrade.document_preview``) for the page's Upgrade
  preview panel.
"""

from django.db.models import Max, Q
from django.utils.translation import gettext_lazy as _

from parishkit.stewardship.audit.models import AuditEvent
from parishkit.stewardship.web.tables import Sorting

from .policy_models import PortalUser
from .user_rows import ROLE_LABELS, role_labels
from .users_schema import ROLE_LEVELS
from .users_upgrade import highest_role


def policy_identities(records):
    """Only the Google identities this policy names, with successful sign-ins.

    Every verified Google attempt records an identity and refreshes its
    verification time, including a stranger's attempt that policy then denies.
    So the read is bounded by policy, not by attempts, and a sign-in means the
    durable login event written only when a session was actually issued.
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
    rows = list(
        PortalUser.objects.filter(named).values(
            "id", "email", "hosted_domain", "disabled"
        )
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
    """Each user's email, Name, role and newest successful sign-in.

    In the users form every ``user`` record is a user. In the older
    sign-in-rules form, before the users upgrade, an exact-address rule that
    grants a role is the nearest thing to a user: it is listed with its
    highest role and no Name. Deny rules, hosted-domain rules and Ministry
    assignments are not users; the upgrade preview lists a domain rule's
    people. Rows come in address order; the caller sorts them.
    """
    latest = {}
    for identity in identities:
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
                "email": values["email"],
                "name": name,
                "role": role,
                "last_login": latest.get(values["email"]),
            }
        )
    return sorted(rows, key=lambda row: row["email"])


def _source(user, address_rules):
    """Where a converted user comes from: its address rule or its domain rule."""
    rule = address_rules.get(user["email"])
    if rule is not None:
        return {"kind": "address", "roles": role_labels(rule["roles"])}
    return {"kind": "domain", "domain": user["email"].rsplit("@", 1)[1]}


# The Upgrade preview's users table sorts like the Users table it becomes:
# Email by default, Role by level with Administrator first.
UPGRADE_SORTING = Sorting.by_column(
    {
        "email": lambda row: row["email"],
        "name": lambda row: row["name"].casefold(),
        "role": _level,
        "source": lambda row: row["source_label"].casefold(),
    },
    default="email",
    descending_first={"role"},
)


def upgrade_rows(preview):
    """The Upgrade preview's users table: one row per user the upgrade writes.

    ``preview`` is ``document_preview``'s result for a configuration still in
    the sign-in-rules form. Each row names the rule the user comes from.
    """
    address_rules = {rule["email"]: rule for rule in preview["address_rules"]}
    rows = []
    for user in preview["users"]:
        source = _source(user, address_rules)
        label = (
            _("Exact-address rule")
            if source["kind"] == "address"
            else _("Hosted-domain rule %(domain)s") % {"domain": source["domain"]}
        )
        rows.append(
            user
            | {
                "role_label": ROLE_LABELS[user["role"]],
                "source": source,
                "source_label": str(label),
            }
        )
    return rows


def upgrade_panel(preview):
    """Everything the Upgrade preview panel shows except its users table.

    None once the configuration already holds users: there is nothing left to
    convert, so the page shows no panel. The Administrators come first, as
    the operator must confirm exactly that list before the conversion.
    """
    if preview["already_users"]:
        return None
    return {
        "administrators": preview["administrators"],
        "domain_rules": [
            {
                "domain": rule["domain"],
                "roles": role_labels(rule["roles"]),
                "role": ROLE_LABELS[rule["role"]],
                "count": len(rule["users"]),
            }
            for rule in preview["domain_rules"]
        ],
        "dropped": preview["dropped_deny_rules"],
        "never_signed_in": preview["not_converted"]["never_signed_in"],
        "disabled": preview["not_converted"]["disabled"],
    }
