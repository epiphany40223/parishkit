"""Campaign images: which slots can take an upload for a campaign (#563)."""

from types import SimpleNamespace as Value

import pytest

from parishkit.stewardship.accounts.artwork_views import (
    UNAVAILABLE,
    ArtworkForm,
    unavailable,
)
from parishkit.stewardship.campaigns.configuration import ARTWORK_SLOTS
from parishkit.stewardship.web.content import MAX_IMAGE_BYTES


def campaign(*modules):
    """A campaign whose configuration enables ``modules``."""
    return Value(active_configuration=Value(values={"modules": list(modules)}))


@pytest.mark.parametrize(
    ("modules", "closed"),
    [
        (("census",), {"financial"}),
        (("ministry",), {"financial"}),
        (("financial",), {"member"}),
        (("census", "financial"), set()),
        (("census", "financial", "ministry"), set()),
    ],
)
def test_only_slots_whose_family_page_is_shown_take_an_upload(modules, closed):
    """The Financial icon needs Financial stewardship; the Member pages icon
    needs Census or Ministries; the banner, welcome and closing slots are
    always available. An unavailable slot carries its plain-language reason."""
    reasons = {slot: unavailable(campaign(*modules), slot) for slot in ARTWORK_SLOTS}
    assert {slot for slot, reason in reasons.items() if reason} == closed
    for slot in closed:
        assert reasons[slot] == UNAVAILABLE[slot]


def test_the_image_field_is_required_and_bounded_in_the_browser():
    """The upload field is required (the complete gate holds Upload until an
    image is chosen) and names the server's size bound for the live check."""
    html = str(ArtworkForm(auto_id="id_banner_%s")["image"])
    assert " required" in html
    assert f'data-max-bytes="{MAX_IMAGE_BYTES}"' in html
    assert 'data-max-bytes-message="Choose an image no larger than 5 MB."' in html
