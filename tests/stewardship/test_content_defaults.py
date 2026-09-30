"""Approved default page and email text passes every real content validator."""

from pathlib import Path
from uuid import uuid4

import pytest

from parishkit.stewardship.accounts import content_defaults
from parishkit.stewardship.accounts.content_defaults import (
    EMAILS,
    PAGES,
    default_data,
    default_initial,
    email_text,
)
from parishkit.stewardship.accounts.content_forms import ContentForm, sample_render
from parishkit.stewardship.accounts.content_schema import (
    EMAIL_SLOTS,
    PAGE_SLOTS,
    validate_content_records,
)
from parishkit.stewardship.jobs.digest_content import DigestTemplate
from parishkit.stewardship.jobs.family_mail_content import (
    FamilyMailTemplate,
    render_family_mail,
)
from parishkit.stewardship.jobs.outbox_validation import DeliveryIdentity
from parishkit.stewardship.web.content import (
    PLACEHOLDER,
    sanitize_html,
    validate_admin_digest_content,
    validate_family_email,
    validate_receipt_content,
    validate_template,
)
from parishkit.stewardship.web.presentation import campaign_year

from .campaign_factory import campaign, financial

# The retired receipt closing note (#260) has no default.
LIVE_PAGE_SLOTS = PAGE_SLOTS - {"submission_confirmation"}
SLOTS = [("page", slot) for slot in sorted(LIVE_PAGE_SLOTS)] + [
    ("email", slot) for slot in sorted(EMAIL_SLOTS)
]
PARISH = {
    "name": "Sample Parish",
    "website": "https://parish.example.org/",
    "phone": "+12125551234",
    "email": "office@parish.example.org",
    "online_giving_url": "https://give.example.org/parish",
}
CAMPAIGN = campaign(
    modules=["census", "ministry", "financial"],
    ministry_duids=[1],
    financial=financial(),
    year_label=None,
)["values"]


def saved(kind, slot):
    """Save one default through the editor form exactly as a manual save does."""
    form = ContentForm(
        default_data(kind, slot) | {"base_digest": "a" * 64}, kind=kind, slot=slot
    )
    assert form.is_valid(), form.errors
    return form.values(campaign_id=str(uuid4()), slot=slot)


def test_every_slot_has_a_default():
    """No named slot is left without approved text."""
    assert set(PAGES) == LIVE_PAGE_SLOTS and set(EMAILS) == EMAIL_SLOTS


@pytest.mark.parametrize("kind,slot", SLOTS)
def test_default_passes_every_content_validator(kind, slot):
    """Form, sanitizer, placeholder, credential, receipt and digest rules all pass."""
    value = saved(kind, slot)
    assert value["html"] == sanitize_html(value["html"])
    for part in (value["html"], value["text"]):
        validate_template(part)
    validate_content_records(
        {
            "sections": {
                "campaigns": [
                    {"id": value["campaign_id"], "values": {"content_versions": {}}}
                ],
                "content": [{"id": str(uuid4()), "values": value}],
            }
        }
    )
    if kind == "email":
        validate_template(value["subject"], subject=True)
    if slot in {"initial", "reminder"}:
        validate_family_email(value["subject"], value["html"], value["text"])
        FamilyMailTemplate(value["subject"], value["html"], value["text"])
    if slot == "confirmation":
        validate_receipt_content(value["subject"] or "", value["html"], value["text"])
    if slot in {"daily_digest", "weekly_digest", "critical_alert"}:
        # Critical alerts have no dedicated validator; they must still use only
        # public campaign facts, like the digests.
        validate_admin_digest_content(value["subject"], value["html"], value["text"])
    if slot in {"daily_digest", "weekly_digest"}:
        DigestTemplate(value["subject"], value["html"], value["text"])


@pytest.mark.parametrize("kind,slot", SLOTS)
def test_default_renders_every_placeholder_with_sample_values(kind, slot):
    """The fictional preview fills every placeholder and keeps the HTML safe."""
    rendered = sample_render(saved(kind, slot), parish=PARISH, campaign=CAMPAIGN)
    for part in rendered.values():
        assert part is None or not PLACEHOLDER.search(part)
    assert rendered["html"] == sanitize_html(rendered["html"])
    assert "Sample Parish" in rendered["html"] or slot in {
        "census",
        "member_census",
        "ministry",
        # The sign-in help is one generic sentence (#206).
        "login_help",
        # A general reflection on caring for creation; it names no parish.
        "closing",
        # One instruction line; the page's Submit button names the parish.
        "review",
    }


def test_emails_are_html_with_link_preserving_plain_text():
    """Plain text writes each link target out, so the Family link survives."""
    for slot in EMAILS:
        value = saved("email", slot)
        assert value["html"].startswith("<p>") and value["text"]
    initial = saved("email", "initial")
    assert "Begin your household’s renewal: {{ family_url }}" in initial["text"]
    assert "{{ family_code }}" in initial["text"]
    assert email_text('<p><a href="https://example.org/">Go</a></p>') == (
        "Go: https://example.org/"
    )


def test_no_default_text_or_template_advises_turning_a_phone_sideways():
    """The Family form works in portrait, so orientation advice is obsolete."""
    templates = Path(content_defaults.__file__).parent / "templates"
    texts = [repr(PAGES), repr(EMAILS)] + [
        path.read_text() for path in templates.rglob("*.html")
    ]
    for text in texts:
        assert "landscape" not in text.lower() and "sideways" not in text.lower()


def test_receipt_links_to_online_giving_without_assuming_a_pledge():
    """The real receipt renderer fills the giving link; no pledge is assumed.

    A campaign without the Financial module has no pledge, and placeholders
    have no conditionals, so the default only invites online giving (#385).
    """
    rendered = sample_render(
        saved("email", "confirmation"), parish=PARISH, campaign=CAMPAIGN
    )
    assert 'href="https://give.example.org/parish"' in rendered["html"]
    assert "pledge" not in rendered["html"] and "pledge" not in rendered["text"]
    assert "please click here: https://give.example.org/parish" in rendered["text"]
    fallback = sample_render(
        saved("email", "confirmation"),
        parish={
            key: value for key, value in PARISH.items() if key != "online_giving_url"
        },
        campaign=CAMPAIGN,
    )
    assert 'href="https://parish.example.org/"' in fallback["html"]


def test_invitation_renders_through_the_family_mail_renderer():
    """The credential-redacting Family renderer accepts the default invitation."""
    value = saved("email", "initial")
    scope = uuid4()
    rendered = render_family_mail(
        identity=DeliveryIdentity(
            scope_id=scope,
            campaign_id=scope,
            family_id=uuid4(),
            semantic_key=uuid4(),
            mode="production",
            routing="production",
            purpose="initial",
            credential_namespace="production",
        ),
        configuration_id=uuid4(),
        template_id=uuid4(),
        template=FamilyMailTemplate(value["subject"], value["html"], value["text"]),
        values={
            "parish_name": "Sample Parish",
            "parish_phone": "+12125551234",
            "parish_email": "office@parish.example.org",
            "campaign_year": "2027",
            "financial_start": "January 1, 2027",
            "family_member_names": "Alex and Sam Sample",
            "generic_family_url": "https://parish.example.org/",
        },
        sender="parish@example.org",
        intended_recipients=("family@example.org",),
    )
    assert rendered.subject == "Sample Parish 2027 Stewardship Renewal"
    assert "office@parish.example.org" in rendered.text


def test_ministry_instructions_match_the_family_form_controls():
    """The default names the exact on-page Ministry controls from family-v1.js."""
    from pathlib import Path

    import parishkit.stewardship.accounts as accounts

    script = (
        Path(accounts.__file__).parent / "static/stewardship/family-v1.js"
    ).read_text()
    for label in (
        "Continue in this ministry",
        "Stop participating in this ministry",
        "here to join more ministries",
    ):
        assert label in script and f"<strong>{label}</strong>" in PAGES["ministry"]
    # The "Current ministries" heading is no longer shown (#292).
    assert "Current ministries" not in PAGES["ministry"]


@pytest.mark.parametrize("kind,slot", SLOTS)
def test_initial_values_start_an_unsaved_editor(kind, slot):
    """Every default starts with generated plain text (links as "label: URL")."""
    initial = default_initial(kind, slot)
    assert initial["html"] == default_data(kind, slot)["html"]
    assert initial["generate_text"] is True
    assert ("subject" in initial) is (kind == "email")


def test_default_content_selects_new_page_revisions_for_a_new_campaign():
    """Every applicable slot gets a default; legacy page references select them."""
    from parishkit.stewardship.accounts.content_forms import (
        applicable_slots,
        default_content,
        matches_default,
    )

    owner = str(uuid4())
    values = campaign(additional_information=False)["values"]
    records, versions = default_content(owner, values)
    assert [
        (row["values"]["kind"], row["values"]["slot"]) for row in records
    ] == applicable_slots(values)
    assert len(records) == 10 + 6
    assert all(matches_default(row["values"]) for row in records)
    assert versions == {
        row["values"]["slot"]: row["id"]
        for row in records
        if row["values"]["slot"]
        in {"welcome", "census", "closing", "review", "thank_you"}
        and row["values"]["kind"] == "page"
    }
    validate_content_records(
        {
            "sections": {
                "campaigns": [
                    {"id": owner, "values": values | {"content_versions": versions}}
                ],
                "content": records,
            }
        }
    )
    edited = records[0]["values"] | {"html": "<p>Mine</p>", "text": "Mine"}
    assert not matches_default(edited)


@pytest.mark.parametrize("financial_module", [False, True])
def test_invitation_reads_correctly_with_or_without_a_financial_period(
    financial_module,
):
    """No sentence depends on a placeholder that can be empty (no "as of .")."""
    values = (
        campaign(modules=["financial"], financial=financial())
        if financial_module
        else campaign()
    )["values"]
    for slot in ("initial", "reminder"):
        rendered = sample_render(saved("email", slot), parish=PARISH, campaign=values)
        for part in (rendered["html"], rendered["text"]):
            assert " ." not in part and " as of" not in part
    rendered = sample_render(saved("email", "initial"), parish=PARISH, campaign=values)
    year = campaign_year(values)
    assert f"The commitment you make is for {year}." in rendered["text"]


def test_content_saved_from_a_retired_default_still_matches_the_default():
    """Improving a default must not make a parish's untouched content "Custom"."""
    from parishkit.stewardship.accounts.content_defaults import (
        RETIRED_EMAILS,
        retired_data,
    )
    from parishkit.stewardship.accounts.content_forms import (
        _DefaultForm,
        default_values,
        matches_default,
    )

    owner = str(uuid4())
    assert "pledge" in RETIRED_EMAILS["confirmation"][0].html
    (data,) = retired_data("email", "confirmation")
    form = _DefaultForm(data, kind="email", slot="confirmation")
    assert form.is_valid()
    retired = form.values(campaign_id=owner, slot="confirmation")
    current = default_values("email", "confirmation", campaign_id=owner)
    assert retired != current
    assert matches_default(retired) and matches_default(current)
    assert not matches_default(retired | {"subject": "Edited"})
    assert retired_data("page", "welcome") == []
