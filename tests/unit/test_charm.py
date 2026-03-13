# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Unit tests."""

from secrets import token_hex

import pytest
from ops import testing
from ops.testing import Exec, Secret

from src.charm import PenpotCharm
from tests.unit.conftest import (
    PEER_SECRET_ID,
    SMTP_SECRET_ID,
    SMTP_TEST_PASSWORD,
    SMTP_TEST_USER,
    ingress_relation,
    peer_relation,
    penpot_container,
    postgresql_relation,
    redis_relation,
    s3_relation,
    smtp_relation,
)


def test_postgresql_config(monkeypatch: pytest.MonkeyPatch, context: testing.Context[PenpotCharm]):
    """
    arrange: initialize the testing context with required integrations.
    act: run reconcile via config-changed and retrieve the output state.
    assert: ensure postgresql variables are present in the backend container env.
    """
    monkeypatch.setattr(PenpotCharm, "_check_penpot_backend_ready", lambda self: True)
    peer_secret = Secret(tracked_content={"penpot-secret-key": token_hex(16)}, id=PEER_SECRET_ID)
    state = testing.State(
        relations={
            peer_relation(secret_id=peer_secret.id),
            postgresql_relation(),
            redis_relation(),
            s3_relation(),
            ingress_relation(),
        },
        secrets={peer_secret},
        containers={penpot_container()},
    )
    out = context.run(context.on.config_changed(), state)
    assert out.unit_status == testing.ActiveStatus()
    backend_env = out.get_container("penpot").plan.services["backend"].environment
    assert backend_env["PENPOT_DATABASE_PASSWORD"] == "postgresql-password"
    assert backend_env["PENPOT_DATABASE_URI"] == "postgresql://postgresql-endpoint:5432/penpot"
    assert backend_env["PENPOT_DATABASE_USERNAME"] == "postgresql-username"


def test_redis_config(monkeypatch: pytest.MonkeyPatch, context: testing.Context[PenpotCharm]):
    """
    arrange: initialize the testing context with required integrations.
    act: run reconcile via config-changed and retrieve the output state.
    assert: ensure redis variables are present in the backend container env.
    """
    monkeypatch.setattr(PenpotCharm, "_check_penpot_backend_ready", lambda self: True)
    peer_secret = Secret(tracked_content={"penpot-secret-key": token_hex(16)}, id=PEER_SECRET_ID)
    state = testing.State(
        relations={
            peer_relation(secret_id=peer_secret.id),
            postgresql_relation(),
            redis_relation(),
            s3_relation(),
            ingress_relation(),
        },
        secrets={peer_secret},
        containers={penpot_container()},
    )
    out = context.run(context.on.config_changed(), state)
    assert out.unit_status == testing.ActiveStatus()
    backend_env = out.get_container("penpot").plan.services["backend"].environment
    assert backend_env["PENPOT_REDIS_URI"] == "redis://redis-hostname:6379"


def test_s3_config(monkeypatch: pytest.MonkeyPatch, context: testing.Context[PenpotCharm]):
    """
    arrange: initialize the testing context with required integrations.
    act: run reconcile via config-changed and retrieve the output state.
    assert: ensure s3 variables are present in the backend container env.
    """
    monkeypatch.setattr(PenpotCharm, "_check_penpot_backend_ready", lambda self: True)
    peer_secret = Secret(tracked_content={"penpot-secret-key": token_hex(16)}, id=PEER_SECRET_ID)
    state = testing.State(
        relations={
            peer_relation(secret_id=peer_secret.id),
            postgresql_relation(),
            redis_relation(),
            s3_relation(),
            ingress_relation(),
        },
        secrets={peer_secret},
        containers={penpot_container()},
    )
    out = context.run(context.on.config_changed(), state)
    assert out.unit_status == testing.ActiveStatus()
    backend_env = out.get_container("penpot").plan.services["backend"].environment
    assert backend_env["AWS_ACCESS_KEY_ID"] == "s3-access-key"
    assert backend_env["AWS_SECRET_ACCESS_KEY"] == "s3-secret-key"
    assert backend_env["PENPOT_ASSETS_STORAGE_BACKEND"] == "assets-s3"
    assert backend_env["PENPOT_STORAGE_ASSETS_S3_BUCKET"] == "penpot"
    assert backend_env["PENPOT_STORAGE_ASSETS_S3_ENDPOINT"] == "s3-endpoint"
    assert backend_env["PENPOT_STORAGE_ASSETS_S3_REGION"] == "us-east-1"


def test_smtp_config(monkeypatch: pytest.MonkeyPatch, context: testing.Context[PenpotCharm]):
    """
    arrange: initialize the testing context with required integrations.
    act: run reconcile via config-changed and retrieve the output state.
    assert: ensure smtp variables are present in the backend container env.
    """
    monkeypatch.setattr(PenpotCharm, "_check_penpot_backend_ready", lambda self: True)
    peer_secret = Secret(tracked_content={"penpot-secret-key": token_hex(16)}, id=PEER_SECRET_ID)
    state = testing.State(
        relations={
            peer_relation(secret_id=peer_secret.id),
            postgresql_relation(),
            redis_relation(),
            s3_relation(),
            smtp_relation(),
            ingress_relation(),
        },
        secrets={peer_secret},
        containers={penpot_container()},
    )
    out = context.run(context.on.config_changed(), state)
    assert out.unit_status == testing.ActiveStatus()
    backend_env = out.get_container("penpot").plan.services["backend"].environment
    assert backend_env["PENPOT_SMTP_DEFAULT_FROM"] == "no-reply@example.com"
    assert backend_env["PENPOT_SMTP_DEFAULT_REPLY_TO"] == "no-reply@example.com"
    assert backend_env["PENPOT_SMTP_HOST"] == "smtp-host"
    assert backend_env["PENPOT_SMTP_PORT"] == "1025"
    assert backend_env["PENPOT_SMTP_SSL"] == "false"
    assert backend_env["PENPOT_SMTP_TLS"] == "false"


def test_smtp_config_with_password(
    monkeypatch: pytest.MonkeyPatch, context: testing.Context[PenpotCharm]
):
    """
    arrange: set up required integrations and smtp password authentication.
    act: run reconcile via config-changed and retrieve the output state.
    assert: ensure smtp password variables are present in the backend container env.
    """
    monkeypatch.setattr(PenpotCharm, "_check_penpot_backend_ready", lambda self: True)
    smtp_secret = Secret(tracked_content={"password": SMTP_TEST_PASSWORD}, id=SMTP_SECRET_ID)
    peer_secret = Secret(tracked_content={"penpot-secret-key": token_hex(16)}, id=PEER_SECRET_ID)
    state = testing.State(
        relations={
            peer_relation(secret_id=peer_secret.id),
            postgresql_relation(),
            redis_relation(),
            s3_relation(),
            smtp_relation(use_password=True, password_id=smtp_secret.id),
            ingress_relation(),
        },
        secrets={peer_secret, smtp_secret},
        containers={penpot_container()},
    )
    out = context.run(context.on.config_changed(), state)
    assert out.unit_status == testing.ActiveStatus()
    backend_env = out.get_container("penpot").plan.services["backend"].environment
    assert backend_env["PENPOT_SMTP_DEFAULT_FROM"] == f"{SMTP_TEST_USER}@example.com"
    assert backend_env["PENPOT_SMTP_DEFAULT_REPLY_TO"] == f"{SMTP_TEST_USER}@example.com"
    assert backend_env["PENPOT_SMTP_HOST"] == "smtp-host"
    assert backend_env["PENPOT_SMTP_PASSWORD"] == SMTP_TEST_PASSWORD
    assert backend_env["PENPOT_SMTP_PORT"] == "1025"
    assert backend_env["PENPOT_SMTP_SSL"] == "false"
    assert backend_env["PENPOT_SMTP_TLS"] == "false"
    assert backend_env["PENPOT_SMTP_USERNAME"] == SMTP_TEST_USER


def test_smtp_config_override_from_address(
    monkeypatch: pytest.MonkeyPatch, context: testing.Context[PenpotCharm]
):
    """
    arrange: initialize required integrations and set smtp-from-address config.
    act: run reconcile via config-changed and retrieve the output state.
    assert: ensure smtp override variables are present in the backend container env.
    """
    monkeypatch.setattr(PenpotCharm, "_check_penpot_backend_ready", lambda self: True)
    smtp_secret = Secret(tracked_content={"password": SMTP_TEST_PASSWORD}, id=SMTP_SECRET_ID)
    peer_secret = Secret(tracked_content={"penpot-secret-key": token_hex(16)}, id=PEER_SECRET_ID)
    state = testing.State(
        relations={
            peer_relation(secret_id=peer_secret.id),
            postgresql_relation(),
            redis_relation(),
            s3_relation(),
            smtp_relation(use_password=True, password_id=smtp_secret.id),
            ingress_relation(),
        },
        secrets={peer_secret, smtp_secret},
        containers={penpot_container()},
        config={"smtp-from-address": "test@test.com"},
    )
    out = context.run(context.on.config_changed(), state)
    assert out.unit_status == testing.ActiveStatus()
    backend_env = out.get_container("penpot").plan.services["backend"].environment
    assert backend_env["PENPOT_SMTP_DEFAULT_FROM"] == "test@test.com"
    assert backend_env["PENPOT_SMTP_DEFAULT_REPLY_TO"] == "test@test.com"
    assert backend_env["PENPOT_SMTP_HOST"] == "smtp-host"
    assert backend_env["PENPOT_SMTP_PASSWORD"] == SMTP_TEST_PASSWORD
    assert backend_env["PENPOT_SMTP_PORT"] == "1025"
    assert backend_env["PENPOT_SMTP_SSL"] == "false"
    assert backend_env["PENPOT_SMTP_TLS"] == "false"
    assert backend_env["PENPOT_SMTP_USERNAME"] == SMTP_TEST_USER


def test_smtp_penpot_option(context: testing.Context[PenpotCharm]):
    """
    arrange: initialize the testing context.
    act: retrieve the penpot options with different smtp setup.
    assert: ensure the penpot options matches the expectations.
    """
    base_state = testing.State(containers={penpot_container()})
    with context(context.on.start(), base_state) as mgr:
        mgr.run()
        charm = mgr.charm
        flags = charm._get_penpot_backend_options()
    assert flags == [
        "disable-log-emails",
        "disable-onboarding-questions",
        "disable-registration",
        "disable-smtp",
        "disable-telemetry",
        "enable-login-with-password",
        "enable-prepl-server",
    ]

    smtp_state = testing.State(
        relations={smtp_relation(use_password=True)},
        containers={penpot_container()},
    )
    with context(context.on.start(), smtp_state) as mgr:
        mgr.run()
        charm = mgr.charm
        flags = charm._get_penpot_backend_options()
    assert flags == [
        "disable-log-emails",
        "disable-onboarding-questions",
        "disable-registration",
        "disable-telemetry",
        "enable-login-with-password",
        "enable-prepl-server",
        "enable-smtp",
    ]


def test_public_uri(context: testing.Context[PenpotCharm]):
    """
    arrange: initialize the testing context with the ingress integration.
    act: retrieve the public URI configuration for penpot.
    assert: ensure the public URI for penpot matches the expectations.
    """
    state = testing.State(
        relations={ingress_relation()},
        containers={penpot_container()},
    )
    with context(context.on.start(), state) as mgr:
        mgr.run()
        charm = mgr.charm
    assert charm._get_public_uri() == "https://penpot.local/"


def test_penpot_pebble_layer(context: testing.Context[PenpotCharm]):
    """
    arrange: initialize the testing context and set up all required integrations.
    act: retrieve the pebble layer for penpot.
    assert: ensure the pebble layer for penpot matches the expectations.
    """
    peer_secret = Secret(tracked_content={"penpot-secret-key": "secret"}, id=PEER_SECRET_ID)
    state = testing.State(
        relations={
            peer_relation(secret_id=peer_secret.id, peers=(1, 2)),
            postgresql_relation(),
            redis_relation(),
            s3_relation(),
            smtp_relation(),
            ingress_relation(),
        },
        secrets={peer_secret},
        containers={penpot_container()},
        leader=True,
        model=testing.Model(name="test"),
    )

    with context(context.on.start(), state) as mgr:
        mgr.run()
        charm = mgr.charm
    plan = charm._gen_pebble_plan()
    del plan["services"]["backend"]["environment"]["PENPOT_SECRET_KEY"]
    del plan["services"]["frontend"]["environment"]["PENPOT_INTERNAL_RESOLVER"]
    assert plan == {
        "checks": {
            "backend-ready": {
                "exec": {
                    # pylint: disable=line-too-long
                    "command": 'bash -c "pebble services backend | grep -q inactive || curl -f -m 5 localhost:6060/readyz"'
                },
                "level": "alive",
                "override": "replace",
                "period": "30s",
            }
        },
        "description": "penpot services",
        "services": {
            "backend": {
                "command": "/opt/penpot/backend/run.sh",
                "environment": {
                    "AWS_ACCESS_KEY_ID": "s3-access-key",
                    "AWS_SECRET_ACCESS_KEY": "s3-secret-key",
                    "JAVA_HOME": "/usr/lib/jvm/java-21-openjdk-amd64",
                    "PENPOT_ASSETS_STORAGE_BACKEND": "assets-s3",
                    "PENPOT_DATABASE_PASSWORD": "postgresql-password",
                    "PENPOT_DATABASE_URI": "postgresql://postgresql-endpoint:5432/penpot",
                    "PENPOT_DATABASE_USERNAME": "postgresql-username",
                    "PENPOT_FLAGS": (
                        "disable-log-emails "
                        "disable-onboarding-questions "
                        "disable-registration "
                        "disable-telemetry "
                        "enable-login-with-password "
                        "enable-prepl-server "
                        "enable-smtp"
                    ),
                    "PENPOT_PUBLIC_URI": "https://penpot.local/",
                    "PENPOT_REDIS_URI": "redis://redis-hostname:6379",
                    "PENPOT_SMTP_DEFAULT_FROM": "no-reply@example.com",
                    "PENPOT_SMTP_DEFAULT_REPLY_TO": "no-reply@example.com",
                    "PENPOT_SMTP_HOST": "smtp-host",
                    "PENPOT_SMTP_PORT": "1025",
                    "PENPOT_SMTP_SSL": "false",
                    "PENPOT_SMTP_TLS": "false",
                    "PENPOT_STORAGE_ASSETS_S3_BUCKET": "penpot",
                    "PENPOT_STORAGE_ASSETS_S3_ENDPOINT": "s3-endpoint",
                    "PENPOT_STORAGE_ASSETS_S3_REGION": "us-east-1",
                    "PENPOT_TELEMETRY_ENABLED": "false",
                },
                "override": "replace",
                "working-dir": "/opt/penpot/backend/",
            },
            "exporter": {
                "after": ["backend", "frontend"],
                "command": "node app.js",
                "environment": {
                    "PENPOT_PUBLIC_URI": "http://127.0.0.1:8080",
                    "PENPOT_REDIS_URI": "redis://redis-hostname:6379",
                    "PLAYWRIGHT_BROWSERS_PATH": "/opt/penpot/exporter/browsers",
                },
                "override": "replace",
                "working-dir": "/opt/penpot/exporter/",
            },
            "frontend": {
                "after": ["backend"],
                "command": './nginx-entrypoint.sh nginx -g "daemon off;"',
                "environment": {
                    "PENPOT_BACKEND_URI": "http://127.0.0.1:6060",
                    "PENPOT_EXPORTER_URI": (
                        "http://penpot-0.penpot-endpoints.test.svc.cluster.local:6061"
                    ),
                    "PENPOT_FLAGS": (
                        "disable-onboarding-questions "
                        "disable-registration "
                        "enable-login-with-password"
                    ),
                },
                "override": "replace",
                "working-dir": "/opt/penpot/frontend/",
            },
        },
        "summary": "penpot services",
    }


def test_penpot_exporter_unit(context: testing.Context[PenpotCharm]):
    """
    arrange: initialize the testing context and set up some penpot units.
    act: retrieve the penpot exporter unit.
    assert: penpot exporter unit is the unit with the least unit number.
    """
    state = testing.State(
        relations={peer_relation(secret_id=PEER_SECRET_ID, peers=(1, 2))},
        containers={penpot_container()},
    )
    with context(context.on.start(), state) as mgr:
        mgr.run()
        charm = mgr.charm
    assert charm._get_penpot_exporter_unit() == "penpot/0"


def test_penpot_create_profile_action(
    monkeypatch: pytest.MonkeyPatch, context: testing.Context[PenpotCharm]
):
    """
    arrange: initialize the testing context and set up all required integrations.
    act: run create-profile charm action.
    assert: ensure correct commands are executed.
    """
    command = [
        "python3",
        "manage.py",
        "create-profile",
        "--email",
        "test@test.com",
        "--fullname",
        "test",
    ]
    state = testing.State(
        containers={penpot_container(include_backend=True, execs={Exec(command)})}
    )

    monkeypatch.setattr("secrets.token_urlsafe", lambda _: "test-password")
    event = context.on.action(
        "create-profile", params={"email": "test@test.com", "fullname": "test"}
    )
    with context(event, state) as mgr:
        mgr.run()
    assert context.action_results == {
        "email": "test@test.com",
        "fullname": "test",
        "password": "test-password",
    }
    exec_args = context.exec_history["penpot"][0]
    assert exec_args.command == command
    assert exec_args.stdin == "test-password\n"


def test_penpot_delete_profile_action(context: testing.Context[PenpotCharm]):
    """
    arrange: initialize the testing context and set up all required integrations.
    act: run delete-profile charm action.
    assert: ensure correct commands are executed.
    """
    command = [
        "python3",
        "manage.py",
        "delete-profile",
        "--email",
        "test@test.com",
    ]
    state = testing.State(
        containers={penpot_container(include_backend=True, execs={Exec(command)})}
    )

    event = context.on.action("delete-profile", params={"email": "test@test.com"})
    with context(event, state) as mgr:
        mgr.run()
    assert context.action_results == {"email": "test@test.com"}
    exec_args = context.exec_history["penpot"][0]
    assert exec_args.command == command
