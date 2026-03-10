#!/usr/bin/env python3

# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Integration tests."""

import logging
import re
import tempfile

import jubilant
import requests
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page, expect
from tenacity import (
    Retrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_fixed,
)

from tests.integration.helpers import (
    wait_for_endpoint,
)

logger = logging.getLogger(__name__)


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


def test_create_profile(
    juju: jubilant.Juju, deployment: list[str], public_url: str, ingress_host: str
):
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
    login_headers: dict[str, str] = {"Host": ingress_host}
    logger.info(
        "login endpoint: %s headers=%s",
        login_endpoint,
        login_headers,
    )
    wait_for_endpoint(f"{public_url}/#/auth/login", headers=login_headers)
    session = requests.Session()

    for attempt in Retrying(stop=stop_after_attempt(30), wait=wait_fixed(5), reraise=True):
        with attempt:
            response = session.post(
                login_endpoint,
                headers=login_headers,
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
                headers=login_headers,
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
    page: Page,
    ext_idp_service,
):
    """Run OAuth login flow through oauth_tools compatibility path."""
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
    juju.wait(lambda status: jubilant.all_active(status, "penpot"), timeout=300)
    wait_for_endpoint("https://penpot.local/#/auth/login", timeout=300)

    for attempt in Retrying(
        stop=stop_after_attempt(5),
        wait=wait_fixed(60),
        retry=retry_if_exception_type((AssertionError, PlaywrightError)),
        reraise=True,
        before_sleep=lambda retry_state: logger.exception(
            "login attempt %d failed, retrying in 60 seconds", retry_state.attempt_number
        ),
    ):
        with attempt:
            page.goto("https://penpot.local/#/auth/login")
            with page.expect_navigation():
                page.get_by_text("OpenID").click()
            page.wait_for_url(re.compile(r".*/ui/login.*"), timeout=60000)
            with page.expect_navigation():
                page.get_by_role("button", name="Dex").click()
            ext_idp_service.complete_user_login(page)
            expect(page).to_have_url(re.compile("^https://penpot\\.local/#/auth/register.*"))
