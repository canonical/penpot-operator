# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Unit tests fixtures."""

import json
from collections.abc import Iterable
from secrets import token_hex
from typing import cast

import pytest
from ops import pebble, testing
from ops.testing import Exec, PeerRelation, Relation, Secret
from scenario.state import Container as ScenarioContainer

from src.charm import PenpotCharm

SMTP_TEST_PASSWORD = token_hex(16)
SMTP_TEST_USER = "smtp-user"
SMTP_SECRET_ID = token_hex(16)
PEER_SECRET_ID = token_hex(16)
POSTGRESQL_PASSWORD = token_hex(16)
S3_SECRET_KEY = token_hex(16)
VALKEY_USERNAME = "relation-5-penpot"
VALKEY_PASSWORD = token_hex(16)
VALKEY_ENDPOINT = "valkey-0.valkey-endpoints:6379"
# data-interfaces v1 rejects secret IDs that do not start with "secret:".
VALKEY_USER_SECRET_ID = f"secret:{token_hex(10)}"
VALKEY_TLS_SECRET_ID = f"secret:{token_hex(10)}"
VALKEY_REDIS_URI = f"redis://{VALKEY_USERNAME}:{VALKEY_PASSWORD}@{VALKEY_ENDPOINT}/0"


@pytest.fixture(name="context")
def context_fixture() -> testing.Context[PenpotCharm]:
    """Context fixture."""
    return testing.Context(PenpotCharm)


def penpot_container(
    *,
    can_connect: bool = True,
    include_backend: bool = False,
    execs: Iterable[Exec] | None = None,
) -> ScenarioContainer:
    layers = {}
    service_statuses = {}
    if include_backend:
        layers["penpot"] = pebble.Layer(
            {
                "services": {
                    "backend": {
                        "command": "/opt/penpot/backend/run.sh",
                        "override": "replace",
                    }
                }
            }
        )
        service_statuses["backend"] = pebble.ServiceStatus.ACTIVE
    return cast(
        ScenarioContainer,
        testing.Container(
            "penpot",
            can_connect=can_connect,
            layers=layers,
            service_statuses=service_statuses,
            execs=frozenset(execs or ()),
        ),  # type: ignore[call-arg]
    )


def postgresql_relation() -> Relation:
    return Relation(
        endpoint="postgresql",
        remote_app_name="postgresql",
        remote_app_data={
            "data": json.dumps(
                {
                    "database": "penpot",
                    "requested-secrets": json.dumps(["username", "password"]),
                }
            ),
            "database": "penpot",
            "endpoints": "postgresql-endpoint:5432",
            "username": "postgresql-username",
            "password": POSTGRESQL_PASSWORD,
            "version": "14.11",
        },
    )


def valkey_secrets(*, password: str = VALKEY_PASSWORD, tls: bool = False) -> set[Secret]:
    """Secrets shared by the valkey charm over the valkey_client integration."""
    return {
        Secret(
            id=VALKEY_USER_SECRET_ID,
            tracked_content={"username": VALKEY_USERNAME, "password": password},
        ),
        Secret(id=VALKEY_TLS_SECRET_ID, tracked_content={"tls": "true" if tls else "false"}),
    }


def valkey_relation(
    *, with_credentials: bool = True, endpoints: str = VALKEY_ENDPOINT
) -> Relation:
    """Valkey integration as written by the valkey charm (data-interfaces v1)."""
    response = {
        "resource": "*",
        "request-id": "penpot-valkey-request",
        "salt": "penpot-valkey-salt",
        "endpoints": endpoints,
        "secret-user": VALKEY_USER_SECRET_ID if with_credentials else None,
        "secret-tls": VALKEY_TLS_SECRET_ID,
        "mode": "sentinel",
        "version": "9.0.0",
    }
    return Relation(
        endpoint="valkey",
        remote_app_name="valkey",
        remote_app_data={"version": "v1", "requests": json.dumps([response])},
    )


def s3_relation() -> Relation:
    return Relation(
        endpoint="s3",
        remote_app_name="s3-integrator",
        remote_app_data={
            "access-key": "s3-access-key",
            "secret-key": S3_SECRET_KEY,
            "bucket": "penpot",
            "endpoint": "s3-endpoint",
        },
    )


def smtp_relation(*, use_password: bool = False, password_id: str | None = None) -> Relation:
    app_data = {
        "auth_type": "plain" if use_password else "none",
        "domain": "example.com",
        "host": "smtp-host",
        "port": "1025",
        "transport_security": "none",
    }
    if use_password:
        app_data["user"] = SMTP_TEST_USER
        if password_id:
            app_data["password_id"] = password_id
        else:
            app_data["password"] = SMTP_TEST_PASSWORD
    return Relation(endpoint="smtp", remote_app_name="smtp-integrator", remote_app_data=app_data)


def ingress_relation(url: str = "https://penpot.local/") -> Relation:
    return Relation(
        endpoint="ingress",
        remote_app_name="nginx-ingress-integrator",
        remote_app_data={"ingress": json.dumps({"url": url})},
    )


def peer_relation(*, secret_id: str, peers: Iterable[int] = (1,)) -> PeerRelation:
    return PeerRelation(
        endpoint="penpot_peer",
        local_app_data={"secrets": secret_id},
        peers_data={peer_id: {} for peer_id in peers},
    )
