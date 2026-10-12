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
a test can see the indicator follow along. Share options is served at
``SHARE`` and ``SHARE_REVIEW``: its form is a region of its
own, redrawn by the answer with the values sent (#750). ``SHARE_REFUSED`` is
a refused Apply's answer, as the view draws it: the list changed elsewhere,
at a newer version, with the refusal in the review region (#768). Apply's
answer on Share options is ``SHARE_PENDING`` (polling ``SHARE_STATUS``, which
answers Applied and follows to ``SHARE_SETTLED``, the applied list at version
``"b" * 64``), or, when the change has already settled, ``SHARE_APPLIED``.
Campaign settings reviews in the review dialog (#1119) and is served by
``campaign_settings_components``; the live end date's check (#944) there is
answered, by date, with one of ``END_CHECKS``' answers (the real check
fragment): none, a past date, a date before the start, or mail the date
would strand. A live campaign's Campaign
Ministries (#750) is served at ``MINISTRIES`` (the editor, whose Apply leads
to ``MINISTRIES_PENDING``) and ``MINISTRIES_REVIEW`` (its impact review).
Ministries' row selection (#750) is served at ``MINISTRY_TABLE`` (page 1
of 30 Ministries, 25 to a page, whose Apply leads to
``MINISTRY_TABLE_PENDING``), ``MINISTRY_TABLE_REVIEW`` (its review of
inactivating Ministry 1) and ``MINISTRY_TABLE_PAGE2`` (the table's second
page, as a GET draws it). The pending change polls ``MINISTRY_TABLE_STATUS``,
which answers Applied and follows to ``MINISTRY_TABLE_SETTLED``, the page
refreshed with Ministry 01 inactive and Ministry 25 gone from the catalog.
"""

from types import SimpleNamespace
from uuid import UUID

from django.template.loader import render_to_string

from parishkit.stewardship.accounts.admin_editing import review_region
from parishkit.stewardship.accounts.campaign_ministry_views import (
    LiveMinistriesForm,
)
from parishkit.stewardship.accounts.ministry_views import CATALOG_SORTING
from parishkit.stewardship.accounts.parish_views import TIMEZONE_NOTE, ParishForm
from parishkit.stewardship.accounts.share_forms import (
    ShareOptions,
    default_share_options,
)
from parishkit.stewardship.web.tables import paginate

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
SHARE_STATUS = "/share-in-place-status"
SHARE_PENDING = f"{SHARE}?request={REQUEST}"
SHARE_SETTLED = f"{SHARE}?request={REQUEST}&settled=1"
MINISTRIES = "/campaign-ministries-in-place"
MINISTRIES_REVIEW = "/campaign-ministries-in-place-review"
MINISTRIES_PENDING = f"{MINISTRIES}?request={REQUEST}"
# A live campaign's offered Ministries; both are selected.
MINISTRY_CHOICES = [("4", "Community outreach"), ("9", "Food pantry")]
MINISTRY_TABLE = "/ministries-in-place"
MINISTRY_TABLE_REVIEW = "/ministries-in-place-review"
MINISTRY_TABLE_PAGE2 = "/ministries-in-place-page-2"
MINISTRY_TABLE_PENDING = f"{MINISTRY_TABLE}?request={REQUEST}"
MINISTRY_TABLE_STATUS = "/ministries-in-place-status"
MINISTRY_TABLE_SETTLED = f"{MINISTRY_TABLE}?request={REQUEST}&settled=1"
# Thirty active Ministries, so the table has a second page.
MINISTRY_ROWS = [
    {"duid": duid, "name": f"Ministry {duid:02d}", "active": True, "included": True}
    for duid in range(1, 31)
]
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
    SHARE: (303, SHARE_PENDING, ""),
    MINISTRIES: (303, MINISTRIES_PENDING, ""),
    MINISTRY_TABLE: (303, MINISTRY_TABLE_PENDING, ""),
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

    def ministries(step, **region):
        """A live campaign's Campaign Ministries at ``step`` (#750), both
        offered Ministries selected, with ``region``'s review context."""
        form = LiveMinistriesForm(
            initial={"ministry_duids": ["4", "9"], "base_digest": "a" * 64},
            ministries=MINISTRY_CHOICES,
        )
        return (
            "text/html",
            render_to_string(
                "stewardship/campaign-ministries.html",
                context
                | {
                    "admin_chrome": admin | {"flow_steps": _steps(step)},
                    "campaign": {
                        "pk": REQUEST,
                        "state": "active",
                        "active_configuration": {"name": "Sample campaign"},
                    },
                    "form": form,
                }
                | review_region("campaign_ministries", None, **region),
            ),
        )

    def ministry_table(step, page="1", rows=MINISTRY_ROWS, **region):
        """Ministries at ``step``: ``page`` of ``rows``, 25 to a page, with
        ``region``'s review context under it (#750)."""
        values = review_region("ministries", None, **region)
        if region.get("receipt"):
            # The fixture server serves Ministries' Change status here.
            values["status_url"] = MINISTRY_TABLE_STATUS
        return (
            "text/html",
            render_to_string(
                "stewardship/ministries.html",
                context
                | {
                    "admin_chrome": admin | {"flow_steps": _steps(step)},
                    "table": paginate(
                        rows,
                        {"size": "25", "page": page},
                        carry=(("q", ""), ("state", "all")),
                        sorting=CATALOG_SORTING,
                    ),
                    "query": "",
                    "state": "all",
                }
                | values,
            ),
        )

    options = default_share_options()

    def share(
        data,
        step,
        previous=None,
        digest="a" * 64,
        template="stewardship/share-settings.html",
        **region,
    ):
        """Share options at ``step``, its formset bound to ``data`` if given.

        ``previous`` (default the stock options) is the saved list, at
        version ``digest``; ``template`` draws the page.
        """
        formset = ShareOptions(data, prefix="options", previous=previous or options)
        values = review_region("share_settings", None, **region)
        if region.get("receipt"):
            # The fixture server serves Share options' Change status here.
            values["status_url"] = SHARE_STATUS
        return (
            "text/html",
            render_to_string(
                template,
                context
                | {
                    "admin_chrome": admin | {"flow_steps": _steps(step)},
                    "campaign": {
                        "pk": REQUEST,
                        "active_configuration": {"name": "Sample campaign"},
                    },
                    "formset": formset,
                    "base_digest": digest,
                }
                | values,
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
        MINISTRIES: ministries(0),
        MINISTRIES_REVIEW: ministries(
            1,
            review={
                "template": "stewardship/campaign-ministries-review.html",
                "notes": [],
                "added": [],
                "removed": [
                    {"name": "Food pantry", "duid": 9, "answers": 1, "open": 1}
                ],
                "open_forms": 3,
                "preview": "synthetic-signed-intent",
            },
        ),
        MINISTRIES_PENDING: ministries(2, receipt=pending),
        MINISTRY_TABLE: ministry_table(0),
        MINISTRY_TABLE_REVIEW: ministry_table(
            1,
            review={
                "template": "stewardship/ministry-review.html",
                "notes": [],
                "changing": MINISTRY_ROWS[:1],
                "unchanged": [],
                "not_included": [],
                "new_active": False,
                "preview": "synthetic-signed-intent",
            },
        ),
        MINISTRY_TABLE_PAGE2: ministry_table(0, page="2"),
        MINISTRY_TABLE_PENDING: ministry_table(2, receipt=pending),
        MINISTRY_TABLE_STATUS: (
            "text/html",
            render_to_string(
                "stewardship/configuration-request.html",
                context
                | {
                    "admin_chrome": admin,
                    "receipt": applied,
                    "follow_url": MINISTRY_TABLE_SETTLED,
                },
            ),
        ),
        MINISTRY_TABLE_SETTLED: ministry_table(
            2,
            # Ministry 25 left the catalog meanwhile, so a tick on it goes.
            rows=[
                dict(MINISTRY_ROWS[0], active=False),
                *(row for row in MINISTRY_ROWS[1:] if row["duid"] != 25),
            ],
            receipt=applied,
        ),
        SHARE: share(None, 0),
        SHARE_REVIEW: share(
            sent,
            1,
            review={
                "template": "stewardship/option-review.html",
                "before": options,
                "after": renamed,
                "preview": "synthetic-signed-intent",
            },
        ),
        SHARE_PENDING: share(None, 2, receipt=pending),
        SHARE_STATUS: (
            "text/html",
            render_to_string(
                "stewardship/configuration-request.html",
                context
                | {
                    "admin_chrome": admin,
                    "receipt": applied,
                    "follow_url": SHARE_SETTLED,
                },
            ),
        ),
        SHARE_SETTLED: share(
            None, 2, previous=renamed, digest="b" * 64, receipt=applied
        ),
        SHARE_APPLIED: share(
            None, 2, previous=renamed, digest="b" * 64, receipt=applied
        ),
        SHARE_REFUSED: share(
            None,
            0,
            previous=[dict(options[0], label="Changed elsewhere"), *options[1:]],
            digest="c" * 64,
            message="This preview is out of date.",
        ),
        END_CHECKS["2054-11-15"]: check(),
        END_CHECKS["2026-10-05"]: check(PASSED),
        END_CHECKS["2054-09-30"]: check(BEFORE_START),
        END_CHECKS["2054-10-20"]: check(
            STRANDING,
            {"url": COMBINED_REVIEW, "label": "Change the end date and its mailings"},
        ),
    }
