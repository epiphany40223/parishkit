"""The optional parish online giving URL: schema, forms, patches and placeholders."""

from uuid import uuid4

import pytest

from parishkit.config import ConfigError
from parishkit.stewardship.accounts.content_forms import sample_render
from parishkit.stewardship.accounts.parish_views import ParishForm
from parishkit.stewardship.accounts.request_patch import build_candidate
from parishkit.stewardship.accounts.setup_forms import SetupParishForm, validate_values
from parishkit.stewardship.jobs.campaign_mail_values import campaign_values
from parishkit.stewardship.responses.page_content import public_values
from parishkit.stewardship.web.content import (
    ADMIN_DIGEST_PLACEHOLDERS,
    PLACEHOLDERS,
    SHARE_PLACEHOLDERS,
)

from .campaign_factory import campaign
from .configuration_factory import configuration_document, configuration_version
from .test_request_patch import parish_patch

GIVING = "https://give.example.org/parish"


def profile(**changes):
    """Complete parish form data, optionally with a giving URL."""
    return {
        "name": "Sample Parish",
        "website": "https://parish.example.org/",
        "timezone": "America/New_York",
        "phone": "+12125551234",
        "base_digest": "a" * 64,
        **changes,
    }


@pytest.mark.parametrize("value", [GIVING, "HTTPS://give.example.org/"])
def test_schema_accepts_an_optional_https_giving_url(value):
    """The key is optional; when present it is a credential-free HTTPS URL."""
    document = configuration_document()
    configuration_version(document)
    document["sections"]["parish"][0]["values"]["online_giving_url"] = value
    configuration_version(document)


@pytest.mark.parametrize(
    "value",
    [None, "", "http://give.example.org/", "https://u:p@give.example.org/", 7],
)
def test_schema_rejects_unsafe_or_empty_giving_urls(value):
    """An empty value is spelled by omitting the key, never by null or ''."""
    document = configuration_document()
    document["sections"]["parish"][0]["values"]["online_giving_url"] = value
    with pytest.raises(ConfigError):
        configuration_version(document)


@pytest.mark.parametrize("form_type", [ParishForm, SetupParishForm])
def test_forms_accept_blank_or_https_and_reject_http(form_type):
    """Both profile forms share the field and its HTTPS-only rule."""
    blank = form_type(profile())
    assert blank.is_valid(), blank.errors
    assert blank.cleaned_data["online_giving_url"] == ""
    assert form_type(profile(online_giving_url=GIVING)).is_valid()
    # Giving providers need query parameters, e.g. "?tab=home".
    assert form_type(
        profile(online_giving_url="https://give.example.org/app?tab=home")
    ).is_valid()
    for bad in ("http://give.example.org/", "https://u:p@give.example.org/"):
        assert not form_type(profile(online_giving_url=bad)).is_valid()


def test_setup_parish_step_stores_the_key_and_tolerates_older_drafts():
    """New saves always carry the key; an older four-field draft means unset."""
    values = profile()
    del values["base_digest"]
    assert validate_values("parish", values | {"online_giving_url": GIVING})[
        "online_giving_url"
    ] == (GIVING)
    assert validate_values("parish", values)["online_giving_url"] == ""


def test_patch_sets_and_clears_the_giving_url():
    """Clearing removes the key rather than storing an invalid null."""
    base = configuration_version()
    added = build_candidate(
        base, parish_patch(base, online_giving_url=GIVING), candidate_id=uuid4()
    ).candidate
    parish = added.document()["sections"]["parish"][0]["values"]
    assert parish["online_giving_url"] == GIVING
    cleared = build_candidate(
        added, parish_patch(added, online_giving_url=None), candidate_id=uuid4()
    ).candidate
    assert (
        "online_giving_url" not in cleared.document()["sections"]["parish"][0]["values"]
    )


def test_placeholder_falls_back_to_the_website():
    """An unset giving page never renders an empty link target."""
    assert "online_giving_url" in PLACEHOLDERS & ADMIN_DIGEST_PLACEHOLDERS
    assert "online_giving_url" not in SHARE_PLACEHOLDERS
    parish = {
        "name": "P",
        "website": "https://parish.example.org/",
        "phone": "+12125551234",
    }
    values = campaign()["values"]
    for renderer in (
        lambda item: campaign_values(parish=item, campaign=values),
        lambda item: public_values(item, values),
    ):
        assert renderer(parish)["online_giving_url"] == parish["website"]
        configured = parish | {"online_giving_url": GIVING}
        assert renderer(configured)["online_giving_url"] == GIVING
    link = {
        "subject": None,
        "html": '<p><a href="{{ online_giving_url }}">Give</a></p>',
        "text": "{{ online_giving_url }}",
    }
    sample = sample_render(
        link, parish=parish | {"online_giving_url": GIVING}, campaign=values
    )
    assert f'href="{GIVING}"' in sample["html"] and sample["text"] == GIVING
