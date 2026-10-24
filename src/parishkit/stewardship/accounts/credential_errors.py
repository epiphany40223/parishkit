"""Credential failure types safe to classify before Django is configured."""


class CredentialValidationUnavailable(Exception):
    """A provider adapter requests retry without exposing provider error text."""

    def __init__(self):
        super().__init__("Credential validation is temporarily unavailable.")


class CredentialOrganizationMismatch(Exception):
    """A ParishSoft key that authenticates but does not reach the configured
    organization (#1495); it may reach others (#1554).

    Refused for good, as a rejected key is, but named apart so the installer
    can record that cause and the settings page can say which one applied.
    """

    def __init__(self):
        super().__init__(
            "The key does not reach the configured ParishSoft organization."
        )
