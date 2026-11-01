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
    monkeypatch.setattr(public_pages, "current_campaign_values", lambda: None)
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


# Each Privacy policy paragraph that follows the configuration, by the fact
# that turns it on (architecture spec, "Configured content").
CENSUS = "correct its household's information, add a household member"
MINISTRY = "choose ministries to join or leave."
LEADERS = "Ministry leaders see, for the ministries they lead only"
FINANCIAL = "each Family's pledges and gifts (amount, date and fund)"
PLEDGE = "make a pledge for the coming year"
INTENTION = "A pledge records an intention only."
ADDITIONAL = "write a note to the parish."
GENERAL = "this page describes everything the site can collect"
ATTEND_ONLY = "cannot attend Mass or prayer services because of physical limitations"
WRITES_CONFIRMED = "The site copies approved changes into ParishSoft only after"
WRITES_NEVER = "The site does not change ParishSoft"
WRITES_MAYBE = "Copying approved changes into ParishSoft is off unless"


def _privacy(client, monkeypatch, values, *, publication=False, url="/privacy/"):
    """Render a page for a parish whose current campaign has ``values``."""
    parish = SimpleNamespace(
        name="St. Example Parish", website="", phone="", configuration_id="c"
    )
    monkeypatch.setattr(public_pages, "display_parish", lambda request: parish)
    monkeypatch.setattr(public_pages, "current_campaign_values", lambda: values)
    monkeypatch.setattr(
        "parishkit.stewardship.responses.page_content.reply_to", lambda c: ""
    )
    monkeypatch.setattr(
        "django.conf.settings.STEWARDSHIP_PARISHSOFT_PUBLICATION", publication
    )
    return _html(client.get(url))


@pytest.mark.parametrize(
    ("modules", "additional", "present", "absent"),
    [
        (
            ["census", "ministry", "financial"],
            True,
            [CENSUS, MINISTRY, LEADERS, FINANCIAL, PLEDGE, INTENTION, ADDITIONAL],
            [GENERAL, ATTEND_ONLY],
        ),
        (
            ["census"],
            False,
            [CENSUS, ATTEND_ONLY],
            [MINISTRY, LEADERS, FINANCIAL, PLEDGE, INTENTION, ADDITIONAL, GENERAL],
        ),
        (
            ["ministry"],
            False,
            [MINISTRY, LEADERS],
            [CENSUS, FINANCIAL, PLEDGE, INTENTION, ADDITIONAL, ATTEND_ONLY],
        ),
        (
            ["financial"],
            True,
            [FINANCIAL, PLEDGE, INTENTION, ADDITIONAL, ATTEND_ONLY],
            [CENSUS, MINISTRY, LEADERS],
        ),
    ],
)
def test_the_privacy_policy_follows_the_campaigns_modules(
    client, monkeypatch, modules, additional, present, absent
):
    """Each step, read and audience shows only when the campaign uses it."""
    values = {"modules": modules, "additional_information": additional}
    html = _privacy(client, monkeypatch, values)
    for text in present:
        assert text in html, text
    for text in absent:
        assert text not in html, text
    # The rosters and the household records are read for every campaign.
    assert "the parish's ministries and who serves in them" in html
    assert "No other ParishSoft family or member data is used." in html
    assert "It does not copy Social Security numbers" not in html


@pytest.mark.parametrize(
    ("modules", "publication", "family", "service"),
    [
        (["census"], True, WRITES_CONFIRMED, "writes back only the changes"),
        (["census"], False, WRITES_NEVER, "never writes to it"),
        (["ministry"], True, None, "never writes to it"),
    ],
)
def test_the_privacy_policy_follows_parishsoft_publication(
    client, monkeypatch, modules, publication, family, service
):
    """Write-back is described as on only when census changes can publish."""
    values = {"modules": modules, "additional_information": False}
    html = _privacy(client, monkeypatch, values, publication=publication)
    for text in (WRITES_CONFIRMED, WRITES_NEVER, WRITES_MAYBE):
        assert (text in html) is (text == family), text
    assert service in html


@pytest.mark.parametrize("parish", [False, True])
def test_without_a_current_campaign_the_privacy_policy_is_general(
    client, monkeypatch, parish
):
    """Before setup, or with no campaign: everything, and a note saying so."""
    if parish:
        html = _privacy(client, monkeypatch, None, publication=True)
    else:
        html = _html(client.get("/privacy/"))
    for text in (GENERAL, CENSUS, MINISTRY, LEADERS, FINANCIAL, PLEDGE, ADDITIONAL):
        assert text in html, text
    assert WRITES_MAYBE in html
    assert "depending on what the parish asks this year" in html


@pytest.mark.parametrize("modules", [["census"], ["ministry"], ["financial"], None])
def test_the_first_parishsoft_links_to_its_website(client, monkeypatch, modules):
    """Only the page's first "ParishSoft" is a link, in every variation."""
    values = modules and {"modules": modules, "additional_information": False}
    html = _privacy(client, monkeypatch, values)
    body = html[html.index("<h1>") :]
    link = '<a href="https://www.parishsoft.com/">ParishSoft</a>'
    assert body.index("ParishSoft") == body.index(link) + link.index("ParishSoft")
    assert body.count("parishsoft.com") == 1


@pytest.mark.parametrize(("financial", "shown"), [(True, True), (False, False)])
def test_the_terms_show_pledges_only_with_a_pledge(
    client, monkeypatch, financial, shown
):
    """The Terms' "Pledges" section follows the financial module."""
    modules = ["census", "financial"] if financial else ["census"]
    values = {"modules": modules, "additional_information": False}
    html = _privacy(client, monkeypatch, values, url="/terms/")
    assert ("<h2>Pledges</h2>" in html) is shown


def test_the_privacy_policy_leaves_out_the_suspense_flag(client):
    """The Administrator's decision (#2360): no mention of ParishSoft's suspense."""
    assert "suspense" not in _html(client.get("/privacy/")).lower()


@pytest.mark.parametrize("url", ["/privacy/", "/about/"])
def test_the_pages_name_and_link_the_software(client, url):
    """The Administrator's request (#2360): ParishKit, linked to its source.

    On the Privacy policy the note sits near the bottom, before the contact
    block and the "Last updated" line.
    """
    html = _html(client.get(url))
    link = '<a href="https://github.com/epiphany40223/parishkit">ParishKit</a>'
    assert html.count(link) == 1
    assert "the site sends no information to the ParishKit project." in html
    if url == "/privacy/":
        note = html.index("<h2>About this software</h2>")
        assert note < html.index("<h2>Contact the parish</h2>")
        assert note > html.index("<h2>Correcting your information</h2>")
