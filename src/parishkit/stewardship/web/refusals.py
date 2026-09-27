"""User-facing refusals: say what was wrong and how to fix it, safely.

Most refusals stay closed: ``error_response`` maps an exception to a generic,
static message and never shows exception text, because that text may echo
submitted values or internal detail. A refusal an Admin can actually trigger
and act on (a template that a schedule still uses, a page changed in another
tab, a missing earlier setup step) can instead raise one of the types below.
Each carries a reviewed, static explanation, an optional "how to fix it"
sentence and an optional link to the page that fixes it. The browser error
page, the setup "not available" page and the JSON body for scripts show these
fields; the HTTP status stays the one the base exception type already maps to.

Never build a refusal message from submitted values or another exception's
text: pass only translated literals (or a fixed choice among them) and
server-owned paths from ``reverse``.
"""

from dataclasses import dataclass

from django.utils.translation import gettext_lazy as _

from parishkit.stewardship.storage import StaleRecordError


@dataclass(frozen=True)
class Refusal:
    """The static, reviewed text of one refusal and its optional fix link."""

    message: str
    fix: str | None = None
    link: str | None = None
    link_label: str | None = None

    def as_dict(self):
        """JSON-safe fields for scripts; the link is a same-origin path."""
        return {
            "message": str(self.message),
            "fix": str(self.fix) if self.fix else None,
            "link": (
                {"url": self.link, "label": str(self.link_label)} if self.link else None
            ),
        }


class UserFacing:
    """Mixin giving an exception a safe ``refusal`` to show the Admin.

    ``link`` must be a server-owned same-origin path (for example from
    ``reverse``); ``link_label`` names where it goes.
    """

    def __init__(self, message, *, fix=None, link=None, link_label=None):
        if link is not None and (
            type(link) is not str or not link.startswith("/") or link.startswith("//")
        ):
            raise TypeError("A refusal link must be a same-origin path.")
        if (link is None) != (link_label is None):
            raise TypeError("A refusal link needs a label, and a label a link.")
        super().__init__(str(message))
        self.refusal = Refusal(message, fix, link, link_label)


class UserFacingError(UserFacing, ValueError):
    """An invalid request the Admin can correct (HTTP 400)."""


class UserFacingStale(UserFacing, StaleRecordError):
    """The page is out of date and must be reloaded (HTTP 409)."""


class UserFacingMissing(UserFacing, LookupError):
    """A prerequisite is missing, usually an earlier step (HTTP 404)."""


class UserFacingDenied(UserFacing, PermissionError):
    """The action is not allowed in the current state (HTTP 403)."""


def stale_page():
    """The standard refusal when a form's version is older than the saved data."""
    return UserFacingStale(
        _("This page changed in another tab or session."),
        fix=_(
            "Reload the page to see the latest version, then make your change again."
        ),
    )


def unexpected_fields():
    """The standard refusal for a form posted with fields it does not have."""
    return UserFacingError(
        _("The form was submitted with unexpected data."),
        fix=_("Reload the page and try again."),
    )
