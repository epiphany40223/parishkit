"""Implementation for the pk-sync-ps-to-cc command."""

from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import logging
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from importlib.metadata import version
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from parishkit.cli import (
    DEFAULT_RUN_DIR,
    parser_with_common_options,
    resolve_common_options,
    run_user_facing,
)
from parishkit.config import ConfigData, ConfigError, load_yaml_config
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
from parishkit.parishsoft import ParishSoftData, load_families_and_members
from parishkit.parishsoft_runtime import parishsoft_client_from_config

DEFAULT_UNSUBSCRIBED_REPORT_STATE = (
    DEFAULT_RUN_DIR / "pk-sync-ps-to-cc-unsubscribed-report.json"
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


@dataclass(frozen=True)
class CCSyncMapping:
    """One configured source-to-target sync.

    Maps a single ParishSoft workgroup to a single Constant Contact list, plus
    the addresses that should receive a summary notification for that pairing.
    """

    source_workgroup: str
    target_list: str
    notifications: tuple[str, ...] = ()


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
    state_file: Path = DEFAULT_UNSUBSCRIBED_REPORT_STATE


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
    sync_config = cc_sync_config_from_yaml(config)
    # CLI flags can only turn the toggles on, never off: a command-line opt-in
    # is OR'd with whatever the YAML already requested.
    sync_config = CCSyncConfig(
        mappings=sync_config.mappings,
        update_names=sync_config.update_names or bool(args.update_names),
        sender=sync_config.sender,
        unsubscribed_report=sync_config.unsubscribed_report,
    )
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
    cc_client = cc_factory(config) if cc_factory else constant_contact_client(config)
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
        contact["email_address"]["address"].lower(): contact for contact in cc_contacts
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
        )
    )
    execute_actions(
        cc_client,
        actions,
        contacts_by_email,
        ps_members_by_email,
        dry_run=common.dry_run,
        log=log,
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
        provider = provider_from_config(_mapping(config.get("email", {}), "email"))
    send_notifications(provider, sync_config, actions, unsubscribed)
    sent_reports = send_unsubscribed_report(
        provider,
        sync_config,
        unsubscribed,
        report_decision,
        dry_run=common.dry_run,
        log=log,
    )
    if sent_reports and not common.dry_run:
        mark_unsubscribed_report_sent(
            sync_config.unsubscribed_report,
            report_decision.run_date,
            now=now or dt.datetime.now(ZoneInfo(common.timezone)),
        )
    log.info("Computed %s Constant Contact action(s)", len(actions))
    return 0


def cc_sync_config_from_yaml(config: ConfigData) -> CCSyncConfig:
    """Build a ``CCSyncConfig`` from the ``sync`` config section.

    Validates and parses the configured list mappings (which must be non-empty)
    and the optional notification sender. Raises ``ConfigError`` if required
    values are missing or malformed.
    """
    section = _mapping(config.get("sync", {}), "sync")
    mappings = tuple(
        _mapping_config(item, f"sync.lists[{index}]")
        for index, item in enumerate(_list(section.get("lists"), "sync.lists"))
    )
    if not mappings:
        raise ConfigError("sync.lists must not be empty")
    notifications = _mapping(section.get("notifications", {}), "sync.notifications")
    sender = _optional_string(notifications.get("sender"), "sync.notifications.sender")
    report = _unsubscribed_report_config(
        section.get("unsubscribed_report", {}),
        "sync.unsubscribed_report",
    )
    return CCSyncConfig(
        mappings=mappings,
        update_names=_bool(section.get("update_names", False), "sync.update_names"),
        sender=sender,
        unsubscribed_report=report,
    )


def constant_contact_client(config: ConfigData) -> ConstantContactClient:
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
    client_id = load_client_id(Path(client_id_file))
    access_token = get_access_token(Path(access_token_file), client_id)
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
        workgroup = workgroup_by_name.get(mapping.source_workgroup)
        if workgroup is None:
            raise ConfigError(
                f"ParishSoft workgroup not found: {mapping.source_workgroup}"
            )
        cc_list = list_by_name.get(mapping.target_list)
        if cc_list is None:
            raise ConfigError(f"Constant Contact list not found: {mapping.target_list}")
        emails = set()
        for item in workgroup.get("membership", []):
            member_id = item.get("py member duid")
            member = data.members.get(member_id)
            # Use only the member's primary (first) email; members without any
            # email simply contribute nothing to the desired set.
            if member and member.get("py emailAddresses"):
                emails.add(str(member["py emailAddresses"][0]).lower())
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
) -> list[CCAction]:
    """Find contacts whose Constant Contact name differs from ParishSoft.

    Returns an empty list unless ``update_names`` is set. For each contact
    linked to ParishSoft members, the canonical salutation name is compared
    against the stored Constant Contact name, and an ``update_name`` action is
    produced for any difference.
    """
    if not update_names:
        return []
    from parishkit.parishsoft import salutation_for_members

    actions = []
    for email, contact in contacts_by_email.items():
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
        actions.append(
            CCAction(
                type="update_name",
                email=email,
                sync_index=None,
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
        if not list_actions and not unsubscribed[index]:
            continue
        lines = [
            f"Constant Contact sync update: {mapping.target_list}",
            f"ParishSoft workgroup: {mapping.source_workgroup}",
            "",
        ]
        lines.extend(action.detail for action in list_actions)
        for email, names, duids in unsubscribed[index]:
            lines.append(f"Unsubscribed contact filtered: {email} {names} {duids}")
        provider.send(
            Email(
                subject=f"Constant Contact sync update: {mapping.target_list}",
                sender=config.sender,
                to=mapping.notifications,
                text="\n".join(lines),
            ),
            dry_run=False,
        )


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
    if provider is None or not config.sender:
        log.warning(
            "Unsubscribed report is due, but email provider or sender is not configured"
        )
        return 0

    sent = 0
    generated_at = dt.datetime.now(dt.UTC)
    for index, mapping in enumerate(config.mappings):
        if not unsubscribed[index]:
            continue
        if not mapping.notifications:
            log.warning(
                "Unsubscribed report for %s has no notification recipients",
                mapping.target_list,
            )
            continue
        provider.send(
            build_unsubscribed_report_email(
                mapping,
                unsubscribed[index],
                sender=config.sender,
                generated_at=generated_at,
            ),
            dry_run=False,
        )
        sent += 1
    return sent


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


def mark_unsubscribed_report_sent(
    config: CCUnsubscribedReportConfig,
    run_date: str,
    *,
    now: dt.datetime,
) -> None:
    """Record that the standalone report was sent for ``run_date``."""
    atomic_write_text(
        config.state_file,
        json.dumps(
            {
                "last_sent_date": run_date,
                "last_sent_at": now.isoformat(),
            },
            sort_keys=True,
            indent=2,
        ),
    )


def parishsoft_members_by_email(
    members: Mapping[int, dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    """Index ParishSoft members by each of their lowercased email addresses.

    A member may appear under multiple addresses, and several members can share
    one address, so each key maps to a list of members.
    """
    by_email: dict[str, list[dict[str, Any]]] = {}
    for member in members.values():
        for email in member.get("py emailAddresses", []):
            by_email.setdefault(str(email).lower(), []).append(member)
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
    )


def _unsubscribed_report_config(value: Any, name: str) -> CCUnsubscribedReportConfig:
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
            item.get("state_file", DEFAULT_UNSUBSCRIBED_REPORT_STATE),
            f"{name}.state_file",
        ),
    )


def _last_unsubscribed_report_date(path: Path) -> str | None:
    """Read the last report date from the state file, if it exists."""
    try:
        payload = json.loads(path.expanduser().read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except json.JSONDecodeError as exc:
        raise ConfigError(
            f"invalid unsubscribed report state file {path}: {exc}"
        ) from exc
    if not isinstance(payload, Mapping):
        raise ConfigError(f"unsubscribed report state file {path} must be a mapping")
    value = payload.get("last_sent_date")
    if value is None:
        return None
    if not isinstance(value, str):
        raise ConfigError(
            f"unsubscribed report state file {path} last_sent_date must be a string"
        )
    return value


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


def _path(value: Any, name: str) -> Path:
    """Read a path config value."""
    if isinstance(value, Path):
        return value.expanduser()
    if not isinstance(value, str) or not value:
        raise ConfigError(f"{name} must be a path string")
    return Path(value).expanduser()


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
