"""Parish settings reviewed and applied in place (#532), as fixture pages.

The real Parish settings template is served at ``SETTINGS`` with the context
each step of the view renders:

- the editor (``SETTINGS``), at version ``"a" * 64``;
- the review (``REVIEW``) and a refused review (``REFUSED``), which the
  browser tests serve as the answer to the form's POST (a page route reads
  the POST body: Review or Apply);
- Apply's answer: ``POSTS`` redirects the form's POST to
  ``SETTINGS?request=…`` (Post/Redirect/Get), the page with the change's
  pending status, whose live region polls ``STATUS`` (Change status read
  with in_place);
- ``STATUS`` answers that the change was applied, with the hidden in-place
  follow-up, which leads to ``SETTLED``: the page again, now at version
  ``"b" * 64``.

Every page draws the Make changes / Review / Apply indicator at its step, so
a test can see the indicator follow along. Campaign settings is served the
same way at ``CAMPAIGN`` (the editor) and ``CAMPAIGN_REVIEW`` (its review),
so a test can check its module scripts survive an in-place review. A live
campaign's Ministry leader roles form (#922) is served at ``LEADERS``, with
its review at ``LEADERS_REVIEW``; Apply's answer names the change at
``LEADERS_PENDING``.
"""

from types import SimpleNamespace
from uuid import UUID

from django.template.loader import render_to_string

from parishkit.stewardship.accounts import setup_help
from parishkit.stewardship.accounts.admin_editing import review_region
from parishkit.stewardship.accounts.campaign_forms import (
    CampaignSettingsForm,
    LeaderRolesForm,
)
from parishkit.stewardship.accounts.parish_views import TIMEZONE_NOTE, ParishForm

SETTINGS = "/settings-in-place"
REVIEW = "/settings-in-place-review"
REFUSED = "/settings-in-place-refused"
STATUS = "/settings-in-place-status"
REQUEST = UUID("53200000-0000-4000-8000-000000000532")
PENDING = f"{SETTINGS}?request={REQUEST}"
SETTLED = f"{SETTINGS}?request={REQUEST}&settled=1"
CAMPAIGN = "/campaign-in-place"
CAMPAIGN_REVIEW = "/campaign-in-place-review"
LEADERS = "/campaign-leaders-in-place"
LEADERS_REVIEW = "/campaign-leaders-in-place-review"
LEADERS_PENDING = f"{LEADERS}?request={REQUEST}"
# The role labels the current rosters use, and the roles in effect.
ROLE_CHOICES = [(name, name) for name in ("Chairperson", "Member", "Staff")]
DEFAULT_ROLES = ["Chairperson", "Staff"]
CAMPAIGN_VALUES = {
    "name": "Sample campaign",
    "timezone": "America/New_York",
    "start_date": "2026-10-01",
    "end_date": "2026-10-31",
    "census": True,
    "base_digest": "a" * 64,
}
# The confirmation's answer: back to this page, naming the change.
POSTS = {
    SETTINGS: (303, PENDING, ""),
    LEADERS: (303, LEADERS_PENDING, ""),
    # A Review answered with a redirect to another page, which does not draw
    # the review region (Campaign settings' dates-only change, #532).
    "/settings-in-place-elsewhere": (303, "/in-place-other", ""),
}
STEPS = ("Make changes", "Review", "Apply")
VALUES = {
    "name": "Sample Parish",
    "website": "https://example.org",
    "timezone": "America/New_York",
    "phone": "+12125551234",
}


def _steps(current):
    """The flow indicator with ``current`` (0-based) as the current step."""
    return [
        {
            "label": label,
            "state": "done"
            if index < current
            else "current"
            if index == current
            else "upcoming",
        }
        for index, label in enumerate(STEPS)
    ]


def components(context, admin):
    """Every page above, keyed by the exact path and query it is fetched at."""

    def page(form, step, **region):
        """Parish settings at ``step`` with ``region``'s review context."""
        values = review_region("parish_settings", form, **region)
        if region.get("receipt"):
            # The fixture server serves Change status at STATUS.
            values["status_url"] = STATUS
        return (
            "text/html",
            render_to_string(
                "stewardship/parish-settings.html",
                context
                | {
                    "admin_chrome": admin | {"flow_steps": _steps(step)},
                    "configuration": {
                        "mode": "testing",
                        "testing_recipient": "testing@example.org",
                    },
                    "form": form,
                }
                | values,
            ),
        )

    def editor(digest):
        """The unbound editor at version ``digest``."""
        return ParishForm(initial=VALUES | {"base_digest": digest})

    renamed = VALUES | {"name": "Renamed Parish", "base_digest": "a" * 64}
    reviewed = ParishForm(data=renamed)
    reviewed.is_valid()
    refused = ParishForm(data=renamed | {"phone": "123"})
    refused.is_valid()

    # The fields of a RequestStatus the pages read.
    def campaign(data, step, **region):
        """Campaign settings at ``step``, its form bound to ``data`` if given."""
        form = setup_help.apply(
            CampaignSettingsForm(
                data,
                initial=CAMPAIGN_VALUES | {"ministry_leader_roles": DEFAULT_ROLES},
                ministries=[("4", "Community outreach")],
                funds=[("9", "Offertory")],
                leader_choices=ROLE_CHOICES,
                leader_roles=DEFAULT_ROLES,
            ),
            setup_help.ADMIN_CAMPAIGN,
            replace=True,
        )
        return (
            "text/html",
            render_to_string(
                "stewardship/campaign-settings.html",
                context
                | {
                    "admin_chrome": admin | {"flow_steps": _steps(step)},
                    "campaign": {
                        "pk": REQUEST,
                        "state": "draft",
                        "active_configuration": {"name": "Sample campaign"},
                    },
                    "editable": True,
                    "form": form,
                }
                | review_region("campaign_settings", None, **region),
            ),
        )

    def leaders(step, **region):
        """A live campaign's page at ``step``: settings read-only, leader roles
        in their own form with the review region under it (#922)."""
        form = setup_help.apply(
            CampaignSettingsForm(
                None,
                initial=CAMPAIGN_VALUES
                | {"ministry": True, "ministry_leader_roles": DEFAULT_ROLES},
                ministries=[("4", "Community outreach")],
                funds=[("9", "Offertory")],
                leader_choices=ROLE_CHOICES,
                leader_roles=DEFAULT_ROLES,
            ),
            setup_help.ADMIN_CAMPAIGN,
            replace=True,
        )
        # As the view does, the read-only settings leave the roles to their
        # own form.
        form.fields.pop("ministry_leader_roles")
        leader_form = setup_help.apply(
            LeaderRolesForm(
                initial={
                    "ministry_leader_roles": DEFAULT_ROLES,
                    "base_digest": "a" * 64,
                },
                roles=ROLE_CHOICES,
            ),
            {},
        )
        return (
            "text/html",
            render_to_string(
                "stewardship/campaign-settings.html",
                context
                | {
                    "admin_chrome": admin | {"flow_steps": _steps(step)},
                    "campaign": {
                        "pk": REQUEST,
                        "state": "active",
                        "active_configuration": {"name": "Sample campaign"},
                    },
                    "editable": False,
                    "form": form,
                    "leader_form": leader_form,
                }
                | review_region("campaign_settings", None, **region),
            ),
        )

    pending = SimpleNamespace(state="staged", request_id=REQUEST, failure_code="")
    applied = SimpleNamespace(state="applied", request_id=REQUEST, failure_code="")
    return {
        SETTINGS: page(editor("a" * 64), 0),
        REVIEW: page(
            reviewed,
            1,
            review={
                "changes": [
                    {
                        "label": "Parish name",
                        "before": "Sample Parish",
                        "after": "Renamed Parish",
                    }
                ],
                "notes": [TIMEZONE_NOTE],
                "preview": "synthetic-signed-intent",
            },
        ),
        REFUSED: page(refused, 0),
        PENDING: page(editor("a" * 64), 2, receipt=pending),
        STATUS: (
            "text/html",
            render_to_string(
                "stewardship/configuration-request.html",
                context
                | {
                    "admin_chrome": admin,
                    "receipt": applied,
                    "follow_url": SETTLED,
                },
            ),
        ),
        SETTLED: page(editor("b" * 64), 2, receipt=applied),
        CAMPAIGN: campaign(None, 0),
        LEADERS: leaders(0),
        LEADERS_REVIEW: leaders(
            1,
            review={
                "changes": [
                    {
                        "label": "Ministry leader roles",
                        "before": "Chairperson, Staff",
                        "after": "Chairperson",
                    }
                ],
                "notes": [],
                "preview": "synthetic-signed-intent",
            },
        ),
        LEADERS_PENDING: leaders(2, receipt=pending),
        CAMPAIGN_REVIEW: campaign(
            None,
            1,
            review={
                "changes": [
                    {
                        "label": "Campaign name",
                        "before": "Sample campaign",
                        "after": "Renamed campaign",
                    }
                ],
                "notes": [],
                "preview": "synthetic-signed-intent",
            },
        ),
    }
