"""Credential failure types safe to classify before Django is configured."""


class CredentialValidationUnavailable(Exception):
    """A provider adapter requests retry without exposing provider error text."""

    def __init__(self):
        super().__init__("Credential validation is temporarily unavailable.")
