"""Weekly compilation is deterministic, complete and inert without database I/O."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from parishkit.stewardship.reports.links import report_url
from parishkit.stewardship.reports.weekly_digest import (
    WeeklyCorrection,
    WeeklyDigestDocument,
    WeeklyInformation,
    excerpt,
    render_weekly_digest,
)
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
            "Family DUID 1,234",
            "Please call us.",
            "2026-11-02T00:15:00-05:00",
            "America/New_York",
            "EST",
            "Superseded:",
            "Withdrawn:",
            "New actionable requests: 1. Corrections: 2.",
            "staff login required",
        ):
            assert required in body
        for row in (*document().information, *document().corrections):
            assert document().report_path + f"items/{row.item_id}/" in body
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
    assert len(excerpt(text)) == 240 and excerpt(text).endswith("…")
    assert item.text == text
    assert result.subject.startswith("Manual weekly")
    assert "full details" in result.text and excerpt(text) in result.text
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
    assert "Example Parish" in result.html
    assert "Annual Campaign" in result.html
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
    assert "New actionable requests: 5,000." in result.text
    assert result.html.count("/items/") == 5000
    assert result.text.count("/items/") == 5000
    assert "Family DUID 5,000" in result.text
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
