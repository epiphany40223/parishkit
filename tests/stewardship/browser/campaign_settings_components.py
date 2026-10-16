"""Campaign settings as one editor (#1119), rendered from the real templates.

The browser checks need no database. Every page is the real
``campaign-settings.html`` with a form built and locked as the view builds
and locks it (``campaign_views.lock_settings``):

- ``DRAFT``: the current Testing draft, every setting open. Its Review
  posts to ``DRAFT`` (a test answers it with ``DRAFT_REVIEW``, the real
  review fragment of a renamed campaign, or ``DRAFT_REFUSED``, the page
  drawn again with the name's error); Apply posts to ``DRAFT``, which the
  fixture server redirects to ``APPLIED``, the change's status page,
  Applied. The form's region is then redrawn from ``DRAFT_SETTLED``, the
  renamed campaign at version ``"b" * 64``.
- ``LIVE``: a live campaign that asks about Ministries. Its end date (#912),
  Ministry leader roles (#922) and Ministry list (#342) are open; every
  other setting is disabled where it stands, with the reason in its tip.
  The end date is checked as it is
  typed (#944; ``settings_components.END_CHECKS`` answer). Its Review is
  answered with ``LIVE_REVIEW`` (an end date and a leader-role change in one
  review), ``LIVE_MINISTRIES_REVIEW`` (a Ministry removed, with its impact),
  ``LIVE_MIXED`` (a Ministry change made with another, refused as JSON
  for the dialog) or ``LIVE_REFUSED`` (the page with the end date's own error);
  Apply posts to ``LIVE``, which the fixture server redirects to
  ``APPLIED``; ``LIVE_REFUSING`` is the same page whose Apply is answered
  with ``NOT_APPLIED``, the status of a change the installer refused.
- ``LOCKED``: an archived campaign: nothing can change, so Save
  stays unavailable with its reason.

``POSTS`` lists the fixture server's answers to the pages' Apply POSTs.
"""

import json
from types import SimpleNamespace
from uuid import UUID

from django.template.loader import render_to_string

from parishkit.stewardship.accounts import setup_help
from parishkit.stewardship.accounts.admin_editing import summary_errors
from parishkit.stewardship.accounts.campaign_end_date import (
    CAMPAIGN_END_TEXT,
    FAMILY_ACCESS,
)
from parishkit.stewardship.accounts.campaign_forms import CampaignSettingsForm
from parishkit.stewardship.accounts.campaign_views import (
    LOCKED_CLOSED,
    LOCKED_LIVE,
    MINISTRIES_ALONE,
    MINISTRIES_ALONE_FIX,
    end_check_words,
    lock_settings,
)

DRAFT = "/campaign-settings-editor"
DRAFT_REVIEW = "/campaign-settings-editor-review"
DRAFT_REFUSED = "/campaign-settings-editor-refused"
DRAFT_SETTLED = "/campaign-settings-editor-settled"
LIVE = "/campaign-settings-live"
LIVE_REVIEW = "/campaign-settings-live-review"
LIVE_MINISTRIES_REVIEW = "/campaign-settings-live-ministries-review"
LIVE_MIXED = "/campaign-settings-live-mixed"
LIVE_REFUSED = "/campaign-settings-live-refused"
LIVE_REFUSING = "/campaign-settings-live-refusing"
LOCKED = "/campaign-settings-locked"
APPLIED = "/campaign-settings-change-applied"
NOT_APPLIED = "/campaign-settings-change-not-applied"
ELSEWHERE = "/campaign-settings-elsewhere"
REQUEST = UUID("11190000-0000-4000-8000-000000001119")
# Why the installer refused the live change, as Change status says it.
END_REFUSAL = (
    "The campaign closed, or its previous end date passed, before the end "
    "date could change, so it was not applied."
)
ROLE_CHOICES = [(name, name) for name in ("Chairperson", "Member", "Staff")]
DEFAULT_ROLES = ["Chairperson", "Staff"]
MINISTRY_CHOICES = [("4", "Community outreach"), ("9", "Food pantry")]
VALUES = {
    "name": "Sample campaign",
    "timezone": "America/New_York",
    "start_date": "2054-10-01",
    "end_date": "2054-10-31",
    "census": True,
    "base_digest": "a" * 64,
}
LIVE_VALUES = VALUES | {"ministry": True, "ministry_duids": ["4", "9"]}
# The live campaign's open settings.
LIVE_OPEN = frozenset({"end_date", "ministry_leader_roles", "ministry_duids"})
POSTS = {}


def _form(values, *, data=None, open_fields=None, reason=None, reasons=None):
    """The settings form as the view builds it, locked as the view locks it."""
    form = setup_help.apply(
        CampaignSettingsForm(
            data,
            initial=values | {"ministry_leader_roles": DEFAULT_ROLES},
            ministries=MINISTRY_CHOICES,
            funds=[("12", "Offertory")],
            leader_choices=ROLE_CHOICES,
            leader_roles=DEFAULT_ROLES,
            whole=open_fields is None,
        ),
        setup_help.ADMIN_CAMPAIGN,
        replace=True,
    )
    if open_fields is not None:
        lock_settings(
            form,
            open_fields=open_fields,
            reason=reason,
            reasons=reasons,
            live_end="end_date" in open_fields,
        )
    return form


def components(context, admin):
    """Every page above, keyed by its path (see the module docstring)."""
    values = context | {"admin_chrome": admin}

    def page(form, *, state, path, refresh=None, live=False, opened=True):
        """Campaign settings around ``form``, posting to ``path``."""
        return (
            "text/html",
            render_to_string(
                "stewardship/campaign-settings.html",
                values
                | {
                    "campaign": {
                        "pk": REQUEST,
                        "state": state,
                        "active_configuration": {"name": "Sample campaign"},
                    },
                    "form": form,
                    "editable": not live and opened,
                    "open": opened,
                    "end_saved": "2054-10-31" if live else None,
                    "end_check": end_check_words() if live else None,
                    "live_ministries": live,
                    "page_url": path,
                    "refresh_url": refresh or path,
                    "errors": summary_errors(form) if form.is_bound else [],
                },
            ),
        )

    def live_form(data=None):
        """The live campaign's form: end date and leader roles open."""
        return _form(
            LIVE_VALUES,
            data=data,
            open_fields=LIVE_OPEN,
            reason=LOCKED_LIVE,
        )

    def review(changes, notes=(), ministries=None, compact=False):
        """The review dialog's fragment for ``changes``; ``compact`` as
        ``campaign_views._compact`` decides it (#1356)."""
        return (
            "text/html",
            render_to_string(
                "stewardship/campaign-settings-review.html",
                {
                    "review": {
                        "changes": changes,
                        "ministries": ministries,
                        "notes": list(notes),
                        "compact": compact,
                        "preview": "synthetic-signed-intent",
                    }
                },
            ),
        )

    def status(receipt):
        """The change's status page, which the dialog follows."""
        return (
            "text/html",
            render_to_string(
                "stewardship/configuration-request.html",
                values | {"receipt": receipt, "follow_url": None},
            ),
        )

    refused = _form(VALUES, data=VALUES | {"name": ""})
    refused.is_valid()
    late = live_form(
        {
            "end_date": "2054-11-15",
            "ministry_leader_roles": DEFAULT_ROLES,
            "base_digest": "a" * 64,
        }
    )
    late.is_valid()
    late.add_error(
        "end_date",
        "Some scheduled emails would no longer fit the campaign, such as "
        "invitations or Reminders after the new end date. Change the end date "
        "together with those mailings on Dates and mail schedules: reschedule "
        "or delete each one there.",
    )
    POSTS.update(
        {
            DRAFT: (303, APPLIED, ""),
            LIVE: (303, APPLIED, ""),
            LIVE_REFUSING: (303, NOT_APPLIED, ""),
            # A draft Review the server answers with another page (a change
            # of its dates alone leads to Dates and mail schedules).
            ELSEWHERE: (303, "/in-place-other", ""),
        }
    )
    return {
        DRAFT: page(_form(VALUES), state="draft", path=DRAFT, refresh=DRAFT_SETTLED),
        DRAFT_SETTLED: page(
            _form(VALUES | {"name": "Renamed campaign", "base_digest": "b" * 64}),
            state="draft",
            path=DRAFT,
            refresh=DRAFT_SETTLED,
        ),
        DRAFT_REVIEW: review(
            [
                {
                    "label": "Campaign name",
                    "before": "Sample campaign",
                    "after": "Renamed campaign",
                }
            ],
            compact=True,
        ),
        DRAFT_REFUSED: page(refused, state="draft", path=DRAFT),
        LIVE: page(live_form(), state="active", path=LIVE, live=True),
        LIVE_REFUSING: page(live_form(), state="active", path=LIVE_REFUSING, live=True),
        LIVE_REVIEW: review(
            [
                {
                    "label": "Campaign end date",
                    "before": "October 31, 2054",
                    "after": "November 15, 2054",
                },
                {
                    "label": "Ministry leader roles",
                    "before": "Chairperson, Staff",
                    "after": "Chairperson",
                },
            ],
            [FAMILY_ACCESS, CAMPAIGN_END_TEXT],
        ),
        LIVE_REFUSED: page(late, state="active", path=LIVE, live=True),
        LIVE_MINISTRIES_REVIEW: review(
            [],
            ministries={
                "added": [],
                "removed": [
                    {"name": "Food pantry", "duid": 9, "answers": 1, "open": 1}
                ],
                "open_forms": 3,
            },
        ),
        LIVE_MIXED: (
            "application/json",
            json.dumps(
                {
                    "errors": [{"code": "invalid"}],
                    "refusal": {
                        "message": str(MINISTRIES_ALONE),
                        "fix": str(MINISTRIES_ALONE_FIX),
                    },
                }
            ),
        ),
        LOCKED: page(
            _form(
                LIVE_VALUES,
                open_fields=frozenset(),
                reason=LOCKED_CLOSED,
            ),
            state="archived",
            path=LOCKED,
            opened=False,
        ),
        APPLIED: status({"request_id": REQUEST, "state": "applied"}),
        NOT_APPLIED: status(
            SimpleNamespace(
                state="failed",
                request_id=REQUEST,
                failure_code="invalid_candidate",
                refusal=END_REFUSAL,
            )
        ),
    }
