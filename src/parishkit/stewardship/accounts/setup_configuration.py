"""Frozen public intent for the one bootstrap-to-configured transition.

Parsing proves shape and immutable retry identity, not authority or readiness.
The setup finalizer must separately bind the original attempt, installed secret
receipts, staged source and atomic configured-marker activation. Ordinary
configuration intake never selects this request format implicitly.
"""

import hashlib
import json
from uuid import UUID

from parishkit.config import ConfigError

from .authority import ConfigurationVersion, parse_version
from .bootstrap_schema import validate_bootstrap_sections
from .configuration_schema import validator_for
from .policy_schema import validate_policy_change

REQUEST_SCHEMA = "initial-setup-patch-v7"
_SECTIONS = frozenset(
    {"parish", "integrations", "login_rules", "campaigns", "schedules", "content"}
)


def _invalid():
    """Submitted public forms may contain private mistakes; never echo values."""
    raise ConfigError("Invalid initial setup configuration intent.")


def build_setup_candidate(base, patch, *, candidate_id):
    """Add one complete draft while preserving every bootstrap Admin exactly.

    This format deliberately permits only additions. Even a valid configured
    document cannot act as a base, and existing bootstrap identities cannot be
    edited or replaced. The 400-record ceiling accommodates five bounded access
    lists, 100 schedules and all named content slots, plus core records.
    Historical v1-v6 parsers and their smaller patch limits remain unchanged.
    """
    from .request_patch import PatchedConfiguration

    if not isinstance(base, ConfigurationVersion) or not isinstance(candidate_id, UUID):
        raise TypeError("Explicit configuration and candidate identities are required.")
    if candidate_id == base.version_id:
        _invalid()
    document = base.document()
    if parse_version(document, validate_sections=validate_bootstrap_sections) != base:
        _invalid()
    if type(patch) is not list or not 1 <= len(patch) <= 400:
        _invalid()
    # Parse detached bytes before returning. No caller-owned mutable object is
    # retained in either the candidate or the canonical retry payload.
    seen = {row["id"] for row in document["sections"]["login_rules"]}
    normalized = []
    for operation in patch:
        if (
            type(operation) is not dict
            or set(operation) != {"operation", "section", "id", "values"}
            or operation["operation"] != "add"
            or type(operation["section"]) is not str
            or operation["section"] not in _SECTIONS
            or type(operation["values"]) is not dict
        ):
            _invalid()
        identifier = operation["id"]
        try:
            if type(identifier) is not str or str(UUID(identifier)) != identifier:
                _invalid()
        except ValueError:
            _invalid()
        if identifier in seen:
            _invalid()
        seen.add(identifier)
        document["sections"].setdefault(operation["section"], []).append(
            {"id": identifier, "values": operation["values"]}
        )
        normalized.append(operation)
    document["version_id"] = str(candidate_id)
    document["predecessor_digest"] = base.digest
    candidate = parse_version(
        document, validate_sections=validator_for("campaign-content-v5")
    )
    sections = candidate.document()["sections"]
    validate_policy_change(
        base.document()["sections"]["login_rules"], sections["login_rules"]
    )
    _complete_shape(sections)
    canonical = json.dumps(
        sorted(normalized, key=lambda row: (row["section"], row["id"])),
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return PatchedConfiguration(
        candidate,
        canonical,
        hashlib.sha256(base.digest.encode("ascii") + canonical).hexdigest(),
    )


def _complete_shape(sections):
    """Require one draft and credential references, never claim provider success."""
    campaigns = sections.get("campaigns", [])
    if len(campaigns) != 1:
        _invalid()
    if (
        campaigns[0]["values"]["timezone"]
        != sections["parish"][0]["values"]["timezone"]
    ):
        _invalid()
    integrations = {
        row["values"]["kind"]: row["values"] for row in sections.get("integrations", [])
    }
    if not {"parishsoft", "google_workspace", "email"} <= integrations.keys():
        _invalid()
    if integrations.keys() - {
        "parishsoft",
        "google_workspace",
        "email",
        "google_oauth",
        "slack",
    }:
        _invalid()
    for kind, values in integrations.items():
        if (
            kind in {"parishsoft", "google_workspace", "slack"}
            and values["credential_fingerprint"] is None
        ):
            _invalid()
        if kind == "email" and values["credential_fingerprint"] is not None:
            _invalid()
