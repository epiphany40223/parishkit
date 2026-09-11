"""Focused foundation review regressions with no provider or database credentials."""

from dataclasses import replace
from io import BytesIO
from unittest.mock import Mock

import pytest
from django.core.exceptions import ValidationError
from django.db import DatabaseError
from PIL import Image

from parishkit.config import ConfigError
from parishkit.stewardship.accounts.auth_incidents import record_login_rejection
from parishkit.stewardship.accounts.limiting import LimiterUnavailable
from parishkit.stewardship.campaigns.credential_models import DeploymentCredentialState
from parishkit.stewardship.cli import main
from parishkit.stewardship.runtime_diagnostics import ObservationUnavailable
from parishkit.stewardship.runtime_paths import RuntimeLayout
from parishkit.stewardship.storage import validate_mutation
from parishkit.stewardship.web.content import prepare_graphics

from .test_runtime_topology import configuration_at


def test_nullable_mutation_validates_other_fields():
    """Legitimate nulls pass; invalid non-null field values still fail."""
    record = DeploymentCredentialState()
    validate_mutation(record)
    record.restore_id = "not-a-uuid"
    with pytest.raises(ValidationError):
        validate_mutation(record)


def test_public_rejection_store_failure_has_typed_private_outcome(monkeypatch):
    """The request owner can return its ordinary retryable denial on SQL outages."""
    monkeypatch.setattr(
        "parishkit.stewardship.accounts.auth_incidents._record_login_rejection",
        Mock(side_effect=DatabaseError("private")),
    )
    with pytest.raises(LimiterUnavailable) as error:
        record_login_rejection("family_login_failed")
    assert "private" not in str(error.value)


def test_incomplete_diagnostics_are_not_fabricated_dependency_failures(
    monkeypatch, capsys
):
    """A fresh but incomplete observation has a distinct bounded operator result."""
    monkeypatch.setattr(
        "parishkit.stewardship.runtime_diagnostics.load_deployment", lambda _: object()
    )
    monkeypatch.setattr(
        "parishkit.stewardship.runtime_diagnostics.health_command",
        Mock(side_effect=ObservationUnavailable()),
    )
    assert main(["health", "--config", "synthetic"]) == 3
    output = capsys.readouterr()
    assert not output.out
    assert "observation incomplete" in output.err


@pytest.mark.parametrize("kind", ["duplicate", "interlock", "configuration"])
def test_individual_database_files_cannot_alias_runtime_inputs(tmp_path, kind):
    """Overrides retain independent SQL identities and immutable startup metadata."""
    configuration = configuration_at(tmp_path)
    layout = RuntimeLayout(configuration)
    selected = tmp_path / "same-password"
    paths = (
        {"web": selected, "download": selected}
        if kind == "duplicate"
        else {
            "web": layout.interlock if kind == "interlock" else tmp_path / "config.yaml"
        }
    )
    configuration = replace(
        configuration,
        configuration_file=tmp_path / "config.yaml",
        postgres=replace(configuration.postgres, password_files=paths),
    )
    with pytest.raises(ConfigError, match="alias"):
        RuntimeLayout(configuration).validate()


@pytest.mark.parametrize(
    "format,mode", [("JPEG", "RGB"), ("JPEG", "CMYK"), ("WEBP", "RGB")]
)
def test_static_rasters_are_oriented_and_stripped(format, mode):
    """Accepted codecs produce inert PNGs, preserving orientation but not metadata."""
    source, stream = Image.new(mode, (40, 20)), BytesIO()
    exif = Image.Exif()
    exif[274], exif[270] = 6, "private-image-metadata"
    source.save(stream, format=format, exif=exif)
    result = prepare_graphics(BytesIO(stream.getvalue()))
    with Image.open(BytesIO(result["large"].data)) as decoded:
        assert decoded.format == "PNG" and decoded.size == (20, 40)
        assert not decoded.info


@pytest.mark.parametrize("format", ["PNG", "WEBP"])
def test_animated_rasters_are_rejected(format):
    """Real APNG/WebP frames must not slip through static-graphic admission."""
    stream = BytesIO()
    Image.new("RGB", (4, 4), "red").save(
        stream,
        format=format,
        save_all=True,
        append_images=[Image.new("RGB", (4, 4), "blue")],
        duration=100,
        loop=0,
    )
    with pytest.raises(ValueError, match="static image"):
        prepare_graphics(BytesIO(stream.getvalue()))


def test_missing_predecessor_is_a_typed_intake_refusal(monkeypatch):
    """Damaged operator state does not escape the configuration-error boundary."""
    from types import SimpleNamespace
    from uuid import uuid4

    from parishkit.stewardship.accounts import request_admission

    model = request_admission.AppliedConfigurationVersion
    manager = Mock()
    query = manager.select_related.return_value.prefetch_related.return_value
    query.filter.return_value.first.return_value = SimpleNamespace(
        predecessor_id=uuid4()
    )
    manager.values_list.return_value.get.side_effect = model.DoesNotExist()
    monkeypatch.setattr(model, "objects", manager)
    with pytest.raises(ConfigError, match="complete prepared base"):
        request_admission.intake_base("synthetic-digest")


@pytest.mark.parametrize(
    "model_name,field_name",
    [
        ("CampaignConfiguration", "starts_at"),
        ("CampaignConfiguration", "ends_at"),
        ("ScheduleRevision", "due_at"),
    ],
)
def test_projection_instant_fields_refuse_naive_writes(model_name, field_name):
    """Projection fields retain the same UTC boundary as other durable instants."""
    from datetime import datetime

    from django.core.exceptions import ValidationError

    from parishkit.stewardship.campaigns import models

    field = getattr(models, model_name)._meta.get_field(field_name)
    with pytest.raises(ValidationError, match="timezone-aware"):
        field.get_prep_value(datetime(2026, 1, 1))


def test_telemetry_broker_has_independent_short_nonretrying_io(tmp_path, monkeypatch):
    """Observability cannot inherit the authentication pool's longer timeouts."""
    from parishkit.stewardship.runtime_web import valkey_client

    configuration = configuration_at(tmp_path)
    monkeypatch.setattr(
        "parishkit.stewardship.runtime_web.read_private", lambda _: b"synthetic"
    )
    ordinary, telemetry = (
        valkey_client(configuration),
        valkey_client(configuration, telemetry=True),
    )
    assert ordinary.connection_pool is not telemetry.connection_pool
    assert ordinary.connection_pool.connection_kwargs["socket_timeout"] == 3
    arguments = telemetry.connection_pool.connection_kwargs
    assert arguments["socket_timeout"] == arguments["socket_connect_timeout"] == 0.05
    assert arguments["retry"]._retries == 0
