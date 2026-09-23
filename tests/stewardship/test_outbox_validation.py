"""Pure delivery inputs reject malformed/private values without leaking them."""

from dataclasses import FrozenInstanceError, replace
from uuid import uuid4

import pytest

from parishkit.stewardship.jobs.outbox_validation import (
    DeliveryEvidence,
    DeliveryIdentity,
    RenderInput,
    SealedSubstitutions,
    identifier,
)


def rendering(**changes):
    """Use only synthetic, non-credential render content."""
    return RenderInput(
        **(
            dict(
                configuration_id=uuid4(),
                template_id=None,
                sender="parish@example.org",
                intended_recipients=("member@example.org",),
                routed_recipients=("testing@example.org",),
                subject="Please respond",
                html="<p>Hello</p>",
                text="Hello",
            )
            | changes
        )
    )


def test_render_is_frozen_detached_and_deterministic():
    """Mutation of caller lists or returned fields cannot alter pinned content."""
    intended = ["member@example.org"]
    value = rendering(intended_recipients=intended)
    expected = value.fields()
    intended.append("another@example.org")
    assert value.fields() == expected
    expected["routed_recipients"].append("unwanted@example.org")
    assert value.fields()["routed_recipients"] == ["testing@example.org"]
    assert replace(value).fields()["payload_digest"] == value.fields()["payload_digest"]
    assert (
        replace(value, text="Different").fields()["payload_digest"]
        != value.fields()["payload_digest"]
    )
    with pytest.raises(FrozenInstanceError):
        value.subject = "Changed"


@pytest.mark.parametrize(
    "field,value",
    [
        ("sender", "private@example.org\r\nBcc: stolen@example.org"),
        ("sender", " private@example.org"),
        ("sender", "not-an-address"),
        ("sender", None),
        ("intended_recipients", []),
        ("intended_recipients", "private@example.org"),
        ("intended_recipients", ["private@example.org"] * 101),
        ("intended_recipients", ["private@example.org", "PRIVATE@example.org"]),
        ("routed_recipients", [None]),
        ("routed_recipients", ["private\n@example.org"]),
        ("subject", "private\nsubject"),
        ("subject", "\x00"),
        ("subject", " " * 254),
        ("subject", "x" * 255),
        ("subject", None),
        ("html", ""),
        ("html", "\x00"),
        ("text", " "),
        ("text", None),
        ("text", "é" * 524289),
    ],
)
def test_invalid_render_is_rejected_without_raw_values(field, value):
    """No field-specific validation error includes a private recipient or body."""
    with pytest.raises(ValueError) as error:
        rendering(**{field: value})
    assert "private" not in str(error.value)


@pytest.mark.parametrize("value", [None, True, 1, "private", [], {}])
def test_identifiers_require_actual_uuid_objects(value):
    """Arbitrary input cannot become a SQL identity or an error message."""
    with pytest.raises(TypeError, match="identifiers must be UUIDs"):
        identifier(value)
    identifier(None, optional=True)
    identifier(uuid4())


@pytest.mark.parametrize(
    "field,value",
    [
        ("provider_key_digest", "provider-private-id"),
        ("provider_message_digest", "a" * 63),
        ("evidence_digest", "A" * 64),
        ("evidence_digest", None),
        ("evidence_note", "x" * 2001),
        ("evidence_note", "private\x00"),
        ("evidence_note", None),
        ("reason", "private phrase"),
        ("reason", None),
    ],
)
def test_invalid_evidence_has_safe_errors(field, value):
    """Private notes remain bounded and provider IDs are fingerprints only."""
    with pytest.raises(ValueError) as error:
        DeliveryEvidence(**{field: value})
    assert "private" not in str(error.value)


@pytest.mark.parametrize("value", [None, {}, "private", "x" * 32769])
def test_invalid_seal_is_not_logged_or_echoed(value):
    """The journal cannot store a raw token in place of an encrypted envelope."""
    with pytest.raises(ValueError, match="Invalid sealed delivery substitution"):
        SealedSubstitutions(value)


def identity(**changes):
    """A synthetic testing Family message defaults to no embedded credential."""
    campaign = uuid4()
    return DeliveryIdentity(
        **(
            dict(
                scope_id=campaign,
                campaign_id=campaign,
                semantic_key=uuid4(),
                mode="testing",
                routing="testing_override",
                purpose="initial",
                family_id=uuid4(),
            )
            | changes
        )
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"mode": "other"},
        {"purpose": "arbitrary"},
        {"family_id": None},
        {"routing": "production"},
        {"campaign_id": uuid4()},
        {"credential_namespace": "other"},
        {"credential_namespace": "production"},
        {"credential_namespace": "rehearsal"},
        {"rehearsal_epoch_id": uuid4()},
    ],
)
def test_identity_rejects_cross_mode_routing_and_credential_fallback(changes):
    """Credentials and operational exemptions cannot be selected independently."""
    with pytest.raises(ValueError):
        identity(**changes)


@pytest.mark.parametrize(
    "changes",
    [
        {"credential_namespace": "none"},
        {
            "mode": "production",
            "routing": "production",
            "credential_namespace": "production",
        },
        {"family_id": None},
    ],
)
def test_family_test_identity_is_testing_only_rehearsal_mail(changes):
    """A chosen-Family test carries a rehearsal credential and never leaves Testing."""
    valid = identity(
        purpose="family_test",
        credential_namespace="rehearsal",
        rehearsal_epoch_id=uuid4(),
    )
    assert valid.purpose == "family_test"
    with pytest.raises(ValueError):
        identity(
            **(
                dict(
                    purpose="family_test",
                    credential_namespace="rehearsal",
                    rehearsal_epoch_id=uuid4(),
                )
                | changes
            )
        )


def test_valid_identity_namespaces_and_operational_scope():
    """Legitimate disjoint modes and non-Family operational mail remain available."""
    assert identity().credential_namespace == "none"
    assert identity(credential_namespace="rehearsal", rehearsal_epoch_id=uuid4())
    assert identity(
        mode="production", routing="production", credential_namespace="production"
    )
    assert identity(
        purpose="operational", family_id=None, campaign_id=None, routing="operational"
    )
    assert DeliveryEvidence(
        evidence_digest="a" * 64, evidence_note="Provider checked", reason="confirmed"
    )
