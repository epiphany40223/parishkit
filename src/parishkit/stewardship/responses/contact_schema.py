"""The recorded ParishSoft Member contact request schema, and its value check.

A module of constants and one pure function, with no ParishSoft client,
transport or write: the human-run smoke check (``contact_smoke``) and an
Administrator's Edit value (``census_review``) both check values against
it, and only the smoke command may import the smoke check itself
(test_publication_worker pins that).
"""

import re

# The recorded MemberContactUpdateRequestModel constraints (a test pins them
# to tests/stewardship/fixtures/parishsoft_contact_capabilities.json): every
# field is a nullable string with these (minimum, maximum) lengths; the two
# dates are ``format: date``.
PUT_SCHEMA = {
    "firstName": (0, 30),
    "nickName": (0, 30),
    "middleName": (0, 30),
    "lastName": (0, 50),
    "maidenName": (0, 50),
    "dateOfBirth": (0, None),
    "dateOfDeath": (0, None),
    "language": (0, 50),
    "emailAddress": (0, 150),
    "homePhone": (10, 25),
    "cellPhone": (10, 25),
    "workPhone": (10, 25),
    "pager": (10, 25),
    "fax": (10, 25),
    "gender": (0, 1),
}
DATE_FIELDS = frozenset({"dateOfBirth", "dateOfDeath"})


def sendable(name, value):
    """Whether one value as read satisfies the recorded PUT schema.

    The smoke check reports it for each field as read, and an
    Administrator's edited census value is held to the same check
    (``census_review.edited_value``), so the two never disagree.
    """
    if value is None:
        return True
    if type(value) is not str:
        return False
    if name in DATE_FIELDS:
        return re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value) is not None
    minimum, maximum = PUT_SCHEMA[name]
    return minimum <= len(value) and (maximum is None or len(value) <= maximum)
