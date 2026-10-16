"""Parish logos reviewed and applied in the review dialog (#750), as fixtures.

The real Parish logos template is served at its real address, ``PAGE``, and
the answers its form and the dialog post for are served by the fixture
server:

- ``PAGE`` is the upload form with no current logos, saved from Basic; its
  upload and the dialog's Apply both post to ``PAGE``, and once a change is
  applied the form and the current logos are redrawn from ``SETTLED``;
- the fixture server answers a POST to ``PAGE`` as Apply's (a redirect to
  ``APPLIED``, the change's status, Applied); a test answers the upload's
  POST (multipart, which the fixture server cannot tell apart) with
  ``REVIEW``, the review dialog's fragment of every staged size, or with a
  refusal (status 400): ``REFUSED``, the field error and its summary, or
  ``REFUSED_SIZE``, a favicon uploaded on its own (#1062) refused, drawn in
  Advanced, the mode it was sent from;
- ``ADVANCED_PAGE`` draws current logos saved from Advanced, which opens in
  Advanced and asks before Basic (#1062, #1121); it is redrawn from
  ``SETTLED_ADVANCED``, the new logos saved from Advanced too;
- ``BASIC_TO_ADVANCED`` is ``PAGE`` (no logos, opening in Basic) redrawn
  from ``SETTLED_ADVANCED``: an Advanced upload applied from a Basic page.
"""

from types import SimpleNamespace as Value
from uuid import UUID

from django import forms
from django.template.loader import render_to_string

from parishkit.stewardship.accounts.admin_editing import summary_errors
from parishkit.stewardship.accounts.branding_views import LogoUploadForm

PAGE = "/admin/parish/logos/"
BUNDLE = UUID("75000000-0000-4000-8000-0000000000d1")
REQUEST = UUID("75000000-0000-4000-8000-0000000000e1")
REVIEW = "/branding-dialog-review"
APPLIED = "/branding-dialog-applied"
SETTLED = "/branding-dialog-settled"
SETTLED_ADVANCED = "/branding-dialog-settled-advanced"
REFUSED = "/branding-dialog-refused"
REFUSED_SIZE = "/branding-dialog-refused-size"
ADVANCED_PAGE = f"{PAGE}?stored=advanced"
BASIC_TO_ADVANCED = f"{PAGE}?then=advanced"
SIZE_ERROR = (
    "Favicon: the image is 400 × 120 pixels; a favicon must be square (within 10%)."
)
# The answer the fixture server gives the page's POSTs: Apply's.
POSTS = {PAGE: (303, APPLIED, "")}


def components(context, admin):
    """Every page above, keyed by the exact path and query it is fetched at."""
    sizes = {"favicon": 32, "icon": 128, "large": 512, "menu": 64}
    assets = [
        Value(
            pk=UUID(int=200 + index),
            label=label,
            width=size,
            height=size,
            changed=label == "favicon",
        )
        for index, (label, size) in enumerate(sizes.items())
    ]
    applied = Value(state="applied", request_id=REQUEST, failure_code="")
    steps = [
        {"label": label, "state": "current" if index == 0 else "upcoming"}
        for index, label in enumerate(("Make changes", "Review", "Apply"))
    ]

    def page(current, form=None, stored="basic", refresh=SETTLED):
        """Parish logos with ``current`` logos saved from the ``stored``
        mode, redrawn from ``refresh``; ``form`` is a refused upload's."""
        drawn = form or LogoUploadForm(initial={"base_digest": "a" * 64})
        drawn.stored_mode = stored
        return (
            "text/html",
            render_to_string(
                "stewardship/branding-settings.html",
                context
                | {
                    "admin_chrome": admin | {"flow_steps": steps},
                    "form": drawn,
                    "assets": current,
                    "page_url": PAGE,
                    "refresh_url": refresh,
                    "errors": summary_errors(drawn) if form is not None else [],
                },
            ),
        )

    refused = LogoUploadForm(data={"base_digest": "a" * 64}, files={})
    refused.is_valid()
    # The server's refusal of a favicon uploaded on its own; the fixture
    # server never reads the file, so the error is the one check_slot gives.
    refused_size = LogoUploadForm(
        data={"base_digest": "a" * 64, "editor_mode": "advanced"}
    )
    refused_size.is_valid()
    refused_size.errors.clear()
    refused_size.add_error(
        "favicon", forms.ValidationError(SIZE_ERROR, code="wrong_shape")
    )
    return {
        PAGE: page([]),
        REVIEW: (
            "text/html",
            render_to_string(
                "stewardship/branding-review.html",
                {
                    "review": {
                        "bundle": BUNDLE,
                        "assets": assets,
                        "preview": "synthetic-signed-intent",
                    }
                },
            ),
        ),
        APPLIED: (
            "text/html",
            render_to_string(
                "stewardship/configuration-request.html",
                context | {"admin_chrome": admin, "receipt": applied},
            ),
        ),
        SETTLED: page(assets),
        SETTLED_ADVANCED: page(assets, stored="advanced", refresh=SETTLED_ADVANCED),
        REFUSED: page([], refused),
        REFUSED_SIZE: page([], refused_size),
        ADVANCED_PAGE: page(assets, stored="advanced", refresh=SETTLED_ADVANCED),
        BASIC_TO_ADVANCED: page([], refresh=SETTLED_ADVANCED),
    }
