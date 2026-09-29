"""Read-only scan for Families whose form ParishSoft Member data would refuse.

One malformed ParishSoft Member or contact value (a name over the length
limit, a tab or newline, a non-ISO birth date, an over-long email) makes the
Family form's inputs raise ``MemberSourceUnavailable``, and the whole form is
refused for that Family (#315). Nothing reports this until the Family tries to
open the form. This scan checks every portal-eligible Family of the current
campaign against the promoted source snapshot, field by field, so staff can
fix the values in ParishSoft before launch.

It runs inside an admitted web container exactly like ``pk-stewardship
load-check``: under the web's own restricted SQL login, in read-only campaign
guards, with no Valkey client and no writes. The output names only Family and
Member DUIDs and fixed field names, never a value.
"""

import json
import sys

from parishkit.config import ConfigError

from .deployment import ServiceRole, load_deployment
from .observability import Event, configure_logging, emit_failure
from .runtime_paths import RuntimeLayout
from .startup_interlock import StartupBusy, StartupLease

# Structural problems census_inputs refuses before it reads any field: a
# Member whose DUID or active/deceased flags are unusable. Reported under this
# fixed name so the refused Family is not silently missed.
MEMBER_RECORD = "member_record"


class ScanRefused(ConfigError):
    """The scan cannot start: no current campaign or no promoted source."""


def _scanned_fields(census):
    """The Member fields ``census_inputs`` reads from source, and their contact use.

    This mirrors census_inputs: with the census module every Member and
    request field is read, with the contact; without it only the first and
    last name are read, without a contact.
    """
    from .responses.inputs import MEMBER_FIELDS
    from .responses.member_requests import REQUEST_FIELDS

    if census:
        return tuple((field, True) for field in (*MEMBER_FIELDS, *REQUEST_FIELDS))
    return tuple(
        (field, False)
        for field in MEMBER_FIELDS
        if field.name in {"first_name", "last_name"}
    )


def _usable_record(member):
    """The checks census_inputs applies to each Member before reading fields."""
    identifier = member.get("memberDUID")
    return (
        type(identifier) is int
        and 0 < identifier < 2**31
        and type(member.get("active")) is bool
        and type(member.get("deceased")) is bool
    )


def member_source_findings(family_duids, members, contacts, *, census):
    """Every (Family, Member, field) whose source value the form would refuse.

    ``members`` are snapshot Member payloads and ``contacts`` maps a Member
    DUID string to its snapshot contact payload. Unlike census_inputs, which
    stops at the first bad value, this checks every field of every active,
    living Member, so one run lists everything that needs fixing. Returns
    sorted dicts of ``family_duid``, ``member_duid`` and ``field``.
    """
    from .responses.inputs import MemberSourceUnavailable, member_field_value

    eligible = {str(duid): duid for duid in family_duids}
    fields = _scanned_fields(census)
    findings = []
    for member in members:
        family = eligible.get(member.get("family_key"))
        if family is None:
            continue
        if not _usable_record(member):
            findings.append(
                {"family_duid": family, "member_duid": None, "field": MEMBER_RECORD}
            )
            continue
        if not member["active"] or member["deceased"]:
            continue
        contact = contacts.get(str(member["memberDUID"]))
        for field, with_contact in fields:
            try:
                member_field_value(member, contact if with_contact else None, field)
            except MemberSourceUnavailable as error:
                # The exception's context is already sanitized to the three
                # identifiers; it never carries the unusable value.
                findings.append(dict(error.context))
    return sorted(
        findings,
        key=lambda item: (item["family_duid"], item["member_duid"] or 0, item["field"]),
    )


def scan():
    """Read the current campaign's eligible Families and scan their Members.

    Every read is inside a read-only transaction with the interactive
    statement and lock limits (reusing the load check's guards), so the scan
    cannot write or hold locks; the current Families' Members are loaded in
    two queries rather than one form load per Family.
    """
    from .accounts.runtime_models import SystemConfiguration
    from .campaigns.credential_models import FamilyCampaign
    from .campaigns.models import Campaign
    from .load_check import _guard, bounded_read, current_source
    from .source.version_models import SnapshotContact, SnapshotMember

    with bounded_read():
        campaign_id = SystemConfiguration.objects.values_list(
            "current_campaign_id", flat=True
        ).get()
    if campaign_id is None:
        raise ScanRefused("There is no current campaign.")
    with _guard(campaign_id):
        source = current_source()
        configuration = (
            Campaign.objects.select_related("active_configuration")
            .get(pk=campaign_id)
            .active_configuration.values
        )
        census = "census" in configuration["modules"]
        families = list(
            FamilyCampaign.objects.filter(campaign_id=campaign_id, portal_eligible=True)
            .order_by("family_duid")
            .values_list("family_duid", flat=True)
        )
        members = [
            row.payload.payload
            for row in SnapshotMember.objects.filter(
                snapshot_id=source.snapshot_id,
                payload__family_key__in=[str(duid) for duid in families],
            ).select_related("payload")
        ]
        contacts = (
            {
                row.payload.owner_key: row.payload.payload
                for row in SnapshotContact.objects.filter(
                    snapshot_id=source.snapshot_id, payload__owner_kind="member"
                ).select_related("payload")
            }
            if census
            else {}
        )
    findings = member_source_findings(families, members, contacts, census=census)
    return {
        "check": "source_form",
        "portal_eligible_families": len(families),
        "families_refused": len({item["family_duid"] for item in findings}),
        "findings": findings,
        "result": "findings" if findings else "clean",
    }


def source_form_check_command(configuration):
    """Admit as the load check does (web profile, lease, web login), then scan."""
    from django.db import connections

    from .operator_commands import configure_operator_database
    from .runtime_grants import admit_runtime_database
    from .runtime_web import admit_lifecycle_mounts
    from .service_boundaries import admit_online_service

    if admit_online_service(configuration) is not ServiceRole.WEB:
        raise ConfigError("The source form check requires the admitted web profile.")
    admit_lifecycle_mounts(configuration)
    with StartupLease(RuntimeLayout(configuration).interlock, offline=False):
        configure_operator_database(configuration)
        try:
            admit_runtime_database(configuration)
            return scan()
        finally:
            connections.close_all()


def execute_source_form_check(args):
    """Console entry: one JSON document (exit 0 clean, 1 findings) or one error line.

    Errors print fixed wording only; exception text could carry deployment
    paths or source values.
    """
    configure_logging()
    try:
        if args.config is None:
            raise ConfigError("The source form check requires a configuration.")
        document = source_form_check_command(load_deployment(args.config))
    except StartupBusy:
        print(
            "ERROR: offline maintenance is in progress; retry the check",
            file=sys.stderr,
        )
        return 2
    except (ConfigError, PermissionError) as error:
        emit_failure(error, event=Event.STARTUP_REJECTED)
        print(
            "ERROR: source form check refused; verify the web profile, a current "
            "campaign and a promoted source",
            file=sys.stderr,
        )
        return 2
    except Exception as error:
        emit_failure(error, event=Event.TASK_FAILED)
        print(
            "ERROR: source form check stopped by an unexpected error; see the "
            "process log",
            file=sys.stderr,
        )
        return 2
    print(json.dumps(document, sort_keys=True))
    return 0 if document["result"] == "clean" else 1
