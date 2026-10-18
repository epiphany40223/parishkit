"""Google Account guidance for people whose address is not a Gmail address.

The Admin portal signs in with Google only, and a user's address must have a
Google Account. Many volunteer Ministry leaders use addresses at other
providers that have none, and Google refuses such an address before the
portal ever sees it (#1593). This wording is the one copy of what to tell
them: the Admin login page, the generic Admin not-authorized page and the
Users page About panel all render it (through the ``google_account_help``
template tag), and a later leader email (#1592) should reuse these strings
rather than restate them. The admin-portal spec's "Login and denial
behavior" section describes where it appears.
"""

from django.utils.translation import gettext_lazy as _

# Google's own help article on creating a Google Account, which covers using
# an existing (non-Gmail) email address. Linking Google's page rather than
# describing its screens step by step keeps this text right when Google
# changes its sign-up flow.
HELP_URL = "https://support.google.com/accounts/answer/27441"

WHICH_ADDRESS = _(
    "Sign in with the email address the parish gave access to. It doesn't "
    "have to be a Gmail address, but it needs a Google Account."
)
NO_ACCOUNT = _(
    "If that address has no Google Account yet, create one for it first, "
    "keeping your existing address: on Google's sign-up page, choose to use "
    "your existing email address instead of a new Gmail address. Then sign "
    "in again."
)
LINK_LABEL = _("How to create a Google Account (Google help)")
STILL_STUCK = _(
    "Still can't sign in? Ask the parish staff member who manages access to "
    "this portal."
)


def context():
    """The guidance as template context, for the shared help component."""
    return {
        "which_address": WHICH_ADDRESS,
        "no_account": NO_ACCOUNT,
        "help_url": HELP_URL,
        "link_label": LINK_LABEL,
        "still_stuck": STILL_STUCK,
    }
