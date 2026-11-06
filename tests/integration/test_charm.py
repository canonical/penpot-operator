#!/usr/bin/env python3

# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Integration tests."""

import logging
import re
import secrets
import tempfile

import jubilant
import requests
from playwright.sync_api import Page
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from tenacity import (
    Retrying,
    retry,
    retry_if_result,
    stop_after_attempt,
    stop_after_delay,
    wait_fixed,
)

from tests.integration.helpers import (
    get_public_url,
    wait_for_active_with_migrations,
    wait_for_endpoint,
    wait_for_oidc_login,
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


JAVA_HOME = "/usr/lib/jvm/java-25-openjdk-amd64"
OAUTH_CA_ALIAS = "penpot-test-oauth-ca"


def _has_root_cert(juju: jubilant.Juju, unit_name: str) -> bool:
    """Return whether the unit's Java trust store contains the injected OAuth CA."""
    try:
        juju.ssh(
            unit_name,
            f"{JAVA_HOME}/bin/keytool",
            "-list",
            "-alias",
            OAUTH_CA_ALIAS,
            "-keystore",
            f"{JAVA_HOME}/lib/security/cacerts",
            "-storepass",
            "changeit",
            container="penpot",
        )
    except jubilant.CLIError:
        return False
    return True


def _backend_ready(juju: jubilant.Juju, unit_name: str) -> bool:
    """Return whether the penpot backend of the unit answers its readiness endpoint."""
    try:
        juju.ssh(unit_name, "curl", "-sf", "-m", "5", "localhost:6060/readyz", container="penpot")
    except jubilant.CLIError:
        return False
    return True


def inject_root_cert(juju: jubilant.Juju, unit_name: str, ca_cert: str):
    """Inject the CA certificate into the unit's Java trust store and restart the backend."""
    logger.info("copying oauth ca cert into %s", unit_name)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", delete=True) as temp_file:
        temp_file.write(ca_cert)
        temp_file.flush()
        juju.scp(temp_file.name, f"{unit_name}:/oauth.crt", container="penpot")
    logger.info("installing oauth ca cert into %s java trust", unit_name)
    stdout = juju.ssh(
        unit_name,
        f"{JAVA_HOME}/bin/keytool",
        "-import",
        "-trustcacerts",
        "-alias",
        OAUTH_CA_ALIAS,
        "-file",
        "/oauth.crt",
        "-keystore",
        f"{JAVA_HOME}/lib/security/cacerts",
        "-storepass",
        "changeit",
        "-noprompt",
        container="penpot",
    )
    logger.info("keytool import output: %s", stdout)
    logger.info("restart penpot backend in %s", unit_name)
    juju.ssh(unit_name, "pebble", "restart", "backend", container="penpot")


def ensure_root_certs(
    juju: jubilant.Juju, penpot_units: list[str], ca_cert: str, timeout: int = 900
):
    """Make every penpot unit trust the CA, with its backend ready.

    Units are handled one at a time so only one backend restarts at once. A slow backend
    restart can fail the alive-level pebble check, and Kubernetes then restarts the container
    with a fresh trust store, so every unit is re-checked and re-injected until it sticks.
    """

    @retry(
        stop=stop_after_delay(timeout),
        wait=wait_fixed(30),
        retry=retry_if_result(lambda ready: not ready),
    )
    def converge() -> bool:
        for unit in penpot_units:
            if not _has_root_cert(juju, unit):
                try:
                    inject_root_cert(juju, unit, ca_cert)
                except jubilant.CLIError as exc:
                    logger.info("injecting the oauth ca into %s failed: %s", unit, exc.stderr)
                return False
            if not _backend_ready(juju, unit):
                logger.info("waiting for penpot backend in %s", unit)
                return False
        return True

    converge()


def test_create_profile(juju: jubilant.Juju, deployment: list[str]):
    """
    arrange: deploy the Penpot charm.
    act: create a Penpot account using the 'create-profile' charm action.
    assert: the account created can be used to log in to Penpot.
    """
    juju.wait(
        lambda status: jubilant.all_active(status, *deployment),
        timeout=900,
    )
    public_url = get_public_url(juju)

    email = secrets.token_hex(8) + "@example.com"
    unit = "penpot/0"

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
    page: Page,
):
    """
    arrange: deploy the Penpot charm with OAuth identity stack.
    act: integrate penpot with hydra and log in using Kratos native credentials.
    assert: the login flow completes successfully and the user is directed to the validate registration page.
    """
    wait_for_active_with_migrations(juju, deployment_with_identity_bundle, timeout=900)
    public_url = get_public_url(juju)

    ca_cert = juju.run("self-signed-certificates/0", "get-ca-certificate").results[
        "ca-certificate"
    ]
    penpot_units = sorted(
        juju.status().apps["penpot"].units.keys(),
        key=lambda unit_name: int(unit_name.split("/")[-1]),
    )
    juju.integrate("penpot:oauth", "hydra")

    wait_for_oidc_login(juju, penpot_units, timeout=600)
    # Inject the CA only once OIDC is enabled: enabling it restarts the backend, which can
    # restart the container and drop a CA injected earlier.
    ensure_root_certs(juju, penpot_units, ca_cert)

    test_email = secrets.token_hex(8) + "@example.com"
    test_password = secrets.token_hex(16)
    test_username = secrets.token_hex(16)

    if not _admin_identity_exists(juju, test_email):
        juju.run(
            "kratos/0",
            "create-admin-account",
            {"email": test_email, "username": test_username},
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

    idp_url = re.compile(r".*/(oauth2/auth|ui/login)\?.*")
    for attempt in Retrying(stop=stop_after_attempt(4), wait=wait_fixed(10), reraise=True):
        with attempt:
            page.goto(f"{public_url}/#/auth/login", wait_until="domcontentloaded")
            logger.info("Navigated to penpot login. Current URL: %s", page.url)
            logger.info("Penpot login page content (first 1200): %s", page.content()[:1200])

            if idp_url.match(page.url):
                break

            oidc_button = page.locator(
                'a[href*="/api/auth/oauth/oidc"], button:has-text("OpenID"), a:has-text("OpenID")'
            ).first
            try:
                oidc_button.wait_for(state="visible", timeout=30_000)
            except PlaywrightTimeoutError as err:
                raise AssertionError("OIDC button did not become visible") from err

            oidc_button.click()
            try:
                page.wait_for_url(idp_url, timeout=30_000)
            except PlaywrightTimeoutError as err:
                raise AssertionError(
                    "OIDC click did not redirect to the identity provider"
                ) from err

            break
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
