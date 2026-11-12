#!/usr/bin/env python3

# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Integration tests."""

import logging
import re
import secrets
import tempfile
import urllib.parse

import jubilant
import requests
from playwright.sync_api import Page
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from tenacity import (
    Retrying,
    stop_after_attempt,
    wait_fixed,
)

from tests.integration.helpers import (
    get_public_url,
    wait_for_active_with_migrations,
    wait_for_config_flags,
    wait_for_endpoint,
)

logger = logging.getLogger(__name__)

KEYTOOL = "/usr/lib/jvm/java-25-openjdk-amd64/bin/keytool"
TRUSTSTORE = "/usr/lib/jvm/java-25-openjdk-amd64/lib/security/cacerts"
# The JDK's default keystore password, not a secret.
TRUSTSTORE_PASSWORD = "changeit"  # nosec B105
CA_ALIAS = "penpot-test-ca"


def _admin_identity_exists(juju: jubilant.Juju, email: str) -> bool:
    """Return whether a Kratos identity already exists for the given email."""
    try:
        task = juju.run("kratos/0", "get-identity", {"email": email})
        return task.status == "completed"
    except jubilant.TaskError as err:
        logger.info("get-identity failed for %s: %s", email, err)
        return False


def _truststore_has_ca(juju: jubilant.Juju, unit_name: str) -> bool:
    """Return whether the injected CA is present in the unit's Java truststore."""
    try:
        juju.ssh(
            unit_name,
            KEYTOOL,
            "-list",
            "-keystore",
            TRUSTSTORE,
            "-storepass",
            TRUSTSTORE_PASSWORD,
            "-alias",
            CA_ALIAS,
            container="penpot",
        )
    except jubilant.CLIError:
        return False
    return True


def inject_root_certs(juju: jubilant.Juju, penpot_units: list[str], ca_cert: str):
    """Inject CA certificate to penpot Java certificate store.

    The import is idempotent and verified: a unit that does not end up with the
    CA in its truststore fails with a clear error instead of a confusing OIDC
    login timeout later.
    """
    for unit_name in penpot_units:
        logger.info("copying oauth ca cert into %s", unit_name)
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", delete=True) as temp_file:
            temp_file.write(ca_cert)
            temp_file.flush()
            juju.scp(temp_file.name, f"{unit_name}:/oauth.crt", container="penpot")
        if _truststore_has_ca(juju, unit_name):
            logger.info("oauth ca cert already in penpot/%s java trust", unit_name)
        else:
            logger.info("installing oauth ca cert into penpot/%s java trust", unit_name)
            stdout = juju.ssh(
                unit_name,
                KEYTOOL,
                "-import",
                "-trustcacerts",
                "-file",
                "/oauth.crt",
                "-keystore",
                TRUSTSTORE,
                "-storepass",
                TRUSTSTORE_PASSWORD,
                "-alias",
                CA_ALIAS,
                "-noprompt",
                container="penpot",
            )
            logger.info("keytool import output: %s", stdout)
        logger.info("restart penpot backend in penpot/%s", unit_name)
        juju.ssh(unit_name, "pebble", "restart", "backend", container="penpot")
        if not _truststore_has_ca(juju, unit_name):
            raise AssertionError(
                f"CA certificate is missing from the Java truststore of {unit_name} "
                "after restarting the backend"
            )


def _log_unit_diagnostics(juju: jubilant.Juju, unit_name: str, public_url: str) -> None:
    """Log the frontend config, truststore and served certificate of one unit."""
    try:
        config = juju.ssh(unit_name, "cat", "/var/www/app/js/config.js", container="penpot")
        logger.info("penpot %s config.js: %s", unit_name, config[:2000])
    except Exception:
        logger.exception("could not read config.js from %s", unit_name)
    try:
        truststore = juju.ssh(
            unit_name,
            KEYTOOL,
            "-list",
            "-keystore",
            TRUSTSTORE,
            "-storepass",
            TRUSTSTORE_PASSWORD,
            "-alias",
            CA_ALIAS,
            container="penpot",
        )
        logger.info("penpot %s truststore entry %s: %s", unit_name, CA_ALIAS, truststore[:1000])
    except Exception:
        logger.exception("penpot %s has no %s entry in its truststore", unit_name, CA_ALIAS)
    host = urllib.parse.urlsplit(public_url).hostname
    if not host:
        logger.info("skipping served certificate check for %s: no public host", unit_name)
        return
    try:
        served = juju.ssh(
            unit_name,
            KEYTOOL,
            "-printcert",
            "-sslserver",
            f"{host}:443",
            container="penpot",
        )
        logger.info(
            "penpot %s served certificate chain for %s:\n%s", unit_name, host, served[:4000]
        )
    except Exception:
        logger.exception("could not inspect the certificate served by %s", public_url)


def _log_auth_diagnostics(
    juju: jubilant.Juju,
    public_url: str,
    penpot_units: list[str],
) -> None:
    """Log best-effort diagnostics for a failed OIDC login."""
    try:
        logger.info("juju status:\n%s", juju.status())
    except Exception:
        logger.exception("could not read juju status")
    for unit_name in penpot_units:
        _log_unit_diagnostics(juju, unit_name, public_url)


def _wait_for_login_result(
    juju: jubilant.Juju,
    page: Page,
    public_url: str,
    penpot_units: list[str],
) -> None:
    """Wait for the OIDC login to land on the registration page or an error.

    Waiting for the error URL too turns a 2 minute timeout into a fast,
    actionable failure that carries the PKIX hint from the URL.
    """
    login_result_url = re.compile(
        rf"^{re.escape(public_url)}/#/auth/(?:register/validate|login\?error=).*"
    )
    auth_error_url = re.compile(rf"^{re.escape(public_url)}/#/auth/login\?error=")
    try:
        page.wait_for_url(login_result_url, timeout=120_000)
    except PlaywrightTimeoutError as err:
        _log_auth_diagnostics(juju, public_url, penpot_units)
        raise AssertionError(
            f"OIDC login did not complete; final URL: {urllib.parse.unquote(page.url)}"
        ) from err
    if auth_error_url.match(page.url):
        _log_auth_diagnostics(juju, public_url, penpot_units)
        raise AssertionError(f"OIDC login failed: {urllib.parse.unquote(page.url)}")


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
    inject_root_certs(juju, penpot_units, ca_cert)
    juju.integrate("penpot:oauth", "hydra")

    juju.wait(lambda status: jubilant.all_active(status, "penpot", "hydra"), timeout=600)

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
    # The OIDC button only renders once the frontend config has been rewritten
    # with the flags from the oauth relation. Wait for that config instead of
    # relying on the 30s button timeout, which is what produced the flaky
    # "OIDC button did not become visible" failures.
    wait_for_config_flags(f"{public_url}/js/config.js", ["enable-login-with-oidc"], timeout=120)

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
                _log_auth_diagnostics(juju, public_url, penpot_units)
                raise AssertionError("OIDC button did not become visible") from err

            oidc_button.click()
            try:
                page.wait_for_url(idp_url, timeout=30_000)
            except PlaywrightTimeoutError as err:
                _log_auth_diagnostics(juju, public_url, penpot_units)
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
    _wait_for_login_result(juju, page, public_url, penpot_units)
