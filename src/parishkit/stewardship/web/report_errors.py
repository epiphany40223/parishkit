"""The Admin response for a report that cannot be read right now.

Report views catch a closed set of transient failures (database, storage,
configuration or limiter outages, unavailable facts or snapshots). They used
to answer with the Family sign-in denial page ("Sign-in temporarily
unavailable"), which misled Admins and logged nothing. This module gives them
one shared response instead: a debug log of the swallowed exception, then a
typed 503 that the browser-error middleware renders as an Admin page and that
scripts receive as JSON, with Retry-After kept.
"""

from django.utils.translation import gettext_lazy as _

from parishkit.stewardship.observability import debug_swallowed

from .contracts import ErrorCode, FieldError, validation_response
from .refusals import Refusal

REPORT_UNAVAILABLE = Refusal(
    _("This report is temporarily unavailable."),
    fix=_(
        "Try again in a moment. If it keeps happening, an Administrator can "
        "check System logs for the cause."
    ),
)


def report_unavailable():
    """Log the failure being handled and answer with the Admin 503 page.

    Call from inside the view's ``except`` block so the debug log carries the
    real exception; the response itself shows only static text.
    """
    debug_swallowed("report request unavailable")
    return validation_response(
        [FieldError(ErrorCode.UNAVAILABLE)], status=503, refusal=REPORT_UNAVAILABLE
    )
