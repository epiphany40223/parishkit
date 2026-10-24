"""Create the campaign's confirmation dialog (#142, #1356), as fixture pages.

The browser checks need no database. Every page is the real
``campaign-create.html`` (or its confirmation fragment):

- ``PAGE``: the empty creation form, posting to ``PAGE``, on step 1 of the
  campaign wizard (#1404 PR 3): the stepper with every later step
  unavailable, and Campaign settings' form, leader roles included. A test answers
  its Review with ``REVIEW`` (the real confirmation fragment) or
  ``REFUSED`` (the page drawn again with the name's error, status 400).
- Apply posts to ``PAGE``, which the fixture server redirects to
  ``APPLIED``: the page naming its change, already applied, so the dialog
  stops following at once and redraws the page's region from that address:
  the campaign created, its status and the offer to load its ParishSoft data.

``ONWARD`` is the confirmation the view now draws (#1404): it names the
next Campaign setup step, the stepper's Share options fixture page
(``stepper_components.PATH``), which the dialog opens once Apply is applied.

``POSTS`` lists the fixture server's answer to the page's Apply POST.
"""

from types import SimpleNamespace
from uuid import UUID

from django.template.loader import render_to_string

from parishkit.stewardship.accounts import campaign_wizard, setup_help
from parishkit.stewardship.accounts.admin_editing import (
    receipt_status,
    summary_errors,
)
from parishkit.stewardship.accounts.campaign_forms import CampaignSettingsForm

from .stepper_components import PATH as STEPPER

PAGE = "/campaign-create-confirm"
REVIEW = "/campaign-create-confirm-review"
REFUSED = "/campaign-create-confirm-refused"
ONWARD = "/campaign-create-confirm-onward"
REQUEST = UUID("13560000-0000-4000-8000-000000001356")
APPLIED = f"{PAGE}?request={REQUEST}"
# The values a test types, which the confirmation must never repeat.
VALUES = {
    "name": "Annual stewardship",
    "start_date": "2054-10-01",
    "end_date": "2054-10-31",
}
POSTS = {PAGE: (303, APPLIED, "")}
ROLES = ["Chairperson", "Staff"]


def _form(data=None):
    """The creation form as the view builds it, bound to ``data`` if given."""
    form = setup_help.apply(
        CampaignSettingsForm(
            data,
            initial={
                "timezone": "America/New_York",
                "census": True,
                "additional_information": True,
                "ministry_leader_roles": ROLES,
                "base_digest": "a" * 64,
            },
            previous={},
            ministries=[("4", "Community outreach"), ("5", "Choir")],
            funds=[("9", "Offertory")],
            leader_choices=[(role, role) for role in ROLES],
            leader_roles=ROLES,
        ),
        setup_help.ADMIN_CAMPAIGN,
        replace=True,
    )
    if data is not None:
        form.is_valid()
    return form


def components(context, admin):
    """Every page above, keyed by the exact path and query it is fetched at."""

    def page(form, receipt=None, *, created=False):
        """Create the campaign around ``form``, or the status of ``receipt``."""
        return (
            "text/html",
            render_to_string(
                "stewardship/campaign-create.html",
                context
                | {
                    # Before the campaign exists the page is step 1; the
                    # status once it is created draws no stepper.
                    "admin_chrome": admin
                    if created
                    else admin | {"campaign_wizard": campaign_wizard.creation()},
                    "configuration": {
                        "mode": "testing",
                        "testing_recipient": "testing@example.org",
                    },
                    "form": form,
                    "created": created,
                    "page_url": PAGE,
                    "errors": summary_errors(form) if form.is_bound else [],
                    "refresh_key": "13560000-0000-4000-8000-00000000beef"
                    if receipt
                    else None,
                }
                | receipt_status(receipt),
            ),
        )

    applied = SimpleNamespace(state="applied", request_id=REQUEST, failure_code="")
    sent = {
        "timezone": "America/New_York",
        "census": "on",
        "additional_information": "on",
        "base_digest": "a" * 64,
        **VALUES,
        "name": "",
    }
    return {
        PAGE: page(_form()),
        REVIEW: (
            "text/html",
            render_to_string(
                "stewardship/campaign-create-review.html",
                {"review": {"content": 7, "preview": "synthetic-signed-intent"}},
            ),
        ),
        ONWARD: (
            "text/html",
            render_to_string(
                "stewardship/campaign-create-review.html",
                {
                    "review": {
                        "content": 7,
                        "preview": "synthetic-signed-intent",
                        "next_url": STEPPER,
                    }
                },
            ),
        ),
        REFUSED: page(_form(sent)),
        APPLIED: page(_form(), applied, created=True),
    }


# Families in this campaign (#1393): the page with the section drawn over a
# synthetic snapshot's groups and facts (families_choice.Source). The count
# preview's post is answered by the test (families_answer), from the same
# facts, so the page shows exactly what the server would count.
FAMILIES = "/campaign-create-families"
FAMILIES_PREVIEW = "/admin/campaign/first/families/"


def families_source():
    """The synthetic snapshot: three Active-group Families, one Staff, one
    Inactive and one with no group."""
    from datetime import UTC, datetime

    from parishkit.stewardship.accounts import families_choice as choice

    groups = (
        choice.Group(7, "Active", 3),
        choice.Group(8, "Staff", 1),
        choice.Group(9, "Inactive", 1),
        choice.Group(0, "(No Family Group)", 1),
    )
    active, emailed = frozenset({"Active"}), frozenset({"Active"})
    facts = (
        (True, 7, "Active", active, emailed),
        (True, 7, "Active", active, emailed),
        (True, 7, "Active", active, frozenset()),
        (True, 8, "Staff", active, emailed),
        (True, 9, "Inactive", frozenset({"Unknown"}), frozenset({"Unknown"})),
        (True, None, None, active, emailed),
    )
    return choice.Source(None, datetime(2054, 9, 1, 6, 5, tzinfo=UTC), groups, facts)


def families_answer(post_data):
    """The count preview's JSON answer for a posted form (as the view)."""
    from urllib.parse import parse_qs

    from parishkit.stewardship.accounts import families_choice as choice

    data = families_source()
    posted = parse_qs(post_data or "", keep_blank_values=True)
    form = choice.SectionForm(
        {
            name: values
            if len(values) > 1 or name in (choice.GROUPS, choice.STATUSES)
            else values[0]
            for name, values in posted.items()
            if name in choice.FIELDS
        },
        source=data,
    )
    form.is_valid()
    rule, _field, message = choice.posted_rule(form.cleaned_data, data.groups)
    if rule is None:
        return {"count": str(message), "emailable": "", "matches": 0}
    return choice.preview_line(data, rule)


def families_page(context, admin):
    """Create the campaign with its Families section, as the view draws it."""

    from parishkit.stewardship.accounts import families_choice as choice

    form = _form()
    choice.add_fields(form, families_source())
    return {
        FAMILIES: (
            "text/html",
            render_to_string(
                "stewardship/campaign-create.html",
                context
                | {
                    "admin_chrome": admin
                    | {"campaign_wizard": campaign_wizard.creation()},
                    "configuration": {
                        "mode": "testing",
                        "testing_recipient": "testing@example.org",
                    },
                    "form": form,
                    "families": choice.section_context(form),
                    "created": False,
                    "page_url": FAMILIES,
                    "errors": [],
                },
            ),
        )
    }
