#!/usr/bin/env python3

# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""OAuth integration test using the current oauth_tools compatibility path."""

import asyncio
import collections
import logging
import pathlib
import re
import time

import boto3
import botocore.client
import kubernetes
import pytest
import pytest_asyncio
from lightkube.resources.core_v1 import Node, Service
from oauth_tools.external_idp import DexIdpService
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import expect

from tests.integration.helpers import (
    OpsJubilantFacade,
    build_ops_model_facade,
    get_required_charm_inputs,
    wait_for_endpoint,
)

logger = logging.getLogger(__name__)

pytest_plugins = ["oauth_tools.fixtures"]


class StableDexIdpService(DexIdpService):
    """Dex manager that tolerates delayed load balancer IP assignment."""

    @property
    def issuer_url(self) -> str:
        for _ in range(40):
            service = self._client.get(Service, "dex", namespace=self.namespace)
            load_balancer = getattr(getattr(service, "status", None), "loadBalancer", None)
            ingress = getattr(load_balancer, "ingress", None)
            if ingress and ingress[0] and getattr(ingress[0], "ip", None):
                return f"http://{ingress[0].ip}:5556/"

            ports = getattr(getattr(service, "spec", None), "ports", None) or []
            node_port = None
            for port in ports:
                if getattr(port, "port", None) == 5556 and getattr(port, "nodePort", None):
                    node_port = port.nodePort
                    break
            if node_port:
                for node in self._client.list(Node):
                    addresses = getattr(getattr(node, "status", None), "addresses", None) or []
                    internal_ip = next(
                        (addr.address for addr in addresses if getattr(addr, "type", None) == "InternalIP"),
                        None,
                    )
                    if internal_ip:
                        return f"http://{internal_ip}:{node_port}/"

            time.sleep(3)
        raise RuntimeError("Dex load balancer ingress IP is not available")


@pytest.fixture(scope="module")
def ext_idp_service(ops_model: OpsJubilantFacade, client):
    """Deploy and manage Dex with resilient issuer URL resolution."""
    logger.info("Deploying dex resources")
    ext_idp_manager = StableDexIdpService(client=client)
    try:
        yield ext_idp_manager
    finally:
        if ops_model.keep_model:
            return
        logger.info("Deleting dex resources")
        ext_idp_manager.remove_idp_service()


@pytest.fixture(scope="module")
def ops_model(ops_test) -> OpsJubilantFacade:
    """Provide a facade to model operations during migration."""
    return build_ops_model_facade(ops_test=ops_test)


@pytest_asyncio.fixture(name="get_unit_ips", scope="module")
async def get_unit_ips_fixture(ops_model: OpsJubilantFacade):
    """A function to get unit ips of a charm application."""

    async def _get_unit_ips(name: str):
        return ops_model.get_unit_ips(name)

    return _get_unit_ips


@pytest.fixture(scope="module", name="load_kube_config")
def load_kube_config_fixture(pytestconfig: pytest.Config):
    """Load kubernetes config file."""
    kube_config = pytestconfig.getoption("--kube-config")
    kubernetes.config.load_kube_config(config_file=kube_config)


@pytest_asyncio.fixture(name="minio", scope="module")
async def minio_fixture(get_unit_ips, load_kube_config, ops_model: OpsJubilantFacade):
    """Deploy test minio service."""
    key = "minioadmin"
    minio = await ops_model.deploy_application(
        "minio", channel="ckf-1.9/stable", config={"access-key": key, "secret-key": key}
    )
    await ops_model.wait_for_idle(apps=[minio.name], status="active", timeout=300)
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
        endpoint=f"http://minio-endpoints.{ops_model.model_name}.svc.cluster.local:9000",
        bucket=bucket,
        access_key=key,
        secret_key=key,
    )


@pytest.fixture(scope="module")
def mailcatcher(load_kube_config, ops_model: OpsJubilantFacade):
    """Deploy test mailcatcher service."""
    namespace = ops_model.model_name
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


async def inject_root_certs(ops_model: OpsJubilantFacade, penpot_units: list[str], ca_cert: str):
    """Inject CA certificate to penpot Java certificate store."""
    for unit_name in penpot_units:
        logger.info("copying oauth ca cert into %s", unit_name)
        await ops_model.run_unit_ssh(
            unit_name,
            "cp",
            "/dev/stdin",
            "/oauth.crt",
            stdin=ca_cert.encode("ascii"),
        )
        code, stdout, _ = await ops_model.run_unit_ssh(
            unit_name,
            "cat",
            "/oauth.crt",
        )
        assert code == 0
        logger.info("copying oauth ca cert into %s result: %s", unit_name, stdout)
        logger.info("installing oauth ca cert into penpot/%s java trust", unit_name)
        code, stdout, stderr = await ops_model.run_unit_ssh(
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
        )
        assert code == 0
        logger.info("keytool import result: %s, %s, %s", code, stdout, stderr)
        logger.info("restart penpot backend in penpot/%s", unit_name)
        code, _, _ = await ops_model.run_unit_ssh(
            unit_name,
            "pebble",
            "restart",
            "backend",
        )
        assert code == 0


@pytest_asyncio.fixture(scope="module")
async def oauth_deployment(
    ops_model: OpsJubilantFacade,
    pytestconfig: pytest.Config,
    minio,
    mailcatcher,
    ext_idp_service,
):
    """Deploy identity bundle and penpot through oauth_tools compatibility path."""
    charm, penpot_image = get_required_charm_inputs(pytestconfig)

    await ops_model.deploy_identity_bundle(
        bundle_url=str(pathlib.Path(__file__).parent.joinpath("bundle.yaml").absolute()),
        ext_idp_service=ext_idp_service,
    )
    await ops_model.bootstrap_identity_login_ui()
    (
        _penpot,
        redis_k8s,
        smtp_integrator,
        s3_integrator,
        nginx_ingress_integrator,
    ) = await asyncio.gather(
        ops_model.deploy_application(
            f"./{charm}", resources={"penpot-image": penpot_image}, application_name="penpot", num_units=2
        ),
        ops_model.deploy_application("redis-k8s", channel="edge"),
        ops_model.deploy_application(
            "smtp-integrator",
            config={
                "auth_type": "none",
                "domain": "example.com",
                "host": mailcatcher.host,
                "port": mailcatcher.port,
            },
        ),
        ops_model.deploy_application(
            "s3-integrator", config={"bucket": minio.bucket, "endpoint": minio.endpoint}
        ),
        ops_model.deploy_application(
            "nginx-ingress-integrator",
            channel="edge",
            config={"path-routes": "/", "service-hostname": "penpot.local"},
            trust=True,
            revision=109,
        ),
    )
    await ops_model.wait_for_idle(timeout=300, apps=[s3_integrator.name, "self-signed-certificates"])
    ops_model.sync_s3_credentials(
        s3_integrator.name,
        access_key=minio.access_key,
        secret_key=minio.secret_key,
    )
    ops_model.integrate_endpoints("penpot:postgresql", "postgresql-k8s:database")
    ops_model.integrate_endpoints("penpot:redis", redis_k8s.name)
    ops_model.integrate_endpoints("penpot:s3", f"{s3_integrator.name}:s3-credentials")
    ops_model.integrate_endpoints("penpot:smtp", f"{smtp_integrator.name}:smtp")
    ops_model.integrate_endpoints(
        "self-signed-certificates:certificates",
        f"{nginx_ingress_integrator.name}:certificates",
    )
    ops_model.integrate_endpoints("penpot:ingress", f"{nginx_ingress_integrator.name}:ingress")
    await ops_model.wait_for_idle(timeout=300, status="active", raise_on_error=False)


async def test_oauth_login(
    ops_model: OpsJubilantFacade,
    oauth_deployment,
    page,
    ext_idp_service,
):
    """Run OAuth login flow through oauth_tools compatibility path."""
    ca_cert = ops_model.get_ca_certificate()
    penpot_units = ops_model.get_unit_names("penpot")
    await inject_root_certs(ops_model, penpot_units, ca_cert)
    await ops_model.add_relation("penpot:oauth", "hydra")
    ops_model.wait_all_active("penpot", timeout=300)
    wait_for_endpoint("https://penpot.local/#/auth/login", timeout=300)
    for _ in range(5):
        try:
            await ops_model.access_application_login_page(page=page, url="https://penpot.local/#/auth/login")
            await ops_model.click_on_sign_in_button_by_text(page=page, text="OpenID")
            await ops_model.complete_auth_code_login(
                page=page,
                ext_idp_service=ext_idp_service,
            )
            await expect(page).to_have_url(re.compile("^https://penpot\\.local/#/auth/register.*"))
            return
        except (AssertionError, PlaywrightError):
            logger.exception("login failed, retry in 60 seconds")
            time.sleep(60)
    raise AssertionError("oauth login failed after retries")
