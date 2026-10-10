"""Read-only review rows for the applied login rules.

Pure shaping over the applied canonical policy records. It decides nothing:
every role shown comes from the one policy evaluator, given the hosted-domain
claim a recorded Google identity actually presented, so this page never
reasons from an email suffix the way a sign-in itself refuses to. A rule's
Ministry leader role lets a person sign in, but the Ministries they see come
from their ParishSoft Ministry roles (#922). Ministry assignment records from
before #922 stay in older configurations, unread and not shown.

An identity here is a dict with `email`, `hosted_domain`, `disabled` and
`last_login`. Google's stable subject owns identity, so one address can have
several. `last_login` is the latest *successful* sign-in or None: a verified
Google attempt that policy then denied is not a sign-in.
"""

from django.utils.translation import gettext_lazy as _
from django.utils.translation import ngettext_lazy

from .policy import resolve_roles

ROLE_LABELS = {
    "administrator": _("Administrator"),
    "staff": _("Staff"),
    "ministry_leader": _("Ministry leader"),
}
# Neutral nouns: the same origin names a rule's creation and a role grant, and
# a seeded rule was created by confirming a parish source Chairperson before
# #922, not by the Chairperson it names.
ORIGIN_LABELS = {
    "manual": _("Administrator entry"),
    "chair-seed": _("Parish source Chairperson"),
}


def role_labels(roles):
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

    def __init__(self, records, identities):
        """Group rules by address and domain, and identities by address.

        Ministry assignment records, which grant nothing since #922, are
        skipped.
        """
        self.domains, self.addresses, self.identities = {}, {}, {}
        for record in records:
            values = record["values"]
            if values["kind"] == "domain":
                self.domains[values["domain"]] = record
            elif values["kind"] == "address":
                self.addresses[values["email"]] = record
        # Evidence that a domain rule works is an identity the evaluator really
        # authorizes through it: the email suffix, the signed hosted-domain claim
        # and the rule must all agree, no exact rule may replace it, and the
        # identity must be usable. A Workspace alias domain presents the primary
        # claim with another suffix, so a bare claim match would mislead.
        self.suffixed, self.authorized = {}, {}
        for identity in identities:
            email = identity["email"].lower()
            domain = email.rsplit("@", 1)[1]
            self.identities.setdefault(email, []).append(identity)
            self.suffixed.setdefault(domain, []).append(identity)
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
        return [*([rule] if rule else []), *([domain] if domain else [])]

    def resolve(self, email, hosted_domain):
        """What the real evaluator gives this address with this hosted claim."""
        return resolve_roles(email, hosted_domain, self.relevant(email))

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
                "roles": role_labels(record["values"]["roles"]),
                # The stored role keys, so the edit form ticks what is applied.
                "configured": record["values"]["roles"],
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
    """Exact-address rules with granted roles and provenance.

    An exact rule replaces any domain rule and ignores the hosted-domain claim,
    so what policy grants the address does not depend on which identity asks.
    Whether a particular identity may sign in at all is a separate fact, stated
    by the disabled-identity warning rather than hidden inside the roles.
    """
    rows = []
    for email, record in sorted(policy.addresses.items()):
        values = record["values"]
        granted, _ministries = policy.resolve(email, None)
        warnings = []
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
                "configured": values["roles"],
                "granted": role_labels(granted),
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
                "last_login": _latest(policy.identities.get(email, [])),
                "warnings": warnings,
            }
        )
    return rows
