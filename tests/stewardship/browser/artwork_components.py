"""Campaign images (#248) reviewed and applied in the review dialog (#750).

The real Campaign images template is served at its real address, ``PAGE``,
and the answers its forms post for are served by the fixture server:

- ``PAGE`` lists the five slots, the welcome icon set, for a campaign
  without Financial stewardship: the Financial page icon's upload is
  unavailable with its reason (#563). Its forms post to ``PAGE`` (Remove
  this image and the dialog's Apply) and its slots are redrawn from
  ``SETTLED`` once a change is applied;
- an upload's POST to ``UPLOAD`` (the banner's upload address) answers, as
  the view does, with the review dialog's fragment of the staged banner;
- ``REFUSED_UPLOAD`` (the member icon's upload address) answers 400 with the
  page again, the slot's field error and its summary;
- a test answers Remove this image's Review with ``REMOVAL``, the review
  dialog's fragment of removing the welcome icon (``PAGE`` takes both that
  Review and Apply, which the fixture server cannot tell apart);
- Apply's POST to ``PAGE`` answers with a redirect to ``APPLIED``, the
  change's status, Applied; ``SETTLED`` is the page with the banner set too.
"""

from types import SimpleNamespace as Value
from uuid import UUID

from django.template.loader import render_to_string

from parishkit.stewardship.accounts import campaign_wizard
from parishkit.stewardship.accounts.admin_editing import summary_errors
from parishkit.stewardship.accounts.artwork_views import (
    SLOT_LABELS,
    UNAVAILABLE,
    ArtworkForm,
    name_unavailable,
)
from parishkit.stewardship.campaigns.configuration import ARTWORK_SLOTS

PAGE = "/admin/campaign/images/"
BUNDLE = UUID("75000000-0000-4000-8000-0000000000b1")
REQUEST = UUID("75000000-0000-4000-8000-0000000000c1")
UPLOAD = f"{PAGE}banner/"
REFUSED_UPLOAD = f"{PAGE}member/"
REMOVAL = "/artwork-removal-review"
APPLIED = "/artwork-applied"
SETTLED = "/artwork-settled"
# Campaign images as a Campaign setup step (#1404), with its action row.
STEP = "/artwork-step"
# The answers the fixture server gives the page's POSTs; the upload's and
# the refused upload's are added by ``components``.
POSTS = {PAGE: (303, APPLIED, "")}


def _asset(label, width, height, number):
    """A normalized image as the page reads it."""
    return Value(pk=UUID(int=number), label=label, width=width, height=height)


def components(context, admin):
    """Every page above, keyed by the exact path it is fetched at."""
    welcome = _asset("section", 256, 256, 1)
    banner = _asset("banner", 1024, 217, 2)
    campaign = Value(pk=UUID(int=86), active_configuration=Value(name="Annual"))
    applied = Value(state="applied", request_id=REQUEST, failure_code="")
    steps = [
        {"label": label, "state": "current" if index == 0 else "upcoming"}
        for index, label in enumerate(("Make changes", "Review", "Apply"))
    ]

    def page(current, forms=None, wizard=None):
        """Campaign images with ``current`` ({slot: asset}) set; ``forms``
        holds a refused upload's bound form."""
        forms = forms or {}

        def form(slot):
            """The slot's upload form; the Financial one named as unavailable."""
            drawn = forms.get(slot) or ArtworkForm(
                initial={"base_digest": "a" * 64}, auto_id=f"id_{slot}_%s"
            )
            return name_unavailable(drawn, slot) if slot == "financial" else drawn

        slots = [
            {
                "slot": slot,
                "label": SLOT_LABELS[slot],
                "asset": current.get(slot),
                "unavailable": UNAVAILABLE[slot] if slot == "financial" else None,
                "form": form(slot),
            }
            for slot in ARTWORK_SLOTS
        ]
        refused = next(iter(forms.values()), None)
        return render_to_string(
            "stewardship/artwork-settings.html",
            context
            | {
                "admin_chrome": admin
                | {"flow_steps": steps, "campaign_wizard": wizard},
                "campaign": campaign,
                "slots": slots,
                "base_digest": "a" * 64,
                "page_url": PAGE,
                "refresh_url": SETTLED,
                "errors": summary_errors(refused) if refused is not None else [],
            },
        )

    def review(slot, asset):
        """The review dialog's fragment of ``slot`` taking ``asset`` (or
        losing its image, with None)."""
        return render_to_string(
            "stewardship/artwork-review.html",
            {
                "review": {
                    "removing": asset is None,
                    "slot": slot,
                    "bundle": BUNDLE,
                    "asset": asset,
                    "label": SLOT_LABELS[slot],
                    "preview": "synthetic-signed-intent",
                }
            },
        )

    refused = ArtworkForm(
        data={"base_digest": "a" * 64}, files={}, auto_id="id_member_%s"
    )
    refused.is_valid()
    POSTS[UPLOAD] = (200, None, review("banner", banner))
    POSTS[REFUSED_UPLOAD] = (400, None, page({"welcome": welcome}, {"member": refused}))
    return {
        PAGE: ("text/html", page({"welcome": welcome})),
        STEP: (
            "text/html",
            page(
                {"welcome": welcome},
                wizard=campaign_wizard.build(
                    False, "images", done={"settings"}, mail_test_url=None
                ),
            ),
        ),
        REMOVAL: ("text/html", review("welcome", None)),
        APPLIED: (
            "text/html",
            render_to_string(
                "stewardship/configuration-request.html",
                context
                | {"admin_chrome": admin, "receipt": applied, "follow_url": None},
            ),
        ),
        SETTLED: ("text/html", page({"welcome": welcome, "banner": banner})),
    }
