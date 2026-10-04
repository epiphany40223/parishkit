"""Stand-in for ParishSoft's v2 read API, serving the synthetic parish.

The fake lets the unmodified source loaders run in the LOCAL environment:
setup loading, full and delta refreshes, giving and household reads all go
through their real code against this server. It is a standard-library HTTP
server with no dependency beyond the package, implements only the read
endpoints the application uses (see "Endpoint contract" in
docs/specs/stewardship/local-environment/spec.md), has no control or write
endpoint, injects no faults, and answers 401 with an empty body to any
request without the fixed ``LOCAL_PARISHSOFT_KEY``.

It reads its configuration file once at start. Apart from one comparison of
the current time with ``release_at`` (which decides whether the late-added
Family is served yet), every response depends only on that file's contents,
and ordering within every collection is stable across pages and calls.
"""

import json
import logging
import math
import re
import sys
from dataclasses import dataclass
from datetime import UTC, date, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

from parishkit.config import ConfigError
from parishkit.stewardship.deployment import DeploymentProfile, load_deployment

from . import LOCAL_PARISHSOFT_KEY
from .synthetic_parish import MAXIMUM_FAMILIES, generate

LOGGER = logging.getLogger(__name__)
BASE_PATH = "/api/v2/"
DEFAULT_PORT = 8080
DEFAULT_BIND = "0.0.0.0"
MAXIMUM_PAGE_SIZE = 500
MAXIMUM_BODY_BYTES = 65536
CONFIGURATION_FIELDS = frozenset({"seed", "families", "anchor_date", "release_at"})
# The real FamilyChangeList has returned an empty array for every Production
# query so far (#465; see docs/parishsoft-api-analysis.md), so the fake's
# change feed matches reality by default. ``synthetic`` serves the late-added
# Family's release as its one indication, for exercising the delta path.
CHANGE_FEED_MODES = frozenset({"empty", "synthetic"})
DEFAULT_CHANGE_FEED = "empty"
_IDENTIFIER = r"([1-9][0-9]*)"


class _Reply(Exception):
    """A non-success status (401, 400, 404 or 405) with an empty body."""

    def __init__(self, status):
        super().__init__(status)
        self.status = status


@dataclass(frozen=True)
class FakeConfiguration:
    """The file ``up`` writes: seed, Family count, anchor date and release instant."""

    seed: int
    families: int
    anchor_date: date
    release_at: datetime | None
    change_feed: str = DEFAULT_CHANGE_FEED

    @classmethod
    def parse(cls, document):
        """Validate exactly the documented fields; anything else is a bad file."""
        if (
            type(document) is not dict
            or not set(document) >= CONFIGURATION_FIELDS
            or not set(document) <= CONFIGURATION_FIELDS | {"change_feed"}
        ):
            raise ValueError("The fake ParishSoft configuration has unexpected fields.")
        change_feed = document.get("change_feed", DEFAULT_CHANGE_FEED)
        if change_feed not in CHANGE_FEED_MODES:
            raise ValueError("The fake ParishSoft change-feed mode is unknown.")
        seed, families = document["seed"], document["families"]
        if (
            type(seed) is not int
            or not 0 <= seed < 2**63
            or type(families) is not int
            or not 1 <= families <= MAXIMUM_FAMILIES
        ):
            raise ValueError("The fake ParishSoft seed or Family count is invalid.")
        anchor = document["anchor_date"]
        if type(anchor) is not str or date.fromisoformat(anchor).isoformat() != anchor:
            raise ValueError("The fake ParishSoft anchor date is invalid.")
        release = document["release_at"]
        if release is not None:
            if type(release) is not str:
                raise ValueError("The fake ParishSoft release instant is invalid.")
            release = datetime.fromisoformat(release)
            if release.tzinfo is None or release.utcoffset() is None:
                raise ValueError("The fake ParishSoft release instant needs a zone.")
        return cls(seed, families, date.fromisoformat(anchor), release, change_feed)

    @classmethod
    def read(cls, path):
        """Read the mounted configuration file once."""
        return cls.parse(json.loads(Path(path).read_text(encoding="utf-8")))


def _int(value, *, low=0, high=2**31 - 1):
    """A bounded integer from JSON or a query string; anything else is a 400."""
    if type(value) is str and value.isdecimal():
        try:
            value = int(value)
        except ValueError:
            raise _Reply(400) from None
    if type(value) is not int or isinstance(value, bool) or not low <= value <= high:
        raise _Reply(400)
    return value


def _paging(parameters, size_field, position_field, *, zero_origin=False):
    """Page size and 1-based page number from a request's paging fields.

    Missing fields default to the largest page and the first page. Endpoints
    whose position field is documented as zero-based answer both 0 and 1 with
    the first page, as the application's pagination probe expects.
    """
    size = _int(parameters.get(size_field, MAXIMUM_PAGE_SIZE), low=1)
    if size > MAXIMUM_PAGE_SIZE:
        raise _Reply(400)
    position = _int(parameters.get(position_field, 1), low=0 if zero_origin else 1)
    return size, max(position, 1)


def _page(rows, size, number):
    """The rows of one page, in the collection's stable order."""
    start = (number - 1) * size
    return rows[start : start + size]


def _envelope(rows, size, number):
    """The published envelope with internally consistent paging metadata."""
    total = len(rows)
    return {
        "data": _page(rows, size, number),
        "pagingInfo": {
            "totalRecords": total,
            "totalPages": math.ceil(total / size),
            "pageSize": size,
            "pageNumber": number,
        },
    }


def _counted(rows, size, number, total_field, ordinal_field):
    """A bare page whose rows carry the collection total and a 1-based ordinal."""
    start = (number - 1) * size
    return [
        {**row, total_field: len(rows), ordinal_field: start + offset + 1}
        for offset, row in enumerate(_page(rows, size, number))
    ]


def _date_parameter(parameters, name, *, required):
    """An ISO civil date from a query string, or None when optional and absent."""
    value = parameters.get(name)
    if value is None:
        if required:
            raise _Reply(400)
        return None
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        raise _Reply(400) from None


class _View:
    """The collections visible before or after the late-added Family's release."""

    def __init__(self, parish, released, release_at, change_feed):
        hidden = () if released else (parish.late_family_id,)
        self.families = [f for f in parish.families if f["familyDUID"] not in hidden]
        self.members = [m for m in parish.members if m["familyDUID"] not in hidden]
        self.contacts = [c for c in parish.contacts if c["familyDUID"] not in hidden]
        self.family_by_id = {row["familyDUID"]: row for row in self.families}
        self.member_by_id = {row["memberDUID"]: row for row in self.members}
        # A Family's member list is in Member-object spelling (``birthdate``,
        # ``sex``) plus the contact fields the household reader copies, as the
        # real endpoint returns; the contact list uses its own spelling.
        self.household = {}
        for row in self.members:
            self.household.setdefault(row["familyDUID"], []).append(
                {**row, "cellPhone": row["mobilePhone"]}
            )
        # The feed is empty by default, as the real one has been (#465). In
        # ``synthetic`` mode the release is the late Family's one change-feed
        # event, so a delta refresh whose window covers it picks it up.
        self.changes = []
        if released and change_feed == "synthetic":
            self.changes.append(
                {
                    "family_DUID": parish.late_family_id,
                    "currentParishID": parish.organization_id,
                    "previousParishID": None,
                    "logDate": release_at.astimezone(UTC).isoformat(),
                }
            )


class FakeParishSoft:
    """Request handling for one configuration, independent of the HTTP server.

    ``handle`` takes a method, a path (with or without query string), the
    decoded JSON body for POST requests and the request headers, and returns
    ``(status, body bytes)``. Tests call it directly; the HTTP handler and the
    in-process transport adapters are thin wrappers around it. ``now`` is
    injectable so tests can move past ``release_at`` without waiting.
    """

    def __init__(self, configuration, *, parish=None, now=None):
        """Generate the parish once; both release views are precomputed."""
        if not isinstance(configuration, FakeConfiguration):
            raise TypeError("A FakeConfiguration is required.")
        self.configuration = configuration
        self.parish = parish or generate(
            configuration.seed, configuration.families, configuration.anchor_date
        )
        self.organization_id = self.parish.organization_id
        self.now = now or (lambda: datetime.now(UTC))
        # With ``release_at: null`` the released view is never reachable.
        self.views = {False: _View(self.parish, False, None, configuration.change_feed)}
        if configuration.release_at is not None:
            self.views[True] = _View(
                self.parish, True, configuration.release_at, configuration.change_feed
            )
        self._routes = self._build_routes()

    def released(self):
        """Whether the late-added Family is served: the fake's one clock read."""
        release_at = self.configuration.release_at
        return release_at is not None and self.now() >= release_at

    def view(self):
        return self.views[self.released()]

    # -- Dispatch ---------------------------------------------------------------

    def handle(self, method, path, body=None, headers=None):
        """Authenticate, route and answer one request."""
        headers = {name.lower(): value for name, value in (headers or {}).items()}
        try:
            if headers.get("x-api-key") != LOCAL_PARISHSOFT_KEY:
                raise _Reply(401)
            parts = urlsplit(path)
            if not parts.path.startswith(BASE_PATH):
                raise _Reply(404)
            endpoint = parts.path[len(BASE_PATH) :]
            query = dict(parse_qsl(parts.query, keep_blank_values=True))
            for pattern, verbs in self._routes:
                match = pattern.fullmatch(endpoint)
                if match is None:
                    continue
                if method not in verbs:
                    raise _Reply(405)
                parameters = body if method == "POST" else query
                if type(parameters) is not dict:
                    raise _Reply(400)
                value = verbs[method](parameters, *map(int, match.groups()))
                return 200, json.dumps(value, separators=(",", ":")).encode("utf-8")
            raise _Reply(404)
        except _Reply as reply:
            return reply.status, b""

    def _build_routes(self):
        """The read endpoints, each with its permitted method."""
        routes = [
            ("organizations/search", {"POST": self._organizations}),
            ("families/search", {"POST": self._families_search}),
            ("members/search", {"POST": self._members_search}),
            ("members/contact/list", {"POST": self._contacts}),
            ("families/group/lookup/list", {"GET": self._family_groups}),
            ("families/workgroup/list", {"GET": self._family_workgroups}),
            (f"families/workgroup/{_IDENTIFIER}/list", {"GET": self._family_workgroup}),
            ("members/workgroup/lookup/list", {"GET": self._member_workgroups}),
            (f"members/workgroup/{_IDENTIFIER}/list", {"GET": self._member_workgroup}),
            ("ministry/type/list", {"GET": self._ministries}),
            (f"ministry/{_IDENTIFIER}/minister/list", {"GET": self._ministers}),
            (f"offering/{_IDENTIFIER}/funds", {"GET": self._funds}),
            ("offering/pledge/list", {"GET": self._pledges}),
            ("offering/contributiondetail/list", {"GET": self._contributions}),
            ("families/change/list", {"GET": self._changes}),
            (f"families/{_IDENTIFIER}", {"GET": self._family}),
            (f"families/{_IDENTIFIER}/member/list", {"GET": self._household}),
            (f"members/{_IDENTIFIER}", {"GET": self._member}),
        ]
        return [(re.compile(pattern), verbs) for pattern, verbs in routes]

    def _tenant(self, value):
        """An organization parameter other than the synthetic tenant's is a 404."""
        if value != self.organization_id:
            raise _Reply(404)

    def _tenant_list(self, body):
        """Search bodies scope by ``organizationIDs``: exactly this tenant."""
        if body.get("organizationIDs") != [self.organization_id]:
            raise _Reply(404)

    # -- POST searches ----------------------------------------------------------

    def _organizations(self, body):
        return [self.parish.organization]

    def _families_search(self, body):
        self._tenant_list(body)
        size, number = _paging(body, "pageSize", "pageNumber")
        return _counted(self.view().families, size, number, "totalResults", "rowNumber")

    def _members_search(self, body):
        self._tenant_list(body)
        size, number = _paging(body, "maximumRows", "startRowIndex", zero_origin=True)
        return _counted(self.view().members, size, number, "recordCount", "rowNum")

    def _contacts(self, body):
        self._tenant_list(body)
        size, number = _paging(body, "limit", "offset", zero_origin=True)
        return _page(self.view().contacts, size, number)

    # -- GET lookups and lists ------------------------------------------------

    def _family_groups(self, query):
        return self.parish.family_groups

    def _family_workgroups(self, query):
        size, number = _paging(query, "PageSize", "PageNumber")
        return _counted(
            self.parish.family_workgroups, size, number, "recordCount", "rowNum"
        )

    def _family_workgroup(self, query, identifier):
        rows = self.parish.family_workgroup_rosters.get(identifier)
        if rows is None:
            raise _Reply(404)
        return _envelope(rows, *_paging(query, "PageSize", "PageNumber"))

    def _member_workgroups(self, query):
        self._tenant(_int(query.get("organizationId")))
        return _envelope(
            self.parish.member_workgroups, *_paging(query, "PageSize", "PageNumber")
        )

    def _member_workgroup(self, query, identifier):
        rows = self.parish.member_workgroup_rosters.get(identifier)
        if rows is None:
            raise _Reply(404)
        return _envelope(rows, *_paging(query, "PageSize", "PageNumber"))

    def _ministries(self, query):
        self._tenant(_int(query.get("organizationId")))
        return _envelope(
            self.parish.ministries, *_paging(query, "PageSize", "PageNumber")
        )

    def _ministers(self, query, identifier):
        rows = self.parish.ministry_rosters.get(identifier)
        if rows is None:
            raise _Reply(404)
        return _envelope(rows, *_paging(query, "PageSize", "PageNumber"))

    def _funds(self, query, organization_id):
        self._tenant(organization_id)
        return self.parish.funds

    def _pledges(self, query):
        self._tenant(_int(query.get("OrganizationID")))
        rows = self.parish.pledges
        if "FundID" in query:
            fund = _int(query["FundID"])
            rows = [row for row in rows if row["fundID"] == fund]
        return _envelope(rows, *_paging(query, "PageSize", "PageNumber"))

    def _contributions(self, query):
        self._tenant(_int(query.get("OrganizationId")))
        rows = self.parish.contributions
        if "FundId" in query:
            fund = _int(query["FundId"])
            rows = [row for row in rows if row["fundId"] == fund]
        start = _date_parameter(query, "StartDate", required=False)
        end = _date_parameter(query, "EndDate", required=False)
        if start is not None or end is not None:
            rows = [
                row
                for row in rows
                if (
                    start is None
                    or start <= date.fromisoformat(row["contributionDate"][:10])
                )
                and (
                    end is None
                    or date.fromisoformat(row["contributionDate"][:10]) <= end
                )
            ]
        return _envelope(rows, *_paging(query, "PageSize", "PageNumber"))

    def _changes(self, query):
        """Rows whose ``logDate`` day lies in the request's window; no clock.

        Both dates are required and an inverted window is a 400; no other
        server-side window limit is enforced, since the real API documents
        none.
        """
        start = _date_parameter(query, "StartDate", required=True)
        end = _date_parameter(query, "EndDate", required=True)
        if start > end:
            raise _Reply(400)
        return [
            row
            for row in self.view().changes
            if start <= datetime.fromisoformat(row["logDate"]).date() <= end
        ]

    def _family(self, query, identifier):
        row = self.view().family_by_id.get(identifier)
        if row is None:
            raise _Reply(404)
        return row

    def _household(self, query, identifier):
        view = self.view()
        if identifier not in view.family_by_id:
            raise _Reply(404)
        return view.household.get(identifier, [])

    def _member(self, query, identifier):
        row = self.view().member_by_id.get(identifier)
        if row is None:
            raise _Reply(404)
        return row


class _Handler(BaseHTTPRequestHandler):
    """Translate HTTP to ``FakeParishSoft.handle``; nothing else is served."""

    protocol_version = "HTTP/1.1"
    server_version = "FakeParishSoft/1"
    sys_version = ""

    def log_message(self, format, *args):
        """Keep request logging out of stderr; debug logging shows it."""
        LOGGER.debug("fake-parishsoft %s", format % args)

    def _answer(self, method):
        """Read a bounded body, dispatch, and always send a Content-Length."""
        body = None
        length = self.headers.get("Content-Length")
        if length is not None:
            try:
                size = int(length)
            except ValueError:
                size = -1
            if not 0 <= size <= MAXIMUM_BODY_BYTES:
                # The body was not consumed, so the connection cannot be reused.
                self.close_connection = True
                return self._send(400, b"")
            raw = self.rfile.read(size) if size else b"{}"
            try:
                body = json.loads(raw.decode("utf-8"))
            except (ValueError, UnicodeError):
                self.close_connection = True
                return self._send(400, b"")
        elif method == "POST":
            body = {}
        status, payload = self.server.fake.handle(
            method, self.path, body, dict(self.headers)
        )
        self._send(status, payload)

    def _send(self, status, payload):
        self.send_response(status)
        if payload:
            self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        if payload:
            self.wfile.write(payload)

    def do_GET(self):
        self._answer("GET")

    def do_POST(self):
        self._answer("POST")

    def _refuse(self):
        """Every other method is refused; the fake has no write surface."""
        self._send(405, b"")

    do_PUT = do_PATCH = do_DELETE = do_HEAD = do_OPTIONS = _refuse


def start_server(fake, *, host="127.0.0.1", port=0):
    """Bind a threaded server for ``fake``; the caller runs ``serve_forever``."""
    if not isinstance(fake, FakeParishSoft):
        raise TypeError("A FakeParishSoft is required.")
    server = ThreadingHTTPServer((host, port), _Handler)
    server.daemon_threads = True
    server.fake = fake
    return server


def base_url(server):
    """The base URL clients use for a bound server (handy for tests)."""
    host, port = server.server_address[:2]
    return f"http://{host}:{port}{BASE_PATH.rstrip('/')}"


def execute_fake_parishsoft(args):
    """Console entry: refuse outside LOCAL before binding, then serve forever.

    The profile is the admitted deployment configuration's, resolved exactly
    as every other service resolves it (``--config`` file, the
    ``PARISHKIT_STEWARDSHIP_PROFILE`` environment, an explicit ``--profile``
    override); anything but a valid LOCAL deployment is refused before a
    socket is bound. The fake's own configuration file path is explicit
    (``--fake-config``) because it is the service's only mount. Refusals
    print a generic message: the inputs may hold paths the operator did not
    mean to show.
    """
    from parishkit.stewardship.observability import configure_logging

    configure_logging()
    try:
        deployment = load_deployment(
            Path(args.config) if args.config is not None else None,
            overrides=(
                {"PARISHKIT_STEWARDSHIP_PROFILE": args.profile}
                if args.profile is not None
                else None
            ),
        )
        local = deployment.profile is DeploymentProfile.LOCAL
    except (ConfigError, OSError):
        local = False
    if not local:
        print(
            "ERROR: the fake ParishSoft service runs only in the local profile",
            file=sys.stderr,
        )
        return 2
    if args.fake_config is None:
        print("ERROR: fake-parishsoft requires --fake-config", file=sys.stderr)
        return 2
    try:
        configuration = FakeConfiguration.read(args.fake_config)
        port = DEFAULT_PORT if args.port is None else _int(args.port, low=1, high=65535)
        fake = FakeParishSoft(configuration)
    except (OSError, ValueError, _Reply):
        print(
            "ERROR: the fake ParishSoft configuration is unreadable or invalid",
            file=sys.stderr,
        )
        return 2
    server = start_server(fake, host=DEFAULT_BIND, port=port)
    LOGGER.info(
        "fake ParishSoft serving %s Families on port %s", configuration.families, port
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0
