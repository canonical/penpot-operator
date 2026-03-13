#!/usr/bin/env python3

# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Integration tests."""

import logging
import re
import tempfile

import jubilant
import requests
from playwright.sync_api import Page
from tenacity import (
    Retrying,
    stop_after_attempt,
    wait_fixed,
)

from tests.integration.helpers import (
    wait_for_endpoint,
)

logger = logging.getLogger(__name__)


def _admin_identity_exists(juju: jubilant.Juju, email: str) -> bool:
    """Return whether a Kratos identity already exists for the given email."""
    try:
        task = juju.run("kratos/0", "get-identity", {"email": email})
        return task.status == "completed"
    except jubilant.TaskError as err:
        logger.info("get-identity failed for %s: %s", email, err)
        return False


def inject_root_certs(juju: jubilant.Juju, penpot_units: list[str], ca_cert: str):
    """Inject CA certificate to penpot Java certificate store."""
    for unit_name in penpot_units:
        logger.info("copying oauth ca cert into %s", unit_name)
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", delete=True) as temp_file:
            temp_file.write(ca_cert)
            temp_file.flush()
            juju.scp(temp_file.name, f"{unit_name}:/oauth.crt", container="penpot")
        stdout = juju.ssh(unit_name, "cat", "/oauth.crt", container="penpot")
        logger.info("copying oauth ca cert into %s result: %s", unit_name, stdout)
        logger.info("installing oauth ca cert into penpot/%s java trust", unit_name)
        stdout = juju.ssh(
            unit_name,
            "/usr/lib/jvm/java-21-openjdk-amd64/bin/keytool",
            "-import",
            "-trustcacerts",
            "-file",
            "/oauth.crt",
            "-keystore",
            "/usr/lib/jvm/java-21-openjdk-amd64/lib/security/cacerts",
            "-storepass",
            "changeit",
            "-noprompt",
            container="penpot",
        )
        logger.info("keytool import output: %s", stdout)
        logger.info("restart penpot backend in penpot/%s", unit_name)
        juju.ssh(unit_name, "pebble", "restart", "backend", container="penpot")


def test_create_profile(juju: jubilant.Juju, deployment: list[str], public_url: str):
    """
    arrange: deploy the Penpot charm.
    act: create a Penpot account using the 'create-profile' charm action.
    assert: the account created can be used to log in to Penpot.
    """
    juju.wait(
        lambda status: jubilant.all_active(status, *deployment),
        timeout=900,
    )

    email = "test@test.com"
    unit = "penpot/0"

    password = ""  # nosec: B105
    for attempt in Retrying(stop=stop_after_attempt(60), wait=wait_fixed(5), reraise=True):
        with attempt:
            task = juju.run(unit, "create-profile", {"email": email, "fullname": "test"})
            password = task.results.get("password", "")
            if password:
                break
            logger.info("waiting for penpot started: %s", task.results)
            raise AssertionError("profile creation not ready")

    logger.info("`create test penpot` user %s with password: %s", email, password)
    logger.info("using public URL: %s", public_url)
    login_endpoint = f"{public_url}/api/rpc/command/login-with-password"
    logger.info("login endpoint: %s", login_endpoint)
    wait_for_endpoint(f"{public_url}/#/auth/login")
    session = requests.Session()

    for attempt in Retrying(stop=stop_after_attempt(30), wait=wait_fixed(5), reraise=True):
        with attempt:
            response = session.post(
                login_endpoint,
                json={"~:email": email, "~:password": password},
                timeout=10,
                verify=False,
            )
            if response.status_code == 200:
                break
            logger.info(
                "penpot login status: %s (url=%s)",
                response.status_code,
                response.url,
            )
            raise AssertionError(f"login status {response.status_code}")

    juju.run(unit, "delete-profile", {"email": email})

    for attempt in Retrying(stop=stop_after_attempt(60), wait=wait_fixed(5), reraise=True):
        with attempt:
            response = session.post(
                login_endpoint,
                json={"~:email": email, "~:password": password},
                timeout=10,
                verify=False,
            )
            if response.status_code == 400:
                break
            logger.info(
                "penpot login status: %s (url=%s)",
                response.status_code,
                response.url,
            )
            raise AssertionError(f"login status {response.status_code}")


def test_oauth_login(
    juju: jubilant.Juju,
    deployment_with_identity_bundle: set[str],
    public_url: str,
    page: Page,
):
    """
    arrange: deploy the Penpot charm with OAuth identity stack.
    act: integrate penpot with hydra and log in using Kratos native credentials.
    assert: the login flow completes successfully and the user is directed to the validate registration page.
    """
    juju.wait(
        lambda status: jubilant.all_active(status, *deployment_with_identity_bundle), timeout=900
    )

    ca_cert = juju.run("self-signed-certificates/0", "get-ca-certificate").results[
        "ca-certificate"
    ]
    penpot_units = sorted(
        juju.status().apps["penpot"].units.keys(),
        key=lambda unit_name: int(unit_name.split("/")[-1]),
    )
    inject_root_certs(juju, penpot_units, ca_cert)
    juju.integrate("penpot:oauth", "hydra")

    juju.wait(lambda status: jubilant.all_active(status, "penpot", "hydra"), timeout=600)

    test_email = "test@example.com"
    test_password = "Testing1234!"  # nosec: B105
    test_username = "admin"

    if not _admin_identity_exists(juju, test_email):
        juju.run(
            "kratos/0",
            "create-admin-account",
            {"email": test_email, "password": test_password, "username": test_username},
        )

    secret_name = f"oauth-password-{juju.model}"
    secret_id = juju.add_secret(secret_name, {"password": test_password})
    juju.cli("grant-secret", secret_id, "kratos")
    reset_task = juju.run(
        "kratos/0",
        "reset-password",
        {"email": test_email, "password-secret-id": secret_id.split(":")[-1]},
    )
    if reset_task.status != "completed":
        raise AssertionError(f"reset-password failed: {reset_task.results}")

    wait_for_endpoint(f"{public_url}/#/auth/login", timeout=300)

    page.goto(f"{public_url}/#/auth/login")
    logger.info("Navigated to penpot login. Current URL: %s", page.url)
    logger.info("Penpot login page content (first 1200): %s", page.content()[:1200])

    oidc_button = page.locator(
        'a[href*="/api/auth/oauth/oidc"], button:has-text("OpenID"), a:has-text("OpenID")'
    ).first
    oidc_button.wait_for(state="visible", timeout=90_000)
    oidc_button.click()
    page.wait_for_url(re.compile(r".*/(oauth2/auth|ui/login)\?.*"), timeout=60_000)
    logger.info("after OIDC click, url: %s", page.url)
    logger.info("IdP login page content (first 1200): %s", page.content()[:1200])

    email_input = page.locator(
        'input[name="identifier"], input[name="email"], input[type="email"]'
    ).first
    password_input = page.locator('input[name="password"], input[type="password"]').first
    email_input.wait_for(state="visible", timeout=30_000)
    password_input.wait_for(state="visible", timeout=30_000)
    email_input.fill(test_email)
    password_input.fill(test_password)

    submit_button = page.get_by_role(
        "button",
        name="Sign in",
    ).first
    submit_button.wait_for(state="visible", timeout=15_000)
    submit_button.click(timeout=15_000)

    # Current login UI either redirects directly to Penpot or shows consent first.
    page.wait_for_url(
        re.compile(rf".*/ui/consent\?.*|^{re.escape(public_url)}/.*"),
        timeout=30_000,
    )
    if "/ui/consent" in page.url:
        consent_button = page.get_by_role(
            "button",
            name=re.compile(r"accept|allow|authorize|continue", re.IGNORECASE),
        ).first
        # In this UI version, consent can auto-forward quickly after challenge validation.
        if consent_button.is_visible(timeout=5_000):
            consent_button.click(timeout=15_000)
        page.wait_for_url(f"{public_url}/**", timeout=30_000)

    logger.info("final url: %s", page.url)
    logger.info("Final page content (first 1200): %s", page.content()[:1200])
    validate_url = re.compile(rf"^{re.escape(public_url)}/#/auth/register/validate.*")
    page.wait_for_url(validate_url, timeout=120_000)
