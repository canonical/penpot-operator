# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Integration test fixtures."""

# pylint: disable=unused-argument

import collections
import json
import logging
import os
import re
import time
import urllib.parse

import boto3
import botocore.client
import jubilant
import kubernetes
import pytest
from lightkube import Client, KubeConfig
from lightkube.resources.core_v1 import Node, Service
from oauth_tools.external_idp import DexIdpService
from playwright.sync_api import Page, expect

logger = logging.getLogger(__name__)


def pytest_configure(config: pytest.Config):
    """Configure integration test environment."""
    kube_config = config.getoption("kube_config")
    if kube_config and not os.environ.get("TESTING_KUBECONFIG"):
        os.environ["TESTING_KUBECONFIG"] = kube_config


@pytest.fixture(scope="session")
def browser_context_args(browser_context_args: dict) -> dict:
    """Configure Playwright sync browser context for self-signed TLS."""
    return {**browser_context_args, "ignore_https_errors": True}


@pytest.fixture(name="charm_file", scope="module")
def charm_file_fixture(pytestconfig: pytest.Config) -> str:
    """Return the required charm file path for integration tests."""
    charm = pytestconfig.getoption("charm_file")
    assert charm, "--charm-file is required"
    return charm


@pytest.fixture(name="penpot_image", scope="module")
def penpot_image_fixture(pytestconfig: pytest.Config) -> str:
    """Return the required penpot image for integration tests."""
    image = pytestconfig.getoption("penpot_image")
    assert image, "--penpot-image is required"
    return image


@pytest.fixture(name="ingress_host", scope="module")
def ingress_host_fixture(pytestconfig: pytest.Config) -> str:
    """Return the ingress host used for Host header routing."""
    return pytestconfig.getoption("--ingress-address") or "penpot.local"


@pytest.fixture(name="keep_models", scope="module")
def keep_models_fixture(pytestconfig: pytest.Config) -> bool:
    """Return whether integration model retention is enabled."""
    return bool(pytestconfig.getoption("keep_models"))


@pytest.fixture(scope="module", name="load_kube_config")
def load_kube_config_fixture(pytestconfig: pytest.Config):
    """Load kubernetes config file."""
    kube_config = pytestconfig.getoption("kube_config")
    kubernetes.config.load_kube_config(config_file=kube_config)


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
                        (
                            addr.address
                            for addr in addresses
                            if getattr(addr, "type", None) == "InternalIP"
                        ),
                        None,
                    )
                    if internal_ip:
                        return f"http://{internal_ip}:{node_port}/"

            time.sleep(3)
        raise RuntimeError("Dex load balancer ingress IP is not available")

    def complete_user_login(self, page: Page) -> None:
        """Get a page on the IDP login page and login the user."""
        logger.info("Signing in to dex")
        expect(page).to_have_url(re.compile(rf"{self.issuer_url}*"))
        page.get_by_placeholder("email address").click()
        page.get_by_placeholder("email address").fill(self.user_email)
        page.get_by_placeholder("password").click()
        page.get_by_placeholder("password").fill(self.user_password)
        page.get_by_role("button", name="Login").click()


@pytest.fixture(name="juju", scope="module")
def juju_fixture(pytestconfig: pytest.Config):
    """Provide a Jubilant Juju client with a temporary model."""
    keep_models = pytestconfig.getoption("--keep-models")
    with jubilant.temp_model(keep=keep_models) as juju_model:
        yield juju_model


@pytest.fixture(name="get_unit_ips", scope="module")
def get_unit_ips_fixture(juju: jubilant.Juju):
    """A function to get unit ips of a charm application."""

    def _get_unit_ips(name: str):
        """A function to get unit ips of a charm application.

        Args:
            name: The name of the charm application.

        Returns:
            A list of unit ips.
        """
        status = juju.status()
        units = status.apps[name].units
        ip_list = []
        for key in sorted(units.keys(), key=lambda n: int(n.split("/")[-1])):
            ip_list.append(units[key].address)
        return ip_list

    return _get_unit_ips


S3Credential = collections.namedtuple("S3Credential", "endpoint bucket access_key secret_key")


@pytest.fixture(name="minio", scope="module")
def minio_fixture(get_unit_ips, load_kube_config, juju: jubilant.Juju) -> S3Credential:
    """Deploy test minio service."""
    key = "minioadmin"
    juju.deploy("minio", channel="ckf-1.9/stable", config={"access-key": key, "secret-key": key})
    juju.wait(lambda status: jubilant.all_active(status, "minio"), timeout=300)
    ip = get_unit_ips("minio")[0]
    s3 = boto3.client(
        "s3",
        endpoint_url=f"http://{ip}:9000",
        aws_access_key_id=key,
        aws_secret_access_key=key,
        config=botocore.client.Config(signature_version="s3v4"),
    )
    bucket = "penpot"
    s3.create_bucket(Bucket=bucket)
    return S3Credential(
        endpoint=f"http://minio-endpoints.{juju.model}.svc.cluster.local:9000",
        bucket=bucket,
        access_key=key,
        secret_key=key,
    )


SmtpCredential = collections.namedtuple("SmtpCredential", "host port")


@pytest.fixture(name="mailcatcher", scope="module")
def mailcatcher_fixture(load_kube_config, juju: jubilant.Juju) -> SmtpCredential:
    """Deploy test mailcatcher service."""
    namespace = juju.model
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
    return SmtpCredential(
        host=f"mailcatcher-service.{namespace}.svc.cluster.local",
        port=1025,
    )


@pytest.fixture(name="public_url", scope="module")
def public_url_fixture(pytestconfig: pytest.Config, juju: jubilant.Juju) -> str:
    """Get the Penpot public URL.

    Use Traefik app address for transport and ingress databag URL path for routing.
    """
    deadline = time.time() + 300
    while time.time() < deadline:
        status = juju.status()
        traefik_app = status.apps.get("traefik-k8s")
        traefik_addr = ""
        if traefik_app:
            units = getattr(traefik_app, "units", {}) or {}
            if units and "traefik-k8s/0" in units:
                traefik_addr = str(getattr(units["traefik-k8s/0"], "address", "") or "")
            if not traefik_addr:
                traefik_addr = str(getattr(traefik_app, "address", "") or "")
        if not traefik_addr:
            time.sleep(5)
            continue

        stdout = juju.cli("show-unit", "penpot/0", "--format", "json")
        unit_data = json.loads(stdout).get("penpot/0", {})
        relation_info = unit_data.get("relation-info", [])
        for relation in relation_info:
            if relation.get("endpoint") != "ingress":
                continue
            ingress_raw = relation.get("application-data", {}).get("ingress")
            if not ingress_raw:
                continue
            ingress_data = json.loads(ingress_raw)
            ingress_url = ingress_data.get("url")
            if not ingress_url:
                continue
            parsed = urllib.parse.urlparse(str(ingress_url))
            path = parsed.path or ""
            return f"{parsed.scheme}://{traefik_addr}{path}".rstrip("/")

        time.sleep(5)

    raise TimeoutError("timed out waiting for ingress URL in relation databag")


@pytest.fixture(name="ext_idp_service", scope="module")
def ext_idp_service_fixture(keep_models: bool):
    """Deploy and manage Dex with resilient issuer URL resolution."""
    logger.info("Deploying dex resources")
    kubeconfig = os.environ.get("TESTING_KUBECONFIG", "~/.kube/config")
    client = Client(config=KubeConfig.from_file(kubeconfig), field_manager="dex-test")
    ext_idp_manager = StableDexIdpService(client=client)
    try:
        yield ext_idp_manager
    finally:
        if not keep_models:
            logger.info("Deleting dex resources")
            ext_idp_manager.remove_idp_service()


@pytest.fixture(name="deployment", scope="module")
def deployment_fixture(
    juju: jubilant.Juju,
    charm_file: str,
    penpot_image: str,
    minio: S3Credential,
    mailcatcher: SmtpCredential,
) -> list[str]:
    """Deploy base Penpot stack used by integration tests.

    Returns:
        A list of deployed application names.
    """
    juju.deploy("postgresql-k8s", channel="14/stable", trust=True)
    juju.deploy("self-signed-certificates", channel="latest/stable", trust=True)
    juju.deploy(
        f"./{charm_file}",
        app="penpot",
        resources={"penpot-image": penpot_image},
        num_units=2,
    )
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
    juju.deploy(
        "s3-integrator",
        config={"bucket": minio.bucket, "endpoint": minio.endpoint},
    )
    juju.deploy(
        "traefik-k8s",
        channel="latest/stable",
        config={"external_hostname": "penpot.local"},
        trust=True,
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

    juju.integrate("self-signed-certificates:certificates", "traefik-k8s:certificates")
    juju.integrate("penpot:postgresql", "postgresql-k8s:database")
    juju.integrate("penpot:redis", "redis-k8s")
    juju.integrate("penpot:s3", "s3-integrator:s3-credentials")
    juju.integrate("penpot:smtp", "smtp-integrator:smtp")
    juju.integrate("penpot:ingress", "traefik-k8s:ingress")

    deployed_apps = [
        "postgresql-k8s",
        "self-signed-certificates",
        "penpot",
        "redis-k8s",
        "s3-integrator",
        "smtp-integrator",
        "traefik-k8s",
    ]

    return deployed_apps


@pytest.fixture(name="oauth_deployment", scope="module")
def oauth_deployment_fixture(
    juju: jubilant.Juju,
    deployment: list[str],
    ext_idp_service: StableDexIdpService,
):
    """Deploy OAuth identity stack and relations on top of base deployment."""
    juju.deploy(
        "hydra",
        channel="edge",
        revision=339,
        trust=True,
        resources={"oci-image": "ghcr.io/canonical/hydra:2.3.0-canonical"},
    )
    juju.deploy(
        "kratos",
        channel="edge",
        revision=500,
        trust=True,
        resources={"oci-image": "ghcr.io/canonical/kratos:1.3.1"},
    )
    juju.deploy("kratos-external-idp-integrator", channel="edge", revision=245)
    juju.deploy(
        "identity-platform-login-ui-operator",
        channel="edge",
        revision=146,
        trust=True,
        resources={"oci-image": "ghcr.io/canonical/identity-platform-login-ui:v0.21.2"},
    )
    juju.deploy(
        "traefik-k8s",
        app="traefik-admin",
        channel="latest/stable",
        revision=176,
        trust=True,
    )
    juju.deploy(
        "traefik-k8s",
        app="traefik-public",
        channel="latest/stable",
        revision=176,
        trust=True,
    )

    juju.integrate("hydra:pg-database", "postgresql-k8s:database")
    juju.integrate("kratos:pg-database", "postgresql-k8s:database")
    juju.integrate("kratos:hydra-endpoint-info", "hydra:hydra-endpoint-info")
    juju.integrate(
        "kratos-external-idp-integrator:kratos-external-idp", "kratos:kratos-external-idp"
    )
    juju.integrate("hydra:admin-ingress", "traefik-admin:ingress")
    juju.integrate("hydra:public-ingress", "traefik-public:ingress")
    juju.integrate("kratos:admin-ingress", "traefik-admin:ingress")
    juju.integrate("kratos:public-ingress", "traefik-public:ingress")
    juju.integrate("identity-platform-login-ui-operator:ingress", "traefik-public:ingress")
    juju.integrate(
        "identity-platform-login-ui-operator:hydra-endpoint-info", "hydra:hydra-endpoint-info"
    )
    juju.integrate(
        "identity-platform-login-ui-operator:ui-endpoint-info", "hydra:ui-endpoint-info"
    )
    juju.integrate(
        "identity-platform-login-ui-operator:ui-endpoint-info", "kratos:ui-endpoint-info"
    )
    juju.integrate("identity-platform-login-ui-operator:kratos-info", "kratos:kratos-info")
    juju.integrate("traefik-admin:certificates", "self-signed-certificates:certificates")
    juju.integrate("traefik-public:certificates", "self-signed-certificates:certificates")

    juju.config(
        "kratos-external-idp-integrator",
        {
            "client_id": ext_idp_service.client_id,
            "client_secret": ext_idp_service.client_secret,
            "provider": "generic",
            "issuer_url": ext_idp_service.issuer_url,
            "scope": "profile email",
            "provider_id": "Dex",
        },
    )
    juju.wait(
        lambda status: jubilant.all_active(
            status,
            "hydra",
            "kratos",
            "kratos-external-idp-integrator",
        ),
        timeout=300,
    )
    redirect_uri = juju.run("kratos-external-idp-integrator/0", "get-redirect-uri").results.get(
        "redirect-uri"
    )
    assert redirect_uri, (
        "kratos-external-idp-integrator get-redirect-uri did not return redirect-uri"
    )
    ext_idp_service.update_redirect_uri(redirect_uri=redirect_uri)

    deployed_apps = [
        *deployment,
        "hydra",
        "kratos",
        "kratos-external-idp-integrator",
        "identity-platform-login-ui-operator",
        "traefik-admin",
        "traefik-public",
    ]

    return deployed_apps
