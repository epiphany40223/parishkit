"""Campaign images (#248) reviewed and applied in place (#750), as fixture pages.

The real Campaign images template is served at its real address, ``PAGE``,
so the in-place answers (which must come from the page's own address) can
be served by the fixture server:

- ``PAGE`` lists the five slots, the welcome icon set, at the edit step;
- an upload's POST to ``UPLOAD`` (the banner's upload address) answers, as
  the view does, with a redirect to ``REVIEW``: the page naming the staged
  banner, with its review in the review region;
- ``REFUSED_UPLOAD`` (the member icon's upload address) answers 400 with the
  page again, the slot's field error and its summary in the review region;
- ``REMOVE`` is the page reviewing removing the welcome icon;
- Apply's POST from either review answers with a redirect to ``PENDING``,
  the page naming the change, whose status polls ``STATUS``; that answers
  Applied and follows to ``SETTLED``, the page with the banner set.
"""

from types import SimpleNamespace as Value
from uuid import UUID

from django.template.loader import render_to_string

from parishkit.stewardship.accounts.admin_editing import review_region
from parishkit.stewardship.accounts.artwork_views import SLOT_LABELS, ArtworkForm
from parishkit.stewardship.campaigns.configuration import ARTWORK_SLOTS

PAGE = "/admin/campaign/images/"
BUNDLE = UUID("75000000-0000-4000-8000-0000000000b1")
REQUEST = UUID("75000000-0000-4000-8000-0000000000c1")
UPLOAD = f"{PAGE}banner/"
REFUSED_UPLOAD = f"{PAGE}member/"
REVIEW = f"{PAGE}?review=banner&bundle={BUNDLE}"
REMOVE = f"{PAGE}?remove=welcome"
PENDING = f"{PAGE}?request={REQUEST}"
SETTLED = f"{PAGE}?request={REQUEST}&settled=1"
STATUS = "/artwork-in-place-status"
STEPS = ("Make changes", "Review", "Apply")
# The answers the fixture server gives the page's POSTs.
POSTS = {
    UPLOAD: (303, f"{REVIEW}#settings-review", ""),
    REVIEW: (303, f"{PENDING}#settings-review", ""),
    REMOVE: (303, f"{PENDING}#settings-review", ""),
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


def _asset(label, width, height, number):
    """A normalized image as the page reads it."""
    return Value(pk=UUID(int=number), label=label, width=width, height=height)


def components(context, admin):
    """Every page above, keyed by the exact path and query it is fetched at."""
    welcome = _asset("section", 256, 256, 1)
    banner = _asset("banner", 1024, 217, 2)
    campaign = Value(pk=UUID(int=86), active_configuration=Value(name="Annual"))
    pending = Value(state="staged", request_id=REQUEST, failure_code="")
    applied = Value(state="applied", request_id=REQUEST, failure_code="")

    def page(step, current, forms=None, **region):
        """Campaign images at ``step`` with ``current`` ({slot: asset}) set."""
        forms = forms or {}
        values = review_region(
            "artwork_settings", next(iter(forms.values()), None), **region
        )
        if region.get("receipt"):
            values["status_url"] = STATUS
        slots = [
            {
                "slot": slot,
                "label": SLOT_LABELS[slot],
                "asset": current.get(slot),
                "form": forms.get(slot)
                or ArtworkForm(
                    initial={"base_digest": "a" * 64}, auto_id=f"id_{slot}_%s"
                ),
            }
            for slot in ARTWORK_SLOTS
        ]
        return render_to_string(
            "stewardship/artwork-settings.html",
            context
            | {
                "admin_chrome": admin | {"flow_steps": _steps(step)},
                "campaign": campaign,
                "slots": slots,
            }
            | values,
        )

    refused = ArtworkForm(
        data={"base_digest": "a" * 64}, files={}, auto_id="id_member_%s"
    )
    refused.is_valid()
    POSTS[REFUSED_UPLOAD] = (
        400,
        None,
        page(0, {"welcome": welcome}, {"member": refused}),
    )
    review = {"notes": [], "template": "stewardship/artwork-review.html"}
    return {
        PAGE: ("text/html", page(0, {"welcome": welcome})),
        REVIEW: (
            "text/html",
            page(
                1,
                {"welcome": welcome},
                review=review
                | {
                    "removing": False,
                    "asset": banner,
                    "label": SLOT_LABELS["banner"],
                    "preview": "synthetic-signed-intent",
                },
            ),
        ),
        REMOVE: (
            "text/html",
            page(
                1,
                {"welcome": welcome},
                review=review
                | {
                    "removing": True,
                    "asset": None,
                    "label": SLOT_LABELS["welcome"],
                    "preview": "synthetic-signed-intent",
                },
            ),
        ),
        PENDING: ("text/html", page(2, {"welcome": welcome}, receipt=pending)),
        STATUS: (
            "text/html",
            render_to_string(
                "stewardship/configuration-request.html",
                context
                | {"admin_chrome": admin, "receipt": applied, "follow_url": SETTLED},
            ),
        ),
        SETTLED: (
            "text/html",
            page(2, {"welcome": welcome, "banner": banner}, receipt=applied),
        ),
    }
