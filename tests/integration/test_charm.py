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

logger = logging.getLogger(__name__)


def test_build_and_deploy(
    juju: jubilant.Juju, pytestconfig: pytest.Config, minio: Any, mailcatcher: Any
):
    """
    arrange: set up the test Juju model.
    act: build and deploy the Penpot charm with required services.
    assert: the Penpot charm becomes active.
    """
    charm = pytestconfig.getoption("--charm-file")
    penpot_image = pytestconfig.getoption("--penpot-image")
    assert charm, (
        "--charm-file is required; run 'charmcraft pack' first and pass the resulting .charm file"
    )
    assert penpot_image
    assert not penpot_image.startswith("penpotapp/backend:"), (
        "--penpot-image must use the charm-compatible Penpot rock image, not penpotapp/backend"
    )

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

    juju.wait(
        lambda status: jubilant.all_active(status, "s3-integrator", "self-signed-certificates"),
        timeout=900,
    )

    juju.integrate("self-signed-certificates:certificates", "nginx-ingress-integrator:certificates")
    juju.wait(jubilant.all_agents_idle, timeout=300)
    juju.integrate("penpot:postgresql", "postgresql-k8s:database")
    juju.wait(jubilant.all_agents_idle, timeout=300)
    juju.integrate("penpot:redis", "redis-k8s")
    juju.wait(jubilant.all_agents_idle, timeout=300)
    juju.integrate("penpot:s3", "s3-integrator:s3-credentials")
    juju.wait(jubilant.all_agents_idle, timeout=300)
    juju.integrate("penpot:smtp", "smtp-integrator:smtp")
    juju.wait(jubilant.all_agents_idle, timeout=300)
    juju.integrate("penpot:ingress", "nginx-ingress-integrator:ingress")
    juju.wait(jubilant.all_agents_idle, timeout=300)

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
        timeout=900,
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
