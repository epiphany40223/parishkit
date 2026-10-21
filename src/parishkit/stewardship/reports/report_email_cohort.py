"""A report email's recipients: the frozen cohort and the recheck (#1786).

The report-emails spec ("Resolving recipients") freezes each send's cohort
when it records its inputs: the groups expand to the users holding the role
then, a chosen user must be an Administrator or Staff, and each address
appears once, chosen by its first entry. ``resolve_cohort`` does it from a
configuration's address rules; the snapshot's guard recomputes it with
``stewardship_report_email_cohort_v1`` and refuses any other.

Before a recipient's message is made (and, once the conversion wires the
delivery checks, before each submission) the recipient is rechecked by the
database's ``stewardship_report_email_current_v1``, read here, so the page,
the worker and mail dispatch share one rule.
"""

from django.db import connection

from parishkit.stewardship.accounts.models import AddressRule

ADMINISTRATOR, STAFF = "administrator", "staff"


def _eligible(roles):
    """Whether an address rule's roles make it a possible report recipient."""
    return ADMINISTRATOR in roles or STAFF in roles


def _in_group(group, roles):
    """Whether roles hold a group's role: All Staff is Staff without Administrator."""
    if group == "administrators":
        return ADMINISTRATOR in roles
    return STAFF in roles and ADMINISTRATOR not in roles


def resolve_cohort(values, rules):
    """The cohort ``[{address, kind, ref}]`` of a report's entries, by address.

    ``rules`` are ``(record id, address, roles)`` for every address rule of
    the configuration. An entry naming a user who is not an eligible user
    there, or a group with no members, adds nobody.
    """
    rules = [(str(record), email.lower(), roles) for record, email, roles in rules]
    chosen = {}
    for entry in values["recipients"]:
        ((kind, value),) = entry.items()
        if kind == "user":
            found = [
                (email, "user", value)
                for record, email, roles in rules
                if record == value and _eligible(roles)
            ]
        elif kind == "group":
            found = [
                (email, "group", value)
                for _record, email, roles in rules
                if _in_group(value, roles)
            ]
        else:
            found = [(value, "outside", None)]
        for address, how, ref in found:
            chosen.setdefault(address, {"address": address, "kind": how, "ref": ref})
    return [chosen[address] for address in sorted(chosen)]


def frozen_cohort(values, configuration_id):
    """The cohort of a report's entries under one applied configuration."""
    rules = AddressRule.objects.filter(configuration_id=configuration_id).values_list(
        "record_id", "email", "roles"
    )
    return resolve_cohort(values, rules)


def still_recipient(definition_id, member):
    """Whether a cohort member is still a recipient now (spec, the recheck)."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT stewardship_report_email_current_v1(%s,%s,%s,%s)",
            [definition_id, member["kind"], member["ref"], member["address"]],
        )
        return cursor.fetchone()[0] is True


def testing_label(member):
    """How a Testing send's first line names the intended recipient."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT stewardship_report_email_label_v1(%s,%s,%s)",
            [member["kind"], member["ref"], member["address"]],
        )
        return cursor.fetchone()[0]
