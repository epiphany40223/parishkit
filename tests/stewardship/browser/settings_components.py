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
a test can see the indicator follow along. Share options reviews in the
review dialog (#750): ``SHARE`` is the page at version ``"a" * 64``, posting
to itself; a test answers its Review with ``SHARE_REVIEW`` (the real review
fragment of a renamed first option) or ``SHARE_REFUSED`` (the page drawn
again with a row's error, status 400). Its Apply is redirected by the
fixture server to ``SHARE_APPLIED``, the change's status page, Applied; the
form's region is then redrawn from ``SHARE_SETTLED``, the applied list at
version ``"b" * 64``.
Campaign settings reviews in the review dialog (#1119) and is served by
``campaign_settings_components``; the live end date's check (#944) there is
answered, by date, with one of ``END_CHECKS``' answers (the real check
fragment): none, a past date, a date before the start, or mail the date
would strand.
"""

from types import SimpleNamespace
from uuid import UUID

from django.template.loader import render_to_string

from parishkit.stewardship.accounts.admin_editing import (
    review_region,
    summary_errors,
)
from parishkit.stewardship.accounts.parish_views import TIMEZONE_NOTE, ParishForm
from parishkit.stewardship.accounts.share_forms import (
    ShareOptions,
    default_share_options,
)

SETTINGS = "/settings-in-place"
REVIEW = "/settings-in-place-review"
REFUSED = "/settings-in-place-refused"
STATUS = "/settings-in-place-status"
REQUEST = UUID("53200000-0000-4000-8000-000000000532")
PENDING = f"{SETTINGS}?request={REQUEST}"
SETTLED = f"{SETTINGS}?request={REQUEST}&settled=1"
SHARE = "/share-in-place"
SHARE_REVIEW = "/share-in-place-review"
SHARE_REFUSED = "/share-in-place-refused"
SHARE_APPLIED = "/share-in-place-applied"
SHARE_SETTLED = "/share-in-place-settled"
# The live check's answers (#944), by the end date the browser test types.
STRANDING = "Some scheduled emails would no longer fit the campaign."
PASSED = "Choose an end date that has not already passed."
BEFORE_START = "Choose an end date after the campaign's start date, October 1, 2054."
END_CHECKS = {
    "2054-11-15": "/campaign-end-check-clear",
    "2026-10-05": "/campaign-end-check-passed",
    "2054-09-30": "/campaign-end-check-before-start",
    "2054-10-20": "/campaign-end-check-stranding",
}
# Where a refused shortening sends the Administrator (#912).
COMBINED_REVIEW = "/admin/campaign/schedules/?end_date=2054-10-20"
# The confirmation's answer: back to this page, naming the change.
POSTS = {
    SETTINGS: (303, PENDING, ""),
    SHARE: (303, SHARE_APPLIED, ""),
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

    options = default_share_options()

    def share(data=None, previous=None, digest="a" * 64):
        """Share options, its formset bound to ``data`` if given.

        ``previous`` (default the stock options) is the saved list, at
        version ``digest``. A bound formset is a refused Review's page, with
        its errors' summary. Review and Apply post to ``SHARE``; the form's
        region is redrawn from ``SHARE_SETTLED``.
        """
        formset = ShareOptions(data, prefix="options", previous=previous or options)
        return (
            "text/html",
            render_to_string(
                "stewardship/share-settings.html",
                context
                | {
                    "admin_chrome": admin | {"flow_steps": _steps(0)},
                    "campaign": {
                        "pk": REQUEST,
                        "active_configuration": {"name": "Sample campaign"},
                    },
                    "formset": formset,
                    "base_digest": digest,
                    "page_url": SHARE,
                    "refresh_url": SHARE_SETTLED,
                    "errors": summary_errors(formset) if data else [],
                },
            ),
        )

    # The formset as the browser posts it, with the first label renamed.
    sent = {
        "options-TOTAL_FORMS": str(len(options) + 1),
        "options-INITIAL_FORMS": str(len(options)),
        "options-MIN_NUM_FORMS": "0",
        "options-MAX_NUM_FORMS": "1000",
    }
    for index, option in enumerate(options):
        sent[f"options-{index}-id"] = option["id"]
        sent[f"options-{index}-label"] = option["label"]
        sent[f"options-{index}-ORDER"] = str(index + 1)
        if option["free_text"]:
            sent[f"options-{index}-free_text"] = "on"
    sent["options-0-label"] = "Renamed option"
    renamed = [dict(options[0], label="Renamed option"), *options[1:]]

    def check(message=None, link=None):
        """The live end-date check's answer: ``message`` blocks Review."""
        return (
            "text/html",
            render_to_string(
                "stewardship/campaign-end-check.html",
                {"message": message, "link": link},
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
        SHARE: share(),
        SHARE_REVIEW: (
            "text/html",
            render_to_string(
                "stewardship/share-options-review.html",
                {
                    "review": {
                        "before": options,
                        "after": renamed,
                        "preview": "synthetic-signed-intent",
                    }
                },
            ),
        ),
        SHARE_REFUSED: share(sent | {"options-0-label": "{{ family_code }}"}),
        SHARE_APPLIED: (
            "text/html",
            render_to_string(
                "stewardship/configuration-request.html",
                context
                | {
                    "admin_chrome": admin,
                    "receipt": applied,
                    "follow_url": None,
                },
            ),
        ),
        SHARE_SETTLED: share(previous=renamed, digest="b" * 64),
        END_CHECKS["2054-11-15"]: check(),
        END_CHECKS["2026-10-05"]: check(PASSED),
        END_CHECKS["2054-09-30"]: check(BEFORE_START),
        END_CHECKS["2054-10-20"]: check(
            STRANDING,
            {"url": COMBINED_REVIEW, "label": "Change the end date and its mailings"},
        ),
    }
