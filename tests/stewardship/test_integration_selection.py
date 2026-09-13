"""Explicit credential-reference format and closed provider-scope interpretation."""

from uuid import uuid4

import pytest

from parishkit.config import ConfigError
from parishkit.stewardship.accounts.integration_selection import authentication_scope
from parishkit.stewardship.accounts.request_patch import (
    CREDENTIAL_REQUEST_SCHEMA,
    build_candidate,
)

from .configuration_factory import configuration_version


def patch(base, **values):
    """The dedicated format nominates only one already-existing public reference."""
    return [
        {
            "operation": "update",
            "section": "integrations",
            "id": base.document()["sections"]["integrations"][0]["id"],
            "values": values,
        }
    ]


def test_only_explicit_reference_format_can_nominate_a_fingerprint():
    """Ordinary and retained parsers keep their original rejection behavior."""
    base = configuration_version()
    change = patch(base, credential_fingerprint="b" * 64)
    for schema in (
        None,
        "parish-integrations-patch-v1",
        "foundation-policy-patch-v2",
        "campaign-content-patch-v5",
    ):
        with pytest.raises(ConfigError):
            build_candidate(base, change, candidate_id=uuid4(), request_schema=schema)
    candidate = build_candidate(
        base, change, candidate_id=uuid4(), request_schema=CREDENTIAL_REQUEST_SCHEMA
    )
    assert candidate.patch() == change
    assert (
        base.document()["sections"]["integrations"][0]["values"][
            "credential_fingerprint"
        ]
        == "a" * 64
    )


def test_receipt_replay_does_not_select_the_future_emitted_schema(monkeypatch):
    """An existing v6 intent keeps identical canonical output after emission evolves."""
    base = configuration_version()
    change = patch(base, credential_fingerprint="b" * 64)
    identifier = uuid4()
    expected = build_candidate(
        base, change, candidate_id=identifier, request_schema=CREDENTIAL_REQUEST_SCHEMA
    )
    monkeypatch.setattr(
        "parishkit.stewardship.accounts.configuration_schema.schema_for",
        lambda doc: "future-v99",
    )
    assert (
        build_candidate(
            base,
            change,
            candidate_id=identifier,
            request_schema=CREDENTIAL_REQUEST_SCHEMA,
        )
        == expected
    )


@pytest.mark.parametrize(
    "values",
    [
        None,
        {},
        {"credential_fingerprint": None},
        {"credential_fingerprint": "private"},
        {"credential_fingerprint": "b" * 64, "settings": {"organization_id": "3"}},
        {"settings": {"organization_id": "3"}},
    ],
)
def test_reference_format_rejects_unrelated_or_private_values(values):
    """The format cannot be repurposed as a public-settings or raw-secret editor."""
    base = configuration_version()
    change = patch(base, credential_fingerprint="b" * 64)
    change[0]["values"] = values
    with pytest.raises(ConfigError):
        build_candidate(
            base, change, candidate_id=uuid4(), request_schema=CREDENTIAL_REQUEST_SCHEMA
        )


@pytest.mark.parametrize("kind", ["count", "add", "remove", "section"])
def test_reference_format_has_exactly_one_existing_integration(kind):
    """Adding/removing a service or changing Parish/policy remains a separate owner."""
    base = configuration_version()
    change = patch(base, credential_fingerprint="b" * 64)
    if kind == "count":
        change.append(change[0])
    elif kind == "section":
        change[0]["section"] = "parish"
    else:
        change[0]["operation"] = kind
    with pytest.raises(ConfigError):
        build_candidate(
            base, change, candidate_id=uuid4(), request_schema=CREDENTIAL_REQUEST_SCHEMA
        )


@pytest.mark.parametrize(
    "organization", [None, 123, "0123", "１２３", "-1", "0", "2147483648"]
)
def test_scope_refuses_noncanonical_or_out_of_range_organization(organization):
    """Receipt comparison uses the same normalized tenant contract as provider IO."""
    records = {
        "parishsoft": {"values": {"settings": {"organization_id": organization}}}
    }
    with pytest.raises(ConfigError):
        authentication_scope("parishsoft", records)
    from types import SimpleNamespace

    from parishkit.stewardship.accounts.integration_views import _context

    configuration = SimpleNamespace(
        testing_recipient="test@example.org",
        active_configuration=SimpleNamespace(
            canonical_document={
                "sections": {
                    "integrations": [
                        {
                            "values": {
                                "kind": "parishsoft",
                                "settings": {"organization_id": organization},
                            }
                        }
                    ]
                },
            }
        ),
    )
    with pytest.raises(ConfigError):
        _context(configuration, "parishsoft")


def test_workspace_scope_binds_mailbox_sender_and_reply_not_delivery_readiness():
    """The stored recipient is inert scope here; selection does not send a message."""
    records = {
        "google_workspace": {
            "values": {"settings": {"delegated_email": "mail@example.org"}}
        },
        "email": {
            "values": {
                "settings": {
                    "sender": "mail@example.org",
                    "reply_to": "reply@example.org",
                }
            }
        },
    }
    assert authentication_scope(
        "google_workspace", records, recipient="test@example.org"
    ) == {
        "delegated_email": "mail@example.org",
        "sender": "mail@example.org",
        "reply_to": "reply@example.org",
        "recipient": "test@example.org",
    }
    del records["email"]
    with pytest.raises(ConfigError):
        authentication_scope("google_workspace", records, recipient="test@example.org")


def test_slack_scope_is_only_the_closed_channel_binding():
    """URLs and additional context keys cannot extend the provider scope."""
    records = {"slack": {"values": {"settings": {"channel_id": "C123"}}}}
    assert authentication_scope("slack", records) == {"channel_id": "C123"}
    records["slack"]["values"]["settings"]["url"] = "https://example.invalid/"
    with pytest.raises(ConfigError):
        authentication_scope("slack", records)
