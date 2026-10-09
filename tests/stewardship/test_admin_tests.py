"""The sample test email commands' documents and prompt (ADM-11 PR 6b).

Pure tests: the golden documents of ``test sample-preview`` and
``test sample``, built through the projections the commands use, with exact
allowlists of member names (no address and no message body); and that
``test sample`` asks for the page's acknowledgement only while an earlier
test's outcome is unknown. The commands against a real database are in
database/test_admin_test_sample_cli_postgresql.py.
"""

import io
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID

import pytest

from parishkit.stewardship import admin_cli, admin_tests

from .test_admin_reads import FORBIDDEN, members

NOW = datetime(2054, 10, 6, 15, 30, tzinfo=UTC)
CAMPAIGN = UUID("00000000-0000-4000-8000-000000000001")
REVISION = UUID("00000000-0000-4000-8000-000000000003")
KEY = UUID("00000000-0000-4000-8000-00000000000f")
TEST = UUID("00000000-0000-4000-8000-000000000010")
TASK = UUID("00000000-0000-4000-8000-000000000011")


def sample_preview():
    """A preview while an earlier test's outcome is unknown."""
    preview = SimpleNamespace(
        row=SimpleNamespace(campaign_id=CAMPAIGN, request_key=KEY),
        sample=SimpleNamespace(subject="Your pledge", recipient="test@example.org"),
    )
    tests = {
        "pending": False,
        "unknown": True,
        "items": [
            {
                "id": TEST,
                "state": "delivery_unknown",
                "created_at": NOW,
                "current": True,
                "label": "dropped by the projection",
            }
        ],
    }
    return admin_tests.sample_preview_model(preview, tests, "signed", REVISION)


def sample_test():
    """A new test queued by its token."""
    row = SimpleNamespace(
        pk=TEST, request_key=KEY, state="queued", task_id=TASK, created_at=NOW
    )
    return admin_tests.sample_test_model(row, created=True)


def families_preview():
    """Two Families named; one is no longer deliverable."""
    choices = (
        SimpleNamespace(duid=1234, eligible=True, reason="eligible", name="Hidden"),
        SimpleNamespace(
            duid=5678, eligible=False, reason="undeliverable", name="Hidden"
        ),
    )
    preview = SimpleNamespace(
        campaign=SimpleNamespace(pk=CAMPAIGN),
        request_key=KEY,
        families=choices,
        available=8,
        in_progress=2,
        held=False,
        epoch_id=UUID(int=9),
        testing_recipient="test@example.org",
    )
    return admin_tests.families_preview_model(preview, "signed", REVISION)


def families_test():
    """Two new tickets."""
    rows = [
        SimpleNamespace(pk=UUID(int=20 + n), sequence=n, state="queued", task_id=TASK)
        for n in (1, 2)
    ]
    return admin_tests.families_test_model(rows, created=True, request_key=KEY)


def families_status():
    """One prepared ticket; its DUID is not copied."""
    item = {
        "id": TEST,
        "created_at": NOW,
        "request_key": KEY,
        "sequence": 1,
        "state": "prepared",
        "message_state": "pending",
        "duid": 1234,
    }
    return admin_tests.families_status_model(CAMPAIGN, [item])


GOLDEN = {
    "test sample-preview": (
        sample_preview,
        {
            "campaign_id": str(CAMPAIGN),
            "revision_id": str(REVISION),
            "request_key": str(KEY),
            "subject": "[TEST] Your pledge",
            "testing_recipient_set": True,
            "pending": False,
            "unknown": True,
            "tests": [
                {
                    "id": str(TEST),
                    "state": "delivery_unknown",
                    "created_at": NOW.isoformat(),
                    "current": True,
                }
            ],
            "preview": {"token": "signed"},
        },
    ),
    "test families-preview": (
        families_preview,
        {
            "campaign_id": str(CAMPAIGN),
            "revision_id": str(REVISION),
            "request_key": str(KEY),
            "families": [
                {"duid": 1234, "eligible": True, "eligibility": "eligible"},
                {"duid": 5678, "eligible": False, "eligibility": "undeliverable"},
            ],
            "available": 8,
            "in_progress": 2,
            "held": False,
            "credentials_ready": True,
            "testing_recipient_set": True,
            "preview": {"token": "signed"},
        },
    ),
    "test families": (
        families_test,
        {
            "created": True,
            "request_key": str(KEY),
            "tickets": [
                {
                    "id": str(UUID(int=20 + n)),
                    "sequence": n,
                    "state": "queued",
                    "task_id": str(TASK),
                }
                for n in (1, 2)
            ],
        },
    ),
    "test status": (
        families_status,
        {
            "campaign_id": str(CAMPAIGN),
            "tickets": [
                {
                    "id": str(TEST),
                    "created_at": NOW.isoformat(),
                    "request_key": str(KEY),
                    "sequence": 1,
                    "state": "prepared",
                    "message_state": "pending",
                }
            ],
        },
    ),
    "test sample": (
        sample_test,
        {
            "created": True,
            "request_key": str(KEY),
            "test": {
                "id": str(TEST),
                "state": "queued",
                "task_id": str(TASK),
                "created_at": NOW.isoformat(),
            },
        },
    ),
}
ALLOWED = {
    "test sample-preview": {
        "campaign_id",
        "revision_id",
        "request_key",
        "subject",
        "testing_recipient_set",
        "pending",
        "unknown",
        "tests",
        "id",
        "state",
        "created_at",
        "current",
        "preview",
        "token",
    },
    # The preview's input DUIDs are echoed with their eligibility, as the
    # specification says; never a name.
    "test families-preview": {
        "campaign_id",
        "revision_id",
        "request_key",
        "families",
        "eligible",
        "eligibility",
        "available",
        "in_progress",
        "held",
        "credentials_ready",
        "testing_recipient_set",
        "preview",
        "token",
    },
    "test families": {"created", "request_key", "tickets", "id", "sequence"}
    | {"state", "task_id"},
    "test status": {"campaign_id", "tickets", "id", "created_at", "request_key"}
    | {"sequence", "state", "message_state"},
    "test sample": {"created", "request_key", "test", "id", "state", "task_id"}
    | {"created_at"},
}


@pytest.mark.parametrize("command", sorted(GOLDEN))
def test_each_document_is_its_golden_projection(command):
    """The exact document, from the projection the command uses."""
    build, expected = GOLDEN[command]
    assert build().to_document() == expected


@pytest.mark.parametrize("command", sorted(GOLDEN))
def test_each_documents_members_are_exactly_its_allowlist(command):
    """Members at any depth equal the allowlist; none is personal data.

    ``token`` is the one exception to the personal-data pattern, as for
    ``schedule preview``: it is the page's signed preview.
    """
    document = GOLDEN[command][0]().to_document()
    found = set(members(document))
    if command == "test families-preview":
        # The input DUIDs, under "duid", are the one exception (see above).
        assert "duid" in found
        found.discard("duid")
    assert found == ALLOWED[command], command
    for name in ALLOWED[command] - {"token"}:
        assert FORBIDDEN.search(name) is None, (command, name)
    # The Testing recipient's address and Family names never leave the page.
    assert "@" not in str(document) and "Hidden" not in str(document)


def test_every_test_command_has_a_golden_document():
    """The PR 6 test commands, and the catalog lists each model's fields."""
    entries = {entry["name"]: entry for entry in admin_cli.catalog()}
    pr6 = {spec.name for spec in admin_cli.COMMANDS if spec.pr == 6}
    assert {name for name in pr6 if name.split()[0] == "test"} == set(GOLDEN)
    for command, (build, _) in GOLDEN.items():
        assert entries[command]["result_fields"] == list(build().field_names())


@pytest.mark.parametrize("unknown", [False, True])
def test_the_sample_prompts_only_while_an_outcome_is_unknown(monkeypatch, unknown):
    """The page shows its checkbox only then; so the command asks only then."""
    sent = []
    monkeypatch.setattr(admin_tests, "unknown_outcome", lambda caller, run: unknown)
    monkeypatch.setattr(
        admin_tests,
        "send_sample",
        lambda caller, run, **values: sent.append(values["acknowledge_unknown"]),
    )
    stderr = io.StringIO()
    context = {
        "caller": None,
        "stdin": io.BytesIO(b""),
        "stderr": stderr,
        "yes": False,
    }
    args = SimpleNamespace(token="signed")
    if unknown:
        # Nothing typed: exit 4, and nothing is sent.
        with pytest.raises(admin_cli.ConfirmationRequired):
            admin_cli.sample_test(args, None, None, context)
        assert sent == [] and admin_tests.UNKNOWN_ACKNOWLEDGEMENT in stderr.getvalue()
        context["stdin"] = io.BytesIO(b"yes\n")
    admin_cli.sample_test(args, None, None, context)
    assert sent == [unknown]
    assert ("Type yes" in stderr.getvalue()) == unknown


def test_the_sample_token_binds_the_recipient_by_digest():
    """The page's token is signed, not encrypted: it never holds the address."""
    import json

    from parishkit.stewardship.accounts.campaign_mail import (
        MailPreview,
        recipient_digest,
    )

    row = SimpleNamespace(
        requested_by_id=UUID(int=1),
        request_key=KEY,
        configuration_id=UUID(int=2),
        campaign_id=CAMPAIGN,
        template_id=UUID(int=3),
        fingerprint="f",
    )
    sample = SimpleNamespace(recipient=" Test@Example.org ")
    binding = MailPreview(row, sample, "digest").binding()
    assert "example.org" not in json.dumps(binding).lower()
    assert binding["recipient"] == recipient_digest("test@example.org")
    assert recipient_digest("other@example.org") != binding["recipient"]


def test_the_families_send_always_prompts(monkeypatch):
    """The page always asks, so the command always asks; nothing typed sends nothing."""
    sent = []
    monkeypatch.setattr(admin_tests, "families_count", lambda token: 2)
    monkeypatch.setattr(
        admin_tests, "send_families", lambda caller, run, **values: sent.append(1)
    )
    stderr = io.StringIO()
    context = {"caller": None, "stdin": io.BytesIO(b""), "stderr": stderr, "yes": False}
    args = SimpleNamespace(token="signed")
    with pytest.raises(admin_cli.ConfirmationRequired):
        admin_cli.families_test(args, None, None, context)
    assert sent == []
    assert "2 Families" in stderr.getvalue()
    assert admin_tests.FAMILIES_ACKNOWLEDGEMENT in stderr.getvalue()
    context["stdin"] = io.BytesIO(b"yes\n")
    admin_cli.families_test(args, None, None, context)
    assert sent == [1]


def test_the_families_token_binds_the_recipient_by_digest():
    """The chosen-Family token is signed, not encrypted: no address in it."""
    import json

    from parishkit.stewardship.accounts.campaign_family_test import (
        FamilyTestPreview,
    )
    from parishkit.stewardship.accounts.campaign_mail import recipient_digest

    preview = FamilyTestPreview(
        campaign=SimpleNamespace(pk=CAMPAIGN, readiness_revision=1),
        template=SimpleNamespace(record_id=REVISION),
        configuration=SimpleNamespace(digest="d"),
        request_key=KEY,
        actor_id=UUID(int=1),
        epoch_id=None,
        families=(),
        in_progress=0,
        testing_recipient="test@example.org",
    )
    binding = preview.binding()
    assert "example.org" not in json.dumps(binding)
    assert binding["recipient"] == recipient_digest("test@example.org")
