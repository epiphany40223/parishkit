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


def live_end_at(state, campaign):
    """The domain clock's instant if only the end date may change now, else None.

    For a live campaign (#912; ``campaigns.live_end_date``) the pages and
    commands offer the end date alone, which must then end after this
    instant. ``state`` is ``schedule_state``'s, whose fourth item says
    whether background work holds campaign changes. The installer judges
    with the same database clock.
    """
    from parishkit.stewardship.campaigns.live_end_date import live_end_editable
    from parishkit.stewardship.campaigns.runtime import _now

    now = _now()
    return (
        now if live_end_editable(state[0], campaign, held=state[3], now=now) else None
    )


def live_end_held(state, campaign):
    """Whether only background work keeps a live campaign's end date from moving.

    Campaign settings then says the end date can change once that work
    finishes (#944), rather than leaving its panel out without a word.
    """
    from parishkit.stewardship.campaigns.live_end_date import live_end_editable
    from parishkit.stewardship.campaigns.runtime import _now

    return state[3] and live_end_editable(state[0], campaign, held=False, now=_now())


def campaign_schedules(configuration, campaign_id):
    """The campaign's schedule records from the exact applied YAML document."""
    return [
        row
        for row in configuration.active_configuration.canonical_document[
            "sections"
        ].get("schedules", [])
        if row["values"]["campaign_id"] == str(campaign_id)
    ]
