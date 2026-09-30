"""Fixed-content security alerts for high-impact policy changes.

These are the login-policy expansions and a replaced backup encryption key.

The SQL twin `stewardship_security_content_v1` recompiles these exact
alternatives from the immutable event, so every string here is closed and
the deployment mode is visible but never changes routing.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from html import escape
from types import MappingProxyType
from uuid import UUID

from parishkit.stewardship.campaigns.domain import SystemMode

from .operational_content import OperationalContent

# Plain words in the fixed order the Portal users page uses. The private mail
# helper compiles content with no Django settings, so the page's translated
# labels cannot be used here; a case holds these equal to them.
ROLE_WORDS = (
    ("administrator", "Administrator"),
    ("staff", "Staff"),
    ("ministry_leader", "Ministry leader"),
)
KINDS = MappingProxyType(
    {
        "administrator_granted": "Administrator added to an exact address",
        "domain_created": "Hosted-domain rule created",
        "domain_staff_granted": "Staff added to a hosted-domain rule",
        "backup_key_replaced": "Backup encryption key replaced",
    }
)
INSTRUCTION = (
    "This change to who may sign in took effect on activation. Acknowledge it "
    "on the Admin dashboard; it stays there until an Administrator who existed "
    "before the change does."
)
# A replaced backup key (#198) changes who can open every later backup. Its
# before and after "roles" carry the old and new key IDs instead. The SQL
# twin does not escape instructions, so they hold no HTML-special character.
KEY_INSTRUCTION = (
    "An Administrator replaced the public key that backups are encrypted "
    "with. From the next backup on, only the matching private key can open them. "
    "If the parish key holder did not plan this, replace the key again with the "
    "parish key and ask the server operator to check the newest backup. If you "
    "can still sign in as an Administrator, acknowledge it on the Admin dashboard; "
    "it stays there until an Administrator other than the one who made the change "
    "does. If you no longer can, contact the parish or its other Administrators."
)


@dataclass(frozen=True)
class SecurityAlert:
    """Only the event's own bounded facts reach the content compiler."""

    event_id: UUID
    kind: str
    target: str
    actor: str | None
    mode: SystemMode
    occurred_at: datetime
    before_roles: tuple[str, ...]
    after_roles: tuple[str, ...]

    def __post_init__(self):
        """Reject untyped values, unknown kinds and non-UTC times."""
        if (
            not isinstance(self.event_id, UUID)
            or self.kind not in KINDS
            or type(self.target) is not str
            or not self.target
            or (self.actor is not None and type(self.actor) is not str)
            or not isinstance(self.mode, SystemMode)
            or not isinstance(self.occurred_at, datetime)
            or self.occurred_at.utcoffset() != timedelta(0)
        ):
            raise ValueError("Invalid security alert facts.")
        for name in ("before_roles", "after_roles"):
            roles = getattr(self, name)
            if type(roles) is not tuple or any(type(role) is not str for role in roles):
                raise ValueError("Security alert roles must be a tuple of names.")
        if self.kind == "backup_key_replaced" and (
            len(self.after_roles) != 1 or len(self.before_roles) > 1
        ):
            raise ValueError("A key change names one key after and at most one before.")


def _roles(roles):
    """Word a role list in the fixed order the Portal users page uses."""
    return ", ".join(word for role, word in ROLE_WORDS if role in roles) or "none"


def render_security_alert(alert):
    """Render one fixed-content alert; the SQL twin must agree byte for byte."""
    if not isinstance(alert, SecurityAlert):
        raise TypeError("Typed security alert facts are required.")
    title = KINDS[alert.kind]
    subject = f"[{alert.mode.value.upper()}] SECURITY: {title}"
    common = (
        ("By", alert.actor or "Operator recovery"),
        ("When", alert.occurred_at.strftime("%m/%d/%Y %H:%M:%S UTC")),
        ("Deployment mode", alert.mode.value.title()),
        ("Event reference", str(alert.event_id)),
    )
    if alert.kind == "backup_key_replaced":
        instruction = KEY_INSTRUCTION
        rows = (
            (
                "Key before",
                alert.before_roles[0] if alert.before_roles else "not known",
            ),
            ("Key after", alert.after_roles[0]),
            *common,
        )
    else:
        instruction = INSTRUCTION
        rows = (
            ("Target", alert.target),
            ("Roles before", _roles(alert.before_roles)),
            ("Roles after", _roles(alert.after_roles)),
            *common,
        )
    text = title + "\n\n" + instruction + "\n\n"
    text += "\n".join(f"{label}: {value}" for label, value in rows)
    html = f"<h2>{escape(title)}</h2><p>{escape(instruction)}</p><dl>"
    html += "".join(
        f"<dt>{escape(label)}</dt><dd>{escape(value)}</dd>" for label, value in rows
    )
    return OperationalContent(subject, html + "</dl>", text)
