"""Independent, non-secret YAML candidates for pure and PostgreSQL tests."""

from uuid import uuid4

from parishkit.stewardship.accounts.authority import parse_version
from parishkit.stewardship.accounts.configuration_schema import validate_sections


def configuration_document():
    """Create fresh IDs and public synthetic metadata without shared mutation."""
    return {
        "schema_version": 1,
        "version_id": str(uuid4()),
        "predecessor_digest": None,
        "sections": {
            "parish": [
                {
                    "id": str(uuid4()),
                    "values": {
                        "name": "Example Parish",
                        "website": "https://parish.example.org/",
                        "timezone": "America/New_York",
                        "phone": "+12025550123",
                        "branding": {
                            name: str(uuid4())
                            for name in ("large", "menu", "icon", "favicon")
                        },
                    },
                }
            ],
            "integrations": [
                {
                    "id": str(uuid4()),
                    "values": {
                        "kind": "parishsoft",
                        "settings": {"organization_id": "12345"},
                        "credential_fingerprint": "a" * 64,
                    },
                }
            ],
        },
    }


def configuration_version(document=None):
    """Pass every fixture through the same strict parser as the storage service."""
    return parse_version(
        configuration_document() if document is None else document,
        validate_sections=validate_sections,
    )


def successor_document(version):
    """Preserve record IDs while advancing the immutable configuration envelope."""
    document = version.document()
    document["version_id"] = str(uuid4())
    document["predecessor_digest"] = version.digest
    document["sections"]["parish"][0]["values"]["name"] = "Updated Example Parish"
    return document
