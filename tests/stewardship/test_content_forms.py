"""Safe samples, plain-text generation and independent mail revision references."""

from types import SimpleNamespace
from uuid import UUID

import pytest

from parishkit.stewardship.accounts.content_forms import (
    EMAIL_LABELS,
    PAGE_LABELS,
    ContentForm,
    page_slots,
    revision_patch,
    sample_render,
)
from parishkit.stewardship.accounts.content_schema import EMAIL_SLOTS, PAGE_SLOTS

from .campaign_factory import campaign, financial, schedule
from .content_factory import content, content_document


def fields(**changes):
    """Ordinary HTML form payload, never a candidate patch supplied by a browser."""
    return {
        "base_digest": "a" * 64,
        "html": "<p>Hello {{ family_name }}</p>",
        "text": "Edited plain text",
        "generate_text": "on",
        **changes,
    }


def test_slots_and_module_visibility():
    """Every schema slot has a human name; disabled module fields are not offered."""
    assert set(PAGE_LABELS) == PAGE_SLOTS and set(EMAIL_LABELS) == EMAIL_SLOTS
    slots = page_slots(campaign(additional_information=False)["values"])
    assert "census" in slots and "member_census" in slots
    assert not {"additional", "ministry", "financial"} & slots.keys()
    assert "census" not in page_slots(campaign(modules=["ministry"])["values"])
    assert "email delivery" in str(slots["submission_confirmation"])


@pytest.mark.parametrize("generate", [False, True])
def test_content_sanitized_and_plain_text_independent(generate):
    """Sanitize before storage, with explicit generated or edited plaintext."""
    form = ContentForm(
        fields(
            generate_text="on" if generate else "",
            html='<p onclick="alert(1)">Hi</p><script>steal()</script>',
        ),
        kind="page",
    )
    assert form.is_valid(), form.errors
    value = form.values(campaign_id="example", slot="welcome")
    assert value["html"] == "<p>Hi</p>" and value["subject"] is None
    assert value["text"] == ("Hi" if generate else "Edited plain text")


@pytest.mark.parametrize(
    "kind,changes",
    [
        ("page", {"html": "{{ unknown }}"}),
        ("page", {"generate_text": "", "text": "{% unsafe %}"}),
        ("email", {"subject": "Bad\nsubject"}),
        ("email", {"subject": "{{ invalid }}"}),
        ("email", {"subject": ""}),
    ],
)
def test_invalid_template_form(kind, changes):
    """A safe typed field is not enough: placeholder/header rules still apply."""
    form = ContentForm(fields(**changes), kind=kind)
    assert not form.is_valid()
    assert "both body versions" not in str(form.errors)


@pytest.mark.parametrize("slot", ["initial", "reminder"])
@pytest.mark.parametrize(
    "defect", ["html", "text", "generated", "code_subject", "link_subject"]
)
def test_family_access_contract_is_enforced_by_the_editor(slot, defect):
    """Invalid access alternatives never become signed previews or saved drafts."""
    payload = fields(
        subject="Invitation",
        html="<p>{{ family_code }} {{ family_url }}</p>",
        text="{{ family_code }} {{ family_url }}",
        generate_text="",
    )
    valid = ContentForm(payload, kind="email", slot=slot)
    assert valid.is_valid(), valid.errors
    if defect in {"html", "text"}:
        payload[defect] = "{{ family_code }}"
    elif defect == "generated":
        payload.update(
            html='<p>{{ family_code }}</p><a href="{{ family_url }}">Respond</a>',
            generate_text="on",
        )
    else:
        payload["subject"] = (
            "{{ family_code }}" if defect == "code_subject" else "{{ family_url }}"
        )
    form = ContentForm(payload, kind="email", slot=slot)
    assert not form.is_valid()
    assert "both body versions" in str(form.errors)
    assert form.errors.as_data()["__all__"][0].code == "family_access"


def test_family_editor_accepts_explicit_text_with_anchor_link():
    """An author can repair extracted plaintext without changing safe linked HTML."""
    form = ContentForm(
        fields(
            subject="Invitation",
            generate_text="",
            html='<p>{{ family_code }}</p><a href="{{ family_url }}">Respond</a>',
            text="{{ family_code }} {{ family_url }}",
        ),
        kind="email",
        slot="initial",
    )
    assert form.is_valid(), form.errors


@pytest.mark.parametrize(
    "kind,slot", [("page", "submission_confirmation"), ("email", "confirmation")]
)
@pytest.mark.parametrize("private", ["family_code", "family_url"])
def test_receipt_editor_rejects_credentials(kind, slot, private):
    """Both the receipt template and separately selected block are credential-free."""
    form = ContentForm(
        fields(subject="Received", html="<p>{{ " + private + " }}</p>"),
        kind=kind,
        slot=slot,
    )
    assert not form.is_valid()


def test_explicit_clear_and_safe_samples():
    """Clearing is explicit; samples never contain real Family identifiers."""
    form = ContentForm(fields(clear="on"), kind="page")
    assert (
        form.is_valid() and form.values(campaign_id="example", slot="welcome") is None
    )

    value = content(
        "example",
        kind="email",
        slot="initial",
        html="<p>{{ family_code }} {{ family_url }} {{ parish_name }}</p>",
    )["values"]
    rendered = sample_render(
        value,
        parish={"name": "<script>unsafe()</script>"},
        campaign=campaign()["values"],
    )
    assert "SAMPLE" in rendered["html"] and "example.invalid" in rendered["html"]
    assert "<script>" not in rendered["html"] and "&lt;script&gt;" in rendered["html"]
    assert sample_render(None, parish={}, campaign={}) is None
    value["text"] = "{{ financial_period }}"
    assert (
        "January 1, 2027"
        in sample_render(
            value,
            parish={"name": "Example"},
            campaign=campaign(modules=["financial"], financial=financial())["values"],
        )["text"]
    )


@pytest.mark.parametrize("configured", [False, True])
def test_receipt_preview_includes_fixed_facts_and_optional_block(configured):
    """Removing a receipt template previews the built-in confirmation, not silence."""
    from parishkit.stewardship.web.content import SafeContent

    value = (
        content("example", kind="email", slot="confirmation")["values"]
        if configured
        else None
    )
    rendered = sample_render(
        value,
        parish={"name": "Example Parish"},
        campaign=campaign()["values"],
        confirmation=True,
        receipt_block=SafeContent("<p>Optional follow-up.</p>", "Optional follow-up."),
    )
    for body in (rendered["html"], rendered["text"]):
        assert "Sample Family" in body and "Submitted:" in body and "Questions:" in body
        assert "Optional follow-up." in body
        assert "SAMPLE" not in body and "sample-family" not in body


@pytest.mark.parametrize("sections", [{}, {"content": []}])
def test_receipt_preview_without_optional_content_section(sections):
    """A parish without authored blocks can still preview ordinary content edits."""
    from parishkit.stewardship.jobs.receipt_preview import confirmation_block
    from parishkit.stewardship.web.content import SafeContent

    assert confirmation_block({"sections": sections}, "example") == SafeContent("", "")


def test_revision_patch_only_updates_actual_consumers():
    """Separate reminders can retain different subjects and templates."""
    document = content_document()
    owner = document["sections"]["campaigns"][0]
    campaign_row = SimpleNamespace(
        pk=UUID(owner["id"]),
        active_configuration=SimpleNamespace(values=owner["values"]),
    )
    previous = content(owner["id"], kind="email", slot="reminder")
    first = schedule(owner["id"], kind="reminder", template_version=previous["id"])
    second = schedule(owner["id"], kind="reminder")
    document["sections"]["schedules"] = [first, second]
    value = previous["values"] | {"subject": "Changed"}
    patch, affected = revision_patch(document, campaign_row, previous, value)
    assert affected == [first]
    schedules = [row for row in patch if row["section"] == "schedules"]
    assert len(schedules) == 1 and schedules[0]["id"] == first["id"]
    assert schedules[0]["values"]["subject"] == "Changed"
    with pytest.raises(ValueError, match="another template"):
        revision_patch(document, campaign_row, previous, None)
    assert revision_patch(document, campaign_row, previous, previous["values"]) == (
        [],
        [],
    )
    assert revision_patch(document, campaign_row, None, None) == ([], [])


def test_parish_and_civil_date_placeholders_use_campaign_values():
    """Dates remain campaign civil dates rather than browser-shifted UTC instants."""
    owner = campaign(modules=["financial"], financial=financial())["values"]
    value = content(
        "example",
        text=(
            "{{ parish_website }} {{ parish_phone }} {{ campaign_start }} "
            "{{ campaign_end }} {{ campaign_timezone }} {{ campaign_year }} "
            "{{ financial_start }} {{ financial_end }}"
        ),
    )["values"]
    rendered = sample_render(
        value,
        parish={
            "name": "Example",
            "website": "https://example.org/",
            "phone": "+12125550100",
        },
        campaign=owner,
    )
    assert rendered["text"] == (
        "https://example.org/ +12125550100 October 1, 2026 October 31, 2026 "
        "America/New_York 2027 January 1, 2027 December 31, 2027"
    )
