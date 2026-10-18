"""Continue on an unchanged Campaign setup step (#1404).

Each Campaign setup step page ends with an action row (Back and one primary
button, campaign-step-actions.html). While the page's form holds no change,
the primary button is **Continue to <next step>**, a form of its own that
posts here: the step is recorded as kept as is (``campaign_wizard
.record_review``) and the Administrator lands on the next step that is not
done yet. It never saves the page's form; while that form holds a change
the page script holds Continue, so the page's own Save stays the way on.
Pages and emails' editor posts here too, with the item it shows (#1404
PR 6b): its Keep as is and go back marks that one item and returns to
Pages and emails.
"""

from django.db import DatabaseError
from django.http import HttpResponseRedirect
from django.views.decorators.http import require_POST

from parishkit.config import ConfigError
from parishkit.stewardship.storage import StaleRecordError
from parishkit.stewardship.web.contracts import filters

from . import campaign_wizard
from .admin_editing import error_response, principal
from .authentication import runtime
from .limiting import LimiterUnavailable


@require_POST
def step_continue(request):
    """Record the posted step (or item) as kept as is, then open the next step.

    Only the step key (from the closed list), an optional item key (one
    Pages and emails item, which ``record_review`` checks the list shows)
    and the CSRF token are accepted. The redirect goes to the step
    ``record_review`` returns, always one of the stepper's own pages: the
    next one not done yet, or for an item its own step.
    """
    try:
        service = runtime()
        actor = principal(request, service)
        values = filters(request.POST, allowed={"step", "item", "csrfmiddlewaretoken"})
        if request.FILES:
            raise ValueError("Unexpected upload.")
        upcoming = campaign_wizard.record_review(
            service.store, actor.identity, values.get("step"), values.get("item", "")
        )
        if upcoming is None or not upcoming.url:
            raise ValueError("No later Campaign setup step.")
        response = HttpResponseRedirect(upcoming.url, status=303)
        response["Cache-Control"] = "no-store"
        return response
    except (
        ConfigError,
        DatabaseError,
        LimiterUnavailable,
        PermissionError,
        ValueError,
        LookupError,
        StaleRecordError,
    ) as error:
        return error_response(error)
