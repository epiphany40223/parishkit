"""Personalization never mixes namespaces or persists reusable access values."""

import json
from dataclasses import replace
from uuid import UUID, uuid4

import pytest

from parishkit.stewardship.accounts.cryptography import (
    CryptographicError,
    Key,
    TokenPrivateKeyring,
)
from parishkit.stewardship.jobs.family_mail_content import (
    CODE_PLACEHOLDER,
    LINK_PLACEHOLDER,
    FamilyMailTemplate,
    open_family_credentials,
    render_family_mail,
    seal_family_credentials,
)
from parishkit.stewardship.jobs.outbox_validation import (
    DeliveryIdentity,
    SealedSubstitutions,
    substitution_context,
)


def identity(*, testing=False):
    """Use distinct immutable Family, semantic and campaign fixture identities."""
    campaign = uuid4()
    return DeliveryIdentity(
        scope_id=campaign,
        campaign_id=campaign,
        family_id=uuid4(),
        semantic_key=uuid4(),
        mode="testing" if testing else "production",
        routing="testing_override" if testing else "production",
        purpose="initial",
        credential_namespace="rehearsal" if testing else "production",
        rehearsal_epoch_id=uuid4() if testing else None,
    )


def render(scope, **changes):
    """A real inert template with both credential slots and hostile source text."""
    arguments = dict(
        identity=scope,
        configuration_id=uuid4(),
        template_id=uuid4(),
        template=FamilyMailTemplate(
            "{{ parish_name }} invitation",
            "<p>{{ family_member_names }}: {{ family_code }}</p>"
            '<a href="{{ family_url }}" rel="noopener noreferrer">Respond</a>',
            "{{ family_member_names }}: {{ family_code }} {{ family_url }}",
        ),
        values={"parish_name": "Example Parish", "family_member_names": "A & <B>"},
        sender="parish@example.org",
        intended_recipients=("a@example.org", "b@example.org"),
        testing_recipient="test@example.org" if scope.mode == "testing" else None,
    )
    return render_family_mail(**(arguments | changes))


def credentials(scope, content, **changes):
    """Only a public ring enters preparation; the token input is an opaque UUID."""
    private = TokenPrivateKeyring([Key("t1", "active", b"x" * 32)])
    token = uuid4()
    production = scope.mode == "production"
    arguments = dict(
        identity=scope,
        render=content,
        public=private.public(),
        token_id=token,
        code="ABCDEFGH" if production else "IABCDEFG",
        token_generation_id=uuid4() if production else None,
        credential_epoch_id=uuid4() if production else None,
    )
    return private, token, seal_family_credentials(**(arguments | changes))


@pytest.mark.parametrize("testing", [False, True])
def test_redacted_render_and_private_reference_round_trip(testing):
    """Retained parts contain only markers; private open remains Family-bound."""
    scope = identity(testing=testing)
    content = render(scope)
    assert CODE_PLACEHOLDER in content.html and LINK_PLACEHOLDER in content.text
    assert "A &amp; &lt;B&gt;" in content.html and "A & <B>" in content.text
    assert content.routed_recipients == (
        ("test@example.org",) if testing else content.intended_recipients
    )
    assert content.subject.startswith("[TEST]") is testing
    if testing:
        assert "instead of" in content.text and "a@example.org" in content.html
    private, token, sealed = credentials(scope, content)
    reference = open_family_credentials(
        identity=scope, render=content, sealed=sealed, private=private
    )
    assert reference.token_id == token
    assert reference.code == ("IABCDEFG" if testing else "ABCDEFGH")
    assert reference.code not in repr(reference)
    assert reference.code not in str(content.fields())
    assert reference.code not in sealed.envelope
    assert not hasattr(private.public(), "decrypt")


@pytest.mark.parametrize(
    "mutation",
    [
        "family",
        "purpose",
        "epoch",
        "semantic",
        "render",
        "generation",
        "configuration",
        "template",
        "reply_to",
    ],
)
def test_sealed_reference_rejects_transplanted_binding(mutation):
    """An old or foreign envelope cannot silently become another Family message."""
    scope = identity(testing=mutation == "epoch")
    content = render(scope)
    private, _, sealed = credentials(scope, content)
    if mutation == "family":
        scope = replace(scope, family_id=uuid4())
    elif mutation == "purpose":
        scope = replace(scope, purpose="reminder")
    elif mutation == "epoch":
        scope = replace(scope, rehearsal_epoch_id=uuid4())
    elif mutation == "semantic":
        scope = replace(scope, semantic_key=uuid4())
    elif mutation == "render":
        content = replace(content, subject="Changed")
    elif mutation == "configuration":
        content = replace(content, configuration_id=uuid4())
    elif mutation == "template":
        content = replace(content, template_id=uuid4())
    elif mutation == "reply_to":
        content = replace(content, reply_to="other@example.org")
    else:
        sealed = replace(sealed, token_generation_id=uuid4())
    with pytest.raises(CryptographicError, match="unavailable"):
        open_family_credentials(
            identity=scope, render=content, sealed=sealed, private=private
        )


@pytest.mark.parametrize(
    "values",
    [
        {"family_code": "ABCDEFGH"},
        {"family_url": "https://private.example"},
        {"family_name": CODE_PLACEHOLDER},
        {"family_name": LINK_PLACEHOLDER},
        {"unrecognized": "private"},
    ],
)
def test_public_input_cannot_supply_credential_values_or_reserved_markers(values):
    """Only the renderer can insert credential positions in retained content."""
    with pytest.raises(ValueError):
        render(identity(), values=values)


@pytest.mark.parametrize(
    "testing,code",
    [(False, "IABCDEFG"), (True, "ABCDEFGH"), (False, "abcdefgh"), (True, "IABCDEF1")],
)
def test_sealing_never_falls_back_to_other_code_namespace(testing, code):
    """Testing and Production code alphabets are intentionally disjoint."""
    scope = identity(testing=testing)
    with pytest.raises(ValueError, match="credential reference"):
        credentials(scope, render(scope), code=code)


@pytest.mark.parametrize(
    "recipients",
    [
        (),
        ("a@example.org", "a@example.org"),
        ("b@example.org", "a@example.org"),
        ("A@example.org",),
    ],
)
def test_recipient_projection_must_be_nonempty_normalized_and_deterministic(recipients):
    """Rendering cannot normalize away a source-owner admission mistake."""
    with pytest.raises(ValueError):
        render(identity(), intended_recipients=recipients)


def test_testing_override_cannot_be_used_in_production():
    """No mode flag can redirect an already Production-bound message."""
    with pytest.raises(ValueError, match="Testing override"):
        render(identity(), testing_recipient="test@example.org")


def test_submission_receipts_cannot_use_credential_bearing_renderer():
    """BG-07 receipts use a separate non-credential rendering contract."""
    with pytest.raises(TypeError, match="exact Family"):
        render(replace(identity(), purpose="receipt"))


def test_template_is_canonical_and_never_renders_executable_source_values():
    """Existing sanitizer/template rules remain the one rendering implementation."""
    with pytest.raises(ValueError, match="canonical"):
        FamilyMailTemplate("Subject", "<script>alert(1)</script><p>Hello</p>", "Hello")
    with pytest.raises(ValueError, match="reserved"):
        FamilyMailTemplate("Subject", "<p>" + CODE_PLACEHOLDER + "</p>", "Hello")


def test_missing_template_and_zero_uuid_do_not_share_sealing_context():
    """Optional identity markers must be disjoint from every valid UUID value."""
    scope = identity()
    content = replace(render(scope), template_id=None)
    private, _, sealed = credentials(scope, content)
    with pytest.raises(CryptographicError):
        open_family_credentials(
            identity=scope,
            render=replace(content, template_id=UUID(int=0)),
            sealed=sealed,
            private=private,
        )


@pytest.mark.parametrize("part", ["html", "text"])
@pytest.mark.parametrize("missing", ["family_code", "family_url"])
def test_each_body_requires_both_private_slots(part, missing):
    """Every alternative gives the Family both supported campaign access methods."""
    values = dict(
        subject="Hello",
        html="<p>{{ family_code }} {{ family_url }}</p>",
        text="{{ family_code }} {{ family_url }}",
    )
    values[part] = values[part].replace("{{ " + missing + " }}", "")
    with pytest.raises(ValueError, match="requires its code and link"):
        FamilyMailTemplate(**values)


@pytest.mark.parametrize("part", ["subject", "html", "text"])
def test_combined_public_values_cannot_synthesize_private_markers(part):
    """Even split markers must not become unauthorized dispatch substitution slots."""
    template = dict(
        subject="Hello",
        html="<p>{{ family_code }} {{ family_url }}</p>",
        text="{{ family_code }} {{ family_url }}",
    )
    template[part] += " PARISHKIT_REDACTED_{{ family_name }}"
    with pytest.raises(ValueError, match="reserved placeholder"):
        render(
            identity(),
            template=FamilyMailTemplate(**template),
            values={"family_name": "FAMILY_CODE"},
        )


@pytest.mark.parametrize("length", [247, 248, 254])
def test_testing_subject_reserves_mandatory_banner_after_expansion(length):
    """Valid long configured subjects are shortened only for Testing presentation."""
    template = FamilyMailTemplate(
        "{{ parish_name }}",
        "<p>{{ family_code }} {{ family_url }}</p>",
        "{{ family_code }} {{ family_url }}",
    )
    values = {"parish_name": "a" * length}
    production = render(identity(), template=template, values=values)
    testing = render(identity(testing=True), template=template, values=values)
    assert production.subject == values["parish_name"]
    assert testing.subject.startswith("[TEST] ") and len(testing.subject) == 254
    assert testing.subject.endswith("…") is (length > 247)


@pytest.mark.parametrize("private", ["family_code", "family_url"])
@pytest.mark.parametrize("prefix", ["", "a" * 220])
def test_subjects_never_contain_or_truncate_private_slots(private, prefix):
    """Header validation rejects credential slots before any shortening or sealing."""
    with pytest.raises(ValueError, match="not subjects"):
        FamilyMailTemplate(
            prefix + "{{ " + private + " }}",
            "<p>{{ family_code }} {{ family_url }}</p>",
            "{{ family_code }} {{ family_url }}",
        )


def test_empty_head_names_use_family_name_in_testing_banner():
    """An available household name remains visible when source head names are empty."""
    result = render(
        identity(testing=True),
        values={
            "family_member_names": "",
            "family_name": "Example",
            "parish_name": "Parish",
        },
    )
    assert "instead of Example (" in result.text


@pytest.mark.parametrize("token_id", [123, [], {}, None])
def test_malformed_sealed_token_reference_has_uniform_error(token_id):
    """A public-key holder can seal arbitrary JSON, but cannot crash the parser."""
    scope = identity(testing=True)
    content = render(scope)
    private, _, _ = credentials(scope, content)
    sealed = SealedSubstitutions(
        private.public().encrypt(
            json.dumps(
                {"version": 1, "code": "IABCDEFG", "token_id": token_id}
            ).encode(),
            context=substitution_context(scope, content),
        ),
        None,
        None,
    )
    with pytest.raises(CryptographicError, match="unavailable"):
        open_family_credentials(
            identity=scope, render=content, sealed=sealed, private=private
        )
