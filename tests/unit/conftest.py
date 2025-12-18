# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Unit tests fixtures."""

import json
import unittest.mock

import ops.testing
import pytest

from src.charm import PenpotCharm


class Harness:
    """Unit test helper."""

    def __init__(self, harness: ops.testing.Harness) -> None:
        """Initialize harness.

        Args:
            harness: ops.testing.Harness object.
        """
        self.harness = harness
        self.harness.set_model_name("test")

    def setup_postgresql_integration(self):
        """Setup postgresql integration."""
        postgresql_app = "postgresql"
        secret_id = self.harness.add_model_secret(
            postgresql_app, {"username": "postgresql-username", "password": "postgresql-password"}
        )
        relation_id = self.harness.add_relation("postgresql", postgresql_app)
        self.harness.grant_secret(secret_id, self.harness.charm.app)
        self.harness.add_relation_unit(relation_id, f"{postgresql_app}/0")
        self.harness.update_relation_data(
            relation_id,
            postgresql_app,
            {
                "data": json.dumps(
                    {
                        "database": "penpot",
                        "requested-secrets": json.dumps(["username", "password"]),
                    }
                ),
                "database": "penpot",
                "endpoints": "postgresql-endpoint:5432",
                "secret-user": secret_id,
                "version": "14.11",
            },
        )

    def setup_redis_integration(self):
        """Setup redis integration."""
        self.harness.add_relation(
            "redis", "redis", unit_data={"hostname": "redis-hostname", "port": "6379"}
        )

    def setup_s3_integration(self):
        """Setup s3 integration."""
        self.harness.add_relation(
            "s3",
            "s3-integrator",
            app_data={
                "access-key": "s3-access-key",
                "bucket": "penpot",
                "endpoint": "s3-endpoint",
                "secret-key": "s3-secret-key",
            },
        )

    def setup_ingress_integration(self):
        """Setup ingress integration."""
        self.harness.add_network("10.0.0.10")
        self.harness.add_relation(
            "ingress",
            "nginx-ingress-integrator",
            app_data={"ingress": '{"url": "https://penpot.local/"}'},
        )

    def setup_smtp_integration(self, use_password: bool = False):
        """Setup smtp integration.

        Args:
            use_password: use user/password authentication.
        """
        smtp_integrator_app = "smtp-integrator"
        if use_password:
            secret_id = self.harness.add_model_secret(
                smtp_integrator_app,
                {"username": "smtp-username", "password": "smtp-password"},
            )
        relation_id = self.harness.add_relation("smtp", smtp_integrator_app)
        if use_password:
            self.harness.grant_secret(secret_id, self.harness.charm.app)
        self.harness.add_relation_unit(relation_id, f"{smtp_integrator_app}/0")
        app_data = {
            "auth_type": "plain" if use_password else "none",
            "domain": "example.com",
            "host": "smtp-host",
            "port": "1025",
            "transport_security": "none",
        }
        if use_password:
            app_data["user"] = "smtp-user"
            app_data["password_id"] = secret_id
        self.harness.update_relation_data(relation_id, smtp_integrator_app, app_data)

    def setup_integration(self):
        """Setup all integrations."""
        self.setup_postgresql_integration()
        self.setup_redis_integration()
        self.setup_s3_integration()
        self.setup_ingress_integration()
        self.setup_smtp_integration()

    def __getattr__(self, attr):
        """Proxy ops.testing.Harness.

        Args:
            attr: attribute name.

        Returns:
            Proxied attribute.
        """
        return getattr(self.harness, attr)


@pytest.fixture(name="harness")
def harness_fixture(monkeypatch):
    """Harness fixture."""
    monkeypatch.setenv("JUJU_VERSION", "3.5.0")
    with unittest.mock.patch.object(
        PenpotCharm, "_check_penpot_backend_ready", unittest.mock.MagicMock(return_value=True)
    ):
        yield Harness(ops.testing.Harness(PenpotCharm))
