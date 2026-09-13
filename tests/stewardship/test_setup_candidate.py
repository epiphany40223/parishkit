"""Complete public setup intent is deterministic without claiming provider checks."""

from copy import deepcopy
from dataclasses import replace
from uuid import uuid4

import pytest

from parishkit.config import ConfigError
from parishkit.stewardship.accounts.bootstrap_schema import bootstrap_version
from parishkit.stewardship.accounts.setup_candidate import (
    CandidateCredential,
    compile_candidate,
)
from parishkit.stewardship.accounts.setup_content_values import CONTENT_STEPS

from .campaign_factory import campaign, schedule
from .content_factory import content
from .test_setup_forms import VALUES


def compilation():
    """All IDs and values are synthetic; no file, SQL or provider is touched."""
    base = bootstrap_version(uuid4(), "admin@example.org")
    attempt = uuid4()
    sections = deepcopy(VALUES) | {
        "branding": {"bundle_id": str(uuid4())},
        "campaign": {"source_result": str(uuid4()), "campaign": campaign()["values"]},
        "schedules": {"records": []},
    }
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
                sections["mail"]
                | {"recipient": sections["testing"]["testing_recipient"]},
            ),
        ],
    }
    return base, args


def test_compilation_is_stable_detached_and_preserves_bootstrap():
    """Manual additions have real operation provenance; all selections are copied."""
    base, args = compilation()
    sections = args["sections"]
    sections["access"]["staff_addresses"] = ["admin@example.org", "staff@example.net"]
    sections["access"]["ministry_addresses"] = ["staff@example.net"]
    sections["access"]["ministry_domains"] = ["example.org"]
    revision = content(str(args["attempt_id"]))
    sections["page_welcome"] = revision
    before = deepcopy(args)
    result = compile_candidate(base, **args)
    assert result == compile_candidate(base, **args)
    assert args == before
    document = result.candidate.document()["sections"]
    rules = document["login_rules"]
    assert base.document()["sections"]["login_rules"][0] in rules
    assert len(rules) == 4
    merged = next(
        row["values"]
        for row in rules
        if row["values"].get("email") == "staff@example.net"
    )
    assert merged["roles"] == ["ministry_leader", "staff"]
    assert merged["grants"] == {
        role: {"manual": str(args["operation_id"])} for role in merged["roles"]
    }
    assert document["campaigns"][0]["id"] == str(args["attempt_id"])
    assert document["campaigns"][0]["values"]["content_versions"] == {
        "welcome": revision["id"]
    }
    sections["parish"]["name"] = "Changed after compilation"
    assert "Changed after compilation" not in str(result.candidate.document())


@pytest.mark.parametrize(
    "missing",
    [
        "parish",
        "branding",
        "access",
        "mail",
        "testing",
        "slack",
        "campaign",
        "schedules",
    ],
)
def test_all_core_steps_require_explicit_completion(missing):
    """An omitted optional choice differs from an explicitly completed empty step."""
    base, args = compilation()
    del args["sections"][missing]
    with pytest.raises(ConfigError, match="required setup"):
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


def test_disabled_page_remains_in_staging_but_not_in_compiled_selection():
    """Disabling a module must not accidentally publish its retained introduction."""
    base, args = compilation()
    args["sections"]["page_financial"] = content(
        str(args["attempt_id"]), slot="financial"
    )
    result = compile_candidate(base, **args)
    assert not result.candidate.document()["sections"].get("content")
    assert args["sections"]["page_financial"]["values"]


@pytest.mark.parametrize("invalid", ["content_owner", "schedule_owner", "template"])
def test_child_ownership_and_template_resolution_are_required(invalid):
    """Detached child records do not gain authority through configuration assembly."""
    base, args = compilation()
    revision = content(str(args["attempt_id"]), kind="email", slot="initial")
    row = schedule(
        str(args["attempt_id"]),
        template_version=revision["id"],
        subject=revision["values"]["subject"],
    )
    args["sections"].update(email_initial=revision, schedules={"records": [row]})
    assert compile_candidate(base, **args)
    if invalid == "content_owner":
        revision["values"]["campaign_id"] = str(uuid4())
    elif invalid == "schedule_owner":
        row["values"]["campaign_id"] = str(uuid4())
    else:
        row["values"]["template_version"] = str(uuid4())
    with pytest.raises(ConfigError):
        compile_candidate(base, **args)


def test_maximum_wizard_can_compile_without_artificial_combined_record_limit():
    """Five fifty-row access lists plus 100 schedules fit the complete intent."""
    from datetime import date, timedelta

    base, args = compilation()
    sections = args["sections"]
    for key in sections["access"]:
        sections["access"][key] = sorted(
            f"{key.replace('_', '')}{index}.example.org"
            if key.endswith("domains")
            else f"{key}{index}@example.org"
            for index in range(50)
        )
    for step in CONTENT_STEPS:
        kind, _, slot = step.partition("_")
        sections[step] = content(str(args["attempt_id"]), kind=kind, slot=slot)
    sections["campaign"]["campaign"]["end_date"] = "2027-02-01"
    rows = []
    for index in range(100):
        kind = "reminder" if index else "initial"
        template = sections[f"email_{kind}"]
        rows.append(
            schedule(
                str(args["attempt_id"]),
                kind=kind,
                date=(date(2026, 10, 1) + timedelta(days=index)).isoformat(),
                template_version=template["id"],
                subject=template["values"]["subject"],
            )
        )
    sections["schedules"]["records"] = rows
    result = compile_candidate(base, **args)
    assert 350 < len(result.patch()) < 400
