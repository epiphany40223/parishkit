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

# Finding kinds. A "value" finding is one Member field whose ParishSoft value
# the form refuses (MemberSourceUnavailable). A "record" finding is a Member or
# contact record the form cannot read at all: an unusable or duplicate Member
# DUID or active/deceased flag (field "member"), or a malformed contact (the
# field that reads it). Every refusal is reported; none aborts the scan.
VALUE = "value"
RECORD = "record"
# Contact payload faults the form would hit reading a field: an owner mismatch
# (FormInputsUnavailable) or a missing/mistyped key in the payload.
_RECORD_FAULTS = (KeyError, TypeError, AttributeError, ValueError)


class ScanRefused(ConfigError):
    """The scan cannot start: no current campaign or no promoted source."""


def _finding(family, member, field, kind):
    """One output row: identifiers and fixed labels only, never a value."""
    return {"family_duid": family, "member_duid": member, "field": field, "kind": kind}


def member_source_findings(family_duids, members, contacts, *, census):
    """Every Member field or record that would refuse a Family's form.

    ``members`` are snapshot Member payloads and ``contacts`` maps a Member
    DUID string to its snapshot contact payload. The record check and the
    field list are census_inputs' own (``usable_member_record`` and
    ``member_source_fields``). Unlike census_inputs, which stops at the first
    refusal, this checks every field of every active, living Member, so one
    run lists everything that needs fixing. Returns sorted finding dicts.
    """
    from .responses.inputs import (
        FormInputsUnavailable,
        MemberSourceUnavailable,
        member_field_value,
        member_source_fields,
        usable_member_record,
    )

    eligible = {str(duid): duid for duid in family_duids}
    fields = member_source_fields(census)
    findings, seen = [], set()
    for member in members:
        family = eligible.get(member.get("family_key"))
        if family is None:
            continue
        identifier = member.get("memberDUID")
        if not usable_member_record(member, family) or (family, identifier) in seen:
            valid = type(identifier) is int and 0 < identifier < 2**31
            findings.append(
                _finding(family, identifier if valid else None, "member", RECORD)
            )
            continue
        seen.add((family, identifier))
        if not member["active"] or member["deceased"]:
            continue
        contact = contacts.get(str(identifier))
        for field, with_contact in fields:
            try:
                member_field_value(member, contact if with_contact else None, field)
            except MemberSourceUnavailable:
                findings.append(_finding(family, identifier, field.name, VALUE))
            except (FormInputsUnavailable, *_RECORD_FAULTS):
                findings.append(_finding(family, identifier, field.name, RECORD))
    return sorted(
        findings,
        key=lambda item: (
            item["family_duid"],
            item["member_duid"] or 0,
            item["field"],
            item["kind"],
        ),
    )


def scan_identity(campaign_id):
    """What one scan's input is: the current snapshot and campaign configuration.

    Cheap (two single-row reads), so a caller can tell whether an earlier
    result still applies before loading every Member (``reports.source_form``).
    """
    from .campaigns.models import Campaign
    from .load_check import current_source

    source = current_source()
    campaign = Campaign.objects.select_related("active_configuration").get(
        pk=campaign_id
    )
    return {
        "snapshot_id": source.snapshot_id,
        "configuration_id": campaign.active_configuration_id,
        "census": "census" in campaign.active_configuration.values["modules"],
    }


def scan_inputs(campaign_id):
    """The scan's inputs for ``campaign_id``, read inside the caller's guard.

    Returns the portal-eligible Family DUIDs, the current snapshot's Member
    payloads for them and (with the census) their Member contacts, plus what
    identifies this exact input: the snapshot and the campaign configuration.
    The Admin page (#774) and this command both read through here, so they
    can never disagree about which Families are blocked.
    """
    from .campaigns.credential_models import FamilyCampaign
    from .source.version_models import SnapshotContact, SnapshotMember

    identity = scan_identity(campaign_id)
    census = identity["census"]
    families = list(
        FamilyCampaign.objects.filter(campaign_id=campaign_id, portal_eligible=True)
        .order_by("family_duid")
        .values_list("family_duid", flat=True)
    )
    members = [
        row.payload.payload
        for row in SnapshotMember.objects.filter(
            snapshot_id=identity["snapshot_id"],
            payload__family_key__in=[str(duid) for duid in families],
        ).select_related("payload")
    ]
    contacts = (
        {
            row.payload.owner_key: row.payload.payload
            for row in SnapshotContact.objects.filter(
                snapshot_id=identity["snapshot_id"], payload__owner_kind="member"
            ).select_related("payload")
        }
        if census
        else {}
    )
    return identity | {"families": families, "members": members, "contacts": contacts}


def scan():
    """Read the current campaign's eligible Families and scan their Members.

    Every read is inside a read-only transaction with the interactive
    statement and lock limits (reusing the load check's guards), so the scan
    cannot write or hold locks; the current Families' Members are loaded in
    two queries rather than one form load per Family.
    """
    from .accounts.runtime_models import SystemConfiguration
    from .load_check import _guard, bounded_read

    with bounded_read():
        campaign_id = SystemConfiguration.objects.values_list(
            "current_campaign_id", flat=True
        ).get()
    if campaign_id is None:
        raise ScanRefused("There is no current campaign.")
    with _guard(campaign_id):
        inputs = scan_inputs(campaign_id)
    families = inputs["families"]
    findings = member_source_findings(
        families, inputs["members"], inputs["contacts"], census=inputs["census"]
    )
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
