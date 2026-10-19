"""Member contact details for Ministry follow-up requests (#1157).

The follow-up request page and the queue's "Export these requests" show each
request's Member contact details, which the retired Members joining and
Members leaving lists used to show. Both read them here, through SQL that was
already installed, so #1157 needed no schema change:

- ``stewardship_ministry_report_v4``, the function those lists read, for one
  Ministry and request kind at a time: a join request's gender, age, email,
  phones and known primary Family address, and a leave request's current
  Ministry role; and
- ``stewardship_ministry_packet_v1``, the follow-up packet's projection, for
  a leave request's email and phones, which the report gives only for joins.

Each derives the reader's scope in SQL from the signed-in user, applies the
leader contact rule there, and is
cross-checked against Python's view of the same user, so neither can show
more than the other allows (#389 L3). The caller has already read the
requests through the follow-up selection, so every request asked about is in
the reader's scope; a Ministry the report cannot read (for example one
removed from the campaign whose requests were all withdrawn) has no details,
which the page and file say, rather than failing.

A request is read under its current Ministry (``current_ministry``): the one
review's Edit carried forward (#1684), whose leaders now decide it, else the
Family's. Both selections list an edited request with its details under that
Ministry only (frozen migration 0144, #1718), so a leader of only the
carried-forward Ministry gets its details and a leader of only the Family's
Ministry, who sees it read only, does not.

The leader contact rule: Administrators and Staff get operational contact.
A Ministry leader gets the values the Member publishes in ParishSoft and
"Not published" for the rest, except that on a standing request (the
Member's latest version, not withdrawn) the leader gets the requesting
Member's own email and phones, since asking to join or leave is consent to
be contacted about it (Administrator decision, 2026-10-17, #1591; frozen
migration 0119). A Member whose latest live answer (from the Family's
newest response that answers for them) marks them as unable to participate
in any ministries keeps the publish flags on every one of their requests,
current or history, whatever the roster shows (Administrator decision,
2026-10-18, applied per Member). Python never
widens or narrows it: it only renders the visibility the SQL returns.
"""

import json

from django.db import connection

from parishkit.stewardship.accounts.policy import Capability

from .ministries import MinistryQuery, ministry_page, within_principal

# Every version and status of one Ministry's requests of one kind, so any
# request the queue lists (its history option included) is found by id.
EVERY_REQUEST = MinistryQuery(history="all", state="any")


def current_ministry(row):
    """The Ministry a follow-up row is decided and read under (#1718)."""
    return row.get("reviewed_ministry_duid") or row["ministry_duid"]


def _visible(row):
    """A row's email and phone values, without the empty ones."""
    return {
        "email_visibility": row["email_visibility"],
        "emails": [
            entry["value"] for entry in row["emails"] or [] if entry.get("value")
        ],
        "phone_visibility": row["phone_visibility"],
        "phones": {key: value for key, value in (row["phones"] or {}).items() if value},
    }


def _member_key(row):
    """One Member of one request row: its DUID, or the proposed Member's id."""
    if row["member_duid"]:
        return ("member", row["member_duid"])
    return ("proposed", row["proposed_id"])


def packet_contacts(campaign_id, principal, ministries):
    """Leave requests' emails and phones, by request id and by Member.

    One read of the follow-up packet's projection for ``ministries`` (with
    resolved and withdrawn requests), its scope derived in SQL from the
    signed-in user exactly as an export capture derives it. The packet holds
    each Member's latest request only, so an earlier version of a request
    (shown with the queue's history option) takes its Member's values from
    the second map, keyed by Ministry and Member: they are current
    ParishSoft data either way. Returns two empty maps when the packet is
    unavailable.
    """
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT public.stewardship_ministry_packet_v1(%s, %s::bigint[], true, "
            "coalesce(p.scope->'operational'='true'::jsonb,false), "
            "ARRAY(SELECT m.value::bigint FROM jsonb_array_elements_text("
            "coalesce(p.scope->'ministries','[]'::jsonb)) m(value)))::text "
            "FROM (SELECT public.stewardship_ministry_scope_v1(%s) AS scope) p",
            [campaign_id, sorted(ministries), principal.identity],
        )
        value = cursor.fetchone()
    result = json.loads(value[0]) if value and value[0] else {}
    if result.get("disabled") or result.get("unavailable") or not result:
        return {}, {}
    scope = result["authorization_scope"]
    if not within_principal(
        principal, Capability.MINISTRY_REPORT, scope["operational"], scope["ministries"]
    ):
        raise PermissionError("Ministry contact access is unavailable.")
    by_id, by_member = {}, {}
    for section in result["sections"]:
        for row in section["rows"]:
            contacts = _visible(row)
            by_id[row["id"]] = contacts
            by_member[(section["duid"], _member_key(row))] = contacts
    return by_id, by_member


def request_contacts(campaign_id, principal, requests):
    """The contact details of each request in ``requests``, by request id.

    ``requests`` are follow-up rows (``id``, ``ministry_duid``, any
    ``reviewed_ministry_duid``, ``action``) the reader may already see; each
    is read under its current Ministry (``current_ministry``). Each value
    holds ``kind`` (``join`` or ``leave``), ``proposed`` (a Member the
    Family added on the form, whose details are what the Family entered),
    ``proposed_id``, the email and phone values with their visibility, and
    for a join ``gender``, ``age`` and ``address_lines``, for a leave
    ``current_role``. A request with no readable details is left out. The
    report is read once per Ministry and kind present, and the packet once
    for every leave request.
    """
    pairs = sorted({(current_ministry(row), row["action"]) for row in requests})
    wanted = {row["id"] for row in requests}
    found, report_date = {}, None
    for ministry, action in pairs:
        try:
            result = ministry_page(
                campaign_id,
                EVERY_REQUEST,
                principal,
                ministry_id=ministry,
                action=action,
                every=True,
            )
        except PermissionError:
            # The report cannot read this Ministry (see the module note);
            # it is never a wider read, so its requests show no details.
            continue
        report_date = result["metadata"]["report_date"]
        for row in result["rows"]:
            if row["id"] not in wanted:
                continue
            details = {
                "kind": action,
                "ministry_duid": ministry,
                "proposed": row["entity_kind"] == "proposed_member",
                "proposed_id": row["proposed_id"],
                "member_duid": row["member_duid"],
                "email_visibility": None,
                "emails": [],
                "phone_visibility": None,
                "phones": {},
            }
            if action == "join":
                details |= {
                    "gender": row["gender"],
                    "age": row["age"],
                    "address_lines": row["address_lines"],
                    "email_visibility": row["email_visibility"],
                    "emails": row["emails"],
                    "phone_visibility": row["phone_visibility"],
                    "phones": row["phones"],
                }
            else:
                details["current_role"] = row["current_role"]
            found[row["id"]] = details
    leaving = {
        details["ministry_duid"]
        for details in found.values()
        if details["kind"] == "leave"
    }
    if leaving:
        by_id, by_member = packet_contacts(campaign_id, principal, leaving)
        for request_id, details in found.items():
            if details["kind"] != "leave":
                continue
            contacts = by_id.get(request_id) or by_member.get(
                (details["ministry_duid"], _member_key(details))
            )
            if contacts:
                details |= contacts
    for details in found.values():
        details["report_date"] = report_date
    return found
