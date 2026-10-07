"""The current campaign's mail schedules, moved out of the page's view.

Dates and mail schedules (``schedule_views``) reads the campaign, whether its dates
may still change and its schedule records through these functions, and so
does the Admin automation command line (ADM-11), so both see the same
applied configuration. Nothing here takes a request or checks authority; the caller
runs it inside its own snapshot or work lock.
"""

from .campaign_views import _state, _target
from .content_views import _campaign


def schedule_state(service, campaign_id):
    """Pin the applied configuration and find the campaign and its editability.

    Returns ``(state, campaign, editable)``: ``state`` is
    ``campaign_views._state`` (its first item the system configuration),
    and ``editable`` whether the campaign's dates may still change. Raises
    ``LookupError`` for an unknown campaign and ``UserFacingStale`` when the
    campaign is not the current one or background work holds it, as the
    page does.
    """
    state = _state(service)
    campaign = _campaign(state, campaign_id)
    _, editable = _target(state[0], state[1], state[3], campaign_id)
    return state, campaign, editable


def campaign_schedules(configuration, campaign_id):
    """The campaign's schedule records from the exact applied YAML document."""
    return [
        row
        for row in configuration.active_configuration.canonical_document[
            "sections"
        ].get("schedules", [])
        if row["values"]["campaign_id"] == str(campaign_id)
    ]
