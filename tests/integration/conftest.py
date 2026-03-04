# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Integration test fixtures."""

# pylint: disable=unused-argument

import collections
import logging
import os
import time

import boto3
import botocore.client
import jubilant
import kubernetes
import pytest
import pytest_asyncio
from lightkube.resources.core_v1 import Node, Service
from oauth_tools.external_idp import DexIdpService

logger = logging.getLogger(__name__)


def pytest_addoption(parser):
    """Parse integration-specific pytest options."""
    parser.addoption("--charm-file", action="store")
    parser.addoption("--kube-config", action="store")
    parser.addoption("--penpot-image", action="store")
    parser.addoption("--ingress-address", action="store")


def pytest_configure(config):
    """Configure integration test environment."""
    kube_config = config.getoption("--kube-config")
    if kube_config and not os.environ.get("TESTING_KUBECONFIG"):
        os.environ["TESTING_KUBECONFIG"] = kube_config


@pytest.fixture(name="charm_file", scope="module")
def charm_file_fixture(pytestconfig: pytest.Config) -> str:
    """Return the required charm file path for integration tests."""
    charm = pytestconfig.getoption("--charm-file")
    assert charm, "--charm-file is required"
    return charm


@pytest.fixture(name="penpot_image", scope="module")
def penpot_image_fixture(pytestconfig: pytest.Config) -> str:
    """Return the required penpot image for integration tests."""
    image = pytestconfig.getoption("--penpot-image")
    assert image, "--penpot-image is required"
    return image


@pytest.fixture(name="keep_models", scope="module")
def keep_models_fixture(pytestconfig: pytest.Config) -> bool:
    """Return whether integration model retention is enabled."""
    return bool(pytestconfig.getoption("--keep-models"))


@pytest.fixture(scope="module", name="load_kube_config")
def load_kube_config_fixture(pytestconfig: pytest.Config):
    """Load kubernetes config file."""
    kube_config = pytestconfig.getoption("--kube-config")
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


@pytest.fixture(name="minio", scope="module")
def minio_fixture(get_unit_ips, load_kube_config, juju: jubilant.Juju):
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
    S3Credential = collections.namedtuple("S3Credential", "endpoint bucket access_key secret_key")
    return S3Credential(
        endpoint=f"http://minio-endpoints.{juju.model}.svc.cluster.local:9000",
        bucket=bucket,
        access_key=key,
        secret_key=key,
    )


@pytest.fixture(name="mailcatcher", scope="module")
def mailcatcher_fixture(load_kube_config, juju: jubilant.Juju):
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
    SmtpCredential = collections.namedtuple("SmtpCredential", "host port")
    return SmtpCredential(
        host=f"mailcatcher-service.{namespace}.svc.cluster.local",
        port=1025,
    )


@pytest.fixture(name="ingress_address", scope="module")
def ingress_address_fixture(pytestconfig: pytest.Config):
    """Get ingress address test option."""
    address = pytestconfig.getoption("--ingress-address")
    if not address:
        return "127.0.0.1"
    return address


@pytest.fixture(name="ext_idp_service", scope="module")
def ext_idp_service_fixture(keep_models: bool, client):
    """Deploy and manage Dex with resilient issuer URL resolution."""
    logger.info("Deploying dex resources")
    ext_idp_manager = StableDexIdpService(client=client)
    try:
        yield ext_idp_manager
    finally:
        if not keep_models:
            logger.info("Deleting dex resources")
            ext_idp_manager.remove_idp_service()


@pytest_asyncio.fixture(name="oauth_deployment", scope="module")
async def oauth_deployment_fixture(
    juju: jubilant.Juju,
    charm_file: str,
    penpot_image: str,
    minio,
    mailcatcher,
    ext_idp_service,
):
    """Deploy identity stack and penpot through explicit Juju operations."""
    def deploy_if_missing(app_name: str, charm_name: str | None = None, **kwargs):
        if app_name in juju.status().apps:
            logger.info("app %s already deployed, reusing", app_name)
            return
        if charm_name is None:
            juju.deploy(app_name, **kwargs)
            return
        juju.deploy(charm_name, app=app_name, **kwargs)

    def relation_exists(endpoint_a: str, endpoint_b: str) -> bool:
        relations = juju.cli("status", "--relations")
        return any(
            endpoint_a in relation_line and endpoint_b in relation_line
            for relation_line in relations.splitlines()
        )

    def integrate_if_missing(endpoint_a: str, endpoint_b: str):
        if relation_exists(endpoint_a, endpoint_b):
            logger.info("relation already exists: %s <-> %s", endpoint_a, endpoint_b)
            return
        juju.integrate(endpoint_a, endpoint_b)

    deploy_if_missing(
        "hydra",
        channel="edge",
        revision=339,
        trust=True,
        resources={"oci-image": "ghcr.io/canonical/hydra:2.3.0-canonical"},
    )
    deploy_if_missing(
        "kratos",
        channel="edge",
        revision=500,
        trust=True,
        resources={"oci-image": "ghcr.io/canonical/kratos:1.3.1"},
    )
    deploy_if_missing("kratos-external-idp-integrator", channel="edge", revision=245)
    deploy_if_missing(
        "identity-platform-login-ui-operator",
        channel="edge",
        revision=146,
        trust=True,
        resources={"oci-image": "ghcr.io/canonical/identity-platform-login-ui:v0.21.2"},
    )
    deploy_if_missing(
        "postgresql-k8s",
        channel="14/stable",
        trust=True,
        config={
            "plugin_pg_trgm_enable": True,
            "plugin_btree_gin_enable": True,
        },
    )
    juju.config(
        "postgresql-k8s",
        {
            "plugin_pg_trgm_enable": True,
            "plugin_btree_gin_enable": True,
        },
    )
    deploy_if_missing("self-signed-certificates", channel="latest/stable", revision=155)
    deploy_if_missing(
        "traefik-admin",
        charm_name="traefik-k8s",
        channel="latest/stable",
        revision=176,
        trust=True,
    )
    deploy_if_missing(
        "traefik-public",
        charm_name="traefik-k8s",
        channel="latest/stable",
        revision=176,
        trust=True,
    )

    integrate_if_missing("hydra:pg-database", "postgresql-k8s:database")
    integrate_if_missing("kratos:pg-database", "postgresql-k8s:database")
    integrate_if_missing("kratos:hydra-endpoint-info", "hydra:hydra-endpoint-info")
    integrate_if_missing(
        "kratos-external-idp-integrator:kratos-external-idp", "kratos:kratos-external-idp"
    )
    integrate_if_missing("hydra:admin-ingress", "traefik-admin:ingress")
    integrate_if_missing("hydra:public-ingress", "traefik-public:ingress")
    integrate_if_missing("kratos:admin-ingress", "traefik-admin:ingress")
    integrate_if_missing("kratos:public-ingress", "traefik-public:ingress")
    integrate_if_missing("identity-platform-login-ui-operator:ingress", "traefik-public:ingress")
    integrate_if_missing(
        "identity-platform-login-ui-operator:hydra-endpoint-info", "hydra:hydra-endpoint-info"
    )
    integrate_if_missing(
        "identity-platform-login-ui-operator:ui-endpoint-info", "hydra:ui-endpoint-info"
    )
    integrate_if_missing(
        "identity-platform-login-ui-operator:ui-endpoint-info", "kratos:ui-endpoint-info"
    )
    integrate_if_missing("identity-platform-login-ui-operator:kratos-info", "kratos:kratos-info")
    integrate_if_missing("traefik-admin:certificates", "self-signed-certificates:certificates")
    integrate_if_missing("traefik-public:certificates", "self-signed-certificates:certificates")

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
            "identity-platform-login-ui-operator",
            "postgresql-k8s",
            "self-signed-certificates",
            "traefik-admin",
            "traefik-public",
        ),
        timeout=2000,
    )
    redirect_uri = juju.run("kratos-external-idp-integrator/0", "get-redirect-uri").results.get(
        "redirect-uri"
    )
    assert redirect_uri, (
        "kratos-external-idp-integrator get-redirect-uri did not return redirect-uri"
    )
    ext_idp_service.update_redirect_uri(redirect_uri=redirect_uri)
    penpot_app = "penpot"
    redis_app = "redis-k8s"
    smtp_app = "smtp-integrator"
    s3_app = "s3-integrator"
    ingress_app = "nginx-ingress-integrator"

    deploy_if_missing(
        penpot_app,
        charm_name=f"./{charm_file}",
        resources={"penpot-image": penpot_image},
        num_units=2,
    )
    deploy_if_missing(redis_app, channel="edge")
    deploy_if_missing(
        smtp_app,
        config={
            "auth_type": "none",
            "domain": "example.com",
            "host": mailcatcher.host,
            "port": mailcatcher.port,
        },
    )
    deploy_if_missing(s3_app, config={"bucket": minio.bucket, "endpoint": minio.endpoint})
    deploy_if_missing(
        ingress_app,
        channel="edge",
        config={"path-routes": "/", "service-hostname": "penpot.local"},
        trust=True,
        revision=109,
    )
    selected_apps = [s3_app, "self-signed-certificates"]
    juju.wait(
        lambda status: all(
            unit.juju_status.current == "idle"
            for app_name in selected_apps
            for unit in status.apps[app_name].units.values()
        ),
        error=lambda status: any(
            status.apps[app_name].app_status.current == "error"
            or any(
                unit.workload_status.current == "error" or unit.juju_status.current == "error"
                for unit in status.apps[app_name].units.values()
            )
            for app_name in selected_apps
        ),
        timeout=300,
    )
    juju.run(
        "s3-integrator/0",
        "sync-s3-credentials",
        {
            "access-key": minio.access_key,
            "secret-key": minio.secret_key,
        },
    )
    integrate_if_missing("penpot:postgresql", "postgresql-k8s:database")
    integrate_if_missing("penpot:redis", redis_app)
    integrate_if_missing("penpot:s3", f"{s3_app}:s3-credentials")
    integrate_if_missing("penpot:smtp", f"{smtp_app}:smtp")
    integrate_if_missing("self-signed-certificates:certificates", f"{ingress_app}:certificates")
    integrate_if_missing("penpot:ingress", f"{ingress_app}:ingress")
    juju.wait(lambda status: jubilant.all_active(status, *status.apps.keys()), timeout=300)
