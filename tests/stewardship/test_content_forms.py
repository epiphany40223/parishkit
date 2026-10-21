"""Safe samples, plain-text generation and independent mail revision references."""

from types import SimpleNamespace
from uuid import UUID

import pytest

from parishkit.stewardship.accounts.content_forms import (
    EMAIL_LABELS,
    HISTORY_EMAIL_LABELS,
    PAGE_LABELS,
    ContentForm,
    applicable_slots,
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
        "text": "",
        "generate_text": "on",
        **changes,
    }


def test_slots_and_module_visibility():
    """Every schema slot has a human name; disabled module fields are not offered."""
    assert set(PAGE_LABELS) == PAGE_SLOTS and set(HISTORY_EMAIL_LABELS) == EMAIL_SLOTS
    # The never-sent critical alert (#913) only labels history.
    assert set(EMAIL_LABELS) == EMAIL_SLOTS - {"critical_alert"}
    assert "retired" in str(HISTORY_EMAIL_LABELS["critical_alert"])
    assert ("email", "critical_alert") not in applicable_slots(campaign()["values"])
    slots = page_slots(campaign(additional_information=False)["values"])
    assert "census" in slots and "member_census" in slots
    assert not {"additional", "ministry", "financial"} & slots.keys()
    assert "census" not in page_slots(campaign(modules=["ministry"])["values"])
    # The retired receipt closing note (#260) only labels history.
    assert "submission_confirmation" not in slots
    assert "retired" in str(PAGE_LABELS["submission_confirmation"])


@pytest.mark.parametrize("generate", [False, True])
def test_content_sanitized_and_plain_text_independent(generate):
    """Sanitize before storage, with explicit generated or edited plaintext."""
    form = ContentForm(
        fields(
            generate_text="on" if generate else "",
            subject="Received",
            html='<p onclick="alert(1)">Hi</p><script>steal()</script>',
            text="" if generate else "Edited plain text",
        ),
        kind="email",
        slot="confirmation",
    )
    assert form.is_valid(), form.errors
    value = form.values(campaign_id="example", slot="confirmation")
    assert value["html"] == "<p>Hi</p>" and value["subject"] == "Received"
    assert value["text"] == ("Hi" if generate else "Edited plain text")


@pytest.mark.parametrize(
    "kind,changes",
    [
        ("page", {"html": "{{ unknown }}"}),
        ("email", {"subject": "Hi", "generate_text": "", "text": "{% unsafe %}"}),
        ("email", {"subject": "Bad\nsubject"}),
        ("email", {"subject": "{{ invalid }}"}),
        ("email", {"subject": ""}),
    ],
)
def test_invalid_template_form(kind, changes):
    """A safe typed field is not enough: placeholder/header rules still apply."""
    slot = "welcome" if kind == "page" else "confirmation"
    form = ContentForm(fields(**changes), kind=kind, slot=slot)
    assert not form.is_valid()
    assert "both body versions" not in str(form.errors)


@pytest.mark.parametrize("slot", ["initial", "reminder"])
@pytest.mark.parametrize("defect", ["html", "text", "code_subject", "link_subject"])
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
    else:
        payload["subject"] = (
            "{{ family_code }}" if defect == "code_subject" else "{{ family_url }}"
        )
    form = ContentForm(payload, kind="email", slot=slot)
    assert not form.is_valid()
    expected = {
        "html": "The HTML version is missing {{ family_url }}.",
        "text": "The plain-text version is missing {{ family_url }}.",
        "code_subject": "The email subject contains {{ family_code }}.",
        "link_subject": "The email subject contains {{ family_url }}.",
    }[defect]
    assert expected in str(form.errors)
    # Each problem is shown at the field it is about (#563), never above.
    field = "subject" if defect.endswith("_subject") else defect
    assert {error.code for error in form.errors.as_data()[field]} == {"family_access"}
    assert "__all__" not in form.errors


@pytest.mark.parametrize("slot", ["initial", "reminder"])
def test_generated_text_problem_names_the_generator_and_the_fix(slot):
    """A placeholder lost only from generated text says how to fix it."""
    form = ContentForm(
        fields(
            subject="Invitation",
            # A link without the Family link, so neither version has it.
            html="<p>{{ family_code }}</p>",
            text="",
            generate_text="on",
        ),
        kind="email",
        slot=slot,
    )
    assert not form.is_valid()
    # Generated text is fixed in the HTML source, so both are shown there.
    assert list(form.errors) == ["html"]
    messages = [str(error.message) for error in form.errors.as_data()["html"]]
    assert messages[0].startswith("The HTML version is missing {{ family_url }}.")
    assert messages[1].startswith(
        "The plain text generated from the HTML version is missing {{ family_url }}."
    )
    assert "uncheck “Generate plain text from HTML”" in messages[1]


def test_checked_generation_refuses_typed_plain_text_instead_of_dropping_it():
    """Typed text that differs from the generated text is never silently lost."""
    data = fields(subject="Received", html="<p>Hello</p>", generate_text="on")
    receipt = {"kind": "email", "slot": "confirmation"}
    form = ContentForm(data | {"text": "My own words"}, **receipt)
    assert not form.is_valid()
    assert form.errors.as_data()["text"][0].code == "text_conflict"
    # Blank, or exactly the generated text (a read-only preview), is fine.
    for text in ("", "Hello", "A\r\n\r\nB"):
        if "A" in text:
            data = data | {"html": "<p>A</p><p>B</p>"}
        assert ContentForm(data | {"text": text}, **receipt).is_valid()
    # Unchecked, the typed text is kept.
    kept = ContentForm(data | {"generate_text": "", "text": "Mine"}, **receipt)
    assert kept.is_valid() and kept.cleaned_data["prepared"].text == "Mine"


def test_editors_open_with_generation_matching_the_saved_text():
    """Hand-written plain text opens unchecked, so no save discards it."""
    from parishkit.stewardship.accounts.content_forms import text_is_generated

    assert text_is_generated(None)
    assert text_is_generated({"html": "<p>Hi</p>", "text": "Hi"})
    assert not text_is_generated({"html": "<p>Hi</p>", "text": "Hello"})


@pytest.mark.parametrize("slot", ["initial", "reminder"])
def test_generated_family_text_keeps_the_link_target(slot):
    """Generated plain text writes the link as "label: URL", keeping the link."""
    form = ContentForm(
        fields(
            subject="Invitation",
            html='<p>{{ family_code }}</p><a href="{{ family_url }}">Respond</a>',
            text="",
            generate_text="on",
        ),
        kind="email",
        slot=slot,
    )
    assert form.is_valid(), form.errors
    assert "Respond: {{ family_url }}" in form.cleaned_data["prepared"].text


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


@pytest.mark.parametrize("private", ["family_code", "family_url"])
def test_receipt_editor_rejects_credentials(private):
    """The receipt email is credential-free."""
    form = ContentForm(
        fields(subject="Received", html="<p>{{ " + private + " }}</p>"),
        kind="email",
        slot="confirmation",
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
    assert "ABCDEFGH" in rendered["html"]
    assert "https://stewardship.example.invalid/access/" in rendered["html"]
    assert "<script>" not in rendered["html"] and "&lt;script&gt;" in rendered["html"]
    assert sample_render(None, parish={}, campaign={}) is None
    value["text"] = "{{ financial_period }}"
    assert (
        "January 1, 2055"
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
        assert "Family: Sample" in body and "Submitted:" in body
        assert "Questions:" not in body
        assert "Optional follow-up." in body
        assert "ABCDEFGH" not in body and "/access/" not in body


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
    with pytest.raises(ValueError, match="can't be removed"):
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
        "https://example.org/ +1 (212) 555-0100 October 1, 2054 October 31, 2054 "
        "America/New_York 2055 January 1, 2055 December 31, 2055"
    )


def test_every_placeholder_has_a_realistic_fictional_sample():
    """Samples read like real mail, but no sample link or code can be live."""
    from parishkit.stewardship.accounts.cryptography import ALPHABET, canonical_code
    from parishkit.stewardship.web.content import PLACEHOLDERS

    value = {
        "subject": None,
        "html": "".join(f"<p>{name}={{{{ {name} }}}}</p>" for name in PLACEHOLDERS),
        "text": "x",
    }
    rendered = sample_render(
        value,
        parish={"name": "Example Parish"},
        campaign=campaign(modules=["financial"], financial=financial())["values"],
    )["html"]
    samples = dict(
        part.split("=", 1) for part in rendered[3:-4].split("</p><p>") if "=" in part
    )
    assert samples.keys() == PLACEHOLDERS
    assert all(value.strip() for value in samples.values()), samples
    # The sample heads and one more Member, so a preview shows the difference
    # between the name placeholders; the older name equals head_salutation.
    assert samples["head_salutation"] == "Alex and Sam Sample"
    assert samples["family_member_names"] == samples["head_salutation"]
    assert samples["all_family_member_names"] == "Alex, Sam and Jordan Sample"
    assert samples["family_name"] == "Sample"
    code = samples["family_code"]
    assert canonical_code(code) == code and set(code) <= set(ALPHABET)
    for name in ("family_url", "generic_family_url", "parish_website"):
        assert ".example.invalid/" in samples[name]
    assert samples["parish_email"].endswith(".invalid")
    assert samples["online_giving_url"] == samples["parish_website"]


@pytest.mark.parametrize("slot", ["welcome", "financial", "thank_you", None])
def test_page_slots_never_sent_as_email_always_generate_plain_text(slot):
    """Web-only page slots offer no plain-text controls and ignore posted text."""
    form = ContentForm(
        fields(html="<p>Hello</p>", generate_text="", text="Ignored"),
        kind="page",
        slot=slot,
    )
    assert "text" not in form.fields and "generate_text" not in form.fields
    assert form.is_valid(), form.errors
    assert form.values(campaign_id="example", slot=slot or "welcome")["text"] == "Hello"


@pytest.mark.parametrize("slot", sorted(EMAIL_SLOTS))
def test_delivered_plain_text_keeps_its_controls(slot):
    """Every email keeps the plain-text editor."""
    form = ContentForm(kind="email", slot=slot)
    assert "text" in form.fields and "generate_text" in form.fields


def test_editor_template_hides_plain_text_for_web_only_pages():
    """The shared editor fields render the plain-text panel only when delivered."""
    from django.template.loader import render_to_string

    def render(**kwargs):
        return render_to_string(
            "stewardship/content-fields.html", {"form": ContentForm(**kwargs)}
        )

    assert "data-plain-text" not in render(kind="page", slot="welcome")
    assert "data-plain-text" in render(kind="email", slot="confirmation")


@pytest.mark.parametrize(
    ("kind", "changes", "field"),
    [
        ("page", {"html": "<p>{{ unknown_name }}</p>"}, "html"),
        ("email", {"subject": "Hello {{ unknown_name }}"}, "subject"),
        ("email", {"html": "<p>{{ unknown_name }}</p>"}, "html"),
        (
            "email",
            {"generate_text": "", "text": "Hello {{ unknown_name }}"},
            "text",
        ),
    ],
)
def test_placeholder_problems_are_shown_at_their_field(kind, changes, field):
    """An unknown placeholder is a field error, never one above the form (#563)."""
    payload = fields(**({"subject": "Hello"} | changes))
    form = ContentForm(payload, kind=kind, slot="welcome" if kind == "page" else None)
    assert not form.is_valid()
    assert list(form.errors) == [field]
    assert "placeholders" in str(form.errors[field])


def test_an_unavailable_removal_is_drawn_disabled_with_its_reason():
    """The box stays in place, disabled and named by its reason (#563)."""
    reason = "Nothing is saved here yet, so there is nothing to remove."
    form = ContentForm(kind="page", slot="welcome", clear_unavailable=reason)
    box = str(form["clear"])
    assert " disabled" in box and 'aria-describedby="id_clear_helptext"' in box
    assert reason in form["clear"].as_field_group()
    assert " disabled" not in str(ContentForm(kind="page", slot="welcome")["clear"])


def test_an_html_source_error_is_shown_at_the_visual_editor_too():
    """The HTML source is folded away while the visual editor is in use, so
    its error is shown, linked and marked at the editor as well (#563)."""
    from django.template.loader import render_to_string

    form = ContentForm(
        fields(html="<p>{{ unknown_name }}</p>"), kind="page", slot="welcome"
    )
    assert not form.is_valid()
    page = render_to_string("stewardship/content-fields.html", {"form": form})
    editor = page[page.index('class="panel content-editor"') :]
    editor = editor[: editor.index(">")]
    assert 'aria-describedby="visual-content-error"' in editor
    assert 'aria-invalid="true"' in editor
    error = page[page.index('id="visual-content-error"') :]
    assert "placeholders" in error[: error.index("</ul>")]
    valid = ContentForm(fields(), kind="page", slot="welcome")
    assert valid.is_valid()
    shown = render_to_string("stewardship/content-fields.html", {"form": valid})
    assert "visual-content-error" not in shown


def test_the_content_is_greyed_and_the_subject_optional_while_removing():
    """While "Remove this selected content" is ticked the content's fields
    are greyed in place (data-enabled-when), the banner box excepted, and a
    removal posted without the greyed (unsent) subject is accepted (#563
    item 6, #750). An unavailable box needs no marks; without the tick the
    subject is required as before."""
    form = ContentForm(kind="email", slot="initial", banner=True)
    for name in ("subject", "html", "generate_text", "text"):
        assert 'data-enabled-when="clear!=on"' in str(form[name]), name
    assert "data-enabled-when" not in str(form["show_banner"])
    unavailable = ContentForm(kind="email", slot="initial", clear_unavailable="Why.")
    assert "data-enabled-when" not in str(unavailable["subject"])
    removal = ContentForm({"base_digest": "a" * 64, "clear": "on"}, kind="email")
    assert removal.is_valid(), removal.errors
    assert removal.values(campaign_id=UUID(int=1), slot="initial") is None
    edit = ContentForm(fields(), kind="email")
    assert not edit.is_valid() and list(edit.errors) == ["subject"]
