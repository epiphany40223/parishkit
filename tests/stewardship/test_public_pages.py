"""The public About, Privacy policy and Terms of use pages and the site footer.

#2354; architecture spec "Public information pages" and ui-conventions "Site
footer". The scaffold has no runtime or database, which is exactly the
before-setup state: the pages must still render, naming "the parish".
"""

import re
from types import SimpleNamespace

import pytest
from django.template.loader import render_to_string
from django.urls import resolve, reverse

from parishkit.stewardship.accounts import public_pages

PAGES = [
    ("public:about", "/about/", "About this site"),
    ("public:privacy", "/privacy/", "Privacy policy"),
    ("public:terms", "/terms/", "Terms of use"),
]
FOOTER_LINKS = ['href="/about/"', 'href="/privacy/"', 'href="/terms/"']
LIMITED_USE = (
    "ParishKit Stewardship's use and transfer to any other app of information "
    "received from Google APIs will adhere to the "
    '<a href="https://developers.google.com/terms/api-services-user-data-policy">'
    "Google API Services User Data Policy</a>, including the Limited Use "
    "requirements."
)


def _html(response):
    """The decoded body with HTML-escaped apostrophes made literal."""
    return response.content.decode().replace("&#x27;", "'")


@pytest.mark.parametrize(("name", "url", "heading"), PAGES)
def test_each_page_is_public_and_sets_no_cookie(client, name, url, heading):
    """No sign-in, no cookie, safe methods only, under the site's own CSP."""
    assert reverse(name) == url
    assert resolve(url).view_name == name
    response = client.get(url)
    assert response.status_code == 200
    assert f"<h1>{heading}</h1>" in _html(response)
    assert not response.cookies
    assert "default-src 'none'" in response["Content-Security-Policy"]
    assert client.head(url).status_code == 200
    assert client.post(url).status_code == 405


@pytest.mark.parametrize(("name", "url", "heading"), PAGES)
def test_each_page_loads_nothing_from_another_site(client, name, url, heading):
    """Scripts, styles, images and icons all come from this site.

    Plain links (the parish website, Google's policy) are fine: following one
    is the reader's choice, and nothing loads until they do.
    """
    html = _html(client.get(url))
    resources = re.findall(r"<(?:script|img|link|iframe|source)\b[^>]*>", html)
    assert resources
    for tag in resources:
        for value in re.findall(r'\b(?:src|href|srcset)="([^"]*)"', tag):
            assert value.startswith("/") and not value.startswith("//"), tag


@pytest.mark.parametrize(("name", "url", "heading"), PAGES)
def test_before_setup_the_pages_name_the_parish_generically(client, name, url, heading):
    """No parish profile yet: "the parish", no contact lines, and a date."""
    html = _html(client.get(url))
    assert "the parish" in html
    assert "mailto:" not in html
    assert (
        "Questions about this site or your information go to the parish office." in html
    )
    assert "Last updated: November 1, 2026" in html


def test_the_contact_comes_from_the_parish_profile_and_reply_to(client, monkeypatch):
    """Name, website and phone from the profile; email from the mail Reply-to."""
    parish = SimpleNamespace(
        name="St. Example Parish",
        website="https://example.org/",
        phone="+15025551234",
        configuration_id="configuration",
    )
    monkeypatch.setattr(public_pages, "display_parish", lambda request: parish)
    monkeypatch.setattr(
        "parishkit.stewardship.responses.page_content.reply_to",
        lambda configuration_id: "office@example.org",
    )
    for _name, url, _heading in PAGES:
        html = _html(client.get(url))
        assert "St. Example Parish" in html
        assert '<a href="mailto:office@example.org">office@example.org</a>' in html
        assert "+1 (502) 555-1234" in html
        assert '<a href="https://example.org/">https://example.org/</a>' in html


def test_the_privacy_policy_carries_googles_limited_use_statement(client):
    """Google requires this exact commitment, linked to its policy."""
    assert LIMITED_USE in _html(client.get("/privacy/"))


def test_the_privacy_policy_names_only_the_sign_in_data_the_code_requests():
    """The page says email only; the sign-in asks Google for openid and email.

    If the sign-in ever asks for more (``profile``, or #2343's addresses),
    this fails so the Privacy policy is updated in the same change.
    """
    from pathlib import Path

    source = Path(public_pages.__file__).with_name("authentication.py").read_text()
    assert 'scope=["openid", "email"]' in source


def test_every_page_in_the_site_layout_ends_with_the_footer(client):
    """Family, public and Admin layouts share base.html's footer."""
    pages = [
        _html(client.get("/about/")),
        # The before-setup Family page (503) is in the site layout too.
        _html(client.get("/")),
        render_to_string("stewardship/hosted-file-unavailable.html", {}),
        render_to_string("stewardship/admin-base.html", {}),
    ]
    for html in pages:
        footer = html[html.index('<footer class="site-footer">') :]
        assert footer.index("</footer>") < footer.index("</body>")
        assert all(link in footer for link in FOOTER_LINKS)
        assert html.count('<footer class="site-footer">') == 1
