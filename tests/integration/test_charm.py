#!/usr/bin/env python3

# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Integration tests."""

import logging
import re
import time

import juju.action
import pytest
import requests
from oauth_tools.oauth_helpers import (
    access_application_login_page,
    click_on_sign_in_button_by_text,
    complete_auth_code_login,
    deploy_identity_bundle,
)
from playwright.async_api import expect
from pytest_operator.plugin import OpsTest

logger = logging.getLogger(__name__)

pytest_plugins = ["oauth_tools.fixtures"]


@pytest.mark.abort_on_fail
async def test_build_and_deploy(  # pylint: disable=too-many-locals
    ops_test: OpsTest, pytestconfig: pytest.Config, minio, mailcatcher, ext_idp_service
):
    """
    arrange: set up the test Juju model.
    act: build and deploy the Penpot charm with required services.
    assert: the Penpot charm becomes active.
    """
    await deploy_identity_bundle(
        ops_test=ops_test, bundle_channel="latest/edge", ext_idp_service=ext_idp_service
    )
    await ops_test.juju("refresh", "identity-platform-login-ui-operator", "--revision", "105")
    await ops_test.juju(
        "integrate",
        "identity-platform-login-ui-operator:receive-ca-cert",
        "self-signed-certificates",
    )
    charm = pytestconfig.getoption("--charm-file")
    penpot_image = pytestconfig.getoption("--penpot-image")
    assert penpot_image
    if not charm:
        charm = await ops_test.build_charm(".")
    assert ops_test.model
    logger.info("deploying penpot charm")
    num_units = 2
    penpot = await ops_test.model.deploy(
        f"./{charm}", resources={"penpot-image": penpot_image}, num_units=num_units
    )
    redis_k8s = await ops_test.model.deploy("redis-k8s", channel="edge")
    smtp_integrator = await ops_test.model.deploy(
        "smtp-integrator",
        config={
            "auth_type": "none",
            "domain": "example.com",
            "host": mailcatcher.host,
            "port": mailcatcher.port,
        },
    )
    s3_integrator = await ops_test.model.deploy(
        "s3-integrator", config={"bucket": minio.bucket, "endpoint": minio.endpoint}
    )
    nginx_ingress_integrator = await ops_test.model.deploy(
        "nginx-ingress-integrator",
        channel="edge",
        config={"path-routes": "/", "service-hostname": "penpot.local"},
        trust=True,
        revision=109,
    )
    await ops_test.model.wait_for_idle(
        timeout=900, apps=[s3_integrator.name, "self-signed-certificates"]
    )
    action = await s3_integrator.units[0].run_action(
        "sync-s3-credentials",
        **{
            "access-key": minio.access_key,
            "secret-key": minio.secret_key,
        },
    )
    await action.wait()
    await ops_test.model.add_relation("self-signed-certificates", "nginx-ingress-integrator")
    await ops_test.model.add_relation(penpot.name, "postgresql-k8s")
    await ops_test.model.add_relation(penpot.name, redis_k8s.name)
    await ops_test.model.add_relation(penpot.name, s3_integrator.name)
    await ops_test.model.add_relation(penpot.name, f"{smtp_integrator.name}:smtp")
    await ops_test.model.add_relation(penpot.name, nginx_ingress_integrator.name)
    await ops_test.model.wait_for_idle(timeout=900, status="active", raise_on_error=False)
    logger.info(
        "test user account: (%s, %s)", ext_idp_service.user_email, ext_idp_service.user_password
    )
    action = (
        await ops_test.model.applications["self-signed-certificates"]
        .units[0]
        .run_action("get-ca-certificate")
    )
    await action.wait()
    ca_cert: str = action.results["ca-certificate"]
    for unit in range(num_units):
        logger.info("copying oauth ca cert into penpot/%s", unit)
        await ops_test.juju(
            "ssh",
            "--container",
            "penpot",
            f"penpot/{unit}",
            "cp",
            "/dev/stdin",
            "/oauth.crt",
            stdin=ca_cert.encode("ascii"),
        )
        logger.info("installing oauth ca cert into penpot/%s java trust", unit)
        await ops_test.juju(
            "ssh",
            "--container",
            "penpot",
            f"penpot/{unit}",
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
        )
        logger.info("restart penpot backend in penpot/%s", unit)
        await ops_test.juju(
            "ssh",
            "--container",
            "penpot",
            f"penpot/{unit}",
            "pebble",
            "restart",
            "backend",
        )


async def test_create_profile(ops_test: OpsTest, ingress_address):
    """
    arrange: deploy the Penpot charm.
    act: create a Penpot account using the 'create-profile' charm action.
    assert: the account created can be used to log in to Penpot.
    """
    email = "test@test.com"
    assert ops_test.model
    unit = ops_test.model.applications["penpot"].units[0]
    deadline = time.time() + 300
    while time.time() < deadline:
        action: juju.action.Action = await unit.run_action(
            "create-profile", email=email, fullname="test"
        )
        await action.wait()
        if "password" in action.results:
            password = action.results["password"]
            break
        logger.info("waiting for penpot started: %s", action.results)
        time.sleep(5)
    else:
        raise TimeoutError("timed out waiting for profile creation success")
    logger.info("create test penpot user %s with password: %s", email, password)
    session = requests.Session()
    deadline = time.time() + 300
    while time.time() < deadline:
        response = session.post(
            f"http://{ingress_address}/api/rpc/command/login-with-password",
            headers={"Host": "penpot.local"},
            json={"~:email": email, "~:password": password},
            timeout=10,
        )
        if response.status_code == 200:
            break
        logger.info("penpot login status: %s", response.status_code)
        time.sleep(5)
    else:
        raise TimeoutError("timed out waiting for login success")
    action = await unit.run_action("delete-profile", email=email)
    await action.wait()
    deadline = time.time() + 300
    while time.time() < deadline:
        response = session.post(
            f"http://{ingress_address}/api/rpc/command/login-with-password",
            headers={"Host": "penpot.local"},
            json={"~:email": email, "~:password": password},
            timeout=10,
        )
        if response.status_code == 400:
            break
        logger.info("penpot login status: %s", response.status_code)
        time.sleep(5)
    else:
        raise TimeoutError("timed out waiting for login response")
    assert response.status_code == 400


async def test_oauth(ops_test, page, ext_idp_service):
    """
    arrange: integrate the penpot charm with an oauth provider.
    act: login penpot using openid connect.
    assert: login success.
    """
    await ops_test.model.add_relation("penpot:oauth", "hydra")
    await ops_test.model.wait_for_idle(timeout=900, status="active")
    await access_application_login_page(page=page, url="https://penpot.local/#/auth/login")
    await click_on_sign_in_button_by_text(page=page, text="OpenID")
    await complete_auth_code_login(page=page, ops_test=ops_test, ext_idp_service=ext_idp_service)
    await expect(page).to_have_url(re.compile("^https://penpot\\.local/#/auth/register.*"))
