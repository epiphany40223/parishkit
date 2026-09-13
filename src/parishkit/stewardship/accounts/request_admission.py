"""Bounded intake checks, distinct from installer ancestry/corruption verification."""

from django.db import connection

from parishkit.config import ConfigError

from .configuration_models import (
    AppliedConfigurationVersion,
    AppliedIntegration,
    MinistryActivity,
)
from .configuration_snapshots import verified_snapshot_version
from .content_models import ContentVersion


def intake_base(digest):
    """Load only the selected document/projections and its direct predecessor digest."""
    snapshot = (
        AppliedConfigurationVersion.objects.select_related("parish")
        .prefetch_related("integrations")
        .filter(digest=digest)
        .first()
    )
    try:
        if snapshot is not None:
            predecessor = None
            if snapshot.predecessor_id is not None:
                predecessor = AppliedConfigurationVersion.objects.values_list(
                    "digest", flat=True
                ).get(pk=snapshot.predecessor_id)
            return snapshot, verified_snapshot_version(
                snapshot, predecessor_digest=predecessor
            )
    except (ConfigError, AppliedConfigurationVersion.DoesNotExist):
        pass
    raise ConfigError("A complete prepared base configuration is required.")


def check_historical_additions(base_id, patch):
    """Reject retired kind/ID reuse without loading historical JSON into intake.

    Only additions need an ancestry identity check: updates cannot change kind
    or ID, and removals introduce no binding. The recursive query traverses UUID
    links and checks matching projections, returning at most one conflict, not
    every canonical document. Full ancestry validation remains mandatory when
    the installer prepares the candidate; this is not a readiness certificate.
    """
    _check_policy_additions(base_id, patch)
    _check_ministry_additions(base_id, patch)
    _check_content_additions(base_id, patch)
    additions = [
        item
        for item in patch
        if item["section"] == "integrations" and item["operation"] == "add"
    ]
    if not additions:
        return
    # Only model-owned identifiers are interpolated; every submitted value is
    # still a bound parameter. Keep table/FK renames in sync with migrations.
    quote = connection.ops.quote_name
    versions, integrations = AppliedConfigurationVersion._meta, AppliedIntegration._meta
    version_table, integration_table = (
        quote(versions.db_table),
        quote(integrations.db_table),
    )
    version_pk = quote(versions.pk.column)
    predecessor_column = quote(versions.get_field("predecessor").column)
    configuration_column = quote(integrations.get_field("configuration").column)
    kind_column = quote(integrations.get_field("kind").column)
    record_column = quote(integrations.get_field("record_id").column)
    predicates, parameters = [], [base_id]
    for item in additions:
        kind, identifier = item["values"]["kind"], item["id"]
        predicates.append(
            f"((i.{kind_column} = %s AND i.{record_column} <> %s) OR "
            f"(i.{record_column} = %s AND i.{kind_column} <> %s))"
        )
        parameters.extend([kind, identifier, identifier, kind])
    with connection.cursor() as cursor:
        cursor.execute(
            f"""WITH RECURSIVE chain(id, predecessor_id) AS (
                SELECT {version_pk}, {predecessor_column} FROM {version_table}
                WHERE {version_pk} = %s
                UNION
                SELECT p.{version_pk}, p.{predecessor_column} FROM {version_table} p
                JOIN chain c ON p.{version_pk} = c.predecessor_id
            ) SELECT 1 FROM chain c JOIN {integration_table} i
              ON i.{configuration_column} = c.id WHERE """
            + " OR ".join(predicates)
            + " LIMIT 1",
            parameters,
        )
        if cursor.fetchone() is not None:
            raise ConfigError(
                "Integration identities must remain stable across history."
            )


def _history_join(model):
    """Build model-owned identifiers only; the selected base remains a parameter."""
    quote = connection.ops.quote_name
    versions = AppliedConfigurationVersion._meta
    table = quote(versions.db_table)
    pk = quote(versions.pk.column)
    predecessor = quote(versions.get_field("predecessor").column)
    projection = quote(model._meta.db_table)
    configuration = quote(model._meta.get_field("configuration").column)
    return f"""WITH RECURSIVE chain(id, predecessor_id) AS (
        SELECT {pk}, {predecessor} FROM {table} WHERE {pk}=%s
        UNION
        SELECT p.{pk}, p.{predecessor} FROM {table} p
        JOIN chain c ON p.{pk}=c.predecessor_id
    ) SELECT 1 FROM {projection} m
      JOIN chain c ON c.id=m.{configuration} WHERE """


def _columns(model, *names):
    """Quote real projection columns, not canonical-document field spellings."""
    return {
        name: "m." + connection.ops.quote_name(model._meta.get_field(name).column)
        for name in names
    }


def _check_content_additions(base_id, patch):
    """Compare only matching revision payloads across retained immutable ancestry."""
    import json

    additions = [
        item
        for item in patch
        if item["section"] == "content" and item["operation"] == "add"
    ]
    if not additions:
        return
    columns = _columns(
        ContentVersion,
        "record_id",
        "campaign_id",
        "kind",
        "slot",
        "subject",
        "html",
        "text",
    )
    payload = ", ".join(
        f"'{name}', {columns[name]}"
        for name in ("campaign_id", "kind", "slot", "subject", "html", "text")
    )
    predicates, parameters = [], [base_id]
    for item in additions:
        predicates.append(
            f"({columns['record_id']}=%s AND jsonb_build_object({payload}) "
            "IS DISTINCT FROM %s::jsonb)"
        )
        parameters.extend([item["id"], json.dumps(item["values"])])
    with connection.cursor() as cursor:
        cursor.execute(
            _history_join(ContentVersion) + " OR ".join(predicates) + " LIMIT 1",
            parameters,
        )
        if cursor.fetchone() is not None:
            raise ConfigError("Content revision identities must remain immutable.")


def _check_ministry_additions(base_id, patch):
    """Check retired activity identities without loading historical documents."""
    additions = [
        item
        for item in patch
        if item["section"] == "ministries" and item["operation"] == "add"
    ]
    if not additions:
        return
    columns = _columns(
        MinistryActivity, "record_id", "organization_id", "ministry_duid"
    )
    record, organization, ministry = (
        columns[name] for name in ("record_id", "organization_id", "ministry_duid")
    )
    predicates, parameters = [], [base_id]
    for item in additions:
        values = item["values"]
        predicates.append(
            f"(({record}=%s AND ({organization}, {ministry}) "
            "IS DISTINCT FROM (%s::bigint, %s::bigint)) OR "
            f"({record}<>%s AND {organization}=%s AND {ministry}=%s))"
        )
        identity = [values["organization_id"], values["ministry_duid"]]
        parameters.extend([item["id"], *identity, item["id"], *identity])
    with connection.cursor() as cursor:
        cursor.execute(
            _history_join(MinistryActivity) + " OR ".join(predicates) + " LIMIT 1",
            parameters,
        )
        if cursor.fetchone() is not None:
            raise ConfigError("Ministry activity identities must remain stable.")


def _check_policy_additions(base_id, patch):
    """Compare only added policy IDs against retained ancestry, before claiming work."""
    from .configuration_snapshots import _remember_policy

    additions = [
        item
        for item in patch
        if item["section"] == "login_rules" and item["operation"] == "add"
    ]
    if not additions:
        return
    identities = {}
    _remember_policy({"sections": {"login_rules": additions}}, identities)
    quote, meta = connection.ops.quote_name, AppliedConfigurationVersion._meta
    table = quote(meta.db_table)
    pk, predecessor = (
        quote(meta.get_field(field).column) for field in ("id", "predecessor")
    )
    # The immutable projections retain exactly the frozen policy bindings.
    # Filter IDs before comparing them: no historical canonical JSON is loaded,
    # expanded or returned while the intake/installer serialization lock is held.
    import json

    from .policy_models import AddressRule, DomainRule, MinistryAssignment

    sources = []
    parameters = [base_id]
    for model, kind, fields in (
        (DomainRule, "domain", ("domain",)),
        (AddressRule, "address", ("email", "creation_origin", "creation_operation")),
        (
            MinistryAssignment,
            "assignment",
            ("email", "ministry_duid", "source", "operation_id"),
        ),
    ):
        columns = ",".join(
            "p." + quote(model._meta.get_field(name).column) for name in fields
        )
        sources.append(
            f"SELECT p.record_id, jsonb_build_array(%s::text,{columns}) AS identity "
            f"FROM {quote(model._meta.db_table)} p "
            "JOIN chain c ON c.id=p.configuration_id "
            "WHERE p.record_id=ANY(%s::uuid[])"
        )
        parameters.extend([kind, list(identities)])
    predicates = []
    for identifier, identity in identities.items():
        predicates.append("(record_id=%s AND identity IS DISTINCT FROM %s::jsonb)")
        parameters.extend([identifier, json.dumps(identity)])
    with connection.cursor() as cursor:
        cursor.execute(
            f"""
            WITH RECURSIVE chain(id, predecessor_id) AS (
                SELECT {pk}, {predecessor}
                FROM {table} WHERE {pk} = %s
                UNION
                SELECT p.{pk}, p.{predecessor}
                FROM {table} p JOIN chain c ON p.{pk} = c.predecessor_id
            ), bindings AS ("""
            + " UNION ALL ".join(sources)
            + ") SELECT 1 FROM bindings WHERE "
            + " OR ".join(predicates)
            + " LIMIT 1",
            parameters,
        )
        if cursor.fetchone() is not None:
            raise ConfigError(
                "Policy record identities must remain stable across history."
            )
