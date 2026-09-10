"""Real private files exercise sealed staging, rename uncertainty and rollback."""

import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from parishkit.stewardship.accounts import credential_files
from parishkit.stewardship.accounts.credential_files import CredentialFiles
from parishkit.stewardship.accounts.credential_handoff import PrivateHandoff
from parishkit.stewardship.accounts.cryptography import CryptographicError, Key
from parishkit.stewardship.accounts.key_files import (
    file_fingerprint,
    read_private,
    write_private,
)

NOW = datetime(2026, 9, 10, tzinfo=UTC)
PRIOR = b"synthetic-private-previous-credential"
NEXT = b"synthetic-private-next-credential"


@pytest.fixture
def replacement(tmp_path):
    """Synthetic target files are independent from any configured runtime root."""
    directory = tmp_path / "target"
    directory.mkdir(mode=0o700)
    path = directory / "credential"
    write_private(path, PRIOR)
    private = PrivateHandoff("slack", Key("handoff", "active", b"h" * 32))
    request = uuid4()
    intent = dict(
        request_id=request,
        candidate=private.public().seal(request, NEXT),
        expected_fingerprint=file_fingerprint(PRIOR),
        consumers=("worker",),
        expires_at=NOW + timedelta(minutes=5),
        now=NOW,
        validate=lambda value: True,
    )
    return CredentialFiles(path, private), intent


def _journal(files):
    """Inspect only synthetic ciphertext; seeded plaintext must not appear."""
    value = read_private(files.journal_path, maximum=credential_files.JOURNAL_LIMIT)
    assert PRIOR not in value and NEXT not in value
    return json.loads(value)


def test_file_replacement_waits_for_consumer_ack_and_scrubs_staging(replacement):
    files, intent = replacement
    with files.lock():
        first = files.prepare(**intent)
        assert first.state == "prepared"
        assert files.prepare(**intent) == first
        assert read_private(files.path) == PRIOR
        assert _journal(files)["prior_ciphertext"]
        installed = files.install(intent["request_id"], now=NOW)
        assert installed.state == "installed"
        assert installed.fingerprint == file_fingerprint(NEXT)
        assert read_private(files.path) == NEXT
        assert _journal(files)["candidate_ciphertext"] is None
        assert (
            files.acknowledge(intent["request_id"], fingerprints={}, now=NOW)
            == installed
        )
        assert (
            files.acknowledge(
                intent["request_id"],
                fingerprints={"worker": file_fingerprint(PRIOR)},
                now=NOW,
            )
            == installed
        )
        applied = files.acknowledge(
            intent["request_id"],
            fingerprints={"worker": installed.fingerprint},
            now=NOW,
        )
        assert applied.state == "applied"
        assert _journal(files)["prior_ciphertext"] is None
        assert (
            files.install(intent["request_id"], now=NOW + timedelta(days=1)) == applied
        )
        with pytest.raises(CryptographicError, match="new replacement"):
            files.rollback(intent["request_id"])
        with pytest.raises(CryptographicError, match="not durable"):
            files.release(intent["request_id"], record_terminal=lambda receipt: False)
        assert files.journal_path.exists()
        seen = []

        def record(receipt):
            """The later database owner must durably acknowledge this safe receipt."""
            seen.append(receipt)
            return True

        assert files.release(intent["request_id"], record_terminal=record) == applied
        assert seen == [applied]
        assert not files.journal_path.exists()
        assert read_private(files.path) == NEXT


@pytest.mark.parametrize("installed", [False, True])
def test_expiry_restores_prior_working_file_and_scrubs(replacement, installed):
    files, intent = replacement
    with files.lock():
        files.prepare(**intent)
        if installed:
            files.install(intent["request_id"], now=NOW)
        expired = files.install(intent["request_id"], now=intent["expires_at"])
        assert expired.state == "rolled_back"
        assert read_private(files.path) == PRIOR
        assert _journal(files)["candidate_ciphertext"] is None
        assert _journal(files)["prior_ciphertext"] is None
        assert files.rollback(intent["request_id"]) == expired


def test_post_rename_crash_is_reconciled_without_losing_prior(replacement, monkeypatch):
    files, intent = replacement
    original = credential_files.write_private

    def rename_then_fail(path, value, **kwargs):
        """An fsync failure does not prove rename failed to select the candidate."""
        original(path, value, **kwargs)
        if path == files.path:
            raise OSError("Synthetic post-rename interruption")

    with files.lock():
        files.prepare(**intent)
    monkeypatch.setattr(credential_files, "write_private", rename_then_fail)
    with pytest.raises(CryptographicError), files.lock():
        files.install(intent["request_id"], now=NOW)
    assert read_private(files.path) == NEXT
    assert _journal(files)["state"] == "prepared"
    monkeypatch.setattr(credential_files, "write_private", original)
    resumed = CredentialFiles(files.path, files.private)
    with resumed.lock():
        assert resumed.install(intent["request_id"], now=NOW).state == "installed"
        assert resumed.rollback(intent["request_id"]).state == "rolled_back"
    assert read_private(files.path) == PRIOR


def test_failed_validation_preserves_prior_and_never_stages_plaintext(replacement):
    files, intent = replacement

    def fail(value):
        """A bad provider may reflect submitted credentials in an exception."""
        raise ValueError(value.decode())

    with files.lock(), pytest.raises(CryptographicError) as error:
        files.prepare(**{**intent, "validate": fail})
    assert NEXT.decode() not in str(error.value)
    assert not files.journal_path.exists()
    assert read_private(files.path) == PRIOR


def test_wrong_fingerprint_never_clobbers_unrelated_file(replacement):
    files, intent = replacement
    with files.lock():
        with pytest.raises(CryptographicError, match="has changed"):
            files.prepare(**{**intent, "expected_fingerprint": "a" * 64})
        files.prepare(**intent)
        write_private(files.path, b"independently-selected-synthetic-credential")
        for action in (
            lambda: files.install(intent["request_id"], now=NOW),
            lambda: files.rollback(intent["request_id"]),
        ):
            with pytest.raises(CryptographicError, match="unexpected fingerprint"):
                action()
    assert read_private(files.path) == b"independently-selected-synthetic-credential"


def test_initial_install_rollback_restores_absence(replacement):
    files, intent = replacement
    files.path.unlink()
    with files.lock():
        files.prepare(**{**intent, "expected_fingerprint": None})
        files.install(intent["request_id"], now=NOW)
        files.rollback(intent["request_id"])
        assert not files.path.exists()
        assert _journal(files)["state"] == "rolled_back"


def test_target_lock_and_journal_identity_exclude_competing_requests(replacement):
    files, intent = replacement
    with pytest.raises(CryptographicError, match="target lock"):
        files.prepare(**intent)
    another = CredentialFiles(files.path, files.private)
    with files.lock():
        with pytest.raises(CryptographicError), another.lock():
            pytest.fail("Second target lock must not be acquired")
        files.prepare(**intent)
        with pytest.raises(CryptographicError):
            files.prepare(**{**intent, "request_id": uuid4()})
        with pytest.raises(CryptographicError, match="already bound"):
            files.prepare(
                **{
                    **intent,
                    "candidate": files.private.public().seal(
                        intent["request_id"], b"different"
                    ),
                }
            )
        with pytest.raises(CryptographicError, match="not terminal"):
            files.release(intent["request_id"], record_terminal=lambda receipt: True)
    with another.lock():
        assert another.install(intent["request_id"], now=NOW).state == "installed"


def test_rollback_cipher_cannot_be_swapped_with_candidate(replacement):
    files, intent = replacement
    with files.lock():
        files.prepare(**intent)
        journal = _journal(files)
        candidate = journal["candidate_ciphertext"]
        with pytest.raises(CryptographicError):
            files.private.open(intent["request_id"], candidate, purpose="rollback")
        with pytest.raises(CryptographicError):
            files.private.open(intent["request_id"], journal["prior_ciphertext"])


@pytest.mark.parametrize(
    "consumers", [(), ("web",), ("worker", "worker"), ("private-user",)]
)
def test_consumer_inventory_matches_target_authority(replacement, consumers):
    files, intent = replacement
    with files.lock(), pytest.raises(CryptographicError, match="consumer inventory"):
        files.prepare(**{**intent, "consumers": consumers})
    assert read_private(files.path) == PRIOR


def test_maximum_sized_credentials_fit_sealed_journal(replacement):
    files, intent = replacement
    value = b"x" * (128 * 1024)
    write_private(files.path, value)
    with files.lock():
        files.prepare(
            **{
                **intent,
                "expected_fingerprint": file_fingerprint(value),
                "candidate": files.private.public().seal(
                    intent["request_id"], b"y" * len(value)
                ),
            }
        )
        files.install(intent["request_id"], now=NOW)
        files.rollback(intent["request_id"])
    assert read_private(files.path) == value
