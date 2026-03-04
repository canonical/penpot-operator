#!/usr/bin/env python3

# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Integration tests."""

import logging
import re
import tempfile
from typing import Any

import jubilant
import requests
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import expect
from tenacity import retry, stop_after_attempt, wait_fixed

from tests.integration.helpers import (
    wait_for_endpoint,
)

logger = logging.getLogger(__name__)

pytest_plugins = ["oauth_tools.fixtures"]


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


def test_build_and_deploy(
    juju: jubilant.Juju,
    charm_file: str,
    penpot_image: str,
    minio: Any,
    mailcatcher: Any,
):
    """
    arrange: set up the test Juju model.
    act: build and deploy the Penpot charm with required services.
    assert: the Penpot charm becomes active.
    """
    logger.info("deploying penpot charm (jubilant)")
    juju.deploy("postgresql-k8s", channel="14/stable", trust=True)
    juju.deploy("self-signed-certificates", channel="latest/stable", trust=True)
    juju.deploy(f"./{charm_file}", resources={"penpot-image": penpot_image}, num_units=2)
    juju.deploy("redis-k8s", channel="edge")
    juju.deploy(
        "smtp-integrator",
        config={
            "auth_type": "none",
            "domain": "example.com",
            "host": mailcatcher.host,
            "port": mailcatcher.port,
        },
    )
    juju.deploy("s3-integrator", config={"bucket": minio.bucket, "endpoint": minio.endpoint})
    juju.deploy(
        "nginx-ingress-integrator",
        channel="edge",
        config={"path-routes": "/", "service-hostname": "penpot.local"},
        trust=True,
        revision=109,
    )

    juju.wait(jubilant.all_agents_idle, timeout=300)

    juju.run(
        "s3-integrator/0",
        "sync-s3-credentials",
        {
            "access-key": minio.access_key,
            "secret-key": minio.secret_key,
        },
    )

    juju.integrate(
        "self-signed-certificates:certificates", "nginx-ingress-integrator:certificates"
    )
    juju.integrate("penpot:postgresql", "postgresql-k8s:database")
    juju.integrate("penpot:redis", "redis-k8s")
    juju.integrate("penpot:s3", "s3-integrator:s3-credentials")
    juju.integrate("penpot:smtp", "smtp-integrator:smtp")
    juju.integrate("penpot:ingress", "nginx-ingress-integrator:ingress")

    juju.wait(
        lambda status: jubilant.all_active(
            status,
            "postgresql-k8s",
            "self-signed-certificates",
            "penpot",
            "redis-k8s",
            "s3-integrator",
            "smtp-integrator",
            "nginx-ingress-integrator",
        ),
        timeout=300,
    )


def test_create_profile(juju: jubilant.Juju, ingress_address: str):
    """
    arrange: deploy the Penpot charm.
    act: create a Penpot account using the 'create-profile' charm action.
    assert: the account created can be used to log in to Penpot.
    """
    email = "test@test.com"
    unit = "penpot/0"

    @retry(stop=stop_after_attempt(60), wait=wait_fixed(5), reraise=True)
    def create_profile_with_retry() -> str:
        task = juju.run(unit, "create-profile", {"email": email, "fullname": "test"})
        password = task.results.get("password")
        if password:
            return password
        logger.info("waiting for penpot started: %s", task.results)
        raise AssertionError("profile creation not ready")

    password = create_profile_with_retry()
    logger.info("create test penpot user %s with password: %s", email, password)
    wait_for_endpoint(f"https://{ingress_address}/#/auth/login")
    session = requests.Session()

    @retry(stop=stop_after_attempt(60), wait=wait_fixed(5), reraise=True)
    def login_should_succeed() -> None:
        response = session.post(
            f"https://{ingress_address}/api/rpc/command/login-with-password",
            headers={"Host": "penpot.local"},
            json={"~:email": email, "~:password": password},
            timeout=10,
            verify=False,
        )
        if response.status_code == 200:
            return
        logger.info("penpot login status: %s", response.status_code)
        raise AssertionError(f"login status {response.status_code}")

    login_should_succeed()
    juju.run(unit, "delete-profile", {"email": email})

    @retry(stop=stop_after_attempt(60), wait=wait_fixed(5), reraise=True)
    def login_should_fail_after_delete() -> int:
        response = session.post(
            f"https://{ingress_address}/api/rpc/command/login-with-password",
            headers={"Host": "penpot.local"},
            json={"~:email": email, "~:password": password},
            timeout=10,
            verify=False,
        )
        if response.status_code == 400:
            return response.status_code
        logger.info("penpot login status: %s", response.status_code)
        raise AssertionError(f"login status {response.status_code}")

    assert login_should_fail_after_delete() == 400


async def test_oauth_login(
    juju: jubilant.Juju,
    oauth_deployment,
    page,
    ext_idp_service,
):
    """Run OAuth login flow through oauth_tools compatibility path."""
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

    @retry(stop=stop_after_attempt(5), wait=wait_fixed(60), reraise=True)
    async def run_oauth_flow() -> None:
        try:
            await page.goto("https://penpot.local/#/auth/login")
            async with page.expect_navigation():
                await page.get_by_text("OpenID").click()
            await page.wait_for_url(re.compile(r".*/ui/login.*"), timeout=60000)
            async with page.expect_navigation():
                await page.get_by_role("button", name="Dex").click()
            await ext_idp_service.complete_user_login(page)
            await expect(page).to_have_url(re.compile("^https://penpot\\.local/#/auth/register.*"))
        except (AssertionError, PlaywrightError):
            logger.exception("login failed, retry in 60 seconds")
            raise

    await run_oauth_flow()
