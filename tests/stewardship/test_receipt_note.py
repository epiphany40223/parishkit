"""The retired receipt closing note folds into the confirmation email (#260)."""

from uuid import uuid4

import pytest

from parishkit.stewardship.accounts.campaign_cloning import clone_structures
from parishkit.stewardship.accounts.content_forms import (
    default_values,
    matches_default,
    sample_render,
)
from parishkit.stewardship.accounts.receipt_note import fold, legacy_note
from parishkit.stewardship.web.content import SafeContent

from .campaign_factory import campaign
from .content_factory import content, content_document

NOTE = {"html": "<p>Call {{ parish_phone }}.</p>", "text": "Call {{ parish_phone }}."}


def note_for(owner, **changes):
    """One retired closing-note record, as older configurations stored it."""
    return content(owner, slot="submission_confirmation", **NOTE | changes)


@pytest.mark.parametrize("configured", [True, False])
@pytest.mark.parametrize("text", [NOTE["text"], ""])
def test_folded_email_renders_the_same_receipt(configured, text):
    """Email plus note and the folded email alone send identical receipts."""
    owner = str(uuid4())
    email = (
        content(owner, kind="email", slot="confirmation")["values"]
        if configured
        else None
    )
    note = note_for(owner, text=text)["values"]
    values = dict(parish={"name": "Example Parish"}, campaign=campaign()["values"])
    pair = sample_render(
        email,
        confirmation=True,
        receipt_block=SafeContent(note["html"], note["text"]),
        **values,
    )
    folded = fold(email, note, campaign_id=owner)
    assert (folded["kind"], folded["slot"]) == ("email", "confirmation")
    assert sample_render(folded, **values) == pair
    assert "Call " in pair["html"]


def test_legacy_note_selects_only_the_campaigns_note():
    """Another campaign's note, or an ordinary page, is not this campaign's note."""
    owner, other = str(uuid4()), str(uuid4())
    mine = note_for(owner)
    records = [content(owner), note_for(other), mine]
    assert legacy_note(records, owner) is mine
    assert legacy_note(records[:2], owner) is None


@pytest.mark.parametrize("with_email", [True, False])
def test_clone_carries_the_note_inside_the_confirmation_email(with_email):
    """A cloned campaign keeps the note's text, but never as a separate block."""
    document = content_document()
    source = document["sections"]["campaigns"][0]
    rows = document["sections"]["content"]
    if with_email:
        rows.append(content(source["id"], kind="email", slot="confirmation"))
    rows.append(note_for(source["id"]))
    target = uuid4()
    _, cloned, _ = clone_structures(document, source, target)
    assert legacy_note(cloned, target) is None
    # The note merges into the email, or becomes the email when there is none.
    assert len(cloned) == len(rows) - with_email
    (email,) = [row for row in cloned if row["values"]["slot"] == "confirmation"]
    assert email["values"]["html"].endswith(NOTE["html"])
    assert email["values"]["text"].endswith("\n\n" + NOTE["text"])
    assert email["values"]["campaign_id"] == str(target)
    assert cloned == clone_structures(document, source, target)[1]


def test_default_pair_folds_into_the_new_default_email():
    """An untouched pre-#260 email and note fold into exactly today's default."""
    owner = str(uuid4())
    old_email = default_values("email", "confirmation", campaign_id=owner)
    contact = "{{ parish_phone }} or email {{ parish_email }}"
    old_email["html"] = old_email["html"].replace(
        f"<p>If anything needs to change, please contact the parish office at "
        f"{contact}.</p>",
        "",
    )
    old_email["text"] = old_email["text"].rsplit("\n\n", 1)[0]
    old_note = {
        "html": f"<p>If anything needs to change, please contact the parish "
        f"office at {contact}.</p>",
        "text": f"If anything needs to change, please contact the parish office "
        f"at {contact}.",
    }
    assert not matches_default(old_email)
    assert matches_default(fold(old_email, old_note, campaign_id=owner))


@pytest.mark.parametrize(
    "email_html,note_html",
    [
        ("<p>Thanks.</p>", "<p>Call us.</p>"),
        ("Thanks, bare.", "<p>Call us.</p>"),
        ("<p>Thanks.</p>", "Call us, bare."),
    ],
)
def test_generated_parts_fold_into_generated_text(email_html, note_html):
    """Bare-text HTML still folds with "Generate plain text" checked (#260)."""
    from parishkit.stewardship.accounts.content_forms import text_is_generated
    from parishkit.stewardship.web.content import prepare_content

    owner = str(uuid4())
    email = content(owner, kind="email", slot="confirmation", html=email_html)
    email["values"]["text"] = prepare_content(email_html).text
    note = note_for(owner, html=note_html, text=prepare_content(note_html).text)
    folded = fold(email["values"], note["values"], campaign_id=owner)
    assert text_is_generated(folded)
    values = dict(parish={"name": "Example Parish"}, campaign=campaign()["values"])
    assert sample_render(folded, **values) == sample_render(
        email["values"],
        confirmation=True,
        receipt_block=SafeContent(note["values"]["html"], note["values"]["text"]),
        **values,
    )


def test_hand_written_text_is_joined_word_for_word():
    """Plain text that is not generated is kept, after a blank line."""
    owner = str(uuid4())
    email = content(owner, kind="email", slot="confirmation", text="My own words.")
    folded = fold(email["values"], note_for(owner)["values"], campaign_id=owner)
    assert folded["text"] == "My own words.\n\n" + NOTE["text"]


def test_folded_receipt_expands_a_hosted_file_placeholder(monkeypatch):
    """A note's {{ file.slug }} link (#348) renders the same once folded."""
    from parishkit.stewardship.accounts import hosted_file_content
    from parishkit.stewardship.web.content import HostedLinks, prepare_content

    files = HostedLinks(
        "https://parish.example.invalid",
        {"guide": "https://parish.example.invalid/files/guide-token"},
    )
    # The file library is a database table; serve one known file instead.
    monkeypatch.setattr(hosted_file_content, "links_for", lambda origin, *values: files)
    owner = str(uuid4())
    email = content(owner, kind="email", slot="confirmation")["values"]
    prepared = prepare_content('<p><a href="{{ file.guide }}">Parish guide</a></p>')
    note = note_for(owner, html=prepared.html, text=prepared.text)["values"]
    values = dict(parish={"name": "Example Parish"}, campaign=campaign()["values"])
    pair = sample_render(
        email,
        confirmation=True,
        receipt_block=SafeContent(note["html"], note["text"]),
        **values,
    )
    assert sample_render(fold(email, note, campaign_id=owner), **values) == pair
    assert "/files/guide-token" in pair["html"] and "/files/guide-token" in pair["text"]
    assert "{{" not in pair["html"] + pair["text"]
