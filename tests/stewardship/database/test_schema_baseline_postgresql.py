"""Fresh schema preserves the independently captured pre-consolidation contract."""

import hashlib
import json
from pathlib import Path

import pytest
from django.apps import apps
from django.db import connection, models

from parishkit.stewardship.accounts.configuration_models import (
    AppliedConfigurationVersion,
)
from parishkit.stewardship.accounts.request_models import ConfigurationChangeRequest
from parishkit.stewardship.accounts.setup_models import SetupDraftSection
from parishkit.stewardship.campaigns.models import CampaignConfiguration
from parishkit.stewardship.schema_inventory import inventory

from .schema_contract import assert_model_contract


@pytest.mark.django_db(transaction=True)
def test_fresh_schema_matches_verified_baseline():
    """Check every catalog object, including manually named indexes and SQL guards.

    The fixture was captured only after comparing an empty old-history database
    with a fresh baseline. It is not generated during tests. PostgreSQL's pinned
    major version matters to its catalog deparser; see the schema guide before
    intentionally changing this fingerprint.
    """
    expected = json.loads(Path(__file__).with_name("schema-baseline.json").read_text())
    actual = {
        kind: {
            "count": len(objects),
            "sha256": hashlib.sha256(
                json.dumps(objects, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest(),
        }
        for kind, objects in inventory(connection).items()
    }
    assert actual == expected


@pytest.mark.django_db(transaction=True)
def test_model_declarations_match_installed_schema():
    """Model and state edits alone cannot silently omit database protections."""
    mismatches = []
    for model in apps.get_models():
        if model._meta.app_label.startswith("stewardship_") and model._meta.managed:
            try:
                assert_model_contract(connection, model)
            except AssertionError as exc:
                mismatches.append((model._meta.db_table, str(exc)))
    assert not mismatches, "\n".join(f"{table}: {diff}" for table, diff in mismatches)


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(
    "drift", ["field", "constraint", "index", "field_index", "db_default"]
)
def test_model_contract_detects_declaration_only_changes(monkeypatch, drift):
    """Changing Python declarations alone cannot bless unchanged baseline SQL."""
    model = CampaignConfiguration
    if drift == "field":
        monkeypatch.setattr(model._meta.get_field("name"), "max_length", 253)
    elif drift == "field_index":
        monkeypatch.setattr(model._meta.get_field("name"), "db_index", True)
    elif drift == "db_default":
        field = model._meta.get_field("created_at")
        monkeypatch.setattr(
            field,
            "db_default",
            models.Value("2000-01-01T00:00:00Z", output_field=models.DateTimeField()),
        )
        # Django caches the compiled expression on the field object.
        monkeypatch.setattr(field, "_db_default_expression", field.db_default)
    elif drift == "constraint":
        original = next(
            item
            for item in model._meta.constraints
            if item.name == "campaign_ordered_dates"
        )
        replacement = models.CheckConstraint(
            condition=models.Q(end_date__gte=models.F("start_date")),
            name=original.name,
        )
        monkeypatch.setattr(model._meta, "constraints", [replacement])
    else:
        monkeypatch.setattr(
            model._meta,
            "indexes",
            [models.Index(fields=["name"], name="absent_current_model_index")],
        )
    with pytest.raises(AssertionError):
        assert_model_contract(connection, model)


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(
    "drift",
    [
        "remove_default",
        "generated_expression",
        "remove_unique",
        "remove_fk",
        "remove_field_index",
        "remove_constraint",
        "remove_index",
        "opclass",
        "descending",
        "collation",
    ],
)
def test_model_contract_detects_removed_and_changed_semantics(monkeypatch, drift):
    """Negative probes cover removals and details omitted by column-only deparsing."""
    model = ConfigurationChangeRequest
    if drift == "remove_default":
        field = model._meta.get_field("authority")
        monkeypatch.setattr(field, "db_default", models.NOT_PROVIDED)
    elif drift == "generated_expression":
        model = SetupDraftSection
        monkeypatch.setattr(
            model._meta.get_field("scope_digest"), "expression", models.Value("changed")
        )
    elif drift == "remove_unique":
        monkeypatch.setattr(
            model._meta.get_field("candidate_version_id"), "unique", False
        )
    elif drift == "remove_fk":
        monkeypatch.setattr(model._meta.get_field("base"), "db_constraint", False)
    elif drift == "remove_field_index":
        model = CampaignConfiguration
        monkeypatch.setattr(model._meta.get_field("record_id"), "db_index", False)
    elif drift == "remove_constraint":
        monkeypatch.setattr(model._meta, "constraints", model._meta.constraints[1:])
    elif drift == "remove_index":
        monkeypatch.setattr(model._meta, "indexes", [])
    elif drift == "opclass":
        monkeypatch.setattr(model._meta.indexes[0], "opclasses", ["jsonb_ops"])
    elif drift == "descending":
        model = AppliedConfigurationVersion
        replacement = models.UniqueConstraint(
            models.Value(1).desc(),
            condition=models.Q(predecessor__isnull=True),
            name="configuration_single_root",
        )
        monkeypatch.setattr(
            model._meta, "constraints", [replacement, *model._meta.constraints[1:]]
        )
    else:
        monkeypatch.setattr(
            model._meta.get_field("candidate_digest"), "db_collation", "C"
        )
    # Establish that the unchanged model's entire contract passes first in the
    # separate all-model test; each mutation here must fail without editing SQL.
    with pytest.raises(AssertionError):
        assert_model_contract(connection, model)
