#!/usr/bin/env python3

# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""OAuth integration test kept on OpsTest until oauth_tools supports Jubilant."""

import collections
import json
import logging
import pathlib
import re
import time

import boto3
import botocore.client
import juju.action
import kubernetes
import pytest
import pytest_asyncio
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


@pytest_asyncio.fixture(name="get_unit_ips", scope="module")
async def get_unit_ips_fixture(ops_test: OpsTest):
    """A function to get unit ips of a charm application."""

    async def _get_unit_ips(name: str):
        _, status, _ = await ops_test.juju("status", "--format", "json")
        status = json.loads(status)
        units = status["applications"][name]["units"]
        ip_list = []
        for key in sorted(units.keys(), key=lambda n: int(n.split("/")[-1])):
            ip_list.append(units[key]["address"])
        return ip_list

    return _get_unit_ips


@pytest.fixture(scope="module", name="load_kube_config")
def load_kube_config_fixture(pytestconfig: pytest.Config):
    """Load kubernetes config file."""
    kube_config = pytestconfig.getoption("--kube-config")
    kubernetes.config.load_kube_config(config_file=kube_config)


@pytest_asyncio.fixture(name="minio", scope="module")
async def minio_fixture(get_unit_ips, load_kube_config, ops_test: OpsTest):
    """Deploy test minio service."""
    key = "minioadmin"
    assert ops_test.model
    minio = await ops_test.model.deploy(
        "minio", channel="ckf-1.9/stable", config={"access-key": key, "secret-key": key}
    )
    await ops_test.model.wait_for_idle(apps=[minio.name], status="active", timeout=300)
    ip = (await get_unit_ips(minio.name))[0]
    s3 = boto3.client(
        "s3",
        endpoint_url=f"http://{ip}:9000",
        aws_access_key_id=key,
        aws_secret_access_key=key,
        config=botocore.client.Config(signature_version="s3v4"),
    )
    bucket = "penpot"
    s3.create_bucket(Bucket=bucket)
    S3Credential = collections.namedtuple("S3Credential", "endpoint bucket access_key secret_key")
    return S3Credential(
        endpoint=f"http://minio-endpoints.{ops_test.model.name}.svc.cluster.local:9000",
        bucket=bucket,
        access_key=key,
        secret_key=key,
    )


@pytest.fixture(scope="module")
def mailcatcher(load_kube_config, ops_test: OpsTest):
    """Deploy test mailcatcher service."""
    assert ops_test.model
    namespace = ops_test.model.name
    v1 = kubernetes.client.CoreV1Api()
    pod = kubernetes.client.V1Pod(
        api_version="v1",
        kind="Pod",
        metadata=kubernetes.client.V1ObjectMeta(
            name="mailcatcher",
            namespace=namespace,
            labels={"app.kubernetes.io/name": "mailcatcher"},
        ),
        spec=kubernetes.client.V1PodSpec(
            containers=[
                kubernetes.client.V1Container(
                    name="mailcatcher",
                    image="sj26/mailcatcher",
                    ports=[
                        kubernetes.client.V1ContainerPort(container_port=1025),
                        kubernetes.client.V1ContainerPort(container_port=1080),
                    ],
                )
            ],
        ),
    )
    v1.create_namespaced_pod(namespace=namespace, body=pod)
    service = kubernetes.client.V1Service(
        api_version="v1",
        kind="Service",
        metadata=kubernetes.client.V1ObjectMeta(name="mailcatcher-service", namespace=namespace),
        spec=kubernetes.client.V1ServiceSpec(
            type="ClusterIP",
            ports=[
                kubernetes.client.V1ServicePort(port=1025, target_port=1025, name="tcp-1025"),
                kubernetes.client.V1ServicePort(port=1080, target_port=1080, name="tcp-1080"),
            ],
            selector={"app.kubernetes.io/name": "mailcatcher"},
        ),
    )
    v1.create_namespaced_service(namespace=namespace, body=service)
    deadline = time.time() + 300
    while True:
        if time.time() > deadline:
            raise TimeoutError("timeout while waiting for mailcatcher pod")
        try:
            pod = v1.read_namespaced_pod(name="mailcatcher", namespace=namespace)
            if pod.status.phase == "Running":
                logger.info("mailcatcher running at %s", pod.status.pod_ip)
                break
        except kubernetes.client.ApiException:
            pass
        logger.info("waiting for mailcatcher pod")
        time.sleep(1)
    SmtpCredential = collections.namedtuple("SmtpCredential", "host port")
    return SmtpCredential(
        host=f"mailcatcher-service.{namespace}.svc.cluster.local",
        port=1025,
    )


async def inject_root_certs(ops_test, penpot, ca_cert):
    """Inject CA certificate to penpot Java certificate store."""
    for unit in penpot.units:
        logger.info("copying oauth ca cert into %s", unit.name)
        await ops_test.juju(
            "ssh",
            "--container",
            "penpot",
            unit.name,
            "cp",
            "/dev/stdin",
            "/oauth.crt",
            stdin=ca_cert.encode("ascii"),
        )
        code, stdout, _ = await ops_test.juju(
            "ssh",
            "--container",
            "penpot",
            unit.name,
            "cat",
            "/oauth.crt",
        )
        assert code == 0
        logger.info("copying oauth ca cert into %s result: %s", unit.name, stdout)
        logger.info("installing oauth ca cert into penpot/%s java trust", unit.name)
        code, stdout, stderr = await ops_test.juju(
            "ssh",
            "--container",
            "penpot",
            unit.name,
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
        assert code == 0
        logger.info("keytool import result: %s, %s, %s", code, stdout, stderr)
        logger.info("restart penpot backend in penpot/%s", unit.name)
        code, _, _ = await ops_test.juju(
            "ssh",
            "--container",
            "penpot",
            unit.name,
            "pebble",
            "restart",
            "backend",
        )
        assert code == 0


@pytest_asyncio.fixture(scope="module")
async def oauth_deployment(
    ops_test: OpsTest,
    pytestconfig: pytest.Config,
    minio,
    mailcatcher,
    ext_idp_service,
):
    """Deploy identity bundle and penpot through oauth_tools OpsTest path."""
    assert ops_test.model
    charm = pytestconfig.getoption("--charm-file")
    penpot_image = pytestconfig.getoption("--penpot-image")
    assert charm, (
        "--charm-file is required; run 'charmcraft pack' first and pass the resulting .charm file"
    )
    assert penpot_image
    assert not penpot_image.startswith("penpotapp/backend:"), (
        "--penpot-image must use the charm-compatible Penpot rock image, not penpotapp/backend"
    )

    await deploy_identity_bundle(
        ops_test=ops_test,
        bundle_url=str(pathlib.Path(__file__).parent.joinpath("bundle.yaml").absolute()),
        ext_idp_service=ext_idp_service,
    )
    await ops_test.juju("refresh", "identity-platform-login-ui-operator", "--revision", "105")
    await ops_test.juju(
        "integrate",
        "identity-platform-login-ui-operator:receive-ca-cert",
        "self-signed-certificates",
    )
    penpot = await ops_test.model.deploy(
        f"./{charm}", resources={"penpot-image": penpot_image}, application_name="penpot", num_units=2
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
    action: juju.action.Action = await s3_integrator.units[0].run_action(
        "sync-s3-credentials",
        **{
            "access-key": minio.access_key,
            "secret-key": minio.secret_key,
        },
    )
    await action.wait()
    await ops_test.model.add_relation("self-signed-certificates", nginx_ingress_integrator.name)
    await ops_test.model.add_relation(penpot.name, "postgresql-k8s")
    await ops_test.model.add_relation(penpot.name, redis_k8s.name)
    await ops_test.model.add_relation(penpot.name, s3_integrator.name)
    await ops_test.model.add_relation(penpot.name, f"{smtp_integrator.name}:smtp")
    await ops_test.model.add_relation(penpot.name, nginx_ingress_integrator.name)
    await ops_test.model.wait_for_idle(timeout=900, status="active", raise_on_error=False)


async def test_oauth_login(
    ops_test: OpsTest,
    oauth_deployment,
    page,
    ext_idp_service,
):
    """Run OAuth login flow through oauth_tools using the OpsTest path."""
    assert ops_test.model
    action = (
        await ops_test.model.applications["self-signed-certificates"]
        .units[0]
        .run_action("get-ca-certificate")
    )
    await action.wait()
    ca_cert: str = action.results["ca-certificate"]
    penpot = ops_test.model.applications["penpot"]
    await inject_root_certs(ops_test, penpot, ca_cert)
    await ops_test.model.add_relation("penpot:oauth", "hydra")
    await ops_test.model.wait_for_idle(timeout=900, status="active", raise_on_error=False)
    for _ in range(5):
        try:
            await access_application_login_page(page=page, url="https://penpot.local/#/auth/login")
            await click_on_sign_in_button_by_text(page=page, text="OpenID")
            await complete_auth_code_login(
                page=page,
                ops_test=ops_test,
                ext_idp_service=ext_idp_service,
            )
            await expect(page).to_have_url(re.compile("^https://penpot\\.local/#/auth/register.*"))
            return
        except AssertionError:
            logger.exception("login failed, retry in 60 seconds")
            time.sleep(60)
    raise AssertionError("oauth login failed after retries")
