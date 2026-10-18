"""The passive campaign work status that held pages follow (#1619).

Background campaign work (a preparing or running work gate) or an unfinished
Testing cleanup holds every campaign change: Campaign settings draws the
settings it holds disabled, and Home draws Create the campaign unavailable.
Neither tells the reader to reload (UX conventions, "Pages that update
themselves"). Each page instead draws a status region that
``live-status-v1.js`` polls at this address while the hold lasts:

- ``view=settings`` answers ``campaign-work-status.html``, pending while the
  hold lasts. Once it has ended, the region's hidden ``a[data-reread]``
  follow-up has ``ui-v1.js`` re-read Campaign settings once, quietly, in
  place and passively, which draws the freed settings from the server.
- ``view=home`` answers Home's first-campaign panel itself
  (``home-first-campaign.html``), since Home's own view is audited and is
  not re-read: once the hold ends the panel offers Create the campaign.

The read never extends the sign-in, records no audited view, and says only
whether campaign work holds changes (and, for Home, what the panel offers),
never what the work is.
"""

from django.db import DatabaseError
from django.shortcuts import render
from django.views.decorators.http import require_http_methods

from parishkit.config import ConfigError
from parishkit.stewardship.campaigns import single_campaign
from parishkit.stewardship.campaigns.models import Campaign
from parishkit.stewardship.campaigns.work_locks import read_transaction
from parishkit.stewardship.web.contracts import filters

from .admin_editing import editable_configuration, error_response, principal
from .authentication import runtime
from .limiting import LimiterUnavailable
from .policy import Capability, allows
from .sessions import authenticated_admin

# The pages that follow the hold, by the ``view`` each one polls with.
VIEWS = ("settings", "home")
# The in-place region Campaign settings' follow-up re-read redraws.
SETTINGS_REGION = "settings-editor"


def settings_context(held):
    """``campaign-work-status.html``'s context: Campaign settings' region.

    ``held`` says campaign work still holds changes; once it does not, the
    region carries the follow-up that re-reads the page.
    """
    return {"work": {"held": held, "region": SETTINGS_REGION}}


@require_http_methods(["GET", "HEAD"])
def campaign_work_status(request):
    """Whether campaign work still holds changes, for a page that waits on it.

    Polled by Campaign settings and Home (see the module docstring) while
    their held controls wait. It admits passively with those pages'
    Configure capability, reads one snapshot without the writers' work lock
    (so a poll never waits behind the work it follows) and rechecks access
    read-only after rendering, as the pages do. It is never cached.
    """
    try:
        service = runtime()
        principal(request, service, passive=True)
        view = filters(request.GET, allowed={"view"}).get("view")
        if view not in VIEWS:
            raise ValueError("Unknown campaign work status view.")
        with read_transaction():
            if view == "home":
                configuration = editable_configuration(service)
                offer = single_campaign.first_campaign_offer(configuration)
                # No offer is "Campaign created" only when a campaign row
                # exists; otherwise (say, switched to Production mode while
                # held) the panel says what Home says with no campaign.
                created = offer is None and Campaign.objects.exists()
            else:
                # As the page: no edit is possible before setup or during a
                # restore review, so the status is unavailable then too.
                editable_configuration(service)
                held = single_campaign.first_campaign_held()
        if view == "home":
            response = render(
                request,
                "stewardship/home-first-campaign.html",
                {"first_campaign": offer, "created": created, "swapped": True},
            )
        else:
            response = render(
                request,
                "stewardship/campaign-work-status.html",
                settings_context(held),
            )
        if not allows(
            authenticated_admin(request, store=service.store, read_only=True),
            Capability.CONFIGURE,
        ):
            raise PermissionError("Configuration access was revoked.")
        response["Cache-Control"] = "no-store"
        return response
    except (
        ConfigError,
        DatabaseError,
        LimiterUnavailable,
        PermissionError,
        ValueError,
    ) as error:
        return error_response(error)
