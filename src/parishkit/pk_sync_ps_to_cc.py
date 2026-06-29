"""Implementation for the pk-sync-ps-to-cc command."""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import fcntl
import html
import json
import logging
from collections import defaultdict
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass, field
from importlib.metadata import version
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from parishkit.cli import (
    default_run_dir,
    parser_with_common_options,
    require_explicit_write_mode,
    resolve_common_options,
    run_user_facing,
)
from parishkit.config import ConfigData, ConfigError, load_yaml_config, resolve_path
from parishkit.constant_contact import (
    ConstantContactClient,
    ConstantContactConfig,
    create_contact_dict,
    get_access_token,
    link_cc_data,
    link_contacts_to_ps_members,
    load_client_id,
    sign_up_form_body,
    update_contact_body,
)
from parishkit.email.base import Email, EmailProvider, provider_from_config
from parishkit.files import atomic_write_text
from parishkit.logging import log_extra, setup_logging
from parishkit.parishsoft import (
    ParishSoftData,
    load_families_and_members,
    member_email_addresses,
)
from parishkit.parishsoft_runtime import parishsoft_client_from_config

DEFAULT_UNSUBSCRIBED_REPORT_STATE = (
    default_run_dir() / "pk-sync-ps-to-cc-unsubscribed-report.json"
)
DEFAULT_UNSUBSCRIBED_REPORT_TIME = dt.time(hour=2)
DEFAULT_UNSUBSCRIBED_REPORT_WINDOW_MINUTES = 60
WEEKDAY_NAMES = {
    "monday": 0,
    "mon": 0,
    "tuesday": 1,
    "tue": 1,
    "wednesday": 2,
    "wed": 2,
    "thursday": 3,
    "thu": 3,
    "friday": 4,
    "fri": 4,
    "saturday": 5,
    "sat": 5,
    "sunday": 6,
    "sun": 6,
}


def default_unsubscribed_report_state() -> Path:
    """Return the current default Constant Contact report state path."""
    return default_run_dir() / "pk-sync-ps-to-cc-unsubscribed-report.json"


@dataclass(frozen=True)
class CCSyncMapping:
    """One configured source-to-target sync.

    Maps a single ParishSoft workgroup to a single Constant Contact list, plus
    the addresses that should receive a summary notification for that pairing.
    """

    source_workgroup: str
    target_list: str
    notifications: tuple[str, ...] = ()
    allow_empty: bool = False


@dataclass(frozen=True)
class CCUnsubscribedReportConfig:
    """Schedule for the standalone unsubscribed-contacts report.

    ``day_of_week`` optionally restricts the report to one local weekday.
    ``time`` and ``window_minutes`` define the local-time send window.
    ``state_file`` records the last local date for which the report was sent,
    so frequent cron invocations do not send duplicate reports.
    """

    enabled: bool = False
    day_of_week: int | None = None
    time: dt.time = DEFAULT_UNSUBSCRIBED_REPORT_TIME
    window_minutes: int = DEFAULT_UNSUBSCRIBED_REPORT_WINDOW_MINUTES
    state_file: Path = field(
        default_factory=lambda: default_unsubscribed_report_state()
    )


@dataclass(frozen=True)
class ReportScheduleDecision:
    """Decision describing whether the standalone report should run now."""

    due: bool
    reason: str
    run_date: str


@dataclass(frozen=True)
class CCSyncConfig:
    """Resolved configuration for a sync run.

    Holds the ordered list mappings and the global toggle for pushing name
    updates, plus notification and report settings.
    """

    mappings: tuple[CCSyncMapping, ...]
    update_names: bool = False
    sender: str | None = None
    unsubscribed_report: CCUnsubscribedReportConfig = CCUnsubscribedReportConfig()


@dataclass(frozen=True)
class CCAction:
    """A single pending change against Constant Contact.

    ``type`` is one of ``create``, ``subscribe``, ``unsubscribe``, or
    ``update_name``; ``sync_index`` ties the action back to the mapping (and
    thus the notification group) it came from, and is ``None`` for actions such
    as name updates that are not specific to one list.
    """

    type: str
    email: str
    sync_index: int | None
    detail: str
    list_name: str | None = None
    list_uuid: str | None = None
    new_first: str | None = None
    new_last: str | None = None


Loader = Callable[..., ParishSoftData]
CCFactory = Callable[[ConfigData], ConstantContactClient]


def _text_list(values: Sequence[str]) -> str:
    """Render a short list of strings for human-readable log messages."""
    return ", ".join(values) if values else "none"


def _mapping_summary(mapping: CCSyncMapping) -> str:
    """Return a readable source-to-target list mapping label."""
    return f"{mapping.source_workgroup} -> {mapping.target_list}"


def _unsubscribed_summary(
    unsubscribed: Sequence[Sequence[tuple[str, str, str]]],
) -> str:
    """Return readable unsubscribed addresses grouped across mappings."""
    return _text_list(
        [
            email
            for mapping_items in unsubscribed
            for email, _names, _duids in mapping_items
        ]
    )


def main(
    argv: Sequence[str] | None = None,
    *,
    loader: Loader = load_families_and_members,
    cc_factory: CCFactory | None = None,
    email_provider: EmailProvider | None = None,
    now: dt.datetime | None = None,
) -> int:
    """Parse arguments and dispatch the sync command.

    The ``loader``, ``cc_factory``, and ``email_provider`` parameters exist so
    tests can inject fakes in place of the real ParishSoft, Constant Contact,
    and email integrations. ``--version`` short-circuits before any of that
    work. Returns a process exit code.
    """
    parser = parser_with_common_options(
        "pk-sync-ps-to-cc",
        description="Synchronize ParishSoft workgroups to Constant Contact lists.",
    )
    parser.add_argument("--version", action="store_true")
    parser.add_argument("--update-names", action="store_true")
    args = parser.parse_args(argv)
    if args.version:
        print(f"pk-sync-ps-to-cc {version('parishkit')}")
        return 0
    return run_user_facing(lambda: _run(args, loader, cc_factory, email_provider, now))


def _run(
    args: argparse.Namespace,
    loader: Loader,
    cc_factory: CCFactory | None,
    email_provider: EmailProvider | None,
    now: dt.datetime | None,
) -> int:
    """Run the command after common CLI setup.

    The steps are kept explicit so operational behavior remains easy to
    audit and test.
    """
    common = resolve_common_options(args)
    config = load_yaml_config(common.config)
    log = setup_logging(
        verbose=common.verbose or common.dry_run,
        debug=common.debug,
        log_file=common.log_file,
        log_dir=common.log_dir,
        logger_name="parishkit.pk_sync_ps_to_cc",
        slack_token_file=common.slack_token_file,
        slack_channel=common.slack_channel,
        slack_level=common.slack_log_level,
    )
    try:
        require_explicit_write_mode(common, "pk-sync-ps-to-cc")
        config_base_dir = common.config.parent if common.config else None
        sync_config = cc_sync_config_from_yaml(config, base_dir=config_base_dir)
        # CLI flags can only turn the toggles on, never off: a command-line opt-in
        # is OR'd with whatever the YAML already requested.
        sync_config = CCSyncConfig(
            mappings=sync_config.mappings,
            update_names=sync_config.update_names or bool(args.update_names),
            sender=sync_config.sender,
            unsubscribed_report=sync_config.unsubscribed_report,
        )
        log.info(
            "Configured %s Constant Contact list sync(s); update_names=%s",
            len(sync_config.mappings),
            sync_config.update_names,
        )
        log.debug(
            "Constant Contact sync mappings: %s",
            _text_list([_mapping_summary(mapping) for mapping in sync_config.mappings]),
            extra=log_extra(sync_config.mappings),
        )
        ps_client = parishsoft_client_from_config(common, config)
        log.info("Loading active ParishSoft families and members")
        data = loader(ps_client, active_only=True, parishioners_only=False)
        log.info(
            "Loaded %s member(s), %s family/families, %s ministry membership(s), "
            "and %s workgroup membership(s)",
            len(data.members),
            len(data.families),
            len(data.ministry_type_memberships),
            len(data.member_workgroup_memberships),
        )
        log.debug("Dry-run mode is %s", "enabled" if common.dry_run else "disabled")
        validate_configured_parishsoft_workgroups(sync_config, data)
        cc_client = (
            cc_factory(config)
            if cc_factory
            else constant_contact_client(config, base_dir=config_base_dir)
        )
        log.info("Loading Constant Contact lists and contacts")
        cc_lists, cc_contacts = load_cc_data(cc_client)
        log.info(
            "Loaded %s Constant Contact list(s) and %s contact(s)",
            len(cc_lists),
            len(cc_contacts),
        )
        ps_members_by_email = parishsoft_members_by_email(data.members)
        link_contacts_to_ps_members(cc_contacts, data.members)
        desired_emails = resolve_desired_state(sync_config, data, cc_lists)
        for mapping, emails in zip(sync_config.mappings, desired_emails, strict=True):
            log.info(
                "Resolved %s desired email(s) from %s to %s",
                len(emails),
                mapping.source_workgroup,
                mapping.target_list,
            )
            log.debug(
                "Desired emails for %s: %s",
                mapping.target_list,
                _text_list(sorted(emails)),
                extra=log_extra(sorted(emails)),
            )
        unsubscribed = filter_unsubscribed(
            cc_contacts,
            desired_emails,
            ps_members_by_email,
        )
        validate_non_empty_desired_state(sync_config, desired_emails, cc_lists)
        filtered_count = sum(len(items) for items in unsubscribed)
        if filtered_count:
            log.info("Filtered %s unsubscribed desired address(es)", filtered_count)
            log.debug(
                "Filtered unsubscribed addresses: %s",
                _unsubscribed_summary(unsubscribed),
                extra=log_extra(unsubscribed),
            )
        report_decision = unsubscribed_report_decision(
            sync_config.unsubscribed_report,
            now=now or dt.datetime.now(ZoneInfo(common.timezone)),
        )
        if sync_config.unsubscribed_report.enabled:
            log.info("Unsubscribed report schedule: %s", report_decision.reason)
        contacts_by_email = {
            contact["email_address"]["address"].lower(): contact
            for contact in cc_contacts
        }
        actions = compute_all_actions(
            sync_config,
            desired_emails,
            cc_lists,
            contacts_by_email,
        )
        actions.extend(
            detect_name_mismatches(
                contacts_by_email,
                update_names=sync_config.update_names,
                candidate_sync_indices=name_update_candidate_sync_indices(
                    sync_config,
                    desired_emails,
                    cc_lists,
                ),
            )
        )
        validate_unsubscribed_report_config(
            sync_config,
            unsubscribed,
            report_decision,
            dry_run=common.dry_run,
        )
        provider = email_provider
        # Only build a real email provider when one was not injected and the run
        # will actually send: skip it for dry runs or when no mapping requests
        # notification/report mail, so we never touch email config we do not need.
        if (
            provider is None
            and not common.dry_run
            and any(mapping.notifications for mapping in sync_config.mappings)
        ):
            provider = provider_from_config(
                _mapping(config.get("email", {}), "email"),
                base_dir=config_base_dir,
            )
        validate_unsubscribed_report_provider(
            provider,
            sync_config,
            unsubscribed,
            report_decision,
            dry_run=common.dry_run,
        )
        execute_actions(
            cc_client,
            actions,
            contacts_by_email,
            ps_members_by_email,
            dry_run=common.dry_run,
            log=log,
        )
        send_notifications(
            provider,
            sync_config,
            actions,
            unsubscribed,
            contacts_by_email,
            ps_members_by_email,
        )
        send_unsubscribed_report(
            provider,
            sync_config,
            unsubscribed,
            report_decision,
            dry_run=common.dry_run,
            log=log,
        )
        log.info("Computed %s Constant Contact action(s)", len(actions))
        log.info(
            "Constant Contact sync operation completed successfully "
            "for %s list mapping(s)",
            len(sync_config.mappings),
        )
    except ConfigError as exc:
        log.error("Configuration validation failed: %s", exc)
        raise
    return 0


def cc_sync_config_from_yaml(
    config: ConfigData,
    *,
    base_dir: Path | None = None,
) -> CCSyncConfig:
    """Build a ``CCSyncConfig`` from the ``sync`` config section.

    Validates and parses the configured list mappings (which must be non-empty)
    and the optional notification sender. Relative report state paths are
    resolved against ``base_dir`` when the config came from a file. Raises
    ``ConfigError`` if required values are missing or malformed.
    """
    section = _mapping(config.get("sync", {}), "sync")
    mappings = tuple(
        _mapping_config(item, f"sync.lists[{index}]")
        for index, item in enumerate(_list(section.get("lists"), "sync.lists"))
    )
    if not mappings:
        raise ConfigError("sync.lists must not be empty")
    _validate_unique_target_lists(mappings)
    notifications = _mapping(section.get("notifications", {}), "sync.notifications")
    sender = _optional_string(notifications.get("sender"), "sync.notifications.sender")
    if any(mapping.notifications for mapping in mappings) and not sender:
        raise ConfigError(
            "sync.notifications.sender is required when any "
            "sync.lists[].notifications recipient is configured"
        )
    report = _unsubscribed_report_config(
        section.get("unsubscribed_report", {}),
        "sync.unsubscribed_report",
        base_dir=base_dir,
    )
    return CCSyncConfig(
        mappings=mappings,
        update_names=_bool(section.get("update_names", False), "sync.update_names"),
        sender=sender,
        unsubscribed_report=report,
    )


def _validate_unique_target_lists(mappings: Sequence[CCSyncMapping]) -> None:
    """Reject duplicate Constant Contact list targets before reconciliation."""
    seen: set[str] = set()
    duplicates: list[str] = []
    for mapping in mappings:
        normalized = mapping.target_list.casefold()
        if normalized in seen:
            duplicates.append(mapping.target_list)
        seen.add(normalized)
    if duplicates:
        raise ConfigError(
            "sync.lists[].target_list values must be unique; duplicate target "
            f"list(s): {_text_list(duplicates)}"
        )


def validate_configured_parishsoft_workgroups(
    config: CCSyncConfig,
    data: ParishSoftData,
) -> None:
    """Verify configured ParishSoft member workgroups exist before CC access."""
    workgroup_by_name = {
        item["name"]: item for item in data.member_workgroup_memberships.values()
    }
    for mapping in config.mappings:
        if mapping.source_workgroup in workgroup_by_name:
            continue
        raise _missing_workgroup_error(mapping, workgroup_by_name)


def _missing_workgroup_error(
    mapping: CCSyncMapping,
    workgroup_by_name: Mapping[str, Any],
) -> ConfigError:
    """Build the shared missing-workgroup config error."""
    return ConfigError(
        f"Configured ParishSoft member workgroup was not found for "
        f"Constant Contact list {mapping.target_list!r}: "
        f"{mapping.source_workgroup!r}. Check sync.lists[].source_workgroup "
        "in the YAML and make sure it exactly matches a ParishSoft "
        "member workgroup. Available member workgroups: "
        f"{_text_list(sorted(workgroup_by_name))}."
    )


def constant_contact_client(
    config: ConfigData,
    *,
    base_dir: Path | None = None,
) -> ConstantContactClient:
    """Construct a Constant Contact client from configured credential files.

    Reads the client-id and access-token file paths from the
    ``constant_contact`` config section, loads the secrets they point at, and
    returns a ready-to-use client. Raises ``ConfigError`` if either path is
    missing.
    """
    section = _mapping(config.get("constant_contact", {}), "constant_contact")
    client_id_file = _required_string(
        section.get("client_id_file"), "constant_contact.client_id_file"
    )
    access_token_file = _required_string(
        section.get("access_token_file"), "constant_contact.access_token_file"
    )
    client_id = load_client_id(
        resolve_path(
            client_id_file,
            "constant_contact.client_id_file",
            base_dir=base_dir,
        )
    )
    access_token = get_access_token(
        resolve_path(
            access_token_file,
            "constant_contact.access_token_file",
            base_dir=base_dir,
        ),
        client_id,
    )
    return ConstantContactClient(
        ConstantContactConfig(client_id=client_id, access_token=access_token)
    )


def load_cc_data(
    client: ConstantContactClient,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Load Constant Contact list and contact state."""
    lists = client.get_all("contact_lists", "lists")
    contacts = client.get_all(
        "contacts",
        "contacts",
        include="list_memberships",
        status="all",
    )
    link_cc_data(contacts, [], lists)
    return lists, contacts


def resolve_desired_state(
    config: CCSyncConfig,
    data: ParishSoftData,
    cc_lists: Sequence[Mapping[str, Any]],
) -> list[set[str]]:
    """Compute the target email set for each configured mapping.

    Returns one lowercased email set per mapping, in mapping order, drawn from
    the members of the mapped ParishSoft workgroup. Raises ``ConfigError`` if a
    referenced workgroup or Constant Contact list does not exist.
    """
    desired = []
    list_by_name = {item["name"]: item for item in cc_lists}
    workgroup_by_name = {
        item["name"]: item for item in data.member_workgroup_memberships.values()
    }
    for mapping in config.mappings:
        cc_list = list_by_name.get(mapping.target_list)
        if cc_list is None:
            raise ConfigError(
                f"Configured Constant Contact list was not found: "
                f"{mapping.target_list!r}. Check sync.lists[].target_list in "
                "the YAML and make sure it exactly matches an active Constant "
                "Contact list. Available Constant Contact lists: "
                f"{_text_list(sorted(list_by_name))}."
            )
        workgroup = workgroup_by_name.get(mapping.source_workgroup)
        if workgroup is None:
            raise _missing_workgroup_error(mapping, workgroup_by_name)
        emails = set()
        for item in workgroup.get("membership", []):
            member_id = item.get("py member duid")
            member = data.members.get(member_id)
            # Use only the member's primary (first) email; members without any
            # email simply contribute nothing to the desired set.
            if member:
                addresses = member_email_addresses(member)
                if addresses:
                    emails.add(addresses[0])
        desired.append(emails)
    return desired


def filter_unsubscribed(
    contacts: Sequence[Mapping[str, Any]],
    desired_emails: list[set[str]],
    ps_members_by_email: Mapping[str, list[dict[str, Any]]],
) -> list[list[tuple[str, str, str]]]:
    """Drop unsubscribed addresses from the desired sets and report them.

    Mutates each set in ``desired_emails`` in place to remove any address whose
    Constant Contact contact is marked ``unsubscribed``, so the sync never tries
    to re-add someone who opted out. Returns, per mapping, the filtered
    ``(email, names, duids)`` tuples for inclusion in notifications.
    """
    unsubscribed = [[] for _ in desired_emails]
    for contact in contacts:
        email_address = contact.get("email_address", {})
        if email_address.get("permission_to_send") != "unsubscribed":
            continue
        email = str(email_address.get("address", "")).lower()
        for index, desired in enumerate(desired_emails):
            if email not in desired:
                continue
            desired.discard(email)
            members = ps_members_by_email.get(email, [])
            names = ", ".join(
                str(member.get("py friendly name FL", "")) for member in members
            )
            duids = ", ".join(str(member.get("memberDUID", "")) for member in members)
            unsubscribed[index].append((email, names, duids))
    return unsubscribed


def validate_non_empty_desired_state(
    config: CCSyncConfig,
    desired_emails: Sequence[set[str]],
    cc_lists: Sequence[Mapping[str, Any]],
) -> None:
    """Abort before an empty desired set would remove every current contact."""
    list_by_name = {item["name"]: item for item in cc_lists}
    for index, mapping in enumerate(config.mappings):
        if desired_emails[index] or mapping.allow_empty:
            continue
        current = set(list_by_name[mapping.target_list].get("CONTACTS", {}))
        if not current:
            continue
        raise ConfigError(
            f"Constant Contact list {mapping.target_list!r} has current "
            "contacts, but the configured ParishSoft source resolved to zero "
            "desired email addresses. Check sync.lists[].source_workgroup in "
            "the YAML and the source workgroup membership in ParishSoft. To "
            "intentionally empty this list, set sync.lists[].allow_empty to true."
        )


def compute_all_actions(
    config: CCSyncConfig,
    desired_emails: Sequence[set[str]],
    cc_lists: Sequence[Mapping[str, Any]],
    contacts_by_email: Mapping[str, Mapping[str, Any]],
) -> list[CCAction]:
    """Compute all Constant Contact sync actions."""
    actions = []
    actions.extend(compute_create_actions(desired_emails, contacts_by_email))
    actions.extend(
        compute_subscribe_unsubscribe_actions(config, desired_emails, cc_lists)
    )
    return actions


def name_update_candidate_sync_indices(
    config: CCSyncConfig,
    desired_emails: Sequence[set[str]],
    cc_lists: Sequence[Mapping[str, Any]],
) -> dict[str, tuple[int, ...]]:
    """Return emails eligible for name updates and their notification mappings.

    ``update_names`` should only touch contacts related to this sync: desired
    ParishSoft addresses and contacts already on configured Constant Contact
    target lists. Constant Contact accounts can contain many unrelated lists,
    and this command should not rename contacts outside its configured scope.
    """
    cc_list_by_name = {item["name"]: item for item in cc_lists}
    candidates: dict[str, list[int]] = {}
    for index, mapping in enumerate(config.mappings):
        scoped = set(desired_emails[index])
        scoped.update(cc_list_by_name[mapping.target_list].get("CONTACTS", {}))
        for email in scoped:
            candidates.setdefault(email, []).append(index)
    return {email: tuple(indices) for email, indices in candidates.items()}


def name_update_candidate_emails(
    config: CCSyncConfig,
    desired_emails: Sequence[set[str]],
    cc_lists: Sequence[Mapping[str, Any]],
) -> set[str]:
    """Return emails eligible for configured-list name updates."""
    return set(name_update_candidate_sync_indices(config, desired_emails, cc_lists))


def compute_create_actions(
    desired_emails: Sequence[set[str]],
    contacts_by_email: Mapping[str, Mapping[str, Any]],
) -> list[CCAction]:
    """Compute Constant Contact contact-creation actions."""
    actions = []
    # A contact is created once even if it belongs to several mappings; the
    # creation is attributed to the first mapping that wants it.
    all_desired = set().union(*desired_emails) if desired_emails else set()
    for email in sorted(all_desired - set(contacts_by_email)):
        sync_index = next(
            index for index, emails in enumerate(desired_emails) if email in emails
        )
        actions.append(
            CCAction(
                type="create",
                email=email,
                sync_index=sync_index,
                detail=f"Create contact for {email}",
            )
        )
    return actions


def compute_subscribe_unsubscribe_actions(
    config: CCSyncConfig,
    desired_emails: Sequence[set[str]],
    cc_lists: Sequence[Mapping[str, Any]],
) -> list[CCAction]:
    """Diff desired vs. current membership into subscribe/unsubscribe actions.

    For each mapping, addresses in the desired set but not yet on the list
    become ``subscribe`` actions, and addresses currently on the list but no
    longer desired become ``unsubscribe`` actions. Results are emitted in sorted
    order for deterministic, auditable output.
    """
    cc_list_by_name = {item["name"]: item for item in cc_lists}
    actions = []
    for index, mapping in enumerate(config.mappings):
        cc_list = cc_list_by_name[mapping.target_list]
        list_uuid = cc_list["list_id"]
        current = set(cc_list.get("CONTACTS", {}))
        for email in sorted(desired_emails[index] - current):
            actions.append(
                CCAction(
                    type="subscribe",
                    email=email,
                    list_name=mapping.target_list,
                    list_uuid=list_uuid,
                    detail=f"Subscribe {email} to {mapping.target_list}",
                    sync_index=index,
                )
            )
        for email in sorted(current - desired_emails[index]):
            actions.append(
                CCAction(
                    type="unsubscribe",
                    email=email,
                    list_name=mapping.target_list,
                    list_uuid=list_uuid,
                    detail=f"Unsubscribe {email} from {mapping.target_list}",
                    sync_index=index,
                )
            )
    return actions


def detect_name_mismatches(
    contacts_by_email: Mapping[str, Mapping[str, Any]],
    *,
    update_names: bool,
    candidate_emails: Collection[str] | None = None,
    candidate_sync_indices: Mapping[str, Sequence[int]] | None = None,
) -> list[CCAction]:
    """Find contacts whose Constant Contact name differs from ParishSoft.

    Returns an empty list unless ``update_names`` is set. For each contact
    linked to ParishSoft members and included in the supplied scope, the
    canonical salutation name is compared against the stored Constant Contact
    name, and ``update_name`` actions are produced for any difference.
    """
    if not update_names:
        return []
    from parishkit.parishsoft import salutation_for_members

    actions = []
    for email, contact in contacts_by_email.items():
        sync_indices: Sequence[int | None]
        if candidate_sync_indices is not None:
            if email not in candidate_sync_indices:
                continue
            sync_indices = candidate_sync_indices[email]
        elif candidate_emails is not None and email not in candidate_emails:
            continue
        else:
            sync_indices = (None,)
        if candidate_emails is not None and candidate_sync_indices is None:
            sync_indices = (None,)
        members = contact.get("PS MEMBERS")
        if not members:
            continue
        first, last = salutation_for_members(members)
        # Strip periods so abbreviations like "Fr." compare equal to the
        # period-free form Constant Contact stores.
        first = first.replace(".", "")
        if first == contact.get("first_name", "") and last == contact.get(
            "last_name", ""
        ):
            continue
        for sync_index in sync_indices:
            actions.append(
                CCAction(
                    type="update_name",
                    email=email,
                    sync_index=sync_index,
                    detail=f"Update name for {email}",
                    new_first=first,
                    new_last=last,
                )
            )
    return actions


def execute_actions(
    client: ConstantContactClient,
    actions: Sequence[CCAction],
    contacts_by_email: Mapping[str, Mapping[str, Any]],
    ps_members_by_email: Mapping[str, list[dict[str, Any]]],
    *,
    dry_run: bool,
    log: logging.Logger | None = None,
) -> None:
    """Apply the computed actions to Constant Contact, batched per contact.

    Actions are grouped by email so each contact incurs at most one create/sign-up
    POST and one update PUT. ``dry_run`` builds the request bodies (so the work
    is exercised) but skips the actual API calls.
    """
    grouped: dict[str, list[CCAction]] = defaultdict(list)
    for action in actions:
        grouped[action.email].append(action)
    for email, email_actions in grouped.items():
        post_body = post_body_for_actions(
            email, email_actions, contacts_by_email, ps_members_by_email
        )
        put_body = put_body_for_actions(email, email_actions, contacts_by_email)
        # When the same contact has both a POST (subscribe) and a PUT
        # (unsubscribe/rename), fold the subscribe list ids into the PUT so the
        # final membership reflects both operations rather than one clobbering
        # the other.
        if post_body and put_body:
            for list_id in post_body.get("list_memberships", []):
                if list_id not in put_body["list_memberships"]:
                    put_body["list_memberships"].append(list_id)
        if dry_run:
            if log:
                log.info(
                    "dry-run: would apply %s Constant Contact action(s) for %s",
                    len(email_actions),
                    email,
                )
                log.debug(
                    "dry-run: POST body for %s: %s",
                    email,
                    "present" if post_body else "not needed",
                    extra=log_extra(post_body),
                )
                log.debug(
                    "dry-run: PUT body for %s: %s",
                    email,
                    "present" if put_body else "not needed",
                    extra=log_extra(put_body),
                )
            continue
        if post_body:
            if log:
                log.debug(
                    "Posting Constant Contact sign-up body for %s",
                    email,
                    extra=log_extra(post_body),
                )
            client.post("contacts/sign_up_form", sign_up_form_body(post_body))
        if put_body:
            if log:
                log.debug(
                    "Putting Constant Contact update body for %s",
                    email,
                    extra=log_extra(put_body),
                )
            client.put(
                f"contacts/{put_body['contact_id']}", update_contact_body(put_body)
            )


def post_body_for_actions(
    email: str,
    actions: Sequence[CCAction],
    contacts_by_email: Mapping[str, Mapping[str, Any]],
    ps_members_by_email: Mapping[str, list[dict[str, Any]]],
) -> dict[str, Any] | None:
    """Build the sign-up POST body for a contact's create/subscribe actions.

    Returns ``None`` when the contact has neither a create nor a subscribe
    action. A new contact is built from ParishSoft member data; an existing
    contact reuses its stored name. In both cases the body's list memberships
    are the lists named by the subscribe actions.
    """
    creates = [action for action in actions if action.type == "create"]
    subscribes = [action for action in actions if action.type == "subscribe"]
    if not creates and not subscribes:
        return None
    if creates:
        body = create_contact_dict(email, ps_members_by_email[email])
    else:
        contact = contacts_by_email[email]
        body = {
            "email_address": {"address": email},
            "first_name": contact.get("first_name", ""),
            "last_name": contact.get("last_name", ""),
            "list_memberships": [],
        }
    body["list_memberships"] = [
        action.list_uuid for action in subscribes if action.list_uuid
    ]
    return body


def put_body_for_actions(
    email: str,
    actions: Sequence[CCAction],
    contacts_by_email: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any] | None:
    """Build the update PUT body for a contact's unsubscribe/rename actions.

    Returns ``None`` when the contact has neither an unsubscribe nor a name
    update. Starts from the contact's current membership, removes any
    unsubscribed lists, and applies the most recent name update (if any).
    """
    unsubscribes = [action for action in actions if action.type == "unsubscribe"]
    name_updates = [action for action in actions if action.type == "update_name"]
    if not unsubscribes and not name_updates:
        return None
    contact = contacts_by_email[email]
    body = {
        "contact_id": contact["contact_id"],
        "email_address": contact["email_address"],
        "first_name": contact.get("first_name", ""),
        "last_name": contact.get("last_name", ""),
        "list_memberships": list(contact.get("list_memberships", [])),
    }
    for action in unsubscribes:
        if action.list_uuid in body["list_memberships"]:
            body["list_memberships"].remove(action.list_uuid)
    if name_updates:
        update = name_updates[-1]
        body["first_name"] = update.new_first or body["first_name"]
        body["last_name"] = update.new_last or body["last_name"]
    return body


def send_notifications(
    provider: EmailProvider | None,
    config: CCSyncConfig,
    actions: Sequence[CCAction],
    unsubscribed: Sequence[Sequence[tuple[str, str, str]]],
    contacts_by_email: Mapping[str, Mapping[str, Any]],
    ps_members_by_email: Mapping[str, list[dict[str, Any]]],
) -> None:
    """Email a per-mapping summary of actions and filtered unsubscribes.

    Does nothing without both an email provider and a configured sender. For
    each mapping that had any actions or filtered unsubscribes, sends one email
    to that mapping's notification recipients summarizing the changes.
    """
    if provider is None or not config.sender:
        return
    for index, mapping in enumerate(config.mappings):
        list_actions = [action for action in actions if action.sync_index == index]
        suppressed_count = None
        reported_unsubscribed = (
            [] if config.unsubscribed_report.enabled else unsubscribed[index]
        )
        if config.unsubscribed_report.enabled and unsubscribed[index]:
            suppressed_count = len(unsubscribed[index])
        if not list_actions and not reported_unsubscribed:
            continue
        if not mapping.notifications:
            continue
        provider.send(
            build_notification_email(
                mapping,
                list_actions,
                reported_unsubscribed,
                contacts_by_email,
                ps_members_by_email,
                sender=config.sender,
                generated_at=dt.datetime.now(),
                suppressed_unsubscribed_count=suppressed_count,
            ),
            dry_run=False,
        )


def build_notification_email(
    mapping: CCSyncMapping,
    actions: Sequence[CCAction],
    unsubscribed: Sequence[tuple[str, str, str]],
    contacts_by_email: Mapping[str, Mapping[str, Any]],
    ps_members_by_email: Mapping[str, list[dict[str, Any]]],
    *,
    sender: str,
    generated_at: dt.datetime,
    suppressed_unsubscribed_count: int | None = None,
) -> Email:
    """Build the styled Constant Contact sync update notification.

    This mirrors the old Epiphany report's operator-focused structure: a
    generated timestamp, source/target context, summary counts, and striped
    tables of actions and filtered unsubscribed contacts.
    """
    subject = f"Constant Contact sync update: {mapping.target_list}"
    created = [action for action in actions if action.type == "create"]
    subscribed = [action for action in actions if action.type == "subscribe"]
    unsubscribed_actions = [
        action for action in actions if action.type == "unsubscribe"
    ]
    unsubscribed_summary = _unsubscribed_count_summary(
        len(unsubscribed),
        suppressed_unsubscribed_count=suppressed_unsubscribed_count,
    )
    text_lines = [
        subject,
        f"Generated: {generated_at:%Y-%m-%d %H:%M:%S}",
        f"ParishSoft Member Workgroup: {mapping.source_workgroup}",
        f"Constant Contact List: {mapping.target_list}",
        "",
        "Summary:",
        f"- Contacts created: {len(created)}",
        f"- Contacts subscribed: {len(subscribed)}",
        f"- Contacts unsubscribed: {len(unsubscribed_actions)}",
        f"- Unsubscribed contacts filtered: {unsubscribed_summary}",
        "",
    ]
    text_lines.extend(action.detail for action in actions)
    for email, names, duids in unsubscribed:
        text_lines.append(f"Unsubscribed contact filtered: {names} ({duids}) {email}")

    html_parts = [
        '<html><body style="font-family: Arial, sans-serif; font-size: 14px;">',
        '<h2 style="color: #333333;">Constant Contact Sync Update: '
        f"{html.escape(mapping.target_list)}</h2>",
        f'<p style="color: #666666;">Generated: '
        f"{html.escape(generated_at.strftime('%Y-%m-%d %H:%M:%S'))}</p>",
        "<p><strong>ParishSoft Member Workgroup:</strong> "
        f"{html.escape(mapping.source_workgroup)}<br>"
        "<strong>Constant Contact List:</strong> "
        f"{html.escape(mapping.target_list)}</p>",
        "<h3>Summary</h3><ul>",
        f"<li>Contacts created: {len(created)}</li>",
        f"<li>Contacts subscribed: {len(subscribed)}</li>",
        f"<li>Contacts unsubscribed: {len(unsubscribed_actions)}</li>",
        f"<li>Unsubscribed contacts filtered: {html.escape(unsubscribed_summary)}</li>",
        "</ul>",
    ]
    if actions:
        html_parts.extend(
            [
                "<h3>Actions Performed</h3>",
                f'<table style="{_EMAIL_TABLE_STYLE}"><tr>',
                f'<th style="{_EMAIL_HEADER_STYLE}">Action</th>',
                f'<th style="{_EMAIL_HEADER_STYLE}">Contact Name(s)</th>',
                f'<th style="{_EMAIL_HEADER_STYLE}">ParishSoft Member DUID(s)</th>',
                f'<th style="{_EMAIL_HEADER_STYLE}">Email</th></tr>',
            ]
        )
        for row, action in enumerate(sorted(actions, key=_cc_action_sort_key)):
            names, duids = _cc_contact_names_and_duids(
                action.email,
                contacts_by_email,
                ps_members_by_email,
            )
            html_parts.append(
                "<tr>"
                f'<td style="{_email_cell_style(row)}">'
                f"{html.escape(_cc_action_label(action))}</td>"
                f'<td style="{_email_cell_style(row)}">{html.escape(names)}</td>'
                f'<td style="{_email_cell_style(row)}">{html.escape(duids)}</td>'
                f'<td style="{_email_cell_style(row)}">{html.escape(action.email)}</td>'
                "</tr>"
            )
        html_parts.append("</table>")
    if unsubscribed:
        html_parts.extend(
            [
                '<h3 style="color: #cc0000;">Filtered Unsubscribed Contacts</h3>',
                f'<table style="{_EMAIL_TABLE_STYLE}"><tr>',
                f'<th style="{_EMAIL_HEADER_STYLE}">Contact Name(s)</th>',
                f'<th style="{_EMAIL_HEADER_STYLE}">ParishSoft Member DUID(s)</th>',
                f'<th style="{_EMAIL_HEADER_STYLE}">Email</th></tr>',
            ]
        )
        for row, (email, names, duids) in enumerate(
            sorted(unsubscribed, key=_unsubscribed_sort_key)
        ):
            html_parts.append(
                "<tr>"
                f'<td style="{_email_cell_style(row)}">{html.escape(names)}</td>'
                f'<td style="{_email_cell_style(row)}">{html.escape(duids)}</td>'
                f'<td style="{_email_cell_style(row)}">{html.escape(email)}</td>'
                "</tr>"
            )
        html_parts.append("</table>")
    html_parts.extend(
        [
            '<hr style="border: 1px solid #dddddd; margin-top: 30px;">',
            '<p style="color: #999999; font-size: 12px;">'
            "This is an automated message from the ParishSoft to Constant "
            "Contact synchronization script.</p>",
            "</body></html>",
        ]
    )
    return Email(
        subject=subject,
        sender=sender,
        to=mapping.notifications,
        text="\n".join(text_lines),
        html="".join(html_parts),
    )


_EMAIL_TABLE_STYLE = "border-collapse: collapse; margin-bottom: 20px; width: auto;"
_EMAIL_HEADER_STYLE = (
    "border: 1px solid #dddddd; padding: 8px; text-align: center; "
    "background-color: #4472C4; color: white; white-space: nowrap;"
)


def _email_cell_style(row: int) -> str:
    """Return striped table-cell styling for notification emails."""
    background = "#f2f2f2" if row % 2 == 0 else "#ffffff"
    return (
        "border: 1px solid #dddddd; padding: 8px; "
        f"background-color: {background}; white-space: nowrap;"
    )


def _cc_action_sort_key(action: CCAction) -> tuple[int, str, str]:
    """Sort Constant Contact actions by type and address for stable emails."""
    order = {"create": 0, "subscribe": 1, "unsubscribe": 2, "update_name": 3}
    return (order.get(action.type, 99), action.email, action.detail)


def _cc_action_label(action: CCAction) -> str:
    """Return a display label for one Constant Contact action."""
    return action.type.replace("_", " ")


def _unsubscribed_count_summary(
    visible_count: int,
    *,
    suppressed_unsubscribed_count: int | None,
) -> str:
    """Return the sync email's filtered-unsubscribe count explanation."""
    if suppressed_unsubscribed_count is None:
        return str(visible_count)
    return f"{suppressed_unsubscribed_count} (handled by scheduled report)"


def _cc_contact_names_and_duids(
    email: str,
    contacts_by_email: Mapping[str, Mapping[str, Any]],
    ps_members_by_email: Mapping[str, list[dict[str, Any]]],
) -> tuple[str, str]:
    """Return display names and ParishSoft member DUIDs for an email address."""
    contact = contacts_by_email.get(email, {})
    members = contact.get("PS MEMBERS") or ps_members_by_email.get(email, [])
    if members:
        names = ", ".join(
            str(member.get("py friendly name FL", "")) for member in members
        )
        duids = ", ".join(str(member.get("memberDUID", "")) for member in members)
        return names, duids
    name = f"{contact.get('first_name', '')} {contact.get('last_name', '')}".strip()
    return name, ""


def build_unsubscribed_report_email(
    mapping: CCSyncMapping,
    unsubscribed: Sequence[tuple[str, str, str]],
    *,
    sender: str,
    generated_at: dt.datetime,
) -> Email:
    """Build the standalone unsubscribed-contacts report email.

    The content mirrors the old Epiphany report: it explains that the listed
    ParishSoft members are still in the source workgroup but have manually
    unsubscribed in Constant Contact, and should therefore be reviewed in
    ParishSoft.
    """
    sorted_unsubscribed = sorted(unsubscribed, key=_unsubscribed_sort_key)
    subject = f"Constant Contact unsubscribed contacts report: {mapping.target_list}"
    text_lines = [
        subject,
        f"Generated: {generated_at:%Y-%m-%d %H:%M:%S %Z}",
        "",
        "The following ParishSoft Members are in the "
        f"'{mapping.source_workgroup}' workgroup but have manually unsubscribed "
        "from Constant Contact.",
        "",
        "These ParishSoft Members should be removed from the "
        f"'{mapping.source_workgroup}' workgroup in ParishSoft.",
        "",
    ]
    for email, names, duids in sorted_unsubscribed:
        text_lines.append(f"- {names} (DUID: {duids}): {email}")

    table_rows = []
    for row, (email, names, duids) in enumerate(sorted_unsubscribed):
        background = "#f2f2f2" if row % 2 == 0 else "#ffffff"
        cell = (
            "border: 1px solid #dddddd; padding: 8px; "
            f"background-color: {background}; white-space: nowrap;"
        )
        table_rows.append(
            "<tr>"
            f'<td style="{cell}">{html.escape(names)}</td>'
            f'<td style="{cell}">{html.escape(duids)}</td>'
            f'<td style="{cell}">{html.escape(email)}</td>'
            "</tr>"
        )

    header_cell = (
        "border: 1px solid #dddddd; padding: 8px; text-align: center; "
        "background-color: #4472C4; color: white; white-space: nowrap;"
    )
    report_html = (
        '<html><body style="font-family: Arial, sans-serif; font-size: 14px;">'
        '<h2 style="color: #333333;">Constant Contact Unsubscribed '
        f"Contacts Report: {html.escape(mapping.target_list)}</h2>"
        f'<p style="color: #666666;">Generated: '
        f"{html.escape(generated_at.strftime('%Y-%m-%d %H:%M:%S %Z'))}</p>"
        "<p>The following ParishSoft Members are in the "
        f"'{html.escape(mapping.source_workgroup)}' workgroup but have manually "
        "unsubscribed from Constant Contact.</p>"
        '<p style="color: #cc0000; font-weight: bold; font-size: 16px;">'
        "These ParishSoft Members should be removed from the "
        f"'{html.escape(mapping.source_workgroup)}' workgroup in ParishSoft.</p>"
        '<table style="border-collapse: collapse; margin-bottom: 20px; width: auto;">'
        "<tr>"
        f'<th style="{header_cell}">PS Member Name(s)</th>'
        f'<th style="{header_cell}">PS Member DUID(s)</th>'
        f'<th style="{header_cell}">Email</th>'
        "</tr>"
        f"{''.join(table_rows)}</table>"
        '<hr style="border: 1px solid #dddddd; margin-top: 30px;">'
        '<p style="color: #999999; font-size: 12px;">'
        "This is an automated message from the ParishSoft to Constant Contact "
        "synchronization script.</p>"
        "</body></html>"
    )
    return Email(
        subject=subject,
        sender=sender,
        to=mapping.notifications,
        text="\n".join(text_lines),
        html=report_html,
    )


def send_unsubscribed_report(
    provider: EmailProvider | None,
    config: CCSyncConfig,
    unsubscribed: Sequence[Sequence[tuple[str, str, str]]],
    decision: ReportScheduleDecision,
    *,
    dry_run: bool,
    log: logging.Logger,
) -> int:
    """Send the standalone unsubscribed report when the schedule is due.

    Returns the number of report emails sent. Dry runs log the exact report
    contents but do not send email or update the state file, keeping the old
    script's safety behavior.
    """
    if not config.unsubscribed_report.enabled or not decision.due:
        return 0
    total_unsubscribed = sum(len(items) for items in unsubscribed)
    if not total_unsubscribed:
        log.info("Unsubscribed report is due, but there are no contacts to report")
        return 0
    if dry_run:
        log.warning("Unsubscribed report is due, but dry-run mode prevents email")
        for index, mapping in enumerate(config.mappings):
            if not unsubscribed[index]:
                continue
            log.info("Unsubscribed report subject: %s", mapping.target_list)
            log.info("CC List: %s", mapping.target_list)
            log.info("PS Workgroup: %s", mapping.source_workgroup)
            for email, names, duids in unsubscribed[index]:
                log.info("  %s (DUID: %s): %s", names, duids, email)
        return 0
    validate_unsubscribed_report_config(config, unsubscribed, decision, dry_run=dry_run)
    validate_unsubscribed_report_provider(
        provider,
        config,
        unsubscribed,
        decision,
        dry_run=dry_run,
    )

    sent = 0
    generated_at = dt.datetime.now(dt.UTC)
    with unsubscribed_report_state_lock(config.unsubscribed_report) as state:
        for index, mapping in enumerate(config.mappings):
            if not unsubscribed[index]:
                continue
            if unsubscribed_report_mapping_sent(state, decision.run_date, mapping):
                log.info(
                    "Unsubscribed report for %s was already sent for %s",
                    mapping.target_list,
                    decision.run_date,
                )
                continue
            provider.send(
                build_unsubscribed_report_email(
                    mapping,
                    unsubscribed[index],
                    sender=config.sender or "",
                    generated_at=generated_at,
                ),
                dry_run=False,
            )
            mark_unsubscribed_report_mapping_sent(
                config.unsubscribed_report,
                state,
                decision.run_date,
                mapping,
                now=generated_at,
            )
            sent += 1
        if all(
            not items
            or unsubscribed_report_mapping_sent(state, decision.run_date, mapping)
            for mapping, items in zip(config.mappings, unsubscribed, strict=True)
        ):
            mark_unsubscribed_report_sent(
                config.unsubscribed_report,
                state,
                decision.run_date,
                now=generated_at,
            )
    return sent


def validate_unsubscribed_report_config(
    config: CCSyncConfig,
    unsubscribed: Sequence[Sequence[tuple[str, str, str]]],
    decision: ReportScheduleDecision,
    *,
    dry_run: bool,
) -> None:
    """Validate due report config before any external writes happen."""
    if (
        not config.unsubscribed_report.enabled
        or not decision.due
        or dry_run
        or not any(unsubscribed)
    ):
        return
    missing_recipient_targets = [
        mapping.target_list
        for index, mapping in enumerate(config.mappings)
        if unsubscribed[index] and not mapping.notifications
    ]
    if missing_recipient_targets:
        raise ConfigError(
            "sync.lists[].notifications is required when "
            "sync.unsubscribed_report.enabled sends email for: "
            f"{_text_list(missing_recipient_targets)}"
        )
    if not config.sender:
        raise ConfigError(
            "sync.notifications.sender is required when "
            "sync.unsubscribed_report.enabled sends email"
        )
    ensure_unsubscribed_report_state_writable(config.unsubscribed_report)


def validate_unsubscribed_report_provider(
    provider: EmailProvider | None,
    config: CCSyncConfig,
    unsubscribed: Sequence[Sequence[tuple[str, str, str]]],
    decision: ReportScheduleDecision,
    *,
    dry_run: bool,
) -> None:
    """Verify a due report has an email provider before writes happen."""
    if (
        not config.unsubscribed_report.enabled
        or not decision.due
        or dry_run
        or not any(unsubscribed)
    ):
        return
    if provider is None:
        raise ConfigError(
            "email configuration is required when "
            "sync.unsubscribed_report.enabled sends email"
        )


def ensure_unsubscribed_report_state_writable(
    config: CCUnsubscribedReportConfig,
) -> None:
    """Verify the report state directory and lock can be written.

    The report send path records state after each successful per-list email.
    This probe catches bad paths and permissions before any external writes.
    """
    state_path = config.state_file.expanduser()
    probe = state_path.parent / f".{state_path.name}.write-test"
    lock_probe = _unsubscribed_report_lock_path(state_path)
    try:
        atomic_write_text(probe, "{}")
        lock_probe.parent.mkdir(parents=True, exist_ok=True)
        with lock_probe.open("a+", encoding="utf-8"):
            pass
    finally:
        probe.unlink(missing_ok=True)


def unsubscribed_report_decision(
    config: CCUnsubscribedReportConfig,
    *,
    now: dt.datetime,
) -> ReportScheduleDecision:
    """Return whether the daily unsubscribed report should run at ``now``."""
    run_date = now.date().isoformat()
    if not config.enabled:
        return ReportScheduleDecision(False, "disabled", run_date)
    if config.day_of_week is not None and now.weekday() != config.day_of_week:
        return ReportScheduleDecision(
            False,
            f"not configured weekday {_weekday_name(config.day_of_week)}",
            run_date,
        )
    scheduled = dt.datetime.combine(now.date(), config.time, tzinfo=now.tzinfo)
    elapsed = now - scheduled
    if elapsed < dt.timedelta(0):
        return ReportScheduleDecision(
            False,
            f"before daily {config.time:%H:%M}",
            run_date,
        )
    if elapsed >= dt.timedelta(minutes=config.window_minutes):
        return ReportScheduleDecision(
            False,
            f"after daily {config.time:%H:%M}",
            run_date,
        )
    last_sent = _last_unsubscribed_report_date(config.state_file)
    if last_sent == run_date:
        return ReportScheduleDecision(False, f"already sent for {run_date}", run_date)
    return ReportScheduleDecision(True, f"due for {run_date}", run_date)


@contextlib.contextmanager
def unsubscribed_report_state_lock(
    config: CCUnsubscribedReportConfig,
):
    """Yield report state while holding an exclusive state-file lock."""
    state_path = config.state_file.expanduser()
    state_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = _unsubscribed_report_lock_path(state_path)
    with lock_path.open("a+", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield _read_unsubscribed_report_state(state_path)
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def unsubscribed_report_mapping_sent(
    state: Mapping[str, Any],
    run_date: str,
    mapping: CCSyncMapping,
) -> bool:
    """Return whether one mapping's report is already recorded as sent."""
    sent_reports = state.get("sent_reports")
    if not isinstance(sent_reports, Mapping):
        return False
    sent_for_date = sent_reports.get(run_date)
    return isinstance(sent_for_date, list) and _report_mapping_key(mapping) in set(
        sent_for_date
    )


def mark_unsubscribed_report_mapping_sent(
    config: CCUnsubscribedReportConfig,
    state: ConfigData,
    run_date: str,
    mapping: CCSyncMapping,
    *,
    now: dt.datetime,
) -> None:
    """Record one successfully sent per-list report immediately."""
    sent_reports = state.setdefault("sent_reports", {})
    if not isinstance(sent_reports, dict):
        sent_reports = {}
        state["sent_reports"] = sent_reports
    sent_for_date = sent_reports.setdefault(run_date, [])
    if not isinstance(sent_for_date, list):
        sent_for_date = []
        sent_reports[run_date] = sent_for_date
    key = _report_mapping_key(mapping)
    if key not in sent_for_date:
        sent_for_date.append(key)
    state["last_mapping_sent_at"] = now.isoformat()
    _write_unsubscribed_report_state(config.state_file, state)


def mark_unsubscribed_report_sent(
    config: CCUnsubscribedReportConfig,
    state: ConfigData,
    run_date: str,
    *,
    now: dt.datetime,
) -> None:
    """Record that the standalone report was sent for ``run_date``."""
    state["last_sent_date"] = run_date
    state["last_sent_at"] = now.isoformat()
    _write_unsubscribed_report_state(config.state_file, state)


def parishsoft_members_by_email(
    members: Mapping[int, dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    """Index ParishSoft members by each of their lowercased email addresses.

    A member may appear under multiple addresses, and several members can share
    one address, so each key maps to a list of members.
    """
    by_email: dict[str, list[dict[str, Any]]] = {}
    for member in members.values():
        for email in member_email_addresses(member):
            by_email.setdefault(email, []).append(member)
    return by_email


def _mapping_config(value: Any, name: str) -> CCSyncMapping:
    """Parse one list-mapping entry into a ``CCSyncMapping``.

    Accepts both the current snake_case keys and the legacy spaced key names
    (``"source ps member wg"`` / ``"target cc list"``) so older configuration
    files keep working. Raises ``ConfigError`` on missing required fields.
    """
    item = _mapping(value, name)
    return CCSyncMapping(
        source_workgroup=_required_string(
            item.get("source_workgroup", item.get("source ps member wg")),
            f"{name}.source_workgroup",
        ),
        target_list=_required_string(
            item.get("target_list", item.get("target cc list")),
            f"{name}.target_list",
        ),
        notifications=tuple(
            _string_list(item.get("notifications", []), f"{name}.notifications")
        ),
        allow_empty=_bool(item.get("allow_empty", False), f"{name}.allow_empty"),
    )


def _unsubscribed_report_config(
    value: Any,
    name: str,
    *,
    base_dir: Path | None,
) -> CCUnsubscribedReportConfig:
    """Parse the optional standalone unsubscribed-report schedule."""
    item = _mapping(value, name)
    enabled = _bool(item.get("enabled", False), f"{name}.enabled")
    return CCUnsubscribedReportConfig(
        enabled=enabled,
        day_of_week=_day_of_week(item.get("day_of_week"), f"{name}.day_of_week"),
        time=_time(item.get("time", "02:00"), f"{name}.time"),
        window_minutes=_positive_int(
            item.get("window_minutes", DEFAULT_UNSUBSCRIBED_REPORT_WINDOW_MINUTES),
            f"{name}.window_minutes",
        ),
        state_file=_path(
            item.get("state_file", default_unsubscribed_report_state()),
            f"{name}.state_file",
            base_dir=base_dir,
        ),
    )


def _last_unsubscribed_report_date(path: Path) -> str | None:
    """Read the last report date from the state file, if it exists."""
    payload = _read_unsubscribed_report_state(path)
    value = payload.get("last_sent_date")
    if value is None:
        return None
    if not isinstance(value, str):
        raise ConfigError(
            f"unsubscribed report state file {path} last_sent_date must be a string"
        )
    return value


def _read_unsubscribed_report_state(path: Path) -> ConfigData:
    """Read the report state JSON file, returning an empty state if absent."""
    try:
        payload = json.loads(path.expanduser().read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError as exc:
        raise ConfigError(
            f"invalid unsubscribed report state file {path}: {exc}"
        ) from exc
    if not isinstance(payload, Mapping):
        raise ConfigError(f"unsubscribed report state file {path} must be a mapping")
    return dict(payload)


def _write_unsubscribed_report_state(path: Path, state: Mapping[str, Any]) -> None:
    """Write the report state JSON file with stable formatting."""
    atomic_write_text(
        path,
        json.dumps(dict(state), sort_keys=True, indent=2),
    )


def _unsubscribed_report_lock_path(path: Path) -> Path:
    """Return the advisory lock path for a report state file."""
    return path.with_name(f"{path.name}.lock")


def _report_mapping_key(mapping: CCSyncMapping) -> str:
    """Return the stable per-list key used inside the report state file."""
    return json.dumps(
        [mapping.source_workgroup, mapping.target_list],
        separators=(",", ":"),
    )


def _unsubscribed_sort_key(row: tuple[str, str, str]) -> tuple[str, str]:
    """Sort report rows by the first listed member's apparent last/first name."""
    parts = row[1].split(",")[0].strip().split()
    last = parts[-1].lower() if parts else ""
    first = parts[0].lower() if parts else ""
    return (last, first)


def _day_of_week(value: Any, name: str) -> int | None:
    """Read an optional weekday name, returning Python's weekday index."""
    if value in (None, ""):
        return None
    if not isinstance(value, str):
        raise ConfigError(f"{name} must be a weekday name")
    normalized = value.strip().lower()
    if normalized not in WEEKDAY_NAMES:
        raise ConfigError(
            f"{name} must be a weekday name like monday, tuesday, or sunday"
        )
    return WEEKDAY_NAMES[normalized]


def _weekday_name(index: int) -> str:
    """Return the full lowercase weekday name for an index."""
    return (
        "monday",
        "tuesday",
        "wednesday",
        "thursday",
        "friday",
        "saturday",
        "sunday",
    )[index]


def _mapping(value: Any, name: str) -> Mapping[str, Any]:
    """Read a mapping config value."""
    if not isinstance(value, Mapping):
        raise ConfigError(f"{name} must be a mapping")
    return value


def _list(value: Any, name: str) -> list[Any]:
    """Read a list config value."""
    if not isinstance(value, list):
        raise ConfigError(f"{name} must be a list")
    return value


def _string_list(value: Any, name: str) -> list[str]:
    """Read a string list config value."""
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ConfigError(f"{name} must be a list of strings")
    return value


def _required_string(value: Any, name: str) -> str:
    """Read a required string config value."""
    if not isinstance(value, str) or not value:
        raise ConfigError(f"{name} must be a string")
    return value


def _optional_string(value: Any, name: str) -> str | None:
    """Read an optional string config value."""
    if value in (None, ""):
        return None
    return _required_string(value, name)


def _path(value: Any, name: str, *, base_dir: Path | None = None) -> Path:
    """Read a path config value."""
    if isinstance(value, Path):
        path = value.expanduser()
    elif isinstance(value, str) and value:
        path = Path(value).expanduser()
    else:
        raise ConfigError(f"{name} must be a path string")
    if base_dir is not None and not path.is_absolute():
        path = base_dir / path
    return path


def _time(value: Any, name: str) -> dt.time:
    """Read a local HH:MM or HH:MM:SS time config value."""
    if isinstance(value, dt.time):
        if value.tzinfo is not None:
            raise ConfigError(f"{name} must be a local time without timezone")
        return value
    if not isinstance(value, str) or not value:
        raise ConfigError(f"{name} must be a time string like 02:00")
    try:
        parsed = dt.time.fromisoformat(value)
    except ValueError as exc:
        raise ConfigError(f"{name} must be a time string like 02:00") from exc
    if parsed.tzinfo is not None:
        raise ConfigError(f"{name} must be a local time without timezone")
    return parsed


def _positive_int(value: Any, name: str) -> int:
    """Read a positive integer config value."""
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ConfigError(f"{name} must be a positive integer")
    return value


def _bool(value: Any, name: str) -> bool:
    """Read a boolean config value."""
    if not isinstance(value, bool):
        raise ConfigError(f"{name} must be a boolean")
    return value
