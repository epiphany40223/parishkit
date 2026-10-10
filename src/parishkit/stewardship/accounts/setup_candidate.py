"""Deterministic public configuration compilation from a fully owned setup draft.

This pure compiler neither reads credential bytes nor installs anything. Its
caller owns original-session/source/logo admission and must recheck the exact
result before freezing an intent. Credential metadata is explicitly typed and
compared with the settings that were sealed, never treated as a delivery check.
"""

from copy import deepcopy
from dataclasses import dataclass, field
from uuid import UUID, uuid5

from parishkit.config import ConfigError

from .content_forms import LEGACY_PAGE_REFERENCES, page_slots
from .policy_schema import validate_manual_operation
from .provider_context import validated_context, workspace_scope
from .setup_configuration import build_setup_candidate
from .setup_content_values import CONTENT_STEPS, without_retired
from .setup_forms import FORMS, validate_values


@dataclass(frozen=True)
class CandidateCredential:
    """Safe input metadata, deliberately excluding every sealed or private value."""

    target: str
    fingerprint: str
    settings: dict = field(repr=False)


def _rules(base, attempt_id, operation_id, access):
    """Merge repeated identities while preserving each bootstrap Admin exactly."""
    initial = {
        row["values"]["email"] for row in base.document()["sections"]["login_rules"]
    }
    domains, addresses = {}, {}
    for prefix, role in (
        ("staff", "staff"),
        ("ministry", "ministry_leader"),
        ("admin", "administrator"),
    ):
        for email in access[f"{prefix}_addresses"]:
            if email not in initial:
                addresses.setdefault(email, set()).add(role)
        if prefix != "admin":
            for domain in access[f"{prefix}_domains"]:
                domains.setdefault(domain, set()).add(role)
    result = []
    for kind, identities in (("domain", domains), ("address", addresses)):
        for identity, selected in sorted(identities.items()):
            roles = sorted(selected)
            values = {
                "kind": kind,
                "roles": roles,
                "domain" if kind == "domain" else "email": identity,
            }
            if kind == "address":
                values.update(
                    creation_origin="manual",
                    creation_operation=str(operation_id),
                    grants={role: {"manual": str(operation_id)} for role in roles},
                )
            result.append(
                {
                    "id": str(uuid5(attempt_id, f"login-rule:{kind}:{identity}")),
                    "values": values,
                }
            )
    return result


def compile_candidate(
    base, *, attempt_id, candidate_id, operation_id, sections, branding, credentials
):
    """Produce exact reproducible v7 intent without manufacturing readiness.

    UUIDs for new public records are scoped to the original attempt. System
    setup (#142) has no campaign section, so the candidate has no campaign,
    content or schedules, and any content a draft saved before the upgrade is
    ignored. Until the wizard stops offering the campaign pages (#142 part b),
    a draft that staged its first campaign keeps it, and afterwards only an
    attempt confirmed before the upgrade still has one:
    selected content/schedule/option IDs retain their draft identities, and
    the first Campaign uses the attempt UUID already embedded in its
    temporary children.
    """
    if any(
        not isinstance(value, UUID)
        for value in (attempt_id, candidate_id, operation_id)
    ):
        raise TypeError("Explicit setup compilation identities are required.")
    if type(sections) is not dict or not set(FORMS) <= sections.keys():
        raise ConfigError("Complete every required setup section before final preview.")
    # A draft saved before a slot retired may still hold it; leave it behind
    # instead of refusing to finish.
    sections = without_retired(sections)
    if "campaign" not in sections:
        return _system_candidate(
            base,
            attempt_id=attempt_id,
            candidate_id=candidate_id,
            operation_id=operation_id,
            sections=sections,
            branding=branding,
            credentials=credentials,
        )
    if "schedules" not in sections:
        raise ConfigError("Complete every required setup section before final preview.")
    values = {step: validate_values(step, value) for step, value in sections.items()}
    campaign = deepcopy(values["campaign"]["campaign"])
    active_pages = page_slots(campaign)
    content = [
        values[step]
        for step in CONTENT_STEPS
        if values.get(step, {}).get("values")
        and (step.startswith("email_") or step[5:] in active_pages)
    ]
    campaign["content_versions"] = {
        row["values"]["slot"]: row["id"]
        for row in content
        if row["values"]["kind"] == "page"
        and row["values"]["slot"] in LEGACY_PAGE_REFERENCES
    }
    if any(row["values"]["campaign_id"] != str(attempt_id) for row in content):
        raise ConfigError("Setup content belongs to a different campaign.")
    public = _system_records(
        base, attempt_id, operation_id, values, branding, credentials
    )
    public |= {
        "campaigns": [{"id": str(attempt_id), "values": campaign}],
        "content": content,
        "schedules": values["schedules"]["records"],
    }
    selected = {row["id"] for row in content}
    if any(
        row["values"].get("template_version") not in selected
        for row in public["schedules"]
    ):
        raise ConfigError("Every setup schedule requires a saved email template.")
    return _compile(base, public, candidate_id=candidate_id, operation_id=operation_id)


def _system_candidate(
    base, *, attempt_id, candidate_id, operation_id, sections, branding, credentials
):
    """The candidate of system-only setup (#142): no campaign, content or schedules."""
    values = {step: validate_values(step, sections[step]) for step in FORMS}
    public = _system_records(
        base, attempt_id, operation_id, values, branding, credentials
    )
    return _compile(base, public, candidate_id=candidate_id, operation_id=operation_id)


def _system_records(base, attempt_id, operation_id, values, branding, credentials):
    """The Parish profile, integrations and login rules every setup candidate has."""
    return {
        "parish": [
            {
                "id": str(uuid5(attempt_id, "parish")),
                # A blank optional giving URL is omitted, not stored empty.
                "values": {
                    name: value
                    for name, value in values["parish"].items()
                    if name != "online_giving_url" or value
                }
                | {"branding": dict(branding)},
            }
        ],
        "integrations": _integrations(attempt_id, values, credentials),
        "login_rules": _rules(base, attempt_id, operation_id, values["access"]),
    }


def _compile(base, public, *, candidate_id, operation_id):
    """Build the v7 candidate from its public records and check its login rules."""
    compiled = build_setup_candidate(
        base,
        [
            {"operation": "add", "section": section, **row}
            for section, rows in public.items()
            for row in rows
        ],
        candidate_id=candidate_id,
    )
    validate_manual_operation(
        base.document()["sections"]["login_rules"],
        compiled.candidate.document()["sections"]["login_rules"],
        operation_id,
    )
    return compiled


def _integrations(attempt_id, values, credentials):
    """Sealed context must match current draft mail/Slack settings byte for byte."""
    expected = {"parishsoft", "google_workspace"}
    if values["slack"]["enabled"]:
        expected.add("slack")
    selected = {}
    for credential in credentials:
        if (
            not isinstance(credential, CandidateCredential)
            or credential.target in selected
        ):
            raise ConfigError("Invalid setup credential metadata.")
        # Disabled optional Slack staging remains inert until expiry cleanup.
        if credential.target == "slack" and "slack" not in expected:
            continue
        selected[credential.target] = credential
    if set(selected) != expected:
        raise ConfigError("Stage every required setup credential before final preview.")
    results = []
    for target, credential in sorted(selected.items()):
        context = validated_context(target, credential.settings)
        if target == "parishsoft":
            settings = {"organization_id": str(context["organization_id"])}
        elif target == "google_workspace":
            if context != workspace_scope(
                values["mail"], values["testing"]["testing_recipient"]
            ):
                raise ConfigError("Mail settings changed after credential staging.")
            settings = {"delegated_email": context["delegated_email"]}
        else:
            if context != {"channel_id": values["slack"]["channel_id"]}:
                raise ConfigError("Slack settings changed after credential staging.")
            settings = context
        results.append(
            {
                "id": str(uuid5(attempt_id, f"integration:{target}")),
                "values": {
                    "kind": target,
                    "settings": settings,
                    "credential_fingerprint": credential.fingerprint,
                },
            }
        )
    results.append(
        {
            "id": str(uuid5(attempt_id, "integration:email")),
            "values": {
                "kind": "email",
                "settings": {key: values["mail"][key] for key in ("sender", "reply_to")}
                # Omitted when empty, so delivery falls back to the Parish name.
                | (
                    {"sender_name": values["mail"]["sender_name"]}
                    if values["mail"].get("sender_name")
                    else {}
                ),
                "credential_fingerprint": None,
            },
        }
    )
    return results
