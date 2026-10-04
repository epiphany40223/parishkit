"""The caller of an Admin action, independent of how it arrived.

Admission (``sessions.authenticated_admin``), freshness (``require_fresh``),
privileged intake (``privileged_actions.admit_admin_action``) and page
authorization (``admin_editing.principal``) take an ``AdminCaller`` instead of
a Django request. They use only its session store, its ``PortalSession`` and,
on the web, its CSRF state, so one service layer serves the Admin pages and,
later, the host command line (see the Admin automation specification's
"Caller seam").

``AdminCaller.from_request`` is the only web constructor. A web caller keeps
its request privately, mirroring admission results onto it exactly as before
and rotating its CSRF token when authority rotates, so the pages behave as they
did. The automation constructor arrives with automation sessions; until then
the automation rules here are fail-closed and unreachable in production.

Build one caller per request, early, and pass that same caller everywhere. A
web caller snapshots the request's session: if a second caller converted from
the same request rotated authority, the first would still hold the revoked
session. So a web caller must never outlive an authority rotation performed
through another conversion.

``as_caller`` lets the five moved functions still accept a Django request
while their callers move. That fallback is temporary and is removed when the
last module moves onto the caller (the final ADM-11 PR).

Not to be confused with ``admin_context``, which builds page chrome.
"""

from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from django.middleware.csrf import rotate_token

from parishkit.stewardship.observability import bound_correlation

WEB = "web"
AUTOMATION = "automation"
FULL = "full"
READ_ONLY = "read_only"

# Set once at construction: the channel, the scope an automation session was
# approved with, and the web request with its CSRF assertion may only narrow
# what the caller can do, never change.
_FIXED = frozenset(
    {
        "channel",
        "automation_session_id",
        "scope",
        "_request",
        "_state_changing",
        "_constructed",
    }
)


def _csrf_post(request):
    """Whether ``request`` is the CSRF-processed POST under ``/admin/``.

    All four conditions are required: Django's CSRF middleware marked the
    request processed, and no test-client exemption switched enforcement off.
    """
    return (
        request.method == "POST"
        and request.path_info.startswith("/admin/")
        and getattr(request, "csrf_processing_done", False) is True
        and not getattr(request, "_dont_enforce_csrf_checks", False)
    )


@dataclass(eq=False)
class AdminCaller:
    """Who is calling, through which channel, under which session.

    ``portal_session`` and ``principal`` are filled in by admission; a caller
    that has not been admitted has neither. ``channel``,
    ``automation_session_id``, ``scope`` and the web request cannot be
    reassigned. A web caller's scope is always full; an automation caller must
    name its scope explicitly, so nothing defaults to more than was approved.
    """

    session: Any
    channel: str
    portal_session: Any = None
    principal: Any = None
    automation_session_id: UUID | None = None
    scope: str | None = None
    correlation_id: UUID | None = None
    campaign_id: int | None = None
    # Web only: the request whose attributes and CSRF token admission keeps in
    # step, and whether the CSRF-protected POST was asserted at construction.
    _request: Any = field(default=None, repr=False)
    _state_changing: bool = field(default=False, repr=False)

    def __post_init__(self):
        """Refuse an incoherent caller rather than guess what it may do.

        The CSRF assertion is rechecked here, not only in ``from_request``, so
        direct construction or ``dataclasses.replace`` cannot skip it.
        """
        if self.channel == WEB:
            if self.scope is None:
                self.scope = FULL
            coherent = (
                self._request is not None
                and self.automation_session_id is None
                and self.scope == FULL
            )
            if coherent and self._state_changing and not _csrf_post(self._request):
                raise PermissionError("Access is unavailable.")
        elif self.channel == AUTOMATION:
            coherent = (
                self._request is None
                and self.automation_session_id is not None
                and self.scope in {FULL, READ_ONLY}
                and not self._state_changing
            )
        else:
            coherent = False
        if not coherent:
            raise ValueError("Invalid Admin caller.")
        object.__setattr__(self, "_constructed", True)

    def __setattr__(self, name, value):
        """Keep the channel, scope and web request as the constructor set them."""
        if name in _FIXED and getattr(self, "_constructed", False):
            raise AttributeError(f"AdminCaller.{name} is fixed at construction.")
        object.__setattr__(self, name, value)

    @classmethod
    def from_request(cls, request, *, state_changing=False):
        """Build the web caller for one Admin request.

        ``state_changing`` asserts the CSRF-processed POST under ``/admin/``
        that privileged intake requires; a request that fails it is refused
        before any caller exists. Views that already ran admission pass their
        ``portal_session`` and ``principal`` along. The correlation is the
        request's bound one, if any; none is invented.
        """
        if state_changing and not _csrf_post(request):
            raise PermissionError("Access is unavailable.")
        return cls(
            session=getattr(request, "session", None),
            channel=WEB,
            portal_session=getattr(request, "portal_session", None),
            principal=getattr(request, "principal", None),
            correlation_id=bound_correlation(),
            _request=request,
            _state_changing=state_changing,
        )

    @property
    def read_only(self):
        """An automation session approved only for status and reads."""
        return self.scope == READ_ONLY

    @property
    def state_changing(self):
        """Whether this web caller passed the CSRF-protected POST assertion."""
        return self._state_changing

    def admitted(self, portal_session, principal):
        """Record a successful admission, on the request too for the web."""
        self.portal_session, self.principal = portal_session, principal
        if self.channel == WEB:
            self._request.portal_session = portal_session
            self._request.principal = principal

    def replace_session(self, session):
        """Adopt a rotated web session and rotate the CSRF token, as before."""
        if self.channel != WEB:
            raise PermissionError("Access is unavailable.")
        self.session = session
        self._request.session = session
        rotate_token(self._request)


def as_caller(value, *, state_changing=False):
    """Return ``value`` as a caller, converting a Django request on the web.

    Modules not yet moved onto the caller still pass their request; it goes
    through ``AdminCaller.from_request``, the only web constructor. A caller
    passed for a state-changing call must already have passed that assertion.
    This request fallback is temporary: the final ADM-11 PR removes it once
    the last module passes a caller.
    """
    if isinstance(value, AdminCaller):
        if state_changing and value.channel == WEB and not value.state_changing:
            raise PermissionError("Access is unavailable.")
        return value
    return AdminCaller.from_request(value, state_changing=state_changing)
