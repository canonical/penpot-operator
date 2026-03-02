#!/usr/bin/env python3

# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Integration tests."""

import logging
import time
from typing import Any

import jubilant
import pytest
import requests

from tests.integration.helpers import get_required_charm_inputs, wait_for_endpoint

logger = logging.getLogger(__name__)


def test_build_and_deploy(
    juju: jubilant.Juju, pytestconfig: pytest.Config, minio: Any, mailcatcher: Any
):
    """
    arrange: set up the test Juju model.
    act: build and deploy the Penpot charm with required services.
    assert: the Penpot charm becomes active.
    """
    charm, penpot_image = get_required_charm_inputs(pytestconfig)

    logger.info("deploying penpot charm (jubilant)")
    juju.deploy("postgresql-k8s", channel="14/stable", trust=True)
    juju.deploy("self-signed-certificates", channel="latest/stable", trust=True)
    juju.deploy(f"./{charm}", resources={"penpot-image": penpot_image}, num_units=2)
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

    juju.wait(jubilant.all_agents_idle, timeout=900)

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
    deadline = time.time() + 300
    while time.time() < deadline:
        task = juju.run(unit, "create-profile", {"email": email, "fullname": "test"})
        if "password" in task.results:
            password = task.results["password"]
            break
        logger.info("waiting for penpot started: %s", task.results)
        time.sleep(5)
    else:
        raise TimeoutError("timed out waiting for profile creation success")
    logger.info("create test penpot user %s with password: %s", email, password)
    wait_for_endpoint(f"https://{ingress_address}/#/auth/login")
    session = requests.Session()
    deadline = time.time() + 300
    while time.time() < deadline:
        response = session.post(
            f"https://{ingress_address}/api/rpc/command/login-with-password",
            headers={"Host": "penpot.local"},
            json={"~:email": email, "~:password": password},
            timeout=10,
            verify=False,
        )
        if response.status_code == 200:
            break
        logger.info("penpot login status: %s", response.status_code)
        time.sleep(5)
    else:
        raise TimeoutError("timed out waiting for login success")
    juju.run(unit, "delete-profile", {"email": email})
    deadline = time.time() + 300
    while time.time() < deadline:
        response = session.post(
            f"https://{ingress_address}/api/rpc/command/login-with-password",
            headers={"Host": "penpot.local"},
            json={"~:email": email, "~:password": password},
            timeout=10,
            verify=False,
        )
        if response.status_code == 400:
            break
        logger.info("penpot login status: %s", response.status_code)
        time.sleep(5)
    else:
        raise TimeoutError("timed out waiting for login response")
    assert response.status_code == 400
