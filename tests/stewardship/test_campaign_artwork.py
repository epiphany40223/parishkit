"""Campaign artwork (#248): normalization, email banner markup and placement."""

import io

import pytest
from PIL import Image

from parishkit.config import ConfigError
from parishkit.stewardship.campaigns.configuration import (
    ARTWORK_SLOTS,
    campaign_values,
)
from parishkit.stewardship.web.content import email_banner, prepare_artwork

from .campaign_factory import campaign
from .test_family_mail_content import identity, render

BANNER = {
    "url": "https://parish.example.org/branding/abc.png",
    "width": 1024,
    "height": 217,
}


def upload(width, height, image_format="PNG"):
    """An in-memory upload the normalizer must decode and re-encode."""
    stream = io.BytesIO()
    Image.new("RGB", (width, height), "red").save(stream, format=image_format)
    stream.seek(0)
    return stream


@pytest.mark.parametrize(
    "label,size,expected",
    [
        ("banner", (1625, 345), (1024, 217)),
        ("banner", (500, 100), (500, 100)),
        ("section", (600, 600), (256, 256)),
        ("section", (234, 248), (234, 248)),
        ("section", (500, 260), (256, 133)),
    ],
)
def test_artwork_is_fitted_and_reencoded_as_png(label, size, expected):
    """Large uploads shrink to their kind's bound; small ones keep their size."""
    graphics = prepare_artwork(upload(*size, image_format="JPEG"), label)
    assert set(graphics) == {label}
    graphic = graphics[label]
    assert (graphic.width, graphic.height) == expected
    assert graphic.content_type == "image/png"
    assert graphic.data.startswith(b"\x89PNG\r\n\x1a\n")


def test_artwork_refuses_unknown_kinds_and_non_images():
    """Only banner and section images exist; bytes must decode as an image."""
    with pytest.raises(ValueError):
        prepare_artwork(upload(10, 10), "large")
    with pytest.raises(ValueError):
        prepare_artwork(io.BytesIO(b"not an image"), "banner")


def test_email_banner_scales_to_email_width_and_escapes_alt():
    """The banner is at most 600px wide in email, keeps its ratio, and is inert."""
    markup = email_banner(BANNER, 'Stewardship "2027" <renewal>')
    assert markup == (
        '<p><img src="https://parish.example.org/branding/abc.png" '
        'alt="Stewardship &quot;2027&quot; &lt;renewal&gt;" '
        'width="600" height="127" style="display:block;width:100%;'
        'max-width:600px;height:auto;border:0"></p>'
    )
    assert email_banner(None, "x") == ""
    assert 'width="300" height="60"' in email_banner(
        BANNER | {"width": 300, "height": 60}, "x"
    )


@pytest.mark.parametrize("url", ["http://parish.example.org/a.png", "/branding/a.png"])
def test_email_banner_needs_an_absolute_https_address(url):
    """Email images must load from the public HTTPS origin; otherwise none shows."""
    assert email_banner(BANNER | {"url": url}, "x") == ""


@pytest.mark.parametrize(
    "label,size",
    [("banner", (300, 200)), ("section", (600, 200)), ("section", (100, 300))],
)
def test_artwork_refuses_the_wrong_shape(label, size):
    """A banner must be wide; an icon roughly square."""
    with pytest.raises(ValueError):
        prepare_artwork(upload(*size), label)


@pytest.mark.parametrize("testing", [False, True])
def test_banner_leads_the_rendered_invitation(testing):
    """The banner comes before the parish text, after the Testing notice, HTML only."""
    banner = email_banner(BANNER, "Renewal")
    rendered = render(identity(testing=testing), banner=banner)
    assert banner in rendered.html
    assert rendered.html.index(banner) < rendered.html.index("Respond")
    if testing:
        assert rendered.html.index("TEST") < rendered.html.index(banner)
    assert "branding" not in rendered.text
    assert banner not in render(identity(testing=testing)).html


@pytest.mark.parametrize(
    "artwork,valid",
    [
        ({"images": {"banner": "00000000-0000-4000-8000-000000000020"}}, True),
        (
            {
                "images": {
                    slot: "00000000-0000-4000-8000-00000000002" + str(index)
                    for index, slot in enumerate(ARTWORK_SLOTS)
                },
                "hide_banner": ["initial", "confirmation"],
            },
            True,
        ),
        ({"hide_banner": ["reminder"]}, True),
        ({}, False),
        ({"images": {}}, False),
        ({"hide_banner": []}, False),
        ({"hide_banner": ["confirmation", "initial"]}, False),
        ({"hide_banner": ["weekly_digest"]}, False),
        ({"images": {"logo": "00000000-0000-4000-8000-000000000020"}}, False),
        ({"images": {"banner": "not-a-uuid"}}, False),
        ({"images": {"banner": "00000000-0000-4000-8000-000000000020"}, "x": 1}, False),
        (["banner"], False),
    ],
)
def test_campaign_artwork_schema(artwork, valid):
    """Artwork is optional campaign data: never empty, known slots, UUIDs."""
    values = campaign()["values"] | {"artwork": artwork}
    if valid:
        campaign_values(values)
    else:
        with pytest.raises(ConfigError):
            campaign_values(values)


def test_campaign_without_artwork_is_valid():
    """A new campaign has no artwork key at all."""
    values = campaign()["values"]
    assert "artwork" not in values
    campaign_values(values)


ORIGIN = "https://parish.example.org"
ASSET = "https://parish.example.org/branding/0f0e0d0c-0b0a-4908-8706-050403020100.png"


def delivery(html, origin=ORIGIN):
    """Build one Family delivery message, as dispatch does."""
    from uuid import uuid4

    from parishkit.stewardship.family_delivery import FamilyDeliveryMail

    return FamilyDeliveryMail(
        uuid4(),
        "parish@example.org",
        "parish@example.org",
        ("family@example.org",),
        "Renewal",
        html,
        "Renewal",
        banner_origin=origin,
    )


def banner(url=ASSET, width=1024, height=217):
    """The server-built banner markup for one image."""
    return email_banner({"url": url, "width": width, "height": height}, "Renewal")


BODY = "<p>Renewal</p>"
TEST = "<h2>TEST</h2><p>TEST — sent to test@example.org instead of Family.</p>"


@pytest.mark.parametrize("html", [banner() + BODY, TEST + banner() + BODY, BODY])
def test_delivery_admits_one_leading_server_banner(html):
    """A leading banner, or one right after the Testing notice, is delivered."""
    mail = delivery(html)
    assert mail.html == html
    assert type(mail).from_payload(mail.payload()) == mail


@pytest.mark.parametrize(
    "html,origin",
    [
        (BODY + banner(), ORIGIN),
        ("<p>Hello</p>" + banner() + BODY, ORIGIN),
        (
            banner(url=ASSET.replace("parish.example.org", "tracker.example.net"))
            + BODY,
            ORIGIN,
        ),
        (banner() + BODY, "https://other.example.org"),
        (banner() + BODY, ""),
        ('<a href="https://x.example.org">' + banner() + "</a>" + BODY, ORIGIN),
        (banner(width=1, height=1) + BODY, ORIGIN),
        (banner(width=300, height=200) + BODY, ORIGIN),
        (banner() + banner() + BODY, ORIGIN),
    ],
)
def test_delivery_refuses_any_other_image(html, origin):
    """Trailing, embedded, foreign, linked, tiny, square or repeated images fail."""
    with pytest.raises(ValueError):
        delivery(html, origin)
