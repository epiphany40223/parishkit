"""One original browser completes real setup under the actual web SQL identity."""

import json
import sys
import time
from io import BytesIO
from pathlib import Path
from urllib.parse import parse_qs, urlsplit


def google_fixture():
    """Retain OAuth state/nonce/PKCE and JWT verification; fake only Google I/O."""
    import jwt
    from allauth.socialaccount.internal import jwtkit
    from cryptography.hazmat.primitives.asymmetric import rsa
    from django.utils import timezone

    from parishkit.stewardship.accounts.authentication import SignedGoogleAdapter

    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwtkit.fetch_key = lambda *args: ("RS256", private.public_key())

    def exchange(adapter, request, app, client, pkce_code_verifier=None):
        """The synthetic response is bound to the actual browser's exact nonce."""
        assert pkce_code_verifier
        now = int(timezone.now().timestamp())
        claims = {
            "iss": "https://accounts.google.com",
            "aud": "fake-client",
            "sub": "synthetic-initial-admin",
            "email": "admin@example.org",
            "email_verified": True,
            "hd": "example.org",
            "iat": now,
            "auth_time": now,
            "exp": now + 60,
            "nonce": request.stewardship_oauth_state["data"]["nonce"],
        }
        return {
            "access_token": "synthetic-access",
            "id_token": jwt.encode(claims, private, algorithm="RS256"),
        }

    SignedGoogleAdapter.get_access_token_data = exchange


def fields(form):
    """Serialize rendered controls, preserving prefixes and multi-select values."""
    return {
        field.html_name: "" if field.value() is None else field.value()
        for field in form
    }


def main():
    """No owner SQL writes, provider keys on web mounts or fabricated completion."""
    from parishkit.stewardship.deployment import load_deployment
    from parishkit.stewardship.runtime_web import configure_web

    deployment = load_deployment(Path(sys.argv[1]))
    configure_web(deployment)
    from django.core.files.uploadedfile import SimpleUploadedFile
    from django.test import Client
    from django.test.utils import setup_test_environment
    from PIL import Image

    from parishkit.stewardship.accounts.authentication import runtime
    from parishkit.stewardship.accounts.campaign_forms import initial_fields
    from parishkit.stewardship.accounts.configuration_installation import (
        coherent_configuration,
    )
    from parishkit.stewardship.accounts.setup_forms import initial_values
    from parishkit.stewardship.accounts.setup_install_models import SetupCompletion

    setup_test_environment()
    google_fixture()
    fixture = json.loads(sys.argv[2])
    origin = urlsplit(deployment.public_origin)
    browser = Client(enforce_csrf_checks=True, HTTP_HOST=origin.netloc)

    def get(path):
        """Read through all real web middleware, never refreshing idle by fiat."""
        response = browser.get(path, secure=origin.scheme == "https")
        assert response.status_code in {200, 302}, (path, response.status_code)
        return response

    def post(path, data):
        """Use the current CSRF token and actual form owners for every mutation."""
        response = browser.post(
            path,
            data | {"csrfmiddlewaretoken": browser.cookies["csrftoken"].value},
            secure=origin.scheme == "https",
            HTTP_ORIGIN=deployment.public_origin,
        )
        if response.status_code != 302:
            errors = {}
            if response.context:
                for key in ("form", "window", "schedules", "formset"):
                    value = response.context.get(key)
                    if value is not None:
                        errors[key] = str(value.errors)
            raise AssertionError((path, response.status_code, errors))
        return response

    def version(path):
        """Save against the version just displayed to the original browser."""
        return get(path).context["draft"].status.version

    get("/admin/login")
    query = parse_qs(urlsplit(post("/admin/login", {})["Location"]).query)
    callback = get("/admin/oauth/callback?code=synthetic&state=" + query["state"][0])
    assert callback["Location"] == "/admin/", callback["Location"]
    get("/admin/setup")
    post("/admin/setup", {"action": "start"})
    for step, values in fixture["steps"].items():
        path = "/admin/setup/" + step
        post(path, initial_values(step, values) | {"version": version(path)})
    for target in ("parishsoft", "google_workspace"):
        path = "/admin/setup/credentials/" + target
        data = {"version": version(path), "candidate": "synthetic-private-" + target}
        if target == "parishsoft":
            data["organization_id"] = "1"
        else:
            data["candidate"] = fixture["workspace"]
        post(path, data)
    stream = BytesIO()
    Image.new("RGB", (64, 64), "navy").save(stream, format="PNG")
    post(
        "/admin/setup/branding",
        {
            "version": version("/admin/setup/branding"),
            "logo": SimpleUploadedFile("logo.png", stream.getvalue(), "image/png"),
        },
    )
    draft = get("/admin/setup").context["draft"]
    progress = post(
        "/admin/setup",
        {
            "action": "load",
            "attempt": str(draft.status.attempt_id),
            "version": draft.status.version,
        },
    )["Location"]
    deadline = time.monotonic() + 180
    while True:
        data = get(progress + "?format=json").json()
        if data["task_state"] == "succeeded":
            break
        assert data["task_state"] not in {"failed", "cancelled"}, data
        assert time.monotonic() < deadline, data
        time.sleep(0.5)
    assert not runtime().configured() and not SetupCompletion.objects.exists()
    data = initial_fields(fixture["campaign"], digest="")
    data.pop("base_digest")
    post(
        "/admin/setup/campaign",
        {key: "" if value is None else value for key, value in data.items()}
        | {"version": version("/admin/setup/campaign")},
    )
    shares = get("/admin/setup/shares")
    data = fields(shares.context["formset"].management_form)
    for form in shares.context["formset"]:
        data.update(fields(form))
    post(
        "/admin/setup/shares",
        data | {"version": shares.context["draft"].status.version},
    )
    path = "/admin/setup/content/email/initial"
    post(
        path,
        {
            "version": version(path),
            "subject": "Campaign invitation",
            "html": (
                "<p>Please review your Family information.</p>"
                "<p>{{ family_code }} {{ family_url }}</p>"
            ),
            "text": "",
            "generate_text": "on",
        },
    )
    # Save an initial invitation schedule without materializing live fulfillment.
    schedules = get("/admin/setup/schedules")
    data = fields(schedules.context["window"])
    data.update(fields(schedules.context["schedules"].management_form))
    for form in schedules.context["schedules"]:
        data.update(fields(form))
    template = (
        schedules.context["schedules"].forms[0].fields["template_version"].choices[1][0]
    )
    data.update(
        {
            "schedules-0-kind": "initial",
            "schedules-0-date": fixture["campaign"]["start_date"],
            "schedules-0-time": "09:00:00",
            "schedules-0-template_version": template,
        }
    )
    post(
        "/admin/setup/schedules",
        data | {"version": schedules.context["draft"].status.version},
    )
    get("/admin/setup/preview")
    mail = get("/admin/setup/mail-test")
    post("/admin/setup/mail-test", fields(mail.context["form"]))
    deadline = time.monotonic() + 120
    while True:
        status = get("/admin/setup/mail-test/status").json()
        if not status["pending"]:
            assert status["items"][0]["state"] == "accepted", status
            break
        assert time.monotonic() < deadline, status
        time.sleep(0.5)
    confirmation = get("/admin/setup/confirm")
    assert confirmation.context["readiness_problem"] is None
    post(
        "/admin/setup/confirm",
        fields(confirmation.context["form"]) | {"confirmed": "on"},
    )
    deadline = time.monotonic() + 180
    while True:
        response = get("/admin/setup/cancel")
        if fixture.get("abort"):
            from parishkit.stewardship.accounts.secret_models import (
                CredentialConsumerAcknowledgement,
            )
            from parishkit.stewardship.accounts.setup_install_models import (
                SetupCredentialInstallation,
            )

            assert response.status_code == 200
            attempt_id = response.context["attempt"].attempt_id
            installs = SetupCredentialInstallation.objects.filter(
                readiness__intent__attempt_id=attempt_id
            ).values_list("request_id", flat=True)
            if (
                CredentialConsumerAcknowledgement.objects.filter(
                    request_id__in=installs
                ).count()
                == 2
            ):
                post("/admin/setup/cancel", {"attempt": str(attempt_id)})
                assert (
                    not runtime().configured() and not SetupCompletion.objects.exists()
                )
                print("INITIAL_SETUP_CANCELLED_AFTER_ACK_OK")
                return
        if response.status_code == 302:
            assert response["Location"] == "/admin/"
            break
        assert time.monotonic() < deadline, "Initial finalization did not complete"
        time.sleep(0.5)
    assert runtime().configured()
    current = coherent_configuration(runtime().store)
    assert current.mode == "testing"
    assert current.current_campaign.state == "draft"
    assert SetupCompletion.objects.count() == 1
    assert get("/admin/").status_code == 200
    print("INITIAL_SETUP_ATOMIC_COMPLETION_OK")


if __name__ == "__main__":
    main()
