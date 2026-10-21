"""The fake ParishSoft's Member contact read and write (#1884).

Census publication (``responses.publication_gateway``) reads one Member's
contact record keyed by the v2 Member contact PUT's field names and writes
that PUT. Which real ParishSoft GET returns those fields is unconfirmed, so
the real gateway refuses its read; the local fake is the one ParishSoft
whose shape this package defines. This module is that definition, in both
directions, so pk-local can run Update ParishSoft through the real worker,
gateway, session and helper:

- ``contact_record`` maps the fake's ``GET members/{id}`` Member object onto
  the PUT's names, for the gateway's read of the fake; and
- ``contact_edit`` turns one PUT body into an ordinary ``member`` edit
  (``fake_edits``), which the fake records in its configuration file.

A value written reads back exactly as written (a date as ``YYYY-MM-DD``,
phones and email addresses as sent), as the publish worker's read-back
check requires. See "Fake ParishSoft writes" in
docs/specs/stewardship/local-environment/spec.md.
"""

from .fake_edits import DATE, MEMBER_FIELDS, EditError, check_value, served_date

# The PUT fields census publication may write (the Member API fields of
# ``responses.capabilities``), each with the fake Member field that holds it.
WRITABLE = {
    "firstName": "firstName",
    "middleName": "middleName",
    "lastName": "lastName",
    "nickName": "nickName",
    "maidenName": "maidenName",
    "dateOfBirth": "birthdate",
    "language": "language",
    "emailAddress": "emailAddress",
    "homePhone": "homePhone",
    "cellPhone": "mobilePhone",
    "workPhone": "workPhone",
}
# The Member contact smoke check's two marker fields (#1399,
# ``responses.contact_smoke.MARKERS``). Census publication never writes them,
# but the fake's PUT takes them too, so the smoke round trip runs against the
# fake on pk-local; the generated parish leaves both unset, and an operator
# edit fills one in first.
SMOKE_WRITABLE = {"pager": "pager", "fax": "fax"}
# What the read returns: the writable fields plus the two the fake also
# holds. ``fax`` and ``pager`` are left out (publication never writes them),
# so the planner treats them as not read rather than blank; the smoke check
# reads the raw Member object instead.
READABLE = {**WRITABLE, "gender": "sex", "dateOfDeath": "dateOfDeath"}


def contact_record(member):
    """One fake Member object as a contact record keyed by the PUT's names.

    Dates are civil dates (``YYYY-MM-DD``), the form the PUT sends; every
    other value is the Member's own. A field the object lacks is left out.
    Anything that is not a Member object raises ValueError.
    """
    if type(member) is not dict:
        raise ValueError("The fake Member record is not an object.")
    record = {}
    for name, field in READABLE.items():
        if field not in member:
            continue
        value = member[field]
        if MEMBER_FIELDS[field] == DATE and value is not None:
            if type(value) is not str:
                raise ValueError("The fake Member date is not text.")
            value = value[:10]
        record[name] = value
    return record


def contact_edit(member_id, body, parish):
    """The ``member`` edit one PUT ``body`` makes, or EditError if refused.

    The PUT is a partial update: a field left out or sent as ``null`` is
    left unchanged. Only ``WRITABLE`` fields and the smoke check's
    ``SMOKE_WRITABLE`` markers are taken, a blank value is
    refused (as the real API refuses ``""``), and each value must be one the
    source loaders accept (``fake_edits.check_value``). A body that writes
    nothing is refused, so every recorded edit names at least one field
    (which may already hold the value sent: such a PUT is still recorded).
    """
    if type(body) is not dict:
        raise EditError("The contact PUT body is not an object.")
    fields = WRITABLE | SMOKE_WRITABLE
    changes = {}
    for name, value in body.items():
        if name not in fields:
            raise EditError(f"The fake cannot write {name}.")
        if value is None:
            continue
        field = fields[name]
        if MEMBER_FIELDS[field] == DATE:
            # Exactly YYYY-MM-DD, so the value reads back as it was sent.
            value = served_date(value, name)
        check_value(field, value, MEMBER_FIELDS[field], parish)
        changes[field] = value
    if not changes:
        raise EditError("The contact PUT writes no field.")
    return {"kind": "member", "id": member_id, "set": changes}
