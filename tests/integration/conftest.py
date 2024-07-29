# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Integration test fixtures."""

# pylint: disable=unused-argument

import collections
import logging
import time

import boto3
import botocore.client
import kubernetes
import pytest
from pytest_operator.plugin import OpsTest

logger = logging.getLogger(__name__)


@pytest.fixture(scope="module", name="load_kube_config")
def load_kube_config_fixture(pytestconfig: pytest.Config):
    """Load kubernetes config file."""
    kube_config = pytestconfig.getoption("--kube-config")
    kubernetes.config.load_kube_config(config_file=kube_config)


@pytest.fixture(scope="module")
def minio(load_kube_config, ops_test: OpsTest):
    """Deploy test minio service."""
    assert ops_test.model
    namespace = ops_test.model.name
    key = "minioadmin"
    v1 = kubernetes.client.CoreV1Api()
    pod = kubernetes.client.V1Pod(
        api_version="v1",
        kind="Pod",
        metadata=kubernetes.client.V1ObjectMeta(
            name="minio", namespace=namespace, labels={"app.kubernetes.io/name": "minio"}
        ),
        spec=kubernetes.client.V1PodSpec(
            containers=[
                kubernetes.client.V1Container(
                    name="minio",
                    image="minio/minio",
                    args=["server", "/data"],
                    env=[
                        kubernetes.client.V1EnvVar(name="MINIO_ROOT_USER", value=key),
                        kubernetes.client.V1EnvVar(name="MINIO_ROOT_PASSWORD", value=key),
                    ],
                    ports=[kubernetes.client.V1ContainerPort(container_port=9000)],
                )
            ],
        ),
    )
    v1.create_namespaced_pod(namespace=namespace, body=pod)
    service = kubernetes.client.V1Service(
        api_version="v1",
        kind="Service",
        metadata=kubernetes.client.V1ObjectMeta(name="minio-service", namespace=namespace),
        spec=kubernetes.client.V1ServiceSpec(
            type="ClusterIP",
            ports=[kubernetes.client.V1ServicePort(port=9000, target_port=9000)],
            selector={"app.kubernetes.io/name": "minio"},
        ),
    )
    v1.create_namespaced_service(namespace=namespace, body=service)
    deadline = time.time() + 300
    while True:
        if time.time() > deadline:
            raise TimeoutError("timeout while waiting for minio pod")
        try:
            pod = v1.read_namespaced_pod(name="minio", namespace=namespace)
            if pod.status.phase == "Running":
                logger.info("minio running at %s", pod.status.pod_ip)
                break
        except kubernetes.client.ApiException:
            pass
        logger.info("waiting for minio pod")
        time.sleep(1)
    pod_ip = pod.status.pod_ip
    s3 = boto3.client(
        "s3",
        endpoint_url=f"http://{pod_ip}:9000",
        aws_access_key_id=key,
        aws_secret_access_key=key,
        config=botocore.client.Config(signature_version="s3v4"),
    )
    bucket = "penpot"
    s3.create_bucket(Bucket=bucket)
    S3Credential = collections.namedtuple("S3Credential", "endpoint bucket access_key secret_key")
    return S3Credential(
        endpoint=f"http://minio-service.{namespace}.svc.cluster.local:9000",
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


@pytest.fixture(scope="module")
def ingress_address(pytestconfig: pytest.Config):
    """Get ingress address test option."""
    address = pytestconfig.getoption("--ingress-address")
    if not address:
        return "127.0.0.1"
    return address
