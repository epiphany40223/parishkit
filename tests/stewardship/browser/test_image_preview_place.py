"""Every Admin image upload shows its preview above its file field (#2279).

UX conventions, "Image previews": what is there now, then the control that
changes it. Each case opens an upload page's fixture at its real template and
checks, by bounding boxes, that every file field starts below the preview it
belongs to, on a phone and a desktop width. The Parish logos frames also keep
one height with or without logos, so logos appearing never move the form.
"""

import pytest

from .artwork_components import PAGE as ARTWORK
from .artwork_components import STEP as ARTWORK_STEP
from .branding_components import ADVANCED_PAGE, SETTLED
from .branding_components import PAGE as BRANDING

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)

# For each scope (the whole page, or each Campaign images slot), the preview's
# bottom and the top of every visible file field in that scope.
PLACES = """([scope, preview]) => [...document.querySelectorAll(scope)]
  .map((root) => ({
    preview: root.querySelector(preview).getBoundingClientRect().bottom,
    fields: [...root.querySelectorAll('input[type=file]')]
      .filter((field) => field.getClientRects().length)
      .map((field) => field.getBoundingClientRect().top),
  }))"""

# (fixture path, scope, preview) for every image upload page.
CASES = {
    "parish-logos-empty": (BRANDING, "body", "#branding-current"),
    "parish-logos-saved": (SETTLED, "body", "#branding-current"),
    "parish-logos-advanced": (ADVANCED_PAGE, "body", "#branding-current"),
    "campaign-images": (ARTWORK, ".artwork-slot", ".artwork-box"),
    "campaign-images-wizard": (ARTWORK_STEP, ".artwork-slot", ".artwork-box"),
    "setup-logo": ("/setup-branding", "body", "#logo-choice-preview"),
    "setup-logo-saved": ("/setup-branding-saved", "body", "#logo-choice-preview"),
}


@pytest.mark.parametrize("width", [320, 1024])
@pytest.mark.parametrize("case", CASES)
def test_preview_sits_above_its_file_field(page, component_origin, case, width):
    """Each file field on the page starts at or below its preview's bottom."""
    path, scope, preview = CASES[case]
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(component_origin + path)
    places = page.evaluate(PLACES, [scope, preview])
    assert places
    for place in places:
        assert place["fields"], place
        for top in place["fields"]:
            assert top >= place["preview"], place


@pytest.mark.parametrize("width", [320, 1024])
def test_parish_logo_frames_keep_one_height(page, component_origin, width):
    """The current logos take the same room with no logos as with all four,
    so the form below stays where it is when logos are applied (#736)."""
    page.set_viewport_size({"width": width, "height": 900})
    heights = []
    for path in (BRANDING, SETTLED):
        page.goto(component_origin + path)
        heights.append(
            page.locator("#branding-current").evaluate(
                "node => node.getBoundingClientRect().height"
            )
        )
    assert heights[0] == heights[1]
