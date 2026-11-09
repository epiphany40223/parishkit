"""The Ministry packet projection that request contacts still read (#2394).

The multi-Ministry Follow-up packet was removed (#2394), but its frozen SQL
projection, ``stewardship_ministry_packet_v1``, still gives a leave
request's email and phones to the request page and Export these requests
(``ministry_contacts.packet_contacts``). ``packet`` reads it the way that
function does, under the real web database role with the reader's scope
derived in SQL, so the PostgreSQL tests of its leader contact rule and its
Ministry and request selection keep running without the packet's export.
"""

import json

from django.db import connection

from parishkit.stewardship.deployment import ServiceRole

from .test_background_grants_postgresql import task_login

# packet_contacts' statement, with the selection and history as parameters.
PROJECTION = (
    "SELECT public.stewardship_ministry_packet_v1(%s, %s::bigint[], %s, "
    "coalesce(p.scope->'operational'='true'::jsonb,false), "
    "ARRAY(SELECT m.value::bigint FROM jsonb_array_elements_text("
    "coalesce(p.scope->'ministries','[]'::jsonb)) m(value)))::text "
    "FROM (SELECT public.stewardship_ministry_scope_v1(%s) AS scope) p"
)


def packet(harness, actor, ministries=None, *, history=False):
    """The projection for the user ``actor`` in the harness's campaign.

    ``ministries`` (DUIDs) narrows it, intersected with the actor's scope,
    never trusted; None reads every Ministry in that scope. ``history``
    adds resolved and withdrawn requests.
    """
    selection = None if ministries is None else sorted(ministries)
    # The cursor opens after the login switch, on the web role's connection.
    with (
        task_login(ServiceRole.WEB, exact=True, reconnect=True),
        connection.cursor() as cursor,
    ):
        cursor.execute(PROJECTION, [harness.campaign.pk, selection, history, actor])
        (value,) = cursor.fetchone()
    return json.loads(value)


def sections(document):
    """The projection and its Ministry sections by DUID."""
    return document, {entry["duid"]: entry for entry in document.get("sections", ())}
