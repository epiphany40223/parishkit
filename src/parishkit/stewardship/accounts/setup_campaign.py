"""The setup attempt's parish data check, as the confirmation needs it.

Before #142 this module also offered the first campaign's Ministry and fund
choices from the staged load; setup collects no campaign any more. Since
#2201 Review and the final confirmation need the quick parish data check
(``setup_checks.checked_source``), not a staged load: the final load at
Finish setup is the only full ParishSoft load.
"""

from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.web.refusals import UserFacingMissing

from .sessions import database_now
from .setup_checks import checked_source
from .setup_drafts import _owned
from .setup_staging import _expiry


def require_source_check(request, service, attempt_id):
    """Refuse unless the original owner's attempt has its current parish data check.

    The system-only review (#142) needs the check but no campaign catalog,
    so its refusal names Check parish data (#2201).
    """
    with work_transaction():
        attempt = _owned(request, service, attempt_id)[1]
        if (
            attempt.state != "collecting"
            or _expiry(attempt, database_now()) is not None
        ):
            raise PermissionError("Setup review is unavailable.")
        if checked_source(attempt) is None:
            raise UserFacingMissing(
                _("Check the parish data before reviewing setup."),
                link=reverse("admin:setup_source"),
                link_label=_("Go to “Check parish data”"),
            )


def current_source_check(attempt_id):
    """The id of the attempt's current parish data check, for final readiness.

    System setup (#142) has no campaign section to carry it, so final
    confirmation reads it here; the readiness guard checks it again in SQL
    (#2201). Call inside the caller's work transaction.
    """
    from .setup_models import SetupAttempt

    checked = checked_source(SetupAttempt.objects.get(pk=attempt_id))
    if checked is None:
        raise ValueError("Check the parish data before final confirmation.")
    return checked.pk
