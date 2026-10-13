# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Integration test fixtures."""

# pylint: disable=unused-argument

import collections
import logging
import socket
import time
from collections.abc import Iterator

import boto3
import botocore.client
import jubilant
import kubernetes
import pytest

POSTGRESQL_REVISION = 774
REDIS_REVISION = 42
S3_INTEGRATOR_REVISION = 330
S3_ACCESS_KEY = "penpot-test-access-key"
S3_SECRET_KEY = "penpot-test-secret-key"  # nosec B105
S3_PORT = 7480
SELF_SIGNED_CERTIFICATES_REVISION = 264
SMTP_INTEGRATOR_REVISION = 93
TRAEFIK_PUBLIC_REVISION = 277
HYDRA_REVISION = 399
KRATOS_REVISION = 567
IDENTITY_PLATFORM_LOGIN_UI_REVISION = 200

logger = logging.getLogger(__name__)


@pytest.fixture(scope="session")
def browser_type_launch_args(browser_type_launch_args: dict) -> dict:
    """Add --no-sandbox for headless Chromium on Linux (required in some environments)."""
    return {**browser_type_launch_args, "args": ["--no-sandbox"]}


@pytest.fixture(scope="session")
def browser_context_args(browser_context_args: dict) -> dict:
    """Configure Playwright sync browser context for self-signed TLS."""
    return {**browser_context_args, "ignore_https_errors": True}


@pytest.fixture(name="charm_file", scope="module")
def charm_file_fixture(charm_path: str) -> str:
    """Return the charm file path built by opcli."""
    return charm_path


@pytest.fixture(name="penpot_image", scope="module")
def penpot_image_fixture(resource_images: dict[str, str]) -> str:
    """Return the penpot OCI image built by opcli."""
    image = resource_images.get("penpot-image", "")
    assert image, "penpot-image resource not found in artifacts.build.yaml"
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


@pytest.fixture(name="juju", scope="module")
def juju_fixture(request: pytest.FixtureRequest, keep_models: bool) -> Iterator[jubilant.Juju]:
    """Provide a Jubilant Juju client with a temporary model."""
    with jubilant.temp_model(keep=keep_models) as juju_model:
        yield juju_model
        # The model is destroyed on teardown, so dump the logs while it still exists.
        if request.session.testsfailed:
            logger.info("juju debug-log:\n%s", juju_model.debug_log(limit=1000))


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


def _host_ip() -> str:
    """Get the host's primary IP address, which is reachable from Kubernetes pods."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.connect(("8.8.8.8", 80))
        return sock.getsockname()[0]


@pytest.fixture(name="s3", scope="module")
def s3_fixture() -> S3Credential:
    """Create a test bucket in the MicroCeph S3 gateway installed by s3-installation.sh."""
    endpoint = f"http://{_host_ip()}:{S3_PORT}"
    s3 = boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=S3_ACCESS_KEY,
        aws_secret_access_key=S3_SECRET_KEY,
        config=botocore.client.Config(signature_version="s3v4"),
    )
    bucket = "penpot"
    s3.create_bucket(Bucket=bucket)
    return S3Credential(
        endpoint=endpoint,
        bucket=bucket,
        access_key=S3_ACCESS_KEY,
        secret_key=S3_SECRET_KEY,
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


@pytest.fixture(name="deployment", scope="module")
def deployment_fixture(
    juju: jubilant.Juju,
    charm_file: str,
    penpot_image: str,
    s3: S3Credential,
    mailcatcher: SmtpCredential,
    get_unit_ips,
) -> set[str]:
    """Deploy base Penpot stack used by integration tests.

    Returns:
        A set of deployed application names.
    """
    # Every published postgresql-k8s revision declares `assumes: juju < 4`;
    # Juju only skips a failed assumes check when --force is given.
    juju.deploy(
        "postgresql-k8s",
        channel="14/stable",
        revision=POSTGRESQL_REVISION,
        trust=True,
        force=juju.version().major >= 4,
    )
    juju.deploy(
        "self-signed-certificates",
        channel="latest/stable",
        revision=SELF_SIGNED_CERTIFICATES_REVISION,
        trust=True,
    )
    juju.deploy(
        charm_file,
        app="penpot",
        resources={"penpot-image": penpot_image},
        num_units=2,
    )
    juju.deploy("redis-k8s", channel="edge", revision=REDIS_REVISION)
    juju.deploy(
        "smtp-integrator",
        channel="latest/stable",
        revision=SMTP_INTEGRATOR_REVISION,
        config={
            "auth_type": "none",
            "domain": "example.com",
            "host": mailcatcher.host,
            "port": mailcatcher.port,
        },
    )
    juju.deploy(
        "s3-integrator",
        channel="1/stable",
        revision=S3_INTEGRATOR_REVISION,
        config={"bucket": s3.bucket, "endpoint": s3.endpoint},
    )
    juju.deploy(
        "traefik-k8s",
        app="traefik-public",
        channel="latest/edge",
        revision=TRAEFIK_PUBLIC_REVISION,
        trust=True,
    )

    juju.wait(
        lambda status: jubilant.all_agents_idle(status, "traefik-public", "s3-integrator"),
        timeout=300,
    )

    # Use host-based routing so apps are exposed at URL root (required by Penpot SPA).
    # sslip.io maps this hostname back to the Traefik service IP without extra DNS setup.
    traefik_ip = get_unit_ips("traefik-public")[0]
    traefik_hostname = f"{traefik_ip.replace('.', '-')}.sslip.io"
    juju.config(
        "traefik-public",
        {
            "routing_mode": "subdomain",
            "external_hostname": traefik_hostname,
        },
    )
    juju.run(
        "s3-integrator/0",
        "sync-s3-credentials",
        {
            "access-key": s3.access_key,
            "secret-key": s3.secret_key,
        },
    )

    juju.integrate("self-signed-certificates:certificates", "traefik-public:certificates")
    juju.integrate("penpot:postgresql", "postgresql-k8s:database")
    juju.integrate("penpot:redis", "redis-k8s")
    juju.integrate("penpot:s3", "s3-integrator:s3-credentials")
    juju.integrate("penpot:smtp", "smtp-integrator:smtp")
    juju.integrate("penpot:ingress", "traefik-public:ingress")

    deployed_apps = {
        "postgresql-k8s",
        "self-signed-certificates",
        "penpot",
        "redis-k8s",
        "s3-integrator",
        "smtp-integrator",
        "traefik-public",
    }

    return deployed_apps


@pytest.fixture(name="deployment_with_identity_bundle", scope="module")
def deployment_with_identity_bundle_fixture(juju: jubilant.Juju, deployment: set[str]) -> set[str]:
    """Deploy Canonical identity bundle on top of base deployment.

    Deploys hydra, kratos, identity-platform-login-ui-operator and traefik-admin,
    wired to the traefik-public and postgresql-k8s instances from the base deployment.
    """
    juju.deploy("hydra", channel="latest/edge", revision=HYDRA_REVISION, trust=True)
    juju.deploy("kratos", channel="latest/edge", revision=KRATOS_REVISION, trust=True)
    juju.deploy(
        "identity-platform-login-ui-operator",
        channel="latest/edge",
        revision=IDENTITY_PLATFORM_LOGIN_UI_REVISION,
        trust=True,
    )

    juju.integrate("postgresql-k8s:database", "hydra:pg-database")
    juju.integrate("postgresql-k8s:database", "kratos:pg-database")
    juju.integrate("hydra:hydra-endpoint-info", "kratos:hydra-endpoint-info")
    juju.integrate(
        "hydra:hydra-endpoint-info",
        "identity-platform-login-ui-operator:hydra-endpoint-info",
    )
    juju.integrate(
        "hydra:ui-endpoint-info",
        "identity-platform-login-ui-operator:ui-endpoint-info",
    )
    juju.integrate(
        "kratos:ui-endpoint-info",
        "identity-platform-login-ui-operator:ui-endpoint-info",
    )
    juju.integrate("kratos:kratos-info", "identity-platform-login-ui-operator:kratos-info")

    juju.integrate("traefik-public:traefik-route", "hydra:public-route")
    juju.integrate("traefik-public:traefik-route", "kratos:public-route")
    juju.integrate(
        "traefik-public:traefik-route",
        "identity-platform-login-ui-operator:public-route",
    )

    juju.config("kratos", {"enforce_mfa": False})

    return deployment | {
        "hydra",
        "kratos",
        "identity-platform-login-ui-operator",
    }
