"""Exact round-trip of normalized policy projections, never an editable authority."""

from django.db.models import prefetch_related_objects

from parishkit.config import ConfigError

from .policy_models import AddressRoleGrant, AddressRule, DomainRule, MinistryAssignment
from .users_schema import cumulative_roles


def prepare_policy(snapshot, records, attribution):
    """Persist validated policy inside snapshot preparation's transaction."""
    for record in records:
        values = record["values"].copy()
        kind = values.pop("kind")
        common = dict(configuration=snapshot, record_id=record["id"], **attribution)
        if kind == "user":
            # One users-form row (#952): its name and role, and the cumulative
            # roles every SQL check reads; no origins and no grant rows.
            AddressRule.objects.create(
                **common, **values, roles=cumulative_roles(values["role"])
            )
        elif kind == "domain":
            DomainRule.objects.create(**common, **values)
        elif kind == "assignment":
            MinistryAssignment.objects.create(**common, **values)
        else:
            grants = values.pop("grants")
            rule = AddressRule.objects.create(**common, **values)
            AddressRoleGrant.objects.bulk_create(
                [
                    AddressRoleGrant(
                        rule=rule, role=role, origins=origins, **attribution
                    )
                    for role, origins in grants.items()
                ]
            )


def stored_policy(snapshot):
    """Reconstruct rows so missing, extra or altered data fails verification."""
    prefetch_related_objects(
        [snapshot],
        "domainrule_set",
        "addressrule_set__grants",
        "ministryassignment_set",
    )
    records = []
    for row in snapshot.domainrule_set.all():
        records.append(
            {
                "id": str(row.record_id),
                "values": {"kind": "domain", "domain": row.domain, "roles": row.roles},
            }
        )
    for row in snapshot.addressrule_set.all():
        if row.role is not None:
            # The YAML entry carries only the role; the stored cumulative
            # roles must be exactly that role's (a database CHECK holds it
            # too), or the projection does not round-trip.
            if row.roles != cumulative_roles(row.role):
                raise ConfigError("Users projection roles differ from the role.")
            records.append(
                {
                    "id": str(row.record_id),
                    "values": {
                        "kind": "user",
                        "email": row.email,
                        "name": row.name,
                        "role": row.role,
                    },
                }
            )
            continue
        records.append(
            {
                "id": str(row.record_id),
                "values": {
                    "kind": "address",
                    "email": row.email,
                    "roles": row.roles,
                    "creation_origin": row.creation_origin,
                    "creation_operation": str(row.creation_operation),
                    "grants": {grant.role: grant.origins for grant in row.grants.all()},
                },
            }
        )
    for row in snapshot.ministryassignment_set.all():
        records.append(
            {
                "id": str(row.record_id),
                "values": {
                    "kind": "assignment",
                    "email": row.email,
                    "ministry_duid": row.ministry_duid,
                    "source": row.source,
                    "operation_id": str(row.operation_id),
                },
            }
        )
    return sorted(records, key=lambda record: record["id"])
