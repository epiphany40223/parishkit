"""One explicit Slack readiness notification, never a connectivity-only check."""

import json
import re
from dataclasses import dataclass
from uuid import UUID

import requests

from parishkit.config import ConfigError

from .accounts.integration_candidates import _object, slack_candidate
from .accounts.provider_context import validated_context
from .readiness_delivery import DeliveryOutcome

ENDPOINT = "https://slack.com/api/chat.postMessage"
MAX_RESPONSE = 65536
# Only explicit documented refusals prove non-submission. Unknown provider
# errors, including internal_error/fatal_error, may follow a successful effect.
REFUSALS = frozenset(
    {
        "account_inactive",
        "channel_not_found",
        "invalid_auth",
        "is_archived",
        "missing_scope",
        "no_permission",
        "not_authed",
        "not_in_channel",
        "restricted_action",
        "token_expired",
        "token_revoked",
        "ratelimited",
    }
)


@dataclass(frozen=True, repr=False)
class ReadinessNotification:
    """A fixed fictional message cannot disclose Family values or inject mentions."""

    delivery_id: UUID
    channel_id: str

    def __post_init__(self):
        if not isinstance(self.delivery_id, UUID):
            raise ValueError("An exact notification identity is required.")
        validated_context("slack", {"channel_id": self.channel_id})

    def payload(self):
        """The private pipe carries routing and correlation, never arbitrary text."""
        return {"delivery_id": str(self.delivery_id), "channel_id": self.channel_id}

    @classmethod
    def from_payload(cls, value):
        """Reject unknown fields and noncanonical IDs before contacting Slack."""
        if type(value) is not dict or set(value) != {"delivery_id", "channel_id"}:
            raise ValueError("Invalid readiness notification.")
        if type(value["delivery_id"]) is not str:
            raise ValueError("Invalid notification identity.")
        identifier = UUID(value["delivery_id"])
        if str(identifier) != value["delivery_id"]:
            raise ValueError("Invalid notification identity.")
        return cls(identifier, value["channel_id"])

    def message(self):
        """Correlation aids human inspection; it is not an idempotency key."""
        return {
            "channel": self.channel_id,
            "text": "TEST — ParishKit stewardship setup notification. "
            "This fictional readiness test contains no parishioner data. "
            "Reference: " + str(self.delivery_id),
            "mrkdwn": False,
            "parse": "none",
            "unfurl_links": False,
            "unfurl_media": False,
        }


def _outcome(response, notification):
    """Read a bounded response; only exact acceptance or known refusal is decisive."""
    if response.status_code == 429:
        return DeliveryOutcome.NOT_SENT
    if response.status_code != 200:
        return DeliveryOutcome.UNKNOWN
    content = bytearray()
    for chunk in response.iter_content(chunk_size=8192):
        if len(content) + len(chunk) > MAX_RESPONSE:
            return DeliveryOutcome.UNKNOWN
        content.extend(chunk)
    result = json.loads(content.decode("utf-8"), object_pairs_hook=_object)
    if type(result) is not dict:
        return DeliveryOutcome.UNKNOWN
    if result.get("ok") is False and result.get("error") in REFUSALS:
        return DeliveryOutcome.NOT_SENT
    if (
        result.get("ok") is True
        and result.get("channel") == notification.channel_id
        and type(result.get("ts")) is str
        and re.fullmatch(r"[0-9]{1,20}\.[0-9]{1,20}", result["ts"])
    ):
        return DeliveryOutcome.ACCEPTED
    return DeliveryOutcome.UNKNOWN


def deliver_notification(value, notification):
    """Issue one fixed-endpoint POST; the owning subprocess enforces total time.

    No retries, redirects, environment proxies, arbitrary body or recipient are
    admitted. Once HTTP submission begins, missing evidence remains uncertain.
    This adapter is not authorization: its caller must commit submission first.
    """
    try:
        token = slack_candidate(value)
        if not isinstance(notification, ReadinessNotification):
            raise ValueError("Invalid notification.")
    except (ConfigError, ValueError, TypeError):
        return DeliveryOutcome.NOT_SENT
    try:
        with requests.Session() as session:
            session.trust_env = False
            with session.post(
                ENDPOINT,
                headers={"Authorization": "Bearer " + token},
                json=notification.message(),
                timeout=(5, 10),
                allow_redirects=False,
                stream=True,
            ) as response:
                return _outcome(response, notification)
    except Exception:
        return DeliveryOutcome.UNKNOWN
