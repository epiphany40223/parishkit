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
from .waits import eventually

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


# Each visible Parish logos file field's own chosen-image box: the box's top
# and bottom, the top of the field's label and of the field itself, and the
# bottom of the current logos and the top of the form.
FIELD_PREVIEWS = """() => {
  const top = (node) => node.getBoundingClientRect().top;
  return [...document.querySelectorAll('#branding-upload-form input[type=file]')]
    .filter((field) => field.getClientRects().length)
    .map((field) => {
      const box = document.getElementById(field.dataset.previewTarget);
      const rect = box.getBoundingClientRect();
      return {
        id: field.id,
        box: [rect.top, rect.bottom],
        label: top(document.querySelector(`label[for="${field.id}"]`)),
        field: top(field),
        frames: document.getElementById('branding-current')
          .getBoundingClientRect().bottom,
        form: top(document.getElementById('branding-upload-form')),
        next: Boolean(box.nextElementSibling?.matches('.label-row, label')),
      };
    });
}"""


def mode(page, name):
    """The shared Basic / Advanced toggle's radio for ``name`` (#1121)."""
    return page.get_by_role("group", name="Mode", exact=True).get_by_role(
        "radio", name=name
    )


def real_png(size=0):
    """A small drawable PNG, padded with trailing bytes to ``size``."""
    import io

    from PIL import Image

    stream = io.BytesIO()
    Image.new("RGB", (48, 48), "red").save(stream, format="PNG")
    data = stream.getvalue()
    data += b"\0" * max(0, size - len(data))
    return {"name": "logo.png", "mimeType": "image/png", "buffer": data}


def shown(page, field_id):
    """The field's preview image src when shown, else None."""
    return page.evaluate(
        """(id) => {
          const field = document.getElementById(id);
          const img = document.getElementById(field.dataset.previewTarget)
            .querySelector('img');
          return img.hidden ? null : img.getAttribute('src');
        }""",
        field_id,
    )


def places(page):
    """Every visible field's preview, label and field positions."""
    return page.evaluate(FIELD_PREVIEWS)


@pytest.mark.parametrize("width", [320, 1024])
@pytest.mark.parametrize("opened", ["basic", "advanced"])
def test_each_parish_logo_field_has_its_preview_above_it(
    page, component_origin, opened, width
):
    """Basic's one field and each Advanced size field have their own box
    directly above the field's label, below the form's top (so below the
    current logos); choosing a file fills it at once and moves nothing."""
    failures = []
    page.on("pageerror", lambda error: failures.append(str(error)))
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(component_origin + (BRANDING if opened == "basic" else ADVANCED_PAGE))
    before = places(page)
    expected = (
        ["id_logo"]
        if opened == "basic"
        else ["id_favicon", "id_icon", "id_menu", "id_large"]
    )
    assert sorted(place["id"] for place in before) == sorted(expected)
    for place in before:
        assert place["next"], place
        assert place["frames"] <= place["form"] <= place["box"][0], place
        assert place["box"][1] <= place["label"] <= place["field"], place
        assert shown(page, place["id"]) is None
    for place in before:
        page.locator(f"#{place['id']}").set_input_files(real_png())
        page.locator(f"#{place['id']}_preview img").wait_for(state="visible")
        assert shown(page, place["id"]).startswith("blob:")
        assert places(page) == before
    assert not failures


def test_a_refused_logo_file_keeps_no_preview(page, component_origin):
    """A file over its size's limit or not an image empties the box again,
    and a field the mode switch empties (a size, by choosing a Basic image;
    the Basic image, by Switch to Basic) empties its box too."""
    page.goto(component_origin + BRANDING)
    mode(page, "Advanced").check()
    before = places(page)
    favicon = page.locator("#id_favicon")
    favicon.set_input_files(real_png())
    page.locator("#id_favicon_preview img").wait_for(state="visible")
    favicon.set_input_files(real_png(256 * 1024 + 1))
    page.locator("#id_favicon_preview img").wait_for(state="hidden")
    assert shown(page, "id_favicon") is None
    favicon.set_input_files(real_png())
    page.locator("#id_favicon_preview img").wait_for(state="visible")
    favicon.set_input_files(
        {"name": "notes.txt", "mimeType": "text/plain", "buffer": b"hello"}
    )
    page.locator("#id_favicon_preview img").wait_for(state="hidden")
    assert shown(page, "id_favicon") is None
    favicon.set_input_files(real_png())
    page.locator("#id_favicon_preview img").wait_for(state="visible")
    assert places(page) == before
    # Basic needs the dialog's Switch to Basic, as a size is chosen; a Basic
    # image then empties every size, and each box lets its file go.
    mode(page, "Basic").click()
    page.get_by_role("dialog").get_by_role("button", name="Switch to Basic").click()
    page.locator("#id_logo").set_input_files(real_png())
    page.locator("#id_logo_preview img").wait_for(state="visible")
    assert page.locator("#id_favicon").evaluate("node => node.files.length") == 0
    assert shown(page, "id_favicon") is None
    # Back in Advanced, a size chosen again, then Switch to Basic: the Basic
    # image is emptied, and its box lets the file go too.
    mode(page, "Advanced").check()
    favicon.set_input_files(real_png())
    page.locator("#id_favicon_preview img").wait_for(state="visible")
    mode(page, "Basic").click()
    page.get_by_role("dialog").get_by_role("button", name="Switch to Basic").click()
    page.locator("#id_logo_preview img").wait_for(state="hidden")
    assert page.locator("#id_logo").evaluate("node => node.files.length") == 0
    assert shown(page, "id_logo") is None


@pytest.mark.parametrize("path", [BRANDING, SETTLED])
def test_parish_logo_frames_run_smallest_to_largest(page, component_origin, path):
    """The current logos' frames run favicon, icon, menu logo, large logo
    (#2288), with or without saved logos."""
    page.goto(component_origin + path)
    captions = page.locator("#branding-current figcaption").all_inner_texts()
    assert captions == ["Favicon", "Icon", "Menu logo", "Large logo"]


# Each Advanced size's chosen-image box and image, or each current-logo
# frame's box and image, in page order: the box's rectangle, the image's
# drawn size (null while hidden), whether the empty text fits its box, and
# the page's width.
SIZED = """(selector) => [...document.querySelectorAll(selector)].map((node) => {
  const box = node.querySelector('.file-preview-box');
  const rect = box.getBoundingClientRect();
  const image = box.querySelector('img');
  const empty = box.querySelector('.file-preview-empty');
  return {
    size: node.dataset.logoSize,
    box: [rect.left, rect.top, rect.right, rect.bottom],
    image: image && !image.hidden
      ? [image.getBoundingClientRect().width, image.getBoundingClientRect().height]
      : null,
    fits: !empty || empty.hidden || box.scrollHeight <= box.clientHeight,
    page: document.documentElement.clientWidth,
  };
})"""

# Each size's saved bound (web/content.py LOGO_SIZES); the large logo is
# capped at its box instead.
BOUNDS = {"favicon": (32, 32), "icon": (128, 128), "menu": (512, 128)}


def wide_png():
    """A 600 × 300 PNG: larger than every bound but the large logo's, and
    wide, so each size fits it differently."""
    import io

    from PIL import Image

    stream = io.BytesIO()
    Image.new("RGB", (600, 300), "blue").save(stream, format="PNG")
    return {"name": "wide.png", "mimeType": "image/png", "buffer": stream.getvalue()}


def assert_sized_boxes(boxes):
    """Each size's box has its own height (favicon < icon = menu < large),
    holds its empty text, and stays inside the page's width."""
    assert [box["size"] for box in boxes] == ["favicon", "icon", "menu", "large"]
    heights = {box["size"]: box["box"][3] - box["box"][1] for box in boxes}
    assert heights["favicon"] < heights["icon"] == heights["menu"]
    assert heights["menu"] < heights["large"]
    for box in boxes:
        assert box["fits"], box
        assert box["box"][0] >= 0 and box["box"][2] <= box["page"], box


@pytest.mark.parametrize("width", [320, 400, 1024])
def test_each_advanced_size_previews_at_its_saved_size(page, component_origin, width):
    """The same image chosen for every Advanced size is drawn as each size
    will save it: within that size's bound at one CSS pixel per image pixel,
    so the four previews differ in size; the large logo is capped at its
    box, and a column narrower than a bound shrinks the image to fit. Each
    box keeps its own fixed height, so choosing the files moves nothing."""
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(component_origin + ADVANCED_PAGE)
    selector = "#branding-upload-form .file-preview[data-logo-size]"
    before = page.evaluate(SIZED, selector)
    assert_sized_boxes(before)
    fields = places(page)
    for size in ("favicon", "icon", "menu", "large"):
        page.locator(f"#id_{size}").set_input_files(wide_png())
        page.locator(f"#id_{size}_preview img").wait_for(state="visible")
    eventually(
        page,
        """(selector) => [...document.querySelectorAll(selector + ' img')]
          .every((image) => image.complete && image.naturalWidth > 0)""",
        arg=selector,
    )
    after = page.evaluate(SIZED, selector)
    assert places(page) == fields
    assert [box["box"] for box in after] == [box["box"] for box in before]
    # The 2:1 image's expected drawn width: within the bound (and twice the
    # bound's height), the box's content width, and, for the large logo,
    # twice the box's content height. Box padding is 1rem, border 2px.
    drawn = {box["size"]: box["image"] for box in after}
    for box in after:
        inner = box["box"][2] - box["box"][0] - 16 - 2
        if box["size"] == "large":
            height = box["box"][3] - box["box"][1] - 16 - 2
            expected = min(600, inner, 2 * height)
        else:
            bound = BOUNDS[box["size"]]
            expected = min(600, bound[0], inner, 2 * bound[1])
        assert abs(drawn[box["size"]][0] - expected) <= 1, (box, expected)
        assert abs(drawn[box["size"]][1] - expected / 2) <= 1, (box, expected)
    # Smallest to largest; on a phone the column may be narrower than both
    # the menu logo and the large logo, which then share its width. Sizes
    # are rounded to half a pixel first: engines lay out subpixel sizes
    # with float noise (Firefox drew those two shared heights as 120.40001
    # and 120.39999), far below the whole-pixel steps between sizes.
    halves = {size: [round(2 * side) / 2 for side in drawn[size]] for size in drawn}
    areas = [halves[size][0] * halves[size][1] for size in halves]
    assert areas == sorted(areas) and len(set(areas[:3])) == 3, drawn
    if width >= 1024:
        assert len(set(areas)) == 4, drawn


@pytest.mark.parametrize("width", [320, 400, 1024])
@pytest.mark.parametrize("path", [BRANDING, SETTLED])
def test_parish_logo_frames_are_sized_per_logo(page, component_origin, path, width):
    """Each current logo's frame is that size's box (its own height, inside
    the page), and a saved logo is drawn within its size's bound, never
    enlarged."""
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(component_origin + path)
    eventually(
        page,
        """() => [...document.querySelectorAll('#branding-current img')]
          .every((image) => image.complete)""",
    )
    frames = page.evaluate(SIZED, "#branding-current .logo-frame")
    assert_sized_boxes(frames)
    for frame in frames:
        if frame["image"] is None:
            continue
        bound = BOUNDS.get(frame["size"], (1024, 1024))
        assert frame["image"][0] <= bound[0] + 0.5, frame
        assert frame["image"][1] <= bound[1] + 0.5, frame
