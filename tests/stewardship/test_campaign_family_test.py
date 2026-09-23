"""Pure chosen-Family test rules: bounded input, closed bindings, task routing."""

from types import SimpleNamespace
from uuid import uuid4

import pytest
from django.core import signing

from parishkit.stewardship.accounts import campaign_family_test as intake
from parishkit.stewardship.jobs import family_mail_test_tasks as tasks


def test_family_duids_are_bounded_distinct_and_exact():
    """Grouped digits are accepted; names, duplicates and long lists are not."""
    assert intake.parse_family_duids("1\n2,345 \n 7") == (1, 2345, 7)
    assert intake.parse_family_duids("") == ()
    for value in ("1 1", " ".join(str(n) for n in range(1, 12)), "Smith", None, "0"):
        with pytest.raises(ValueError):
            intake.parse_family_duids(value)


def binding(**changes):
    """A complete signed review with every key the confirmation must repeat."""
    values = {
        "actor": str(uuid4()),
        "key": str(uuid4()),
        "campaign": str(uuid4()),
        "template": str(uuid4()),
        "digest": "abc:1",
        "epoch": str(uuid4()),
        "recipient": "test@example.org",
        "families": ["1", "2"],
    }
    return signing.dumps(values | changes, salt=intake.SALT)


@pytest.mark.parametrize(
    "token",
    [
        None,
        "x" * 4097,
        binding(families=[]),
        binding(families=[1]),
        binding(extra="field"),
        signing.dumps({"actor": "a"}, salt=intake.SALT),
        signing.dumps("private", salt=intake.SALT),
        binding() + "changed",
    ],
    # Signed tokens embed random UUIDs and a timestamp; fixed ids keep test
    # collection identical between the host and the image (test_compose).
    ids=[
        "missing",
        "oversized",
        "no-families",
        "integer-family",
        "extra-field",
        "missing-keys",
        "not-a-mapping",
        "bad-signature",
    ],
)
def test_malformed_or_forged_preview_never_reaches_the_database(token):
    """Shape and signature are checked before any session or campaign read."""
    with pytest.raises((ValueError, signing.BadSignature)):
        intake._binding(token)


def test_expired_preview_is_rejected(monkeypatch):
    """A reviewed list is good for fifteen minutes, then must be reviewed again."""
    token = binding()
    assert intake._binding(token)["families"] == ["1", "2"]
    now = signing.time.time()
    monkeypatch.setattr(signing.time, "time", lambda: now + 901)
    with pytest.raises(signing.SignatureExpired):
        intake._binding(token)


def test_acknowledgement_is_required_before_any_lookup():
    """Confirming without the acknowledgement checkbox is a plain input error."""
    with pytest.raises(ValueError, match="Acknowledge"):
        intake.request_tests(
            object(),
            object(),
            uuid4(),
            uuid4(),
            preview_token=binding(),
            acknowledge=False,
        )


def test_sql_recovery_budget_mirrors_the_python_attempt_budget():
    """Lowering either budget without the other must fail here, not in production."""
    from pathlib import Path

    from parishkit.stewardship.jobs import family_mail_delivery_tasks as delivery

    text = (
        Path(delivery.__file__).parents[1] / "schema" / "family_dispatch.sql"
    ).read_text()
    assert f"t.attempt>={delivery.MAX_ATTEMPTS} AND own.purpose='family_test'" in text
    assert text.count("t.attempt>=") == 1


def test_form_reports_bad_duid_lists_as_field_errors():
    """The page re-renders with the typed list and a message, not a generic error."""
    from parishkit.stewardship.accounts.campaign_family_test_views import FamilyTestForm

    valid = FamilyTestForm({"families": "1\n2,345"})
    assert valid.is_valid() and valid.cleaned_data["families"] == (1, 2345)
    for value in ("1 1", "Smith", " ".join(str(n) for n in range(1, 12))):
        form = FamilyTestForm({"families": value})
        assert not form.is_valid() and form.errors["families"]
        assert form["families"].value() == value


def test_scheduler_cannot_execute_and_worker_needs_every_dependency():
    """Only the general worker, with its keyrings and origin, may prepare a test."""
    with pytest.raises(TypeError):
        tasks.family_test_handler(general=object(), mac=object(), public=object())
    handler = tasks.family_test_handler(scheduler=True)
    with pytest.raises(PermissionError):
        handler.execute(object())


@pytest.mark.parametrize(
    "state,scope_live,attempt,expected",
    [
        ("prepared", True, 1, "recovery_complete"),
        ("cancelled", True, 1, "recovery_cancel"),
        ("queued", False, 1, "recovery_cancel"),
        ("queued", True, 1, "recovery_retry"),
        ("queued", True, 5, "recovery_fail"),
    ],
)
def test_recovery_follows_the_ticket_not_the_exception(
    monkeypatch, state, scope_live, attempt, expected
):
    """Abandoned work completes only from a committed receipt, never a resend."""
    ticket = SimpleNamespace(state=state)
    monkeypatch.setattr(tasks, "owned_test", lambda status: ticket)
    monkeypatch.setattr(tasks, "_attempts", lambda status: status.attempt)
    monkeypatch.setattr(
        tasks,
        "disposition",
        lambda row, source_check=False: {
            "prepared": "complete",
            "cancelled": "safe_cancel",
        }.get(row.state, None if scope_live else "safe_cancel"),
    )
    plan = tasks.recover_test(SimpleNamespace(state="abandoned", attempt=attempt))
    assert plan.action == expected
    with pytest.raises(PermissionError):
        tasks.recover_test(SimpleNamespace(state="running", attempt=1))


@pytest.mark.parametrize(
    "action,terminal,expected",
    [
        ("complete", "complete", True),
        ("complete", None, False),
        ("safe_cancel", "safe_cancel", True),
        ("safe_cancel", "complete", False),
        ("claim", None, True),
        ("effect", "complete", True),
        ("retryable_failure", None, True),
        ("progress", "complete", True),
        ("explicit_retry", None, False),
        ("lease_expired", None, True),
    ],
)
def test_admission_requires_matching_terminal_evidence(
    monkeypatch, action, terminal, expected
):
    """Completion and cancellation need their exact proof; ordinary steps do not."""
    monkeypatch.setattr(tasks, "owned_test", lambda status: SimpleNamespace())
    monkeypatch.setattr(
        tasks, "disposition", lambda ticket, source_check=False: terminal
    )
    assert tasks.admit_test(action, SimpleNamespace(state="running")) is expected


def test_held_ticket_is_skipped_at_claim_but_deferred_during_execution(monkeypatch):
    """A temporary gate denies claiming quietly and surfaces as a hold mid-effect."""

    def held(ticket, source_check=False):
        raise tasks.FamilyTestHeld("synthetic gate")

    monkeypatch.setattr(tasks, "owned_test", lambda status: SimpleNamespace())
    monkeypatch.setattr(tasks, "disposition", held)
    status = SimpleNamespace(state="running")
    assert tasks.admit_test("claim", status) is False
    assert tasks.admit_test("hint", status) is False
    with pytest.raises(tasks.FamilyTestHeld):
        tasks.admit_test("effect", status)
    with pytest.raises(tasks.FamilyTestHeld):
        tasks.admit_test("safe_cancel", status)
    assert tasks.recover_test(SimpleNamespace(state="abandoned", attempt=1)) is None
