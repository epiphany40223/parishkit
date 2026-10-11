"""The words the Users page and its alerts use for the three roles (#952).

Roles are levels: Ministry leader < Staff < Administrator, each including
everything below it (``users_schema.ROLE_LEVELS``). ``ROLE_LABELS`` lists
them highest first, the order every page and alert shows them in, and
``ROLE_HELP`` is each role's one plain-language line on New and Edit user.
"""

from django.utils.translation import gettext_lazy as _

ROLE_LABELS = {
    "administrator": _("Administrator"),
    "staff": _("Staff"),
    "ministry_leader": _("Ministry leader"),
}
ROLE_HELP = {
    "ministry_leader": _(
        "Signs in and sees only the Ministries they lead in ParishSoft."
    ),
    "staff": _("Everything a Ministry leader can do, and sees every Ministry."),
    "administrator": _(
        "Everything Staff can do, and can see and change everything, including users."
    ),
}


def role_labels(roles):
    """Present roles in one fixed order rather than a set's arbitrary one."""
    return [ROLE_LABELS[role] for role in ROLE_LABELS if role in roles]
