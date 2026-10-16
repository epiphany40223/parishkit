"""Every prompting ``pk-admin`` command checks its role first (#1339, #1341).

A full-scope session whose user no longer holds the action's capability
(an Administrator who is now Staff) must be ``denied`` before the command
reads an input, looks anything up or prompts, so it sees no prompt and
learns nothing of the state a prompt would describe. Each command checks
the capability its action's own admission checks, so one Staff may run
stays Staff's.
"""

from argparse import Namespace

import pytest

from parishkit.stewardship import (
    admin_changes,
    admin_cli,
    admin_delivery_control,
    admin_digests,
    admin_exports,
    admin_history,
    admin_hosted_files,
    admin_integration_changes,
    admin_integration_keys,
    admin_tests,
    system_health,
)
from parishkit.stewardship.accounts import family_maintenance
from parishkit.stewardship.accounts.policy import Capability

# Each prompting command whose handler checks the role itself, and the
# capability it checks (None: ``CONFIGURE``, an Administrator's).
ROLE_FIRST = {
    "system backup-now": None,
    "system debug-off": None,
    "system refresh-accept": None,
    "system debug-allow": None,
    "config request cancel": None,
    "test sample": None,
    "test families": None,
    "portal maintenance set": None,
    "delivery resend": Capability.BACKGROUND_WORK,
    "delivery refusal-clear": Capability.BACKGROUND_WORK,
    "delivery bounce-report": Capability.BACKGROUND_WORK,
    "export archive": None,
    "export financial": Capability.FINANCIAL_DETAIL,
    "export directory": Capability.FAMILY_CODES,
    "export postal": Capability.FAMILY_CODES,
    "export family-timeline": Capability.CAMPAIGN_REPORT,
    "digest weekly-request": Capability.BACKGROUND_WORK,
    "census resolve": Capability.VIEW_CENSUS,
    "history remove": None,
    "integration confirm": None,
    "integration key finish-switching": None,
    # Pause and resume mail's Confirm (PR 7b, #463).
    "send confirm": None,
}

# The prompting commands Staff may run, as on their pages.
STAFF_MAY = {
    "export financial",
    "export directory",
    "export postal",
    "export family-timeline",
    "census resolve",
}

# Prompting commands whose first step is their page's own admitting read
# (it checks the role before it reads anything), with that read.
ADMITTING_READ = {
    "files delete": (admin_hosted_files, "preview_deletion", {"file_id": ["a"]}),
    "export regenerate": (admin_exports, "regeneration_prompts", {"export_id": "a"}),
    "history confirm": (admin_history, "pending_replacement", {"token": "-"}),
}

# Everything a handler may read, look up or prompt with after its role check.
LATER = (
    (admin_cli, "_input"),
    (admin_cli, "_request_key"),
    (admin_cli, "confirm"),
    (system_health, "backup_preview_lines"),
    (system_health, "debug_off_preview_lines"),
    (system_health, "debug_allow_preview_lines"),
    (system_health, "accept_prompt_lines"),
    (admin_tests, "unknown_outcome"),
    (admin_tests, "families_count"),
    (admin_digests, "acknowledgement"),
    (family_maintenance, "clean_message"),
    (admin_integration_changes, "applied_settings"),
    (admin_integration_changes, "prompt_lines"),
    (admin_integration_keys, "finish_prompt_lines"),
    (admin_delivery_control, "confirm_prompt_lines"),
    (admin_delivery_control, "confirm_control"),
)


class Untouchable:
    """A runtime whose every attribute read fails: nothing may be looked up."""

    def __getattr__(self, name):
        """Refuse any read of the runtime (``runtime.store`` and the like)."""
        raise AssertionError(f"read runtime.{name} before the role check")


def unexpected(*arguments, **options):
    """Nothing after the role check may run."""
    raise AssertionError("read, looked up or prompted before the role check")


def handler(name):
    """The catalog handler of the command ``name``."""
    return next(spec.handler for spec in admin_cli.COMMANDS if spec.name == name)


def test_every_prompting_command_is_covered():
    """A new prompting command must say how it checks its role first."""
    prompting = {spec.name for spec in admin_cli.COMMANDS if spec.prompts}
    assert prompting == set(ROLE_FIRST) | set(ADMITTING_READ)


@pytest.mark.parametrize("name", sorted(ROLE_FIRST))
def test_the_role_is_checked_before_any_input_lookup_or_prompt(name, monkeypatch):
    """The handler's first step is the read-only check of its capability.

    The arguments are empty and the runtime refuses every read, so a handler
    that touched either before the check fails here; so does one reaching
    an input, a request key, a prompt builder or the prompt.
    """
    calls = []

    def denied(caller, service, *, read_only=False, capability=None):
        """Refuse as the admission does for a user who lacks the capability."""
        calls.append((read_only, capability))
        raise PermissionError("denied")

    monkeypatch.setattr(admin_changes, "_admit_change", denied)
    for module, attribute in LATER:
        monkeypatch.setattr(module, attribute, unexpected)
    with pytest.raises(PermissionError):
        handler(name)(Namespace(), None, Untouchable(), {"caller": None})
    assert calls == [(True, ROLE_FIRST[name])]


@pytest.mark.parametrize("name", sorted(ADMITTING_READ))
def test_the_admitting_read_comes_before_any_input_or_prompt(name, monkeypatch):
    """These handlers' first read is their page's admission: a refusal
    there ends the command before an input, a request key or the prompt."""
    module, attribute, arguments = ADMITTING_READ[name]

    def denied(*arguments, **options):
        """Refuse as the page's admission does."""
        raise PermissionError("denied")

    monkeypatch.setattr(module, attribute, denied)
    for later_module, later in LATER:
        monkeypatch.setattr(later_module, later, unexpected)
    with pytest.raises(PermissionError):
        handler(name)(Namespace(**arguments), None, None, {"caller": None})


def test_staff_capabilities_stay_staff_s():
    """The commands Staff may run check a capability Staff holds, so the
    early check never takes a command away from Staff."""
    from uuid import uuid4

    from parishkit.stewardship.accounts.policy import Principal, allows

    staff = Principal(identity=uuid4(), roles=frozenset({"staff"}))
    for name, capability in ROLE_FIRST.items():
        held = allows(staff, capability or Capability.CONFIGURE)
        assert held == (name in STAFF_MAY), name
