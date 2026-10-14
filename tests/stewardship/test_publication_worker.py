"""BG-09's safety switches without a database or network (#528).

The publish worker refuses before anything else while publication is off;
the real ParishSoft gateway cannot read (so it can never reach its PUT); and
only the gateway module builds the one session whose helper may PUT.
"""

import ast
import re
from pathlib import Path
from unittest.mock import Mock

import pytest

from parishkit.config import ConfigError
from parishkit.parishsoft import DEFAULT_API_BASE_URL
from parishkit.parishsoft_transport import (
    BoundedPublicationSession,
    ExactSourceResponse,
    SourceTransportError,
)
from parishkit.stewardship.deployment import (
    PARISHSOFT_PUBLICATION_VARIABLE,
    load_deployment,
)
from parishkit.stewardship.responses.publication_gateway import (
    FAILURE_KINDS,
    ContactGatewayError,
    ParishSoftContactGateway,
)
from parishkit.stewardship.responses.publication_models import FAILURE_KIND
from parishkit.stewardship.responses.publication_worker import (
    PublicationDisabled,
    publish_plan,
)

from .test_deployment import config_file

SRC = Path(__file__).resolve().parents[2] / "src/parishkit"


@pytest.mark.parametrize("enabled", [False, None, 1, "true"])
def test_switched_off_publication_refuses_before_any_call(enabled):
    """Only ``True`` passes; otherwise no lease, read or write is attempted."""
    execution, gateway = Mock(), Mock()
    with pytest.raises(PublicationDisabled):
        publish_plan(execution, object(), gateway, enabled=enabled)
    assert execution.mock_calls == [] and gateway.mock_calls == []


def test_publication_is_off_by_default_and_needs_an_exact_switch(tmp_path):
    """Off unless set to 1 or true; the rendered document keeps it only when on."""
    from parishkit.stewardship.deployment_documents import deployment_document

    name = PARISHSOFT_PUBLICATION_VARIABLE
    default = load_deployment(environ={})
    assert default.parishsoft_publication is False
    assert "parishsoft_publication" not in deployment_document(default)["deployment"]
    assert load_deployment(environ={name: ""}).parishsoft_publication is False
    assert load_deployment(environ={name: "0"}).parishsoft_publication is False
    on = load_deployment(environ={name: "1"})
    assert on.parishsoft_publication is True
    assert deployment_document(on)["deployment"]["parishsoft_publication"] is True
    path = config_file(tmp_path, {"parishsoft_publication": True})
    assert load_deployment(path, environ={}).parishsoft_publication is True
    assert load_deployment(path, environ={name: "0"}).parishsoft_publication is False
    for value in ("yes", "true", "2"):
        with pytest.raises(ConfigError, match="parishsoft_publication"):
            load_deployment(environ={name: value})
    with pytest.raises(ConfigError, match="parishsoft_publication"):
        load_deployment(
            config_file(tmp_path, {"parishsoft_publication": 1}), environ={}
        )


def publication_session():
    """A publication session whose helper is never started here."""
    session = BoundedPublicationSession(
        before_request=lambda seconds: None, check=lambda: None, profile="production"
    )
    session.headers["x-api-key"] = "SYNTHETIC-PRIVATE-KEY"
    return session


def response(status):
    """A transport response with only a status."""
    result = ExactSourceResponse()
    result.status_code = status
    return result


def test_the_real_gateway_cannot_read_so_it_never_writes():
    """Its read refuses (unconfirmed), so the worker's pre-PUT read stops it."""
    gateway = ParishSoftContactGateway(publication_session(), profile="production")
    with pytest.raises(ContactGatewayError) as error:
        gateway.read_member_contact("5", timeout=30)
    assert error.value.kind == "read_unconfirmed"
    with pytest.raises(TypeError):
        ParishSoftContactGateway(Mock(), profile="production")


def test_the_real_gateways_put_is_the_member_contact_put():
    """The URL is built from a positive integer id; failures are fixed words."""
    session = publication_session()
    session.put = Mock(return_value=response(204))
    gateway = ParishSoftContactGateway(session, profile="production")
    gateway.write_member_contact("5", {"firstName": "Pat"}, timeout=30)
    session.put.assert_called_once_with(
        DEFAULT_API_BASE_URL + "/members/5/contact",
        json={"firstName": "Pat"},
        timeout=30,
    )
    for status, kind in ((400, "write_rejected"), (503, "write_unavailable")):
        session.put.return_value = response(status)
        with pytest.raises(ContactGatewayError) as error:
            gateway.write_member_contact("5", {}, timeout=30)
        assert error.value.kind == kind
    session.put.side_effect = SourceTransportError("PRIVATE")
    with pytest.raises(ContactGatewayError) as error:
        gateway.write_member_contact("5", {}, timeout=30)
    assert error.value.kind == "write_unavailable" and "PRIVATE" not in str(error.value)
    for bad in ("0", "-5", "5/../families", "x"):
        with pytest.raises(ValueError):
            gateway.write_member_contact(bad, {}, timeout=30)


def test_every_failure_word_is_a_valid_outcome_kind():
    """Gateway and verification words all fit the outcome table's check."""
    for kind in (*FAILURE_KINDS, "verify_unavailable", "verify_mismatch"):
        assert re.fullmatch(FAILURE_KIND, kind)
    with pytest.raises(ValueError):
        ContactGatewayError("provider said no")


def _names(path):
    """Every name and attribute a module's source mentions."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {
        getattr(node, "id", None) or getattr(node, "attr", None) or ""
        for node in ast.walk(tree)
        if isinstance(node, ast.Name | ast.Attribute)
    } | {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }


def test_only_the_gateway_builds_the_put_session_and_only_the_worker_writes():
    """The PUT is reachable only behind the BG-09 worker."""
    owners = {"BoundedPublicationSession": set(), "write_member_contact": set()}
    for path in SRC.rglob("*.py"):
        names = _names(path)
        for name, found in owners.items():
            if name in names:
                found.add(path.relative_to(SRC).as_posix())
    # The transport defines the class (a ClassDef, not a use) and the
    # gateway is the one module that names it.
    assert owners["BoundedPublicationSession"] == {
        "stewardship/responses/publication_gateway.py",
    }
    # Definitions are not uses: the worker is the one caller of the write.
    assert owners["write_member_contact"] == {
        "stewardship/responses/publication_worker.py",
    }
    # Only the transport and its helper start a helper that may PUT, so no
    # other module can build a write-admitting helper around the session.
    assert {
        path.relative_to(SRC).as_posix()
        for path in SRC.rglob("*.py")
        if "contact_writes" in path.read_text(encoding="utf-8")
    } == {"parishsoft_transport.py", "parishsoft_http_worker.py"}


def test_the_real_gateway_needs_a_timeout_recorder():
    """A publication request killed at its deadline is always logged."""
    execution = Mock()
    credential = Mock(api_key="SYNTHETIC-PRIVATE-KEY")
    with pytest.raises(TypeError, match="timeout recorder"):
        ParishSoftContactGateway.open(
            execution, credential, profile="production", on_timeout=None
        )
    gateway = ParishSoftContactGateway.open(
        execution, credential, profile="production", on_timeout=Mock()
    )
    assert gateway.session.on_timeout is not None
    assert gateway.session._helper.process is None
    gateway.close()
