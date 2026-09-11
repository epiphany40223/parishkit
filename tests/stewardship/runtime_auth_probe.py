"""Exercise runtime-auth consumers with actual disposable SQL and broker grants."""

import sys
from pathlib import Path


def main():
    """Assert consumer contracts without exposing credentials or using Google."""
    from parishkit.stewardship.deployment import load_deployment
    from parishkit.stewardship.runtime_web import configure_web

    configure_web(load_deployment(Path(sys.argv[1])))
    from allauth.socialaccount.adapter import _build_apps_from_settings
    from django.conf import settings
    from django.db import transaction
    from redis.exceptions import NoPermissionError

    from parishkit.stewardship.accounts.limiting import Counter
    from parishkit.stewardship.campaigns.credential_keys import key_set_lock

    family = settings.STEWARDSHIP_FAMILY_RUNTIME
    with transaction.atomic(), key_set_lock(family.general, family.mac, family.public):
        pass

    app = _build_apps_from_settings(provider="google")["google"][0]
    assert app.client_id and app.secret and app.key == ""
    assert "download" in settings.DATABASES
    assert settings.MIDDLEWARE[0].endswith("HttpMetricsMiddleware")
    limiter = settings.STEWARDSHIP_AUTH_RUNTIME.limiter
    assert limiter.elevated("admin") is False
    assert limiter.elevated("family") is False
    counter = Counter("admin_identity_1", "a" * 64, 5, 60)
    limiter.counters((counter,), failure=True)
    limiter.clear(counter)
    assert limiter.outage is False
    for command in (("GET", "unrelated:key"), ("FLUSHDB",), ("ACL", "LIST")):
        try:
            limiter.client.execute_command(*command)
        except NoPermissionError:
            continue
        raise AssertionError("The web ACL admitted unrelated authority")
    print("RUNTIME_AUTH_CONSUMERS_OK")


if __name__ == "__main__":
    main()
