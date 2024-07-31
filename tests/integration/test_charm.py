#!/usr/bin/env python3

# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Integration tests."""

import logging
import time

import juju.action
import pytest
import requests
from pytest_operator.plugin import OpsTest

logger = logging.getLogger(__name__)


@pytest.mark.abort_on_fail
async def test_build_and_deploy(
    ops_test: OpsTest, pytestconfig: pytest.Config, minio, mailcatcher
):
    """
    arrange: set up the test Juju model.
    act: build and deploy the Penpot charm with required services.
    assert: the Penpot charm becomes active.
    """
    charm = pytestconfig.getoption("--charm-file")
    penpot_image = pytestconfig.getoption("--penpot-image")
    assert penpot_image
    if not charm:
        charm = await ops_test.build_charm(".")
    assert ops_test.model
    penpot = await ops_test.model.deploy(
        f"./{charm}", resources={"penpot-image": penpot_image}, num_units=2
    )
    postgresql_k8s = await ops_test.model.deploy("postgresql-k8s", channel="14/stable", trust=True)
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
    await ops_test.model.wait_for_idle(timeout=900)
    action = await s3_integrator.units[0].run_action(
        "sync-s3-credentials",
        **{
            "access-key": minio.access_key,
            "secret-key": minio.secret_key,
        },
    )
    await action.wait()
    await ops_test.model.add_relation(penpot.name, postgresql_k8s.name)
    await ops_test.model.add_relation(penpot.name, redis_k8s.name)
    await ops_test.model.add_relation(penpot.name, s3_integrator.name)
    await ops_test.model.add_relation(penpot.name, f"{smtp_integrator.name}:smtp")
    await ops_test.model.add_relation(penpot.name, nginx_ingress_integrator.name)
    await ops_test.model.wait_for_idle(timeout=900, status="active")


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
