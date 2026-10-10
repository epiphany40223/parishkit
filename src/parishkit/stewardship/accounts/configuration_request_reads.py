"""The configuration request status read, moved out of its page's view.

Change status (``ministry_views.configuration_request``) reads
an Administrator's own request through ``receipt``, and so does the Admin
automation command line (``config request show``, ADM-11), so both report
the same checkpoint. It takes no request: ``caller`` is the page's request or
an ``AdminCaller``.
"""

from dataclasses import replace

from parishkit.stewardship.campaigns.live_end_date import refusal_text

from .configuration_requests import request_status
from .policy import Capability, allows
from .sessions import authenticated_admin


def receipt(caller, service, request_id, actor):
    """The actor's own request status, then the page's recheck of access.

    Runs inside the caller's transaction, which the page also renders in.
    Only the admitted ``actor``'s requests are found: another's, or an
    unknown one, raises ``LookupError``. A revocation committed while it
    read raises ``PermissionError``.
    """
    status = request_status(request_id=request_id, actor_id=actor.identity)
    # A refused live end-date change says why (#944).
    status = replace(status, refusal=refusal_text(status))
    if not allows(
        authenticated_admin(caller, store=service.store, read_only=True),
        Capability.CONFIGURE,
    ):
        raise PermissionError("Configuration access was revoked.")
    return status
