"""Complete public setup intent is deterministic without claiming provider checks."""

from copy import deepcopy
from dataclasses import replace
from uuid import uuid4

import pytest

from parishkit.config import ConfigError
from parishkit.stewardship.accounts.authority import parse_version
from parishkit.stewardship.accounts.bootstrap_schema import bootstrap_version
from parishkit.stewardship.accounts.configuration_schema import (
    schema_for,
    validate_sections,
)
from parishkit.stewardship.accounts.setup_candidate import (
    CandidateCredential,
    compile_candidate,
)
from parishkit.stewardship.accounts.setup_configuration import request_schema_for

from .campaign_factory import campaign, schedule
from .content_factory import content
from .policy_factory import legacy_bootstrap_document
from .test_setup_forms import VALUES


def compilation():
    """All IDs and values are synthetic; no file, SQL or provider is touched."""
    base = bootstrap_version(uuid4(), "admin@example.org")
    attempt = uuid4()
    sections = deepcopy(VALUES) | {"branding": {"bundle_id": str(uuid4())}}
    args = {
        "attempt_id": attempt,
        "candidate_id": uuid4(),
        "operation_id": uuid4(),
        "sections": sections,
        "branding": {key: str(uuid4()) for key in ("large", "menu", "icon", "favicon")},
        "credentials": [
            CandidateCredential("parishsoft", "a" * 64, {"organization_id": 1}),
            CandidateCredential(
                "google_workspace",
                "b" * 64,
                # The staged scope excludes the presentation-only From name.
                {
                    key: sections["mail"][key]
                    for key in ("delegated_email", "sender", "reply_to")
                }
                | {"recipient": sections["testing"]["testing_recipient"]},
            ),
        ],
    }
    return base, args


def test_compilation_is_stable_detached_and_preserves_bootstrap():
    """Setup over a users bootstrap (#993) ends in users; selections are copied.

    Each listed address becomes one user with the highest role it is listed
    under and its local part as Name; the bootstrap Administrator is carried
    unchanged and never listed twice.
    """
    base, args = compilation()
    sections = args["sections"]
    sections["access"]["staff_addresses"] = ["admin@example.org", "staff@example.net"]
    sections["access"]["ministry_addresses"] = [
        "leader@example.net",
        "staff@example.net",
    ]
    before = deepcopy(args)
    result = compile_candidate(base, **args)
    assert result == compile_candidate(base, **args)
    assert args == before
    assert result.candidate.document()["sections"].keys() >= {"parish", "login_rules"}
    assert schema_for(result.candidate.document()) == "users-v10"
    assert request_schema_for(result.candidate) == "initial-setup-users-v10"
    document = result.candidate.document()["sections"]
    rules = document["login_rules"]
    assert base.document()["sections"]["login_rules"][0] in rules
    assert {row["values"]["email"]: row["values"]["role"] for row in rules} == {
        "admin@example.org": "administrator",
        "another@example.net": "administrator",
        "leader@example.net": "ministry_leader",
        "staff@example.net": "staff",
    }
    assert {row["values"]["name"] for row in rules} >= {"leader", "staff"}
    sections["parish"]["name"] = "Changed after compilation"
    assert "Changed after compilation" not in str(result.candidate.document())


def test_setup_over_an_older_bootstrap_keeps_sign_in_rules():
    """A deployment bootstrapped before #993 finishes setup in the v7 format.

    Its candidate keeps exact-address rules with manual provenance, which
    ``upgrade-users`` later converts; the access step grants no domain.
    """
    _, args = compilation()
    base = parse_version(
        legacy_bootstrap_document(), validate_sections=validate_sections
    )
    sections = args["sections"]
    sections["access"]["staff_addresses"] = ["staff@example.net"]
    sections["access"]["ministry_addresses"] = ["staff@example.net"]
    result = compile_candidate(base, **args)
    assert request_schema_for(base) == "initial-setup-patch-v7"
    assert request_schema_for(result.candidate) == "initial-setup-patch-v7"
    rules = result.candidate.document()["sections"]["login_rules"]
    assert base.document()["sections"]["login_rules"][0] in rules
    assert {row["values"]["kind"] for row in rules} == {"address"}
    merged = next(
        row["values"] for row in rules if row["values"]["email"] == "staff@example.net"
    )
    assert merged["roles"] == ["ministry_leader", "staff"]
    assert merged["grants"] == {
        role: {"manual": str(args["operation_id"])} for role in merged["roles"]
    }


@pytest.mark.parametrize(
    "missing",
    [
        "parish",
        "branding",
        "access",
        "mail",
        "testing",
        "slack",
    ],
)
def test_all_core_steps_require_explicit_completion(missing):
    """An omitted optional choice differs from an explicitly completed empty step."""
    base, args = compilation()
    del args["sections"][missing]
    with pytest.raises(ConfigError, match="required setup"):
        compile_candidate(base, **args)


def test_system_setup_compiles_without_a_campaign():
    """System setup (#142): no campaign, content or schedules in the candidate."""
    base, args = compilation()
    sections = compile_candidate(base, **args).candidate.document()["sections"]
    assert not sections.get("campaigns")
    assert not sections.get("content") and not sections.get("schedules")
    assert sections["parish"] and sections["integrations"]


@pytest.mark.parametrize(
    "step", ["campaign", "email_initial", "page_submission_confirmation", "schedules"]
)
def test_sections_outside_the_wizard_are_refused(step):
    """A first-campaign or retired step a draft may hold is refused, not dropped.

    The setup preview passes only the wizard's sections; a caller that passed
    a whole draft must fail closed rather than compile without its campaign.
    """
    base, args = compilation()
    attempt = str(args["attempt_id"])
    revision = content(attempt, kind="email", slot="initial")
    args["sections"][step] = {
        "campaign": {"source_result": str(uuid4()), "campaign": campaign()["values"]},
        "email_initial": revision,
        "page_submission_confirmation": content(
            attempt, slot="submission_confirmation"
        ),
        "schedules": {"records": [schedule(attempt, template_version=revision["id"])]},
    }[step]
    with pytest.raises(ConfigError, match="setup wizard's own sections"):
        compile_candidate(base, **args)


@pytest.mark.parametrize(
    "changed", ["recipient", "sender", "delegated_email", "reply_to"]
)
def test_changed_mail_scope_invalidates_old_sealed_context(changed):
    """A fingerprint alone is not proof that the current recipient/sender was tested."""
    base, args = compilation()
    target = (
        args["sections"]["testing"]
        if changed == "recipient"
        else args["sections"]["mail"]
    )
    target["testing_recipient" if changed == "recipient" else changed] = (
        "new@example.org"
    )
    with pytest.raises(ConfigError, match="Mail settings changed"):
        compile_candidate(base, **args)


def test_from_name_is_compiled_but_is_not_credential_scope():
    """Changing the From name keeps the staged test; blank is omitted."""
    base, args = compilation()
    args["sections"]["mail"]["sender_name"] = "Renamed Stewardship"
    email = next(
        row["values"]["settings"]
        for row in compile_candidate(base, **args).candidate.document()["sections"][
            "integrations"
        ]
        if row["values"]["kind"] == "email"
    )
    assert email["sender_name"] == "Renamed Stewardship"
    args["sections"]["mail"]["sender_name"] = ""
    email = next(
        row["values"]["settings"]
        for row in compile_candidate(base, **args).candidate.document()["sections"][
            "integrations"
        ]
        if row["values"]["kind"] == "email"
    )
    assert "sender_name" not in email


def test_optional_slack_can_be_inert_or_exactly_selected():
    """Disabling Slack excludes its staged credential; enabling it pins its channel."""
    base, args = compilation()
    credential = CandidateCredential("slack", "c" * 64, {"channel_id": "C1234"})
    ordinary = compile_candidate(base, **args)
    args["credentials"].append(credential)
    assert compile_candidate(base, **args) == ordinary
    args["sections"]["slack"] = {"enabled": True, "channel_id": "C1234"}
    result = compile_candidate(base, **args)
    assert any(
        row["values"]["kind"] == "slack"
        for row in result.candidate.document()["sections"]["integrations"]
    )
    args["sections"]["slack"]["channel_id"] = "C5678"
    with pytest.raises(ConfigError, match="Slack settings changed"):
        compile_candidate(base, **args)


@pytest.mark.parametrize(
    "invalid", ["missing", "duplicate", "type", "unknown", "fingerprint"]
)
def test_invalid_credential_metadata_cannot_compile(invalid):
    """Receipt metadata must be complete, uniquely targeted and correctly typed."""
    base, args = compilation()
    if invalid == "missing":
        args["credentials"].pop()
    elif invalid == "duplicate":
        args["credentials"].append(args["credentials"][0])
    elif invalid == "type":
        args["credentials"][0] = {}
    elif invalid == "unknown":
        args["credentials"].append(CandidateCredential("unknown", "a" * 64, {}))
    else:
        args["credentials"][0] = replace(args["credentials"][0], fingerprint="invalid")
    with pytest.raises(ConfigError):
        compile_candidate(base, **args)


def test_maximum_wizard_can_compile_without_artificial_combined_record_limit():
    """Three fifty-row access lists fit the complete intent."""
    base, args = compilation()
    sections = args["sections"]
    for key in sections["access"]:
        sections["access"][key] = sorted(
            f"{key}{index}@example.org" for index in range(50)
        )
    result = compile_candidate(base, **args)
    assert 150 < len(result.patch()) < 200
