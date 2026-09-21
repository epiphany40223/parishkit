"""Read-only review rows for the applied login rules and Ministry assignments.

Pure shaping over the applied canonical policy records. It decides nothing:
every role and Ministry scope shown comes from the one policy evaluator, given
the hosted-domain claim a recorded Google identity actually presented, so this
page never reasons from an email suffix the way a sign-in itself refuses to.

An identity here is a dict with `email`, `hosted_domain`, `disabled` and
`last_login`. Google's stable subject owns identity, so one address can have
several. `last_login` is the latest *successful* sign-in or None: a verified
Google attempt that policy then denied is not a sign-in.
"""

from django.utils.translation import gettext_lazy as _
from django.utils.translation import ngettext_lazy

from .policy import assignment_in_force, resolve_roles

ROLE_LABELS = {
    "administrator": _("Administrator"),
    "staff": _("Staff"),
    "ministry_leader": _("Ministry leader"),
}
# Neutral nouns: the same origin names a rule's creation, a role grant and an
# assignment's source, and a seeded rule was created by reconciling the parish
# source, not by the Chairperson it names.
ORIGIN_LABELS = {
    "manual": _("Administrator entry"),
    "chair-seed": _("Parish source Chairperson"),
}


def _labels(roles):
    """Present roles in one fixed order rather than a set's arbitrary one."""
    return [ROLE_LABELS[role] for role in ROLE_LABELS if role in roles]


def _latest(identities):
    """The most recent successful sign-in among these identities, if any."""
    return max(
        (item["last_login"] for item in identities if item["last_login"]),
        default=None,
    )


class AppliedPolicy:
    """The applied records indexed once, so no row rescans the whole policy."""

    def __init__(self, records, identities, active_seeded=frozenset()):
        """Group rules by address and domain, and identities by address."""
        self.active_seeded = active_seeded
        self.domains, self.addresses = {}, {}
        self.assignments, self.identities = {}, {}
        for record in records:
            values = record["values"]
            if values["kind"] == "domain":
                self.domains[values["domain"]] = record
            elif values["kind"] == "address":
                self.addresses[values["email"]] = record
            else:
                self.assignments.setdefault(values["email"], []).append(record)
        self.suffixed = {}
        for identity in identities:
            email = identity["email"].lower()
            self.identities.setdefault(email, []).append(identity)
            self.suffixed.setdefault(email.rsplit("@", 1)[1], []).append(identity)
        # Evidence that a domain rule works is an identity the evaluator really
        # authorizes through it: the email suffix, the signed hosted-domain claim
        # and the rule must all agree, no exact rule may replace it, and the
        # identity must be usable. A Workspace alias domain presents the primary
        # claim with another suffix, so a bare claim match would mislead.
        self.authorized = {}
        for identity in identities:
            email = identity["email"].lower()
            domain = email.rsplit("@", 1)[1]
            if (
                domain in self.domains
                and email not in self.addresses
                and not identity["disabled"]
                and self.resolve(email, identity["hosted_domain"])[0]
            ):
                self.authorized.setdefault(domain, []).append(identity)

    def relevant(self, email):
        """Only the records the evaluator could use for this one address."""
        rule = self.addresses.get(email)
        domain = self.domains.get(email.rsplit("@", 1)[1])
        return [
            *([rule] if rule else []),
            *([domain] if domain else []),
            *self.assignments.get(email, []),
        ]

    def resolve(self, email, hosted_domain):
        """What the real evaluator gives this address with this hosted claim."""
        return resolve_roles(
            email, hosted_domain, self.relevant(email), self.active_seeded
        )

    def held(self, email):
        """This address's assignments, and whether each is currently in force."""
        return sorted(
            (
                {
                    "ministry_duid": record["values"]["ministry_duid"],
                    "source": ORIGIN_LABELS[record["values"]["source"]],
                    "active": assignment_in_force(record, self.active_seeded),
                }
                for record in self.assignments.get(email, [])
            ),
            key=lambda item: item["ministry_duid"],
        )

    def disabled_warning(self, email):
        """Disabling is per Google identity, so say how many, never just "is"."""
        known = self.identities.get(email, [])
        disabled = sum(item["disabled"] for item in known)
        if not disabled:
            return []
        if disabled == len(known):
            return [
                ngettext_lazy(
                    "The recorded Google identity for this address is disabled "
                    "and cannot sign in, whatever policy grants the address.",
                    "All %(count)d recorded Google identities for this address "
                    "are disabled and cannot sign in, whatever policy grants the "
                    "address.",
                    disabled,
                )
                % {"count": disabled}
            ]
        return [
            ngettext_lazy(
                "%(count)d of %(total)d recorded Google identities for this "
                "address is disabled.",
                "%(count)d of %(total)d recorded Google identities for this "
                "address are disabled.",
                disabled,
            )
            % {"count": disabled, "total": len(known)}
        ]


def disclosed(records):
    """How many rows the page shows, for an audit that names none of them."""
    addressed = {
        record["values"]["email"]
        for record in records
        if record["values"]["kind"] == "address"
    }
    relying = {
        record["values"]["email"]
        for record in records
        if record["values"]["kind"] == "assignment"
    } - addressed
    return sum(record["values"]["kind"] != "assignment" for record in records) + len(
        relying
    )


def domain_rows(policy):
    """Hosted-domain rules, with the recorded accounts each really authorizes.

    An email suffix alone never matches a domain rule. Applied policy never has
    a domain rule without a role, so there is no such case to describe.
    """
    rows = []
    for domain, record in sorted(policy.domains.items()):
        authorized = policy.authorized.get(domain, [])
        rows.append(
            {
                "domain": domain,
                "roles": _labels(record["values"]["roles"]),
                "authorized": len(authorized),
                # As for an address: the latest successful sign-in among every
                # recorded identity at this domain, whatever its state now.
                "last_login": _latest(policy.suffixed.get(domain, [])),
                "warnings": []
                if authorized
                else [
                    _(
                        "No recorded Google account is authorized through this "
                        "rule yet. It needs a sign-in that presents this "
                        "hosted-domain claim from an address in this domain."
                    )
                ],
            }
        )
    return rows


def address_rows(policy):
    """Exact-address rules with granted roles, provenance and assignments.

    An exact rule replaces any domain rule and ignores the hosted-domain claim,
    so what policy grants the address does not depend on which identity asks.
    Whether a particular identity may sign in at all is a separate fact, stated
    by the disabled-identity warning rather than hidden inside the roles.
    """
    rows = []
    for email, record in sorted(policy.addresses.items()):
        values = record["values"]
        granted, ministries = policy.resolve(email, None)
        held = policy.held(email)
        warnings = []
        if "ministry_leader" in values["roles"] and "ministry_leader" not in granted:
            warnings.append(
                _(
                    "The Ministry leader role is suspended: no Chairperson "
                    "assignment is currently confirmed by the parish source."
                )
            )
        elif (
            "ministry_leader" in granted
            and not ministries
            # An Administrator is a leader of everything and needs no assignment.
            and "administrator" not in granted
        ):
            warnings.append(_("Ministry leader with no active Ministry assignment."))
        # Only when the role is not configured at all: for a suspended seeded
        # role the remedy is the parish source, not granting a role it has.
        if (
            held
            and "ministry_leader" not in values["roles"]
            and ("administrator" not in granted)
        ):
            warnings.append(
                _(
                    "Ministry assignments have no effect without the Ministry "
                    "leader role."
                )
            )
        if email.rsplit("@", 1)[1] in policy.domains:
            warnings.append(
                _("This exact address replaces its domain rule for this person.")
            )
        warnings.extend(policy.disabled_warning(email))
        rows.append(
            {
                "email": email,
                # An empty role set is a deliberate denial, never an accident.
                "deny": not values["roles"],
                "granted": _labels(granted),
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
                "last_login": _latest(policy.identities.get(email, [])),
                "warnings": warnings,
            }
        )
    return rows


def domain_assignment_rows(policy):
    """Assignments for people who have no exact rule and rely on a domain rule.

    A recorded identity is judged by the evaluator with the hosted-domain claim
    it really presented: a consumer account on the same suffix receives nothing.
    Only an address that has never been seen is described from policy alone, and
    then as a condition, never as a fact about that person.
    """
    rows = []
    for email in sorted(set(policy.assignments) - set(policy.addresses)):
        known = policy.identities.get(email, [])
        # In effect only when a usable identity receives the role *and* scope:
        # the role alone, with every assignment suspended, leads nothing.
        leading = any(
            not item["disabled"] and "ministry_leader" in roles and ministries
            for item in known
            for roles, ministries in [policy.resolve(email, item["hosted_domain"])]
        )
        domain = email.rsplit("@", 1)[1]
        rule = policy.domains.get(domain)
        warnings = []
        if not (rule and "ministry_leader" in rule["values"]["roles"]):
            # The root cause, stated whether or not anyone has signed in.
            warnings.append(
                _("No login rule gives this person the Ministry leader role.")
            )
        elif not known:
            warnings.append(
                _(
                    "Not seen yet. These assignments take effect only if this "
                    "person's Google account presents the %(domain)s "
                    "hosted-domain claim."
                )
                % {"domain": domain}
            )
        elif not leading:
            warnings.append(
                _(
                    "No usable Google identity recorded for this address "
                    "receives the Ministry leader role with an assignment in "
                    "force, so these assignments have no effect."
                )
            )
        warnings.extend(policy.disabled_warning(email))
        rows.append(
            {
                "email": email,
                "leading": leading,
                "assignments": policy.held(email),
                "last_login": _latest(known),
                "warnings": warnings,
            }
        )
    return rows
