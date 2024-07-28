#!/usr/bin/env python3

# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

# Learn more at: https://juju.is/docs/sdk

"""Penpot charm service."""

import logging
import secrets
import typing

import dns.resolver
import ops

from charms.data_platform_libs.v0.data_interfaces import DatabaseRequires
from charms.data_platform_libs.v0.s3 import S3Requirer
from charms.redis_k8s.v0.redis import RedisRequires, RedisRelationCharmEvents
from charms.smtp_integrator.v0.smtp import SmtpRequires, TransportSecurity
from charms.traefik_k8s.v2.ingress import IngressPerAppRequirer

logger = logging.getLogger(__name__)


class PenpotCharm(ops.CharmBase):
    """Charm the service."""

    on = RedisRelationCharmEvents()

    def __init__(self, *args: typing.Any):
        """Construct.

        Args:
            args: Arguments passed to the CharmBase parent constructor.
        """
        super().__init__(*args)
        self.container = self.unit.get_container("penpot")
        self.postgresql = DatabaseRequires(
            self, relation_name="postgresql", database_name=self.app.name
        )
        self.redis = RedisRequires(self, "redis")
        self.smtp = SmtpRequires(self)
        self.s3 = S3Requirer(self, relation_name="s3")
        self.ingress = IngressPerAppRequirer(self, port=8080)
        self.framework.observe(self.on.config_changed, self._reconcile)
        self.framework.observe(self.on.penpot_peer_relation_created, self._reconcile)
        self.framework.observe(self.on.penpot_peer_relation_changed, self._reconcile)
        self.framework.observe(self.on.secret_changed, self._reconcile)
        self.framework.observe(self.postgresql.on.database_created, self._reconcile)
        self.framework.observe(self.postgresql.on.endpoints_changed, self._reconcile)
        self.framework.observe(self.redis.charm.on.redis_relation_updated, self._reconcile)
        self.framework.observe(self.s3.on.credentials_changed, self._reconcile)
        self.framework.observe(self.s3.on.credentials_gone, self._reconcile)
        self.framework.observe(self.ingress.on.ready, self._reconcile)
        self.framework.observe(self.ingress.on.revoked, self._reconcile)
        self.framework.observe(self.on.penpot_pebble_ready, self._reconcile)
        self.framework.observe(self.on.create_profile_action, self._on_create_profile_action)
        self.framework.observe(self.on.delete_profile_action, self._on_delete_profile_action)

    def _on_create_profile_action(self, event: ops.ActionEvent):
        if not self.container.can_connect() or "backend" not in self.container.get_plan().services:
            event.fail("penpot is not ready")
            return
        email = event.params["email"]
        fullname = event.params["fullname"]
        password = secrets.token_urlsafe(8)
        process = self.container.exec(
            ["python3", "manage.py", "create-profile", "--email", email, "--fullname", fullname],
            service_context="backend",
            stdin=password + "\n",
            combine_stderr=True,
        )
        try:
            process.wait()
        except ops.pebble.ExecError as exc:
            event.fail(exc.stdout)
            return
        event.set_results({"email": email, "fullname": fullname, "password": password})

    def _on_delete_profile_action(self, event: ops.ActionEvent):
        if not self.container.can_connect() or "backend" not in self.container.get_plan().services:
            event.fail("penpot is not ready")
            return
        email = event.params["email"]
        process = self.container.exec(
            ["python3", "manage.py", "delete-profile", "--email", email],
            service_context="backend",
            combine_stderr=True,
        )
        try:
            process.wait()
        except ops.pebble.ExecError as exc:
            event.fail(exc.stdout)
            return
        event.set_results({"email": email})

    def _get_penpot_secret_key(self) -> dict[str, str] | None:
        peer_relation = self.model.get_relation("penpot_peer")
        if peer_relation is None:
            return None
        secret_id = peer_relation.data[self.app].get("secrets")
        if secret_id is None:
            if self.unit.is_leader():
                new_secret = {"penpot-secret-key": secrets.token_urlsafe(64)}
                secret = self.app.add_secret(new_secret)
                secret.set_content(new_secret)
                peer_relation.data[self.app]["secrets"] = secret.id
                return {k.replace("-", "_").upper(): v for k, v in new_secret.items()}
            else:
                return
        secret = self.model.get_secret(id=secret_id)
        return {
            k.replace("-", "_").upper(): v for k, v in secret.get_content(refresh=True).items()
        }

    def _get_postgresql_credentials(self) -> dict[str, str] | None:
        relation = self.model.get_relation("postgresql")
        if not relation or not relation.app:
            return None
        endpoint = self.postgresql.fetch_relation_field(relation.id, "endpoints")
        database = self.postgresql.fetch_relation_field(relation.id, "database")
        username = self.postgresql.fetch_relation_field(relation.id, "username")
        password = self.postgresql.fetch_relation_field(relation.id, "password")
        if not all((endpoint, database, username, password)):
            return None
        return {
            "PENPOT_DATABASE_URI": f"postgresql://{endpoint}/{database}",
            "PENPOT_DATABASE_USERNAME": username,
            "PENPOT_DATABASE_PASSWORD": password,
        }

    def _get_redis_credentials(self) -> dict[str, str] | None:
        relation = self.model.get_relation("redis")
        if not relation or not relation.app:
            return None
        relation_data = self.redis.relation_data
        if not relation_data:
            return None
        return {"PENPOT_REDIS_URI": self.redis.url}

    def _get_smtp_credentials(self) -> dict[str, str] | None:
        relation = self.model.get_relation("smtp")
        if not relation or not relation.app:
            return None
        smtp_data = self.smtp.get_relation_data()
        if not smtp_data:
            return None
        smtp_credentials = {
            "PENPOT_SMTP_DEFAULT_FROM": "no-reply@example.com",
            "PENPOT_SMTP_DEFAULT_REPLY_TO": "no-reply@example.com",
            "PENPOT_SMTP_HOST": smtp_data.host,
            "PENPOT_SMTP_PORT": str(smtp_data.port),
            "PENPOT_SMTP_TLS": "false",
            "PENPOT_SMTP_SSL": "false",
        }
        if smtp_data.user:
            smtp_credentials["PENPOT_SMTP_USERNAME"] = smtp_data.user
        if smtp_data.password:
            smtp_credentials["PENPOT_SMTP_PASSWORD"] = smtp_data.password
        if smtp_data.password_id:
            password_secret = self.model.get_secret(id=smtp_data.password_id)
            password_secret_content = password_secret.get_content(refresh=True)
            smtp_credentials["PENPOT_SMTP_PASSWORD"] = password_secret_content["password"]
        if smtp_data.transport_security == TransportSecurity.TLS:
            smtp_credentials["PENPOT_SMTP_TLS"] = "true"
        if smtp_data.transport_security == TransportSecurity.STARTTLS:
            smtp_credentials["PENPOT_SMTP_SSL"] = "true"
        return smtp_credentials

    def _get_s3_credentials(self) -> dict[str, str] | None:
        relation = self.model.get_relation("s3")
        if not relation or not relation.app:
            return None
        s3_data = self.s3.get_s3_connection_info()
        if not s3_data:
            return None
        return {
            "AWS_ACCESS_KEY_ID": s3_data["access-key"],
            "AWS_SECRET_ACCESS_KEY": s3_data["secret-key"],
            "PENPOT_ASSETS_STORAGE_BACKEND": "assets-s3",
            "PENPOT_STORAGE_ASSETS_S3_REGION": s3_data.get("region", "us-east-1"),
            "PENPOT_STORAGE_ASSETS_S3_BUCKET": s3_data["bucket"],
            "PENPOT_STORAGE_ASSETS_S3_ENDPOINT": s3_data["endpoint"],
        }

    def _get_public_uri(self) -> str | None:
        return self.ingress.url

    def _check_ready(self) -> bool:
        if not self._get_penpot_secret_key():
            self.unit.status = ops.WaitingStatus("waiting for peer integration")
            return False
        if not self._get_postgresql_credentials():
            self.unit.status = ops.WaitingStatus("waiting for postgresql")
            return False
        if not self._get_redis_credentials():
            self.unit.status = ops.WaitingStatus("waiting for redis")
            return False
        if not self._get_smtp_credentials():
            self.unit.status = ops.WaitingStatus("waiting for smtp")
            return False
        if not self._get_s3_credentials():
            self.unit.status = ops.WaitingStatus("waiting for s3")
            return False
        if not self._get_public_uri():
            self.unit.status = ops.WaitingStatus("waiting for ingress")
            return False
        if not self.container.can_connect():
            self.unit.status = ops.WaitingStatus("waiting for penpot container")
            return False
        return True

    def _get_penpot_frontend_options(self) -> list[str]:
        return [
            "enable-login-with-password",
            "disable-registration",
            "disable-onboarding-questions",
        ]

    def _get_penpot_backend_options(self) -> list[str]:
        return [
            "enable-login-with-password",
            "enable-smtp",
            "enable-prepl-server",
            "disable-registration",
            "disable-onboarding-questions",
            # TODO: remove me
            "disable-secure-session-cookies",
        ]

    def _get_local_resolver(self) -> str:
        return dns.resolver.Resolver().nameservers[0]

    def _get_penpot_exporter_unit(self):
        relation = self.model.get_relation("penpot_peer")
        units = list(relation.units)
        units.append(self.unit)
        return sorted(units, key=lambda u: int(u.name.split("/")[-1]))[0].name

    def _get_penpot_exporter_uri(self):
        unit_name = self._get_penpot_exporter_unit().replace("/", "-")
        return f"http://{unit_name}.{self.app.name}-endpoints.{self.model.name}.svc.cluster.local:6061"

    def _gen_pebble_plan(self) -> dict:
        plan = {
            "summary": "penpot services",
            "description": "penpot services",
            "services": {
                "frontend": {
                    "command": './nginx-entrypoint.sh nginx -g "daemon off;"',
                    "working-dir": "/opt/penpot/frontend/",
                    "override": "replace",
                    "after": ["backend"],
                    "environment": {
                        "PENPOT_BACKEND_URI": "http://127.0.0.1:6060",
                        "PENPOT_EXPORTER_URI": self._get_penpot_exporter_uri(),
                        "PENPOT_INTERNAL_RESOLVER": self._get_local_resolver(),
                        "PENPOT_FLAGS": " ".join(self._get_penpot_frontend_options()),
                    },
                },
                "backend": {
                    "command": "/opt/penpot/backend/run.sh",
                    "override": "replace",
                    "working-dir": "/opt/penpot/backend/",
                    "environment": {
                        "JAVA_HOME": "/usr/lib/jvm/java-21-openjdk-amd64",
                        "PENPOT_TELEMETRY_ENABLED": "false",
                        "PENPOT_PUBLIC_URI": self._get_public_uri(),
                        "PENPOT_FLAGS": " ".join(self._get_penpot_backend_options()),
                        **self._get_penpot_secret_key(),
                        **self._get_postgresql_credentials(),
                        **self._get_redis_credentials(),
                        **self._get_smtp_credentials(),
                        **self._get_s3_credentials(),
                    },
                },
                "exporter": {
                    "command": "node app.js",
                    "working-dir": "/opt/penpot/exporter/",
                    "override": "replace",
                    "after": ["backend", "frontend"],
                    "environment": {
                        "PENPOT_PUBLIC_URI": "http://127.0.0.1:8080",
                        "PLAYWRIGHT_BROWSERS_PATH": "/opt/penpot/exporter/browsers",
                        **self._get_redis_credentials(),
                    },
                },
            },
        }
        return plan

    def _reconcile(self, _: ops.EventBase) -> None:
        """Handle changed configuration."""
        if not self._check_ready():
            return
        self.container.add_layer("penpot", self._gen_pebble_plan(), combine=True)
        self.container.replan()
        self.container.start("backend")
        self.container.start("frontend")
        if self.unit.name == self._get_penpot_exporter_unit():
            self.container.start("exporter")
        else:
            self.container.stop("exporter")
        self.unit.status = ops.ActiveStatus()


if __name__ == "__main__":  # pragma: nocover
    ops.main.main(PenpotCharm)
