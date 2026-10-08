"""The fixed setup test email (#142), without a database."""

from parishkit.stewardship.accounts.setup_mail import setup_test_message


def test_setup_test_message_is_fixed_and_escaped():
    """The setup email names the parish, escaped in HTML, and nothing else."""
    message = setup_test_message({"name": "St. A & B <Parish>"})
    assert message["subject"] == "Stewardship setup test"
    assert "St. A &amp; B &lt;Parish&gt;" in message["html"]
    assert "St. A & B <Parish>" in message["text"]
    assert "{{" not in message["html"] + message["text"]
