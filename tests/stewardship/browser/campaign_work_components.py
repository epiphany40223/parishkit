"""Pages that wait on the campaign work hold (#1619), as fixture pages.

Every page is drawn from the real templates, its form built and locked as
``campaign_views`` builds and locks it, and its campaign work status region
drawn from ``campaign_work_status.settings_context``:

- ``HELD``: the current Testing draft while background work holds it: every
  setting disabled with the held reason, and the pending status region.
  ``HELD_FREED`` is the same address drawn once the hold ended: every
  setting open, no status region.
- ``HELD_LIVE``: a live campaign while background work holds its end date
  and Ministries (each with its own reason); its Ministry leader roles stay
  open. ``HELD_LIVE_FREED`` is the same address once the hold ended.
- ``HOME_HELD``: Home with no campaign yet while campaign work holds Create
  the campaign; its first-campaign panel is pending.
- ``WORK_PENDING`` and ``WORK_DONE``: the passive status's answers for
  Campaign settings (``view=settings``); ``HOME_PENDING`` and ``HOME_READY``
  its answers for Home (``view=home``). A test serves them to the pages'
  status reads with ``page.route``.
"""

from django.template.loader import render_to_string

from parishkit.stewardship.accounts.campaign_views import (
    END_HELD,
    LOCKED_HELD,
    LOCKED_LIVE,
    MINISTRIES_HELD,
    end_check_words,
)
from parishkit.stewardship.accounts.campaign_work_status import settings_context

from .campaign_settings_components import LIVE_VALUES, VALUES, _form

HELD = "/campaign-settings-held"
HELD_FREED = "/campaign-settings-held-freed"
HELD_LIVE = "/campaign-settings-held-live"
HELD_LIVE_FREED = "/campaign-settings-held-live-freed"
HOME_HELD = "/home-first-campaign-held"
WORK_PENDING = "/campaign-work-pending"
WORK_DONE = "/campaign-work-done"
HOME_PENDING = "/home-first-campaign-pending"
HOME_READY = "/home-first-campaign-ready"
# The live campaign's settings open while work holds the others.
LIVE_HELD_OPEN = frozenset({"ministry_leader_roles"})
LIVE_OPEN = frozenset({"end_date", "ministry_leader_roles", "ministry_duids"})


def components(context, admin):
    """Every page above, keyed by its path (see the module docstring)."""
    values = context | {"admin_chrome": admin}

    def settings(form, *, path, state, editable, opened, live=False, held=False):
        """Campaign settings around ``form`` at ``path``; ``live`` opens the
        end date's live check, ``held`` draws the pending status region."""
        return (
            "text/html",
            render_to_string(
                "stewardship/campaign-settings.html",
                values
                | {
                    "campaign": {
                        "state": state,
                        "active_configuration": {"name": "Sample campaign"},
                    },
                    "form": form,
                    "editable": editable,
                    "open": opened,
                    "end_saved": "2054-10-31" if live else None,
                    "end_check": end_check_words() if live else None,
                    "live_ministries": live,
                    "page_url": path,
                    "refresh_url": path,
                    "errors": [],
                }
                | (settings_context(True) if held else {}),
            ),
        )

    def fragment(template, extra):
        """One status answer, the template alone."""
        return ("text/html", render_to_string(template, extra))

    home = render_to_string(
        "stewardship/home.html",
        values
        | {
            "dashboard": {
                "campaign": None,
                "first_campaign": "held",
                "refreshed_at": None,
                "can_refresh": True,
            },
        },
    )
    return {
        HELD: settings(
            _form(VALUES, open_fields=frozenset(), reason=LOCKED_HELD),
            path=HELD,
            state="draft",
            editable=False,
            opened=False,
            held=True,
        ),
        HELD_FREED: settings(
            _form(VALUES), path=HELD, state="draft", editable=True, opened=True
        ),
        HELD_LIVE: settings(
            _form(
                LIVE_VALUES,
                open_fields=LIVE_HELD_OPEN,
                reason=LOCKED_LIVE,
                reasons={"end_date": END_HELD, "ministry_duids": MINISTRIES_HELD},
            ),
            path=HELD_LIVE,
            state="active",
            editable=False,
            opened=True,
            held=True,
        ),
        HELD_LIVE_FREED: settings(
            _form(LIVE_VALUES, open_fields=LIVE_OPEN, reason=LOCKED_LIVE),
            path=HELD_LIVE,
            state="active",
            editable=False,
            opened=True,
            live=True,
        ),
        HOME_HELD: ("text/html", home),
        WORK_PENDING: fragment(
            "stewardship/campaign-work-status.html", settings_context(True)
        ),
        WORK_DONE: fragment(
            "stewardship/campaign-work-status.html", settings_context(False)
        ),
        HOME_PENDING: fragment(
            "stewardship/home-first-campaign.html", {"first_campaign": "held"}
        ),
        HOME_READY: fragment(
            "stewardship/home-first-campaign.html", {"first_campaign": "ready"}
        ),
    }
