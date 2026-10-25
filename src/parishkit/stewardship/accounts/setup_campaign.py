"""The setup attempt's staged ParishSoft load, as the confirmation needs it.

Before #142 this module also offered the first campaign's Ministry and fund
choices from the staged load; setup collects no campaign any more, so only
the staged-load checks remain.
"""

from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.web.refusals import UserFacingMissing

from .sessions import database_now
from .setup_drafts import _owned, staged_load
from .setup_staging import _expiry


def _source_result(attempt):
    """The attempt's exact, still-valid staged load result, or None."""
    return staged_load(attempt)


def require_staged_load(request, service, attempt_id):
    """Refuse unless the original owner's attempt has its exact staged load.

    The system-only review (#142) needs the load but no campaign catalog, so
    its refusal names the load, not the first campaign.
    """
    with work_transaction():
        attempt = _owned(request, service, attempt_id)[1]
        if (
            attempt.state != "collecting"
            or _expiry(attempt, database_now()) is not None
        ):
            raise PermissionError("Setup review is unavailable.")
        if _source_result(attempt) is None:
            raise UserFacingMissing(
                _("Load the parish data before reviewing setup."),
                link=reverse("admin:setup_source"),
                link_label=_("Go to “Load parish data”"),
            )


def staged_source_result(attempt_id):
    """The id of the attempt's exact staged load result, for final readiness.

    System setup (#142) has no campaign section to carry it, so final
    confirmation reads it here; the readiness guard checks it again in SQL.
    Call inside the caller's work transaction.
    """
    from .setup_models import SetupAttempt

    result = _source_result(SetupAttempt.objects.get(pk=attempt_id))
    if result is None:
        raise ValueError("Load the parish data before final confirmation.")
    return result.pk
