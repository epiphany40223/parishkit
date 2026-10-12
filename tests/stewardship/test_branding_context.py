"""The header's menu logo size (#1062): drawn within 256 x 64, never enlarged."""

import pytest
from django.template.loader import render_to_string

from parishkit.stewardship.accounts.branding_context import header_logo_size


@pytest.mark.parametrize(
    ("stored", "drawn"),
    [
        # The square 128-pixel menu variant fills the header's 4rem height.
        ((128, 128), (64, 64)),
        # A 4:1 wordmark at the widest stored menu size.
        ((512, 128), (256, 64)),
        # A wordmark stored before slice 2 (fitted within 128 x 128).
        ((128, 32), (128, 32)),
        # Small files are never enlarged.
        ((40, 20), (40, 20)),
        ((150, 80), (120, 64)),
        ((300, 128), (150, 64)),
    ],
)
def test_header_logo_size_fits_without_enlarging(stored, drawn):
    """The drawn size keeps the stored shape within 256 x 64."""
    assert header_logo_size(*stored) == drawn


def test_the_header_gives_the_logo_its_drawn_size():
    """The menu image carries its drawn width and height, so the browser
    reserves its box before the file arrives; without them it has none."""
    branding = {"name": "Saint Example Parish", "menu": "/branding/menu.png"}
    sized = render_to_string(
        "stewardship/family-login.html",
        {"parish_branding": branding | {"menu_width": 256, "menu_height": 64}},
    )
    assert '<img src="/branding/menu.png" alt="" width="256" height="64">' in sized
    unsized = render_to_string(
        "stewardship/family-login.html", {"parish_branding": branding}
    )
    assert '<img src="/branding/menu.png" alt="">' in unsized
