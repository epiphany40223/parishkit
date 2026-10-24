"""Google Account guidance for people whose address is not a Gmail address.

The Admin portal signs in with Google only, and a user's address must have a
Google Account. Many volunteer Ministry leaders use addresses at other
providers that have none, and Google refuses such an address before the
portal ever sees it (#1593). This short wording is the one copy of what to tell
them: the Users page About panel renders it (through the
``google_account_help`` template tag), and the leader email (#1592) reuses
these strings rather than restating them. The Admin "No access with this
account" page carries its own wording with the verified address instead
(#2116). The admin-portal spec's "Login and denial
behavior" section describes where it appears; the Admin login page keeps
only its button and the Family portal link (#2115).
"""

from django.utils.translation import gettext_lazy as _

WHICH_ADDRESS = _(
    "Sign in with the email address the parish gave access to. It doesn't "
    "have to be a Gmail address, but it needs a Google Account."
)
# The Administrator removed the "create a Google Account first" paragraph
# and its Google help link (#2115); the leader sign-in guide keeps that
# fuller help for an Administrator to send.
STILL_STUCK = _(
    "Still can't sign in? Ask the parish staff member who manages access to "
    "this portal."
)


def context():
    """The guidance as template context, for the shared help component."""
    return {
        "which_address": WHICH_ADDRESS,
        "still_stuck": STILL_STUCK,
    }
