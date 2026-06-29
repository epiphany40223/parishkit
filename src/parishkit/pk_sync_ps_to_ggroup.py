"""Implementation for the pk-sync-ps-to-ggroup command."""

from __future__ import annotations

import argparse
import logging
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from importlib.metadata import version
from pathlib import Path
from typing import Any

from parishkit.cli import (
    parser_with_common_options,
    resolve_common_options,
    run_user_facing,
)
from parishkit.config import ConfigData, ConfigError, load_yaml_config
from parishkit.email.base import Email, EmailProvider, provider_from_config
from parishkit.google.auth import (
    GoogleAPIError,
    load_service_account_credentials,
    load_user_credentials,
)
from parishkit.google.groups import (
    build_admin_directory_service,
    build_groups_settings_service,
    delete_group_member,
    get_group_posting_permissions,
    insert_group_member,
    list_group_members,
    update_group_member_role,
)
from parishkit.logging import log_extra, setup_logging
from parishkit.parishsoft import ParishSoftData, load_families_and_members
from parishkit.parishsoft_runtime import parishsoft_client_from_config

ADMIN_SCOPE = "https://www.googleapis.com/auth/admin.directory.group.member"
GROUP_SETTINGS_SCOPE = "https://www.googleapis.com/auth/apps.groups.settings"
LEADER_ROLES = {"Chairperson", "Staff"}


@dataclass(frozen=True)
class StaticMember:
    """A group member listed directly in config rather than sourced from data.

    ``leader`` requests OWNER role; otherwise the member is added as MEMBER.
    """

    email: str
    leader: bool = False


@dataclass(frozen=True)
class Selector:
    """A rule for picking ParishSoft members into a group.

    ``type`` chooses the matching strategy (see ``selector_matches_member``).
    The remaining fields scope that strategy: ``ministry_prefix`` restricts to
    ministries whose name starts with the prefix, ``ministry_pattern`` further
    filters ministry names by regular expression, ``member_roles`` and
    ``leader_roles`` map ministry roles to plain members and owners, and
    ``staff_owner_domains`` promotes matched members to owner when their email
    domain is in the set. ``purpose`` is descriptive metadata only.
    """

    type: str
    ministry_prefix: str | None = None
    ministry_pattern: str | None = None
    member_roles: tuple[str, ...] = ()
    leader_roles: tuple[str, ...] = ()
    staff_owner_domains: tuple[str, ...] = ()
    purpose: str | None = None


@dataclass(frozen=True)
class GroupSync:
    """Resolved sync configuration for a single Google group.

    ``group`` is the group's email/key, ``notify`` lists addresses to email
    after changes, and the remaining fields describe the member sources
    (ministries, workgroups, static members, and selectors).
    """

    group: str
    notify: tuple[str, ...]
    ministries: tuple[str, ...] = ()
    workgroups: tuple[str, ...] = ()
    static_members: tuple[StaticMember, ...] = ()
    selectors: tuple[Selector, ...] = ()


@dataclass(frozen=True)
class SyncConfig:
    """Top-level, validated configuration for a sync run.

    ``google_mail_domains`` holds the case-folded domains treated as
    Gmail-style addresses for normalization when comparing emails.
    """

    groups: tuple[GroupSync, ...]
    sender: str | None
    google_mail_domains: frozenset[str]


@dataclass
class DesiredMember:
    """A member who should be in a group, accumulated across sources.

    ``names`` collects every display name seen for this address so the
    notification email can identify who triggered an add or role change.
    """

    email: str
    leader: bool
    names: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class SyncAction:
    """One pending change to a Google group's membership.

    ``action`` is ``add``, ``change_role``, or ``delete``. ``role`` is the
    target role for adds and role changes; ``group_member_id`` identifies the
    member to remove for deletes; ``desired`` carries names for notifications.
    """

    action: str
    email: str
    role: str | None = None
    group_member_id: str | None = None
    desired: DesiredMember | None = None


Loader = Callable[..., ParishSoftData]
ServiceFactory = Callable[[ConfigData], tuple[Any, Any]]


def _text_list(values: Sequence[str]) -> str:
    """Render a short list of strings for human-readable log messages."""
    return ", ".join(values) if values else "none"


def _selector_summary(selector: Selector) -> str:
    """Return a concise selector label for log messages."""
    pieces = [selector.type]
    if selector.ministry_prefix:
        pieces.append(f"prefix={selector.ministry_prefix}")
    if selector.ministry_pattern:
        pieces.append(f"pattern={selector.ministry_pattern}")
    if selector.purpose:
        pieces.append(f"purpose={selector.purpose}")
    return " ".join(pieces)


def _static_member_summary(member: StaticMember) -> str:
    """Return a static member label including the requested role."""
    role = "OWNER" if member.leader else "MEMBER"
    return f"{member.email} as {role}"


def _desired_member_summary(member: DesiredMember) -> str:
    """Return a desired member label including the intended role."""
    role = "OWNER" if member.leader else "MEMBER"
    names = f" ({_text_list(member.names)})" if member.names else ""
    return f"{member.email} as {role}{names}"


def _current_member_summary(member: Mapping[str, Any]) -> str:
    """Return a current Google Group member label including its role."""
    return f"{member.get('email')} as {member.get('role', 'MEMBER')}"


def _action_summary(action: SyncAction) -> str:
    """Return a human-readable description of a Google Group sync action."""
    if action.action == "add":
        return f"add {action.email} as {action.role}"
    if action.action == "delete":
        return f"delete {action.email}"
    if action.action == "change_role":
        return f"change role {action.email} to {action.role}"
    return f"{action.action} {action.email}"


def _actions_summary(actions: Sequence[SyncAction]) -> str:
    """Return a readable action list for log messages."""
    return _text_list([_action_summary(action) for action in actions])


def main(
    argv: Sequence[str] | None = None,
    *,
    loader: Loader = load_families_and_members,
    service_factory: ServiceFactory | None = None,
    email_provider: EmailProvider | None = None,
) -> int:
    """Run the ``pk-sync-ps-to-ggroup`` console entry point.

    Handles ``--version`` early, then delegates to ``_run`` wrapped in the
    shared user-facing error handler. The ``loader``, ``service_factory``, and
    ``email_provider`` parameters are injectable seams so tests can run the
    command without real ParishSoft, Google, or email credentials. Returns the
    process exit code.
    """
    parser = parser_with_common_options(
        "pk-sync-ps-to-ggroup",
        description="Synchronize Google Groups from ParishSoft sources.",
    )
    parser.add_argument(
        "--version",
        action="store_true",
        help="show that the console entry point is installed",
    )
    args = parser.parse_args(argv)
    if args.version:
        print(f"pk-sync-ps-to-ggroup {version('parishkit')}")
        return 0
    return run_user_facing(lambda: _run(args, loader, service_factory, email_provider))


def _run(
    args: argparse.Namespace,
    loader: Loader,
    service_factory: ServiceFactory | None,
    email_provider: EmailProvider | None,
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
        logger_name="parishkit.pk_sync_ps_to_ggroup",
        slack_token_file=common.slack_token_file,
        slack_channel=common.slack_channel,
        slack_level=common.slack_log_level,
    )
    try:
        sync_config = sync_config_from_yaml(config)
        log.info("Configured %s Google Group sync(s)", len(sync_config.groups))
        log.debug(
            "Google Group sync configuration: %s",
            _text_list([group.group for group in sync_config.groups]),
            extra=log_extra(sync_config.groups),
        )
        client = parishsoft_client_from_config(common, config)
        log.info("Loading active ParishSoft families and members")
        data = loader(client, active_only=True, parishioners_only=False)
        log.info(
            "Loaded %s member(s), %s family/families, %s ministry membership(s), "
            "and %s workgroup membership(s)",
            len(data.members),
            len(data.families),
            len(data.ministry_type_memberships),
            len(data.member_workgroup_memberships),
        )
        validate_configured_parishsoft_sources(data, sync_config)
        log.debug("Dry-run mode is %s", "enabled" if common.dry_run else "disabled")
        admin_service, settings_service = (
            service_factory(config)
            if service_factory is not None
            else build_google_services(load_google_credentials(config))
        )
        # Only build an email provider when one was not injected, we are actually
        # applying changes, and at least one group wants notifications. This avoids
        # requiring email credentials for dry runs or notification-free configs.
        provider = email_provider
        if (
            provider is None
            and not common.dry_run
            and any(group.notify for group in sync_config.groups)
        ):
            provider = provider_from_config(_mapping(config.get("email", {}), "email"))
        for group in sync_config.groups:
            log.info("Synchronizing Google Group %s", group.group)
            log.debug(
                "Sources for %s: ministries=%s workgroups=%s static_members=%s "
                "selectors=%s",
                group.group,
                _text_list(group.ministries),
                _text_list(group.workgroups),
                _text_list(
                    [_static_member_summary(member) for member in group.static_members]
                ),
                _text_list(
                    [_selector_summary(selector) for selector in group.selectors]
                ),
                extra=log_extra(group),
            )
            sync_group(
                admin_service,
                settings_service,
                provider,
                data,
                sync_config,
                group,
                dry_run=common.dry_run,
                log=log,
            )
    except ConfigError as exc:
        log.error("Configuration validation failed: %s", exc)
        raise
    return 0


def sync_config_from_yaml(config: ConfigData) -> SyncConfig:
    """Parse and validate the ``sync`` section into ``SyncConfig``.

    Requires at least one configured group, reads the optional notification
    ``sender``, and case-folds ``google_mail_domains`` (defaulting to
    ``gmail.com``) so later email normalization is case-insensitive. Raises
    ``ConfigError`` on malformed or missing required values.
    """
    section = _mapping(config.get("sync", {}), "sync")
    groups = tuple(
        _group_sync(item, f"sync.groups[{index}]")
        for index, item in enumerate(_list(section.get("groups"), "sync.groups"))
    )
    if not groups:
        raise ConfigError("sync.groups must not be empty")
    notifications = _mapping(section.get("notifications", {}), "sync.notifications")
    sender = _optional_string(notifications.get("sender"), "sync.notifications.sender")
    domains = _string_list(
        section.get("google_mail_domains", ["gmail.com"]),
        "sync.google_mail_domains",
    )
    return SyncConfig(
        groups=groups,
        sender=sender,
        google_mail_domains=frozenset(domain.casefold() for domain in domains),
    )


def load_google_credentials(config: ConfigData) -> Any:
    """Load credentials for Google group synchronization."""
    google = _mapping(config.get("google", {}), "google")
    service_account_file = google.get("service_account_file")
    user_token_file = google.get("user_token_file")
    delegated_subject = google.get("delegated_subject")
    scopes = [ADMIN_SCOPE, GROUP_SETTINGS_SCOPE]
    if service_account_file and user_token_file:
        raise ConfigError(
            "google configuration must not set both service_account_file "
            "and user_token_file"
        )
    if delegated_subject is not None and not isinstance(delegated_subject, str):
        raise ConfigError("google.delegated_subject must be a string")
    if isinstance(service_account_file, str):
        return load_service_account_credentials(
            Path(service_account_file),
            scopes=scopes,
            subject=delegated_subject,
        )
    if isinstance(user_token_file, str):
        return load_user_credentials(Path(user_token_file), scopes=scopes)
    raise ConfigError(
        "google.service_account_file or google.user_token_file is required"
    )


def build_google_services(credentials: Any) -> tuple[Any, Any]:
    """Build Google API services used by sync."""
    return (
        build_admin_directory_service(credentials),
        build_groups_settings_service(credentials),
    )


def sync_group(
    admin_service: Any,
    settings_service: Any,
    email_provider: EmailProvider | None,
    data: ParishSoftData,
    config: SyncConfig,
    group: GroupSync,
    *,
    dry_run: bool,
    log: logging.Logger,
) -> list[SyncAction]:
    """Synchronize one configured Google group and return its actions.

    Computes the desired-vs-current diff and always logs the resulting
    actions. When ``dry_run`` is false it also writes the changes to Google and
    sends the notification email; in dry-run mode it computes and returns the
    actions without performing any side effects. The returned action list is
    the same whether or not it was applied.
    """
    desired = desired_members(data, group)
    log.info("Computed %s desired member(s) for %s", len(desired), group.group)
    log.debug(
        "Desired members for %s: %s",
        group.group,
        _text_list([_desired_member_summary(member) for member in desired]),
        extra=log_extra(desired),
    )
    current = normalized_group_members(
        list_group_members_or_config_error(admin_service, group.group)
    )
    log.info("Loaded %s current member(s) for %s", len(current), group.group)
    log.debug(
        "Current members for %s: %s",
        group.group,
        _text_list([_current_member_summary(member) for member in current]),
        extra=log_extra(current),
    )
    actions = compute_actions(desired, current, config.google_mail_domains)
    log.info(
        "Actions for %s: %s",
        group.group,
        _actions_summary(actions),
        extra=log_extra(actions),
    )
    if dry_run:
        log.info(
            "dry-run: would apply %s Google Group action(s) for %s",
            len(actions),
            group.group,
        )
        return actions
    apply_actions(admin_service, group.group, actions)
    log.info("Applied %s Google Group action(s) for %s", len(actions), group.group)
    send_notification(
        email_provider,
        config,
        group,
        get_group_posting_permissions(settings_service, group.group),
        actions,
    )
    return actions


def list_group_members_or_config_error(
    admin_service: Any,
    group_key: str,
) -> list[dict[str, Any]]:
    """List group members, mapping a missing group to a config error."""
    try:
        return list_group_members(admin_service, group_key)
    except GoogleAPIError as exc:
        if exc.status_code != 404:
            raise
        raise ConfigError(
            f"Configured Google Group was not found: {group_key!r}. Check "
            "sync.groups[].group in the YAML, confirm the group exists in "
            "Google Workspace, and make sure the delegated Google account can "
            "read groups through the Admin SDK Directory API."
        ) from exc


def validate_configured_parishsoft_sources(
    data: ParishSoftData,
    config: SyncConfig,
) -> None:
    """Verify configured ParishSoft ministry/workgroup names exist in data."""
    ministry_names = available_ministry_names(data)
    workgroup_names = available_member_workgroup_source_names(data)
    for group in config.groups:
        for ministry in group.ministries:
            if ministry not in ministry_names:
                raise ConfigError(
                    f"Configured ParishSoft ministry was not found for Google "
                    f"Group {group.group!r}: {ministry!r}. Check "
                    "sync.groups[].ministries in the YAML and make sure each "
                    "name exactly matches a ParishSoft ministry. Available "
                    f"ministries: {_text_list(sorted(ministry_names))}."
                )
        for workgroup in group.workgroups:
            if workgroup not in workgroup_names:
                raise ConfigError(
                    f"Configured ParishSoft member workgroup was not found for "
                    f"Google Group {group.group!r}: {workgroup!r}. Check "
                    "sync.groups[].workgroups in the YAML and make sure each "
                    "name exactly matches a ParishSoft member workgroup. "
                    "Available member workgroups: "
                    f"{_text_list(sorted(workgroup_names))}."
                )
        for selector in group.selectors:
            if selector_matches_any_ministry_name(selector, ministry_names):
                continue
            raise ConfigError(
                f"Configured ParishSoft selector matched no ministries for Google "
                f"Group {group.group!r}: {_selector_summary(selector)!r}. Check "
                "sync.groups[].selectors[].ministry_prefix and "
                "sync.groups[].selectors[].ministry_pattern in the YAML. "
                f"Available ministries: {_text_list(sorted(ministry_names))}."
            )


def available_ministry_names(data: ParishSoftData) -> set[str]:
    """Return ministry names present in loaded ParishSoft data."""
    names = {
        str(item["name"])
        for item in data.ministry_type_memberships.values()
        if item.get("name")
    }
    for member in data.members.values():
        names.update(str(name) for name in member.get("py ministries", {}))
    return names


def available_member_workgroup_source_names(data: ParishSoftData) -> set[str]:
    """Return member workgroup names usable as configured source names."""
    names = {
        str(item["name"])
        for item in data.member_workgroup_memberships.values()
        if item.get("name")
    }
    for member in data.members.values():
        names.update(str(name) for name in member.get("py workgroups", {}))
    for name in tuple(names):
        for suffix in (" Ldr", " Leader"):
            if name.endswith(suffix):
                names.add(name[: -len(suffix)])
    return names


def selector_matches_any_ministry_name(
    selector: Selector,
    ministry_names: set[str],
) -> bool:
    """Return whether a selector's name filters match any loaded ministry."""
    if selector.type not in {"all_ministry_chairs", "ministry_chair", "ministry_role"}:
        raise ConfigError(f"unknown sync selector type: {selector.type}")
    return any(
        ministry_name_matches_selector(name, selector) for name in ministry_names
    )


def desired_members(data: ParishSoftData, group: GroupSync) -> list[DesiredMember]:
    """Build the desired Google group member set from data plus static members.

    Each ParishSoft member matching the group is keyed by their primary email
    (de-duplicated and merged by ``add_desired_member``); members without an
    email are skipped because Google membership is email-keyed. Static members
    from config are added last. Returns the merged member list.
    """
    found: dict[str, DesiredMember] = {}
    for member in data.members.values():
        is_member, is_leader = member_matches_group(member, group)
        if not is_member and not is_leader:
            continue
        emails = member.get("py emailAddresses") or []
        if not emails:
            continue
        # Use only the first (primary) email; a person maps to one group entry.
        add_desired_member(
            found,
            str(emails[0]).lower(),
            is_leader,
            member.get("py friendly name FL") or member_display_name(member),
        )
    for static_member in group.static_members:
        add_desired_member(found, static_member.email, static_member.leader, None)
    return list(found.values())


def member_matches_group(
    member: Mapping[str, Any],
    group: GroupSync,
) -> tuple[bool, bool]:
    """Return ``(is_member, is_leader)`` for a member against all group sources.

    A member qualifies if any of the group's ministries, workgroups, or
    selectors matches; leadership in any source promotes them to leader. The
    results are OR-combined across every source.
    """
    is_member = False
    is_leader = False
    ministry_member, ministry_leader = member_in_ministries(member, group.ministries)
    workgroup_member, workgroup_leader = member_in_workgroups(member, group.workgroups)
    is_member = ministry_member or workgroup_member
    is_leader = ministry_leader or workgroup_leader
    for selector in group.selectors:
        selector_member, selector_leader = selector_matches_member(member, selector)
        is_member = is_member or selector_member
        is_leader = is_leader or selector_leader
    # A leader is always a member, even if only a leader-only source matched.
    if is_leader:
        is_member = True
    return is_member, is_leader


def compute_actions(
    desired: Sequence[DesiredMember],
    group_members: Sequence[dict[str, Any]],
    google_mail_domains: frozenset[str],
) -> list[SyncAction]:
    """Diff desired members against current members into a list of actions.

    Emails are matched with domain-aware normalization (``compare_email``), so
    Gmail-style dotting/aliasing does not cause spurious churn. Produces:

    - ``add`` for desired members with no current match,
    - ``change_role`` when a matched member's role differs from desired,
    - ``delete`` for current members no desired member matched.

    Returning plain action objects keeps decision making separate from Google
    API writes, which makes dry runs and tests deterministic.
    """
    actions: list[SyncAction] = []
    # Track which current members were matched so the rest can be deleted.
    matched_group_indexes: set[int] = set()
    for desired_member in desired:
        match_index = next(
            (
                index
                for index, group_member in enumerate(group_members)
                if compare_email(
                    desired_member.email,
                    str(group_member["email"]),
                    google_mail_domains,
                )
            ),
            None,
        )
        desired_role = "OWNER" if desired_member.leader else "MEMBER"
        if match_index is None:
            actions.append(
                SyncAction(
                    action="add",
                    email=desired_member.email,
                    role=desired_role,
                    desired=desired_member,
                )
            )
            continue
        matched_group_indexes.add(match_index)
        group_member = group_members[match_index]
        current_role = str(group_member.get("role", "")).upper()
        if current_role != desired_role:
            actions.append(
                SyncAction(
                    action="change_role",
                    email=desired_member.email,
                    role=desired_role,
                    desired=desired_member,
                )
            )
    for index, group_member in enumerate(group_members):
        if index not in matched_group_indexes:
            actions.append(
                SyncAction(
                    action="delete",
                    email=str(group_member["email"]),
                    group_member_id=str(
                        group_member.get("id") or group_member["email"]
                    ),
                )
            )
    return actions


def apply_actions(service: Any, group_key: str, actions: Sequence[SyncAction]) -> None:
    """Apply computed Google group changes."""
    for action in actions:
        if action.action == "add":
            insert_group_member(
                service, group_key, action.email, action.role or "MEMBER"
            )
        elif action.action == "change_role":
            update_group_member_role(
                service, group_key, action.email, action.role or "MEMBER"
            )
        elif action.action == "delete":
            delete_group_member(
                service, group_key, action.group_member_id or action.email
            )
        else:
            raise ConfigError(f"unknown sync action: {action.action}")


def send_notification(
    provider: EmailProvider | None,
    config: SyncConfig,
    group: GroupSync,
    posting_permission: str | None,
    actions: Sequence[SyncAction],
) -> None:
    """Email the configured recipients a summary of applied group changes.

    Does nothing when there is no provider, no notify list, or no actions, so
    callers need not pre-check. The summary lists each action with role and any
    known names, plus the group's posting policy. Raises ``ConfigError`` if a
    sender is required but missing. ``provider.send`` is called with
    ``dry_run=False`` because callers already gate this on non-dry-run runs.
    """
    if not provider or not group.notify or not actions:
        return
    if not config.sender:
        raise ConfigError("sync.notifications.sender is required")
    subject = f"Update to Google Group for {group.group}"
    permission_text = posting_permission or "unknown posting policy"
    lines = [
        f"Changes were made to {group.group} ({permission_text}):",
        "",
    ]
    for action in actions:
        names = ", ".join(action.desired.names) if action.desired else ""
        lines.append(f"- {action.action}: {action.email} {action.role or ''} {names}")
    provider.send(
        Email(
            subject=subject,
            sender=config.sender,
            to=group.notify,
            text="\n".join(lines),
        ),
        dry_run=False,
    )


def add_desired_member(
    found: dict[str, DesiredMember],
    email: str,
    leader: bool,
    name: str | None,
) -> None:
    """Insert or merge a desired member into ``found`` keyed by lowercase email.

    If the email is already present, leadership is OR-ed in (any leader source
    wins) and the name, if given, is appended to that member's name list rather
    than overwriting. Otherwise a new ``DesiredMember`` is created.
    """
    normalized = email.lower()
    existing = found.get(normalized)
    if existing:
        existing.leader = existing.leader or leader
        if name:
            existing.names.append(name)
    else:
        found[normalized] = DesiredMember(
            email=normalized,
            leader=leader,
            names=[name] if name else [],
        )


def member_in_ministries(
    member: Mapping[str, Any],
    ministries: Sequence[str],
) -> tuple[bool, bool]:
    """Return whether a member belongs to selected ministries."""
    found = False
    leader = False
    configured = set(ministries)
    for ministry in member.get("py ministries", {}).values():
        if ministry.get("name") in configured:
            found = True
            leader = leader or is_ministry_leader(ministry)
    return found, leader


def member_in_workgroups(
    member: Mapping[str, Any],
    workgroups: Sequence[str],
) -> tuple[bool, bool]:
    """Return whether a member belongs to selected workgroups."""
    found = False
    leader = False
    member_workgroups = member.get("py workgroups", {})
    for workgroup in workgroups:
        if workgroup in member_workgroups:
            found = True
        # Leadership is encoded as a sibling workgroup named "<name> Ldr" or
        # "<name> Leader"; either spelling marks the member as a leader.
        if (
            f"{workgroup} Ldr" in member_workgroups
            or f"{workgroup} Leader" in member_workgroups
        ):
            found = True
            leader = True
    return found, leader


def selector_matches_member(
    member: Mapping[str, Any],
    selector: Selector,
) -> tuple[bool, bool]:
    """Return ``(is_member, is_leader)`` for a member against one selector.

    Supports three selector types:

    - ``all_ministry_chairs``: any matching ministry leader matches; promoted to
      leader only when their email domain is in ``staff_owner_domains``.
    - ``ministry_chair``: a ministry leader whose ministry name starts with
      ``ministry_prefix`` matches as both member and leader.
    - ``ministry_role``: members of ministries matching ``ministry_prefix``
      whose role is in ``member_roles`` (member) or ``leader_roles`` (leader).

    Raises ``ConfigError`` for an unknown selector type.
    """
    if selector.type == "all_ministry_chairs":
        for ministry in member.get("py ministries", {}).values():
            if is_ministry_leader(ministry) and ministry_matches_selector(
                ministry,
                selector,
            ):
                email = str(member.get("emailAddress") or "")
                domain = email.rsplit("@", 1)[-1].lower() if "@" in email else ""
                return True, domain in selector.staff_owner_domains
        return False, False
    if selector.type == "ministry_chair":
        for ministry in member.get("py ministries", {}).values():
            if is_ministry_leader(ministry) and ministry_matches_selector(
                ministry,
                selector,
            ):
                return True, True
        return False, False
    if selector.type == "ministry_role":
        is_member = False
        is_leader = False
        member_roles = set(selector.member_roles)
        leader_roles = set(selector.leader_roles)
        for ministry in member.get("py ministries", {}).values():
            if not ministry_matches_selector(ministry, selector):
                continue
            role = ministry.get("role")
            if role in member_roles or role in leader_roles:
                is_member = True
                is_leader = role in leader_roles or is_ministry_leader(ministry)
        return is_member, is_leader
    raise ConfigError(f"unknown sync selector type: {selector.type}")


def ministry_matches_selector(
    ministry: Mapping[str, Any],
    selector: Selector,
) -> bool:
    """Return whether a ministry name satisfies a selector's name filters."""
    ministry_name = str(ministry.get("name", ""))
    return ministry_name_matches_selector(ministry_name, selector)


def ministry_name_matches_selector(ministry_name: str, selector: Selector) -> bool:
    """Return whether a ministry name satisfies a selector's name filters."""
    if not ministry_name.startswith(selector.ministry_prefix or ""):
        return False
    return not (
        selector.ministry_pattern is not None
        and re.search(
            selector.ministry_pattern,
            ministry_name,
        )
        is None
    )


def is_ministry_leader(ministry: Mapping[str, Any]) -> bool:
    """Return True if the ministry role is one of the configured leader roles."""
    return ministry.get("role") in LEADER_ROLES


def normalize_email(email: str, google_mail_domains: frozenset[str]) -> str:
    """Normalize an email address for equality comparison.

    Always lowercases. For domains in ``google_mail_domains`` (Gmail-style),
    also strips any ``+tag`` suffix and removes dots from the local part, since
    Google treats those as the same mailbox. Addresses without ``@`` or on
    other domains are only lowercased.
    """
    if "@" not in email:
        return email.lower()
    local, domain = email.lower().split("@", 1)
    if domain not in google_mail_domains:
        return f"{local}@{domain}"
    local = local.split("+", 1)[0].replace(".", "")
    return f"{local}@{domain}"


def compare_email(left: str, right: str, google_mail_domains: frozenset[str]) -> bool:
    """Compare two email addresses after normalization."""
    return normalize_email(left, google_mail_domains) == normalize_email(
        right,
        google_mail_domains,
    )


def normalized_group_members(
    members: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Normalize raw Google group members into a uniform comparison shape.

    Lowercases each email, upper-cases the role (defaulting to ``MEMBER``), and
    ensures an ``id`` (falling back to the email) so later diffing and deletes
    have consistent, predictable keys.
    """
    return [
        {
            "email": str(member["email"]).lower(),
            "role": str(member.get("role", "MEMBER")).upper(),
            "id": str(member.get("id") or member["email"]),
        }
        for member in members
    ]


def member_display_name(member: Mapping[str, Any]) -> str:
    """Return the member's "First Last" name, trimmed of missing-part spacing."""
    return f"{member.get('firstName', '')} {member.get('lastName', '')}".strip()


def _group_sync(value: Any, name: str) -> GroupSync:
    """Parse and validate one group entry into a ``GroupSync``.

    Accepts the legacy ``ggroup`` key as an alias for ``group`` and requires at
    least one member source (ministries, workgroups, static members, or
    selectors). Raises ``ConfigError`` on malformed entries.
    """
    item = _mapping(value, name)
    # "ggroup" is the legacy key name; "group" is preferred going forward.
    group = _required_string(item.get("group", item.get("ggroup")), f"{name}.group")
    notify = tuple(_string_list(item.get("notify", []), f"{name}.notify"))
    ministries = tuple(_string_list(item.get("ministries", []), f"{name}.ministries"))
    workgroups = tuple(_string_list(item.get("workgroups", []), f"{name}.workgroups"))
    static_members = tuple(
        _static_member(static, f"{name}.static_members[{index}]")
        for index, static in enumerate(
            _list(item.get("static_members", []), f"{name}.static_members")
        )
    )
    selectors = tuple(
        _selector(selector, f"{name}.selectors[{index}]")
        for index, selector in enumerate(
            _list(item.get("selectors", []), f"{name}.selectors")
        )
    )
    if not any((ministries, workgroups, static_members, selectors)):
        raise ConfigError(f"{name} must configure a source")
    return GroupSync(
        group=group,
        notify=notify,
        ministries=ministries,
        workgroups=workgroups,
        static_members=static_members,
        selectors=selectors,
    )


def _static_member(value: Any, name: str) -> StaticMember:
    """Parse a static Google group member configuration."""
    item = _mapping(value, name)
    return StaticMember(
        email=_required_string(item.get("email"), f"{name}.email").lower(),
        # Accept "owner" as an alias for "leader" for config friendliness.
        leader=_bool(item.get("leader", item.get("owner", False)), f"{name}.leader"),
    )


def _selector(value: Any, name: str) -> Selector:
    """Parse a selector configuration."""
    item = _mapping(value, name)
    selector_type = _required_string(item.get("type"), f"{name}.type")
    return Selector(
        type=selector_type,
        ministry_prefix=_optional_string(
            item.get("ministry_prefix"), f"{name}.ministry_prefix"
        ),
        ministry_pattern=_optional_regex(
            item.get("ministry_pattern"), f"{name}.ministry_pattern"
        ),
        member_roles=tuple(
            _string_list(item.get("member_roles", []), f"{name}.member_roles")
        ),
        leader_roles=tuple(
            _string_list(item.get("leader_roles", []), f"{name}.leader_roles")
        ),
        staff_owner_domains=tuple(
            domain.lower()
            for domain in _string_list(
                item.get("staff_owner_domains", []), f"{name}.staff_owner_domains"
            )
        ),
        purpose=_optional_string(item.get("purpose"), f"{name}.purpose"),
    )


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


def _optional_regex(value: Any, name: str) -> str | None:
    """Read and validate an optional regular expression config value."""
    pattern = _optional_string(value, name)
    if pattern is None:
        return None
    try:
        re.compile(pattern)
    except re.error as exc:
        raise ConfigError(f"{name} must be a valid regular expression: {exc}") from exc
    return pattern


def _bool(value: Any, name: str) -> bool:
    """Read a boolean config value."""
    if not isinstance(value, bool):
        raise ConfigError(f"{name} must be a boolean")
    return value
