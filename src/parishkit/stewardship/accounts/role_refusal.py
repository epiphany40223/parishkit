"""The refusal for a current Admin session whose role lacks a page (#1924).

A signed-in Staff user or Ministry leader who opens an Administrator page
(from a bookmark or a pasted link) used to get the generic denial, which
says to sign in again. Signing in again cannot help: the role is the
reason. This refusal says so in fixed, reviewed words, naming the roles
that hold the page's capability, and links to Administration home instead
of the sign-in page. It is still a 403 with the closed ``denied`` code and
refusal-log reason. Only a current session reaches it: a missing or ended
one keeps the generic denial, so a visitor without one learns nothing new.
"""

from django.utils.translation import gettext_lazy as _

from parishkit.stewardship.web.contracts import (
    ErrorCode,
    FieldError,
    validation_response,
)
from parishkit.stewardship.web.namespaces import ADMIN_HOME
from parishkit.stewardship.web.refusals import (
    AccessDenied,
    UserFacingDenied,
    annotate,
)

from .policy import LEADER_CAPABILITIES, STAFF_CAPABILITIES, Capability, allows

# The sentence for each role level a capability needs. Fixed literals only:
# never the capability's own name.
_FOR_ADMINISTRATORS = _(
    "This page is for Administrators. Your access doesn't include it."
)
_FOR_STAFF = _(
    "This page is for Administrators and Staff. Your access doesn't include it."
)


class RoleDenied(UserFacingDenied):
    """A current session whose role lacks the page's capability (HTTP 403).

    Its own type, so that views with their own error pages (System logs,
    Sent mail) can answer it with the shared role refusal (``response``)
    while every other ``PermissionError`` keeps their page. Signing in
    again cannot help, so the error page offers no Sign in again.
    """

    offers_sign_in = False


def role_denied(capability):
    """The exception for a current principal that lacks ``capability``.

    Only a capability that no Ministry leader holds gets the role wording.
    A Ministry leader's capabilities are scoped to the Ministries it leads,
    so their refusals are about scope, not role, and keep the generic
    denial; so do the Family capabilities, which no Admin page checks.
    """
    if capability in LEADER_CAPABILITIES or capability in {
        Capability.FAMILY_READ,
        Capability.FAMILY_SUBMIT,
    }:
        return AccessDenied("Access is unavailable.")
    staff = capability in STAFF_CAPABILITIES
    return role_denied_for("staff" if staff else "administrator")


def role_denied_for(level):
    """The role refusal for a page gated by role level, not one capability.

    ``level`` is ``"staff"`` (Administrators and Staff) or
    ``"administrator"`` (Administrators only). For pages that check a role
    or a set of sections directly, so they need not borrow an unrelated
    capability to name who may open them. The caller has already checked
    that the session is current.
    """
    if level not in {"staff", "administrator"}:
        raise ValueError("A role refusal level is staff or administrator.")
    return RoleDenied(
        _FOR_STAFF if level == "staff" else _FOR_ADMINISTRATORS,
        fix=_(
            "Choose a page from the menu, or ask a parish administrator if you "
            "need this one."
        ),
        link=ADMIN_HOME,
        link_label=_("Administration home"),
    )


def require_role(actor, level):
    """Return ``actor`` if it holds role ``level``, else raise its refusal.

    ``"administrator"`` needs the Administrator role; ``"staff"`` needs the
    Administrator or Staff role. ``None`` (no current session) keeps the
    generic denial, as in ``require``.
    """
    if actor is None:
        raise AccessDenied("Access is unavailable.")
    allowed = {"administrator", level}
    if not allowed & actor.roles:
        raise role_denied_for(level)
    return actor


def require(actor, capability):
    """Return ``actor`` if it may use ``capability``, else raise its refusal.

    ``actor`` is ``authenticated_admin``'s result: ``None`` (no current
    session) keeps the generic denial, which tells the person to sign in
    again; a current principal without the capability gets ``role_denied``.
    """
    if actor is None:
        raise AccessDenied("Access is unavailable.")
    if not allows(actor, capability):
        raise role_denied(capability)
    return actor


def response(error):
    """The typed 403 for a ``RoleDenied``, for views that answer, not raise.

    The browser-error middleware shows people the standard error page (no
    Sign in again, a link home); scripts and in-place regions get the same
    refusal as JSON.
    """
    return annotate(
        validation_response(
            [FieldError(ErrorCode.DENIED)], status=403, refusal=error.refusal
        ),
        error,
    )
