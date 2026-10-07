"""Weekly compilation is deterministic, complete and inert without database I/O."""

import re
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

import pytest

from parishkit.stewardship.reports.links import report_url
from parishkit.stewardship.reports.weekly_digest import (
    WeeklyCorrection,
    WeeklyDigestDocument,
    WeeklyInformation,
    excerpt,
    render_weekly_digest,
    shorten,
)
from parishkit.stewardship.web import dates
from parishkit.stewardship.web.weekly_digest_content import validate_weekly_body

INSTANT = datetime(2026, 11, 2, 5, 15, tzinfo=UTC)


def document():
    """Include both terminal dispositions without admitting their former text."""
    return WeeklyDigestDocument(
        snapshot_id=UUID(int=1),
        campaign_id=UUID(int=2),
        parish_name="Example Parish",
        campaign_name="Annual campaign",
        campaign_timezone="America/New_York",
        observed_at=INSTANT,
        information=(
            WeeklyInformation(UUID(int=3), 1234, "A & B", INSTANT, "Please call us."),
        ),
        corrections=(
            WeeklyCorrection(UUID(int=4), 56, "C family", INSTANT, "superseded"),
            WeeklyCorrection(UUID(int=5), 78, "D family", INSTANT, "withdrawn"),
        ),
    )


def render(value=None):
    """Use a synthetic public origin without loading runtime or provider settings."""
    return render_weekly_digest(
        document() if value is None else value,
        public_origin="https://campaign.example.org",
    )


def test_weekly_retains_identities_corrections_and_explicit_timezone():
    """Every selected row has a private detail link and a clear local timestamp."""
    result = render()
    for body in (result.html, result.text):
        for required in (
            "Family DUID 1234",
            "Please call us.",
            "Nov 2, 2026 12:15 AM",
            "Superseded:",
            "Withdrawn:",
        ):
            assert required in body
        for row in (*document().information, *document().corrections):
            assert document().report_path + f"items/{row.item_id}/" in body
    for body in (result.html, result.text):
        assert "1 new actionable request" in body
        assert "2 corrections to previously reported requests" in body
    assert "staff login required" in result.text
    # Desktop layout (#720): each section's heading carries its count, each
    # request is one wide row, and the sign-in note is small print last.
    html = result.html
    assert html.index("1 new actionable request") < html.index("Family DUID 1234")
    assert html.index("Family DUID 1234") < html.index("Open protected report")
    assert '<td width="64%"' in html
    assert "A &amp; B" in result.html and "A & B" in result.text
    assert not hasattr(document().corrections[0], "text")
    assert result == render()
    assert "Please call us" not in repr(result)
    assert "Please call us" not in repr(document())
    assert "Please call us" not in repr(document().information[0])


def test_weekly_quotes_are_escaped_not_template_interpreted():
    """Untrusted Family text remains inert data even with quotes or placeholders."""
    value = document()
    item = replace(
        value.information[0],
        family_name='O\'Brien "Family" <img src="evil">',
        text='<script>alert("private")</script> {{ family_code }}',
    )
    result = render(replace(value, information=(item,)))
    assert "<script>" not in result.html and '<img src="evil">' not in result.html
    assert "&lt;script&gt;" in result.html and "{{ family_code }}" in result.html
    assert item.text in result.text
    validate_weekly_body(result.html, result.text)


def test_only_excerpt_is_trimmed_and_manual_label_is_explicit():
    """Displayed excerpts do not mutate retained text or hide manual provenance."""
    value = document()
    text = "private " * 100
    item = replace(value.information[0], text=text)
    result = render(replace(value, information=(item,), manual=True))
    assert len(excerpt(text)) <= 240 and excerpt(text).endswith("private…")
    assert item.text == text
    assert result.subject.startswith("Manual weekly")
    assert excerpt(text) in result.text
    assert "1 current actionable request" in result.text
    assert excerpt("short\n\ttext") == "short text"


@pytest.mark.parametrize("whitespace", ["\u00a0", "\r", "\r\n"])
def test_imported_label_whitespace_compiles_without_changing_retained_identity(
    whitespace,
):
    """Source/configuration names must survive our strict compiled-mail boundary."""
    value = document()
    name = f"Example{whitespace}Family"
    item = replace(value.information[0], family_name=name)
    result = render(
        replace(
            value,
            information=(item,),
            parish_name=f"Example{whitespace}Parish",
            campaign_name=f"Annual{whitespace}Campaign",
        )
    )
    assert item.family_name == name
    assert "Example Family" in result.html
    # The subject names the parish and campaign (#720); the body does not.
    assert "Parish" not in result.html and "Campaign" not in result.html
    validate_weekly_body(result.html, result.text)


def test_empty_interval_requires_durable_empty_outcome_not_email():
    """An empty interval must be recorded without manufacturing a mail message."""
    value = replace(document(), information=(), corrections=())
    assert value.empty
    with pytest.raises(ValueError, match="must not create an email"):
        render(value)
    corrections_only = replace(document(), information=())
    assert not corrections_only.empty
    assert "Withdrawn:" in render(corrections_only).text


def test_reference_family_volume_has_every_identity_and_usa_counts():
    """The reference parish population fits without silently omitting entries."""
    value = document()
    rows = tuple(
        WeeklyInformation(
            UUID(int=100 + index),
            index + 1,
            f"Family {index}",
            INSTANT,
            "Please contact us. " * 30,
        )
        for index in range(5000)
    )
    result = render(replace(value, information=rows, corrections=()))
    assert "5,000 new actionable requests" in result.text
    assert "5000. Family 4999" in result.text
    # Every row is shortened, so each has its name link and "Read the full request".
    assert result.html.count("/items/") == 10000
    assert result.text.count("/items/") == 5000
    assert "Family DUID 5000" in result.text
    validate_weekly_body(result.html, result.text)


@pytest.mark.parametrize(
    "changes",
    [
        {"snapshot_id": "wrong"},
        {"campaign_id": None},
        {"manual": 1},
        {"observed_at": datetime(2026, 11, 2)},
        {"parish_name": ""},
        {"campaign_timezone": "Not/A/Zone"},
        {"campaign_timezone": None},
        {"information": []},
        {"information": (None,)},
        {"corrections": (document().information[0],)},
        {"corrections": tuple(reversed(document().corrections))},
        {"information": (document().information[0], document().information[0])},
        {"observed_at": INSTANT - timedelta(seconds=1)},
        {"corrections": (replace(document().corrections[0], item_id=UUID(int=3)),)},
    ],
)
def test_document_rejects_incoherent_input(changes):
    """Canonical ordered typed observations cannot contain duplicate/future rows."""
    with pytest.raises(ValueError):
        replace(document(), **changes)


@pytest.mark.parametrize(
    "changes",
    [
        {"item_id": "wrong"},
        {"family_duid": True},
        {"family_duid": 0},
        {"family_name": ""},
        {"family_name": "x" * 513},
        {"submitted_at": "2026-11-02"},
        {"text": " "},
        {"text": "bad\x00text"},
        {"text": "\ud800"},
    ],
)
def test_information_rejects_invalid_values(changes):
    """Bad identifiers, dates, Unicode and unbounded identities fail closed."""
    with pytest.raises(ValueError):
        replace(document().information[0], **changes)


def test_corrections_reject_actionable_and_renderer_rejects_untyped_input():
    """Only terminal dispositions and explicit immutable documents are accepted."""
    with pytest.raises(ValueError):
        replace(document().corrections[0], disposition="current_actionable")
    with pytest.raises(TypeError):
        render_weekly_digest(None, public_origin="https://campaign.example.org")


@pytest.mark.parametrize(
    "html",
    [
        "",
        "<script>alert(1)</script>",
        '<img src="https://host/image">',
        '<a href="javascript:alert(1)">Bad</a>',
        '<p onclick="steal()">Bad</p>',
        '<a href="cid:chart" rel="noopener noreferrer">Bad</a>',
        "<p>Private</p><!-- hidden -->",
        "<p>bad\x00text</p>",
        "\ud800",
    ],
)
def test_weekly_body_rejects_active_or_noncanonical_html(html):
    """The private no-image boundary rejects unsafe markup instead of repairing it."""
    with pytest.raises(ValueError, match="Invalid compiled weekly"):
        validate_weekly_body(html, "safe text")


def test_weekly_body_limit_fails_without_silently_dropping_items(monkeypatch):
    """Oversized compilation cannot masquerade as a complete shorter report."""
    from parishkit.stewardship.web import weekly_digest_content

    monkeypatch.setattr(weekly_digest_content, "MAX_WEEKLY_BODY_BYTES", 100)
    with pytest.raises(ValueError, match="Invalid compiled weekly"):
        render()


@pytest.mark.parametrize(
    "path",
    [
        None,
        "//evil",
        "/",
        "/admin/reports/../x",
        "/admin/reports/a?x=1",
        "/admin/reports/%2e%2e/",
        "/admin/reports/a#fragment",
        "/admin/reports/a\n",
    ],
)
def test_report_link_rejects_noncompiler_routes(path):
    """An allowed origin cannot launder an external or caller-controlled route."""
    with pytest.raises(ValueError):
        report_url("https://campaign.example.org", path)


def test_pinned_date_format_overrides_the_ambient_style():
    """The snapshot's style wins over a worker's active one; None means default."""
    local = INSTANT.astimezone(ZoneInfo("America/New_York"))
    with dates.using("iso"):
        pinned = render(replace(document(), date_format="eu_dot"))
        unset = render()
    assert pinned.subject.endswith(dates.format_date(local.date(), "eu_dot"))
    assert dates.format_local(local, "eu_dot", compact=True) in pinned.text
    assert unset.subject.endswith(dates.format_date(local.date(), "us_long"))
    assert (
        dates.format_local(local, "iso", compact=True) not in pinned.text + unset.text
    )


def test_date_format_must_be_text_or_unset():
    """A non-string style is rejected before anything is rendered."""
    with pytest.raises(ValueError, match="typed report identity"):
        replace(document(), date_format=5)


@pytest.mark.parametrize(
    "markup",
    [
        '<p style="display:none">Hidden</p>',
        '<td style="color:red">x</td>',
        '<table role="grid"><tbody><tr><td>x</td></tr></tbody></table>',
        '<table><tbody><tr><td bgcolor="#ff0000">x</td></tr></tbody></table>',
        '<table><tbody><tr><td width="120%">x</td></tr></tbody></table>',
        '<img src="https://example.org/x.png" alt="x">',
    ],
)
def test_weekly_layout_admits_only_the_closed_report_markup(markup):
    """Styles, colours and table attributes outside the closed set are rejected."""
    result = render()
    with pytest.raises(ValueError, match="weekly"):
        validate_weekly_body(result.html + markup, result.text)


def test_requests_are_numbered_in_order_and_corrections_are_not():
    """Readers can refer to "request 2"; the numbers follow the email's order."""
    value = document()
    rows = tuple(
        WeeklyInformation(UUID(int=10 + index), 500 + index, name, INSTANT, "Hi.")
        for index, name in enumerate(("Ames", "Brook", "Cole"))
    )
    result = render(replace(value, information=rows))
    for body in (result.html, result.text):
        positions = [body.index(name) for name in ("Ames", "Brook", "Cole")]
        assert positions == sorted(positions)
    assert "1. Ames" in result.text and "3. Cole" in result.text
    numbers = re.findall(r'<td width="4%"[^>]*>([^<]*)</td>', result.html)
    assert numbers == ["1.", "2.", "3.", "", ""]


@pytest.mark.parametrize(
    ("text", "shortened"),
    [
        ("Please call us.", False),
        ("word " * 47 + "end", False),
        ("Please call us about the choir rehearsal schedule. " * 10, True),
        ("x" * 400, True),
    ],
)
def test_ellipsis_and_full_request_link_only_when_shortened(text, shortened):
    """A shortened excerpt ends with an ellipsis at a word boundary and a link."""
    value = document()
    item = replace(value.information[0], text=text)
    result = render(replace(value, information=(item,), corrections=()))
    shown, cut = shorten(text)
    assert cut is shortened
    assert shown.endswith("…") is shortened
    assert ("Read the full request" in result.html) is shortened
    assert ("Read the full request: " in result.text) is shortened
    if shortened:
        link = f'<a href="https://campaign.example.org{value.report_path}items/'
        assert result.html.count(link) == 2  # the name and "Read the full request"
        if " " in text.strip():
            assert " ".join(text.split()).startswith(shown[:-1])
            assert " ".join(text.split())[len(shown) - 1] == " "
    else:
        assert shown == " ".join(text.split())


def test_each_header_fact_appears_once():
    """The body opens straight into the numbered requests (#720).

    Parish, campaign, report and capture date are left to the subject, so
    the body names none of them, says nothing twice and has no small print.
    """
    result = render()
    visible = re.sub(r"<[^>]+>", " ", result.html)
    for body in (visible, result.text):
        assert "Example Parish" not in body and "Annual campaign" not in body
        assert "EST" not in body and "America/New_York" not in body
        assert "information digest" not in body and "Captured" not in body
        assert "sign-in" not in body and "capture time" not in body
        assert body.lstrip().startswith("1 new actionable request")
    assert result.subject == "Weekly information digest — November 2, 2026"
