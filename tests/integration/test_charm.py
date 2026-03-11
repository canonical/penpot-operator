#!/usr/bin/env python3

# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Integration tests."""

import logging
import re
import tempfile

import jubilant
import requests
from playwright.sync_api import Page, expect
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
    oauth_deployment: list[str],
    public_url: str,
    page: Page,
):
    """
    arrange: deploy the Penpot charm with OAuth identity stack.
    act: integrate penpot with hydra and log in using Kratos native credentials.
    assert: the OAuth login flow redirects back to Penpot successfully.
    """
    juju.wait(lambda status: jubilant.all_active(status, *oauth_deployment), timeout=900)

    ca_cert = juju.run("self-signed-certificates/0", "get-ca-certificate").results[
        "ca-certificate"
    ]
    penpot_units = sorted(
        juju.status().apps["penpot"].units.keys(),
        key=lambda unit_name: int(unit_name.split("/")[-1]),
    )
    inject_root_certs(juju, penpot_units, ca_cert)
    juju.integrate("penpot:oauth", "hydra")
    # Wait for both penpot and hydra to fully settle after oauth integration so that
    # the charm reconcile propagates enable-login-with-oidc to the frontend flags.
    juju.wait(lambda status: jubilant.all_active(status, "penpot", "hydra"), timeout=600)

    test_email = "test@example.com"
    test_password = "Testing1234!"  # nosec: B105
    test_username = "admin"

    if not _admin_identity_exists(juju, test_email):
        for attempt in Retrying(stop=stop_after_attempt(20), wait=wait_fixed(10), reraise=True):
            with attempt:
                task = juju.run(
                    "kratos/0",
                    "create-admin-account",
                    {"email": test_email, "password": test_password, "username": test_username},
                )
                if task.status != "completed":
                    raise AssertionError(f"create-admin-account not ready: {task.results}")

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

    for attempt in Retrying(
        stop=stop_after_attempt(2),
        wait=wait_fixed(30),
        reraise=True,
        before_sleep=lambda retry_state: logger.info(
            "login attempt %d failed, retrying in 30 seconds", retry_state.attempt_number
        ),
    ):
        with attempt:
            page.goto(f"{public_url}/#/auth/login")
            logger.info("Navigated to penpot login. Current URL: %s", page.url)
            # Wait for the ClojureScript SPA to fully boot and render the OpenID action.
            oidc_button = page.locator(
                ", ".join(
                    [
                        'a[href*="/api/auth/oauth/oidc"]',
                        'button:has-text("OpenID")',
                        'a:has-text("OpenID")',
                    ]
                )
            ).first
            try:
                oidc_button.wait_for(state="visible", timeout=90_000)
            except Exception:
                # Copy screenshot to project dir (bind-mounted from host) for easy viewing.
                project_dir = "/home/ubuntu/projects/penpot-operator"
                screenshot_path = f"{project_dir}/penpot-login-screenshot.png"
                try:
                    page.screenshot(path=screenshot_path, full_page=True)
                    logger.info("Screenshot saved to %s", screenshot_path)
                except Exception as ss_err:
                    logger.info("Screenshot failed: %s", ss_err)
                body_html = page.evaluate("() => document.body.innerHTML")
                auth_controls = page.locator("a, button").all_inner_texts()
                logger.info(
                    "OIDC entry point not found after 90s."
                    "\n  Page URL: %s"
                    "\n  Body HTML (first 3000): %s"
                    "\n  Auth control texts (first 30): %s",
                    page.url,
                    body_html[:3000],
                    auth_controls[:30],
                )
                raise
            logger.info("OIDC button visible, clicking")
            oidc_button.click()
            page.wait_for_url(re.compile(r".*/(oauth2/auth|ui/login)\?.*"), timeout=60_000)
            logger.info("after OIDC click, url: %s", page.url)

            # Hydra/Kratos login labels can vary by UI version and locale.
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
                name=re.compile(r"sign in|log in|login|continue|next", re.IGNORECASE),
            ).first
            try:
                submit_button.wait_for(state="visible", timeout=15_000)
                submit_button.click(timeout=15_000)
            except Exception:
                button_texts = page.get_by_role("button").all_inner_texts()
                logger.info(
                    "Hydra submit button not matched; visible button texts: %s",
                    button_texts[:20],
                )
                # Some login UI variants do not expose a semantic submit button.
                password_input.press("Enter")

            try:
                page.wait_for_url(f"{public_url}/**", timeout=30_000)
            except Exception:
                current_url = page.url
                consent_button = page.get_by_role(
                    "button",
                    name=re.compile(r"accept|allow|authorize|continue", re.IGNORECASE),
                ).first
                if "/ui/consent" in current_url or consent_button.count() > 0:
                    logger.info("OIDC consent step detected at %s", current_url)
                    try:
                        consent_button.click(timeout=10_000)
                    except Exception:
                        page.keyboard.press("Enter")

                # One final wait for redirect back to Penpot.
                try:
                    page.wait_for_url(f"{public_url}/**", timeout=30_000)
                except Exception:
                    page_buttons = page.get_by_role("button").all_inner_texts()
                    page_text = page.evaluate("() => document.body.innerText")
                    logger.info(
                        "OIDC login did not return to Penpot."
                        "\n  Current URL: %s"
                        "\n  Visible buttons (first 20): %s"
                        "\n  Body text (first 1200): %s",
                        page.url,
                        page_buttons[:20],
                        page_text[:1200],
                    )
                    raise
            logger.info("final url: %s", page.url)
            expect(page).to_have_url(re.compile(rf"^{re.escape(public_url)}/#/auth/register.*"))
