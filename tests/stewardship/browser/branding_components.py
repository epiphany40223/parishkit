"""Parish logos reviewed and applied in place (#750), as fixture pages.

The real Parish logos template is served at its real address, ``PAGE``, so
the in-place answers (which must come from the page's own address) can be
served by the fixture server:

- ``PAGE`` is the upload form with no current logos, at the edit step;
- an upload's POST to ``PAGE`` answers, as the view does, with a redirect
  to ``REVIEW``: the page naming the staged logos, with every size and
  Apply in the review region;
- Apply's POST from ``REVIEW`` answers with a redirect to ``PENDING``, the
  page naming the change, whose status polls ``STATUS``; that answers
  Applied and follows to ``SETTLED``, the page with the new current logos.

``REFUSED`` is the page refusing an upload (400), the field error in the
form and its summary in the review region; a browser test answers the
upload's POST with it.
"""

from types import SimpleNamespace as Value
from uuid import UUID

from django.template.loader import render_to_string

from parishkit.stewardship.accounts.admin_editing import review_region
from parishkit.stewardship.accounts.branding_views import LogoForm

PAGE = "/admin/parish/logos/"
BUNDLE = UUID("75000000-0000-4000-8000-0000000000d1")
REQUEST = UUID("75000000-0000-4000-8000-0000000000e1")
REVIEW = f"{PAGE}?review={BUNDLE}"
PENDING = f"{PAGE}?request={REQUEST}"
SETTLED = f"{PAGE}?request={REQUEST}&settled=1"
STATUS = "/branding-in-place-status"
REFUSED = "/branding-in-place-refused"
STEPS = ("Make changes", "Review", "Apply")
POSTS = {
    PAGE: (303, f"{REVIEW}#settings-review", ""),
    REVIEW: (303, f"{PENDING}#settings-review", ""),
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
    sizes = {"favicon": 32, "icon": 128, "large": 512, "menu": 64}
    assets = [
        Value(pk=UUID(int=200 + index), label=label, width=size, height=size)
        for index, (label, size) in enumerate(sizes.items())
    ]
    pending = Value(state="staged", request_id=REQUEST, failure_code="")
    applied = Value(state="applied", request_id=REQUEST, failure_code="")

    def page(step, current, form=None, **region):
        """Parish logos at ``step`` with ``current`` logos and the region."""
        form = form or LogoForm(initial={"base_digest": "a" * 64})
        values = review_region("branding_settings", form, **region)
        if region.get("receipt"):
            values["status_url"] = STATUS
        return (
            "text/html",
            render_to_string(
                "stewardship/branding-settings.html",
                context
                | {
                    "admin_chrome": admin | {"flow_steps": _steps(step)},
                    "form": form,
                    "assets": current,
                }
                | values,
            ),
        )

    refused = LogoForm(data={"base_digest": "a" * 64}, files={})
    refused.is_valid()
    return {
        PAGE: page(0, []),
        REVIEW: page(
            1,
            [],
            review={
                "template": "stewardship/branding-review.html",
                "notes": [],
                "assets": assets,
                "preview": "synthetic-signed-intent",
            },
        ),
        PENDING: page(2, [], receipt=pending),
        STATUS: (
            "text/html",
            render_to_string(
                "stewardship/configuration-request.html",
                context
                | {"admin_chrome": admin, "receipt": applied, "follow_url": SETTLED},
            ),
        ),
        SETTLED: page(2, assets, receipt=applied),
        REFUSED: page(0, [], refused),
    }
