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
from charms.redis_k8s.v0.redis import RedisRelationCharmEvents, RedisRequires
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
        self.framework.observe(self.on.postgresql_relation_broken, self._reconcile)
        self.framework.observe(self.redis.charm.on.redis_relation_updated, self._reconcile)
        self.framework.observe(self.on.redis_relation_broken, self._reconcile)
        self.framework.observe(self.s3.on.credentials_changed, self._reconcile)
        self.framework.observe(self.s3.on.credentials_gone, self._reconcile)
        self.framework.observe(self.smtp.on.smtp_data_available, self._reconcile)
        self.framework.observe(self.on.smtp_relation_broken, self._reconcile)
        self.framework.observe(self.ingress.on.ready, self._reconcile)
        self.framework.observe(self.ingress.on.revoked, self._reconcile)
        self.framework.observe(self.on.penpot_pebble_ready, self._reconcile)
        self.framework.observe(self.on.create_profile_action, self._on_create_profile_action)
        self.framework.observe(self.on.delete_profile_action, self._on_delete_profile_action)

    def _on_create_profile_action(self, event: ops.ActionEvent) -> None:
        """Handle create-profile action.

        Args:
            event: Action event.
        """
        if (
            not self.container.can_connect()
            or "backend" not in self.container.get_plan().services
            or not self.container.get_service("backend").is_running()
        ):
            event.fail("penpot is not ready")
            return
        email = event.params["email"]
        fullname = event.params["fullname"]
        password = secrets.token_urlsafe(10)
        process = self.container.exec(
            ["python3", "manage.py", "create-profile", "--email", email, "--fullname", fullname],
            service_context="backend",
            stdin=password + "\n",
            combine_stderr=True,
        )
        try:
            process.wait()
        except ops.pebble.ExecError as exc:
            event.fail(typing.cast(str, exc.stdout))
            return
        event.set_results({"email": email, "fullname": fullname, "password": password})

    def _on_delete_profile_action(self, event: ops.ActionEvent) -> None:
        """Handle delete-profile action.

        Args:
            event: Action event.
        """
        if (
            not self.container.can_connect()
            or "backend" not in self.container.get_plan().services
            or not self.container.get_service("backend").is_running()
        ):
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
            event.fail(typing.cast(str, exc.stdout))
            return
        event.set_results({"email": email})

    def _reconcile(self, _: ops.EventBase) -> None:
        """Reconcile penpot services."""
        if not self._check_ready():
            if self.container.can_connect() and self.container.get_services():
                self.container.stop("backend")
                self.container.stop("frontend")
                self.container.stop("exporter")
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

    def _gen_pebble_plan(self) -> ops.pebble.LayerDict:
        """Generate penpot pebble plan.

        Returns:
            Penpot pebble plan.
        """
        plan = ops.pebble.LayerDict(
            summary="penpot services",
            description="penpot services",
            services={
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
                        "PENPOT_PUBLIC_URI": typing.cast(str, self._get_public_uri()),
                        "PENPOT_FLAGS": " ".join(self._get_penpot_backend_options()),
                        **typing.cast(dict[str, str], self._get_penpot_secret_key()),
                        **typing.cast(dict[str, str], self._get_postgresql_credentials()),
                        **typing.cast(dict[str, str], self._get_redis_credentials()),
                        **typing.cast(dict[str, str], self._get_smtp_credentials()),
                        **typing.cast(dict[str, str], self._get_s3_credentials()),
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
                        **typing.cast(dict[str, str], self._get_redis_credentials()),
                    },
                },
            },
            checks={
                "backend-ready": {
                    "override": "replace",
                    "level": "alive",
                    "period": "30s",
                    "exec": {
                        # pylint: disable=line-too-long
                        "command": 'bash -c "pebble services backend | grep -q inactive || curl -f -m 5 localhost:6060/readyz"'  # noqa: E501
                    },
                }
            },
        )
        return plan

    def _check_ready(self) -> bool:
        """Check if penpot is ready to start.

        Returns:
            True if penpot is ready to start.
        """
        requirements = {
            "peer integration": self._get_penpot_secret_key(),
            "postgresql": self._get_postgresql_credentials(),
            "redis": self._get_redis_credentials(),
            "s3": self._get_s3_credentials(),
            "ingress": self._get_public_uri(),
            "penpot container": self.container.can_connect(),
        }
        unfulfilled = sorted([k for k, v in requirements.items() if not v])
        if unfulfilled:
            self.unit.status = ops.BlockedStatus(f"waiting for {', '.join(unfulfilled)}")
            return False
        return True

    def _get_penpot_secret_key(self) -> dict[str, str] | None:
        """Retrieve or generate a Penpot secret key.

        Checks if the Penpot secret key already exists within the peer relation.
        If it does not exist, a new secret key is generated and stored in the peer relation.
        This key is then returned.

        Returns:
            Penpot secret key.
        """
        peer_relation = self.model.get_relation("penpot_peer")
        if peer_relation is None:
            return None
        secret_id = peer_relation.data[self.app].get("secrets")
        if secret_id is None:
            if self.unit.is_leader():
                new_secret = {"penpot-secret-key": secrets.token_urlsafe(64)}
                secret = self.app.add_secret(new_secret)
                secret.set_content(new_secret)
                peer_relation.data[self.app]["secrets"] = typing.cast(str, secret.id)
                return {k.replace("-", "_").upper(): v for k, v in new_secret.items()}
            return None
        secret = self.model.get_secret(id=secret_id)
        return {
            k.replace("-", "_").upper(): v for k, v in secret.get_content(refresh=True).items()
        }

    def _get_postgresql_credentials(self) -> dict[str, str] | None:
        """Get penpot postgresql credentials from the postgresql integration.

        Returns:
            Penpot postgresql credentials.
        """
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
        """Get penpot redis credentials from the redis integration.

        Returns:
            Penpot redis credentials.
        """
        relation = self.model.get_relation("redis")
        if not relation or not relation.app:
            return None
        relation_data = self.redis.relation_data
        if not relation_data:
            return None
        return {"PENPOT_REDIS_URI": self.redis.url}

    def _get_smtp_credentials(self) -> dict[str, str]:
        """Get penpot smtp credentials from the smtp integration.

        Returns:
            Penpot smtp credentials.
        """
        relation = self.model.get_relation("smtp")
        if not relation or not relation.app:
            return {}
        smtp_data = self.smtp.get_relation_data()
        if not smtp_data:
            return {}
        from_address = f"{smtp_data.user or 'no-reply'}@{smtp_data.domain}"
        config_from_address = self.config.get("smtp-from-address")
        if config_from_address:
            from_address = typing.cast(str, config_from_address)
        smtp_credentials = {
            "PENPOT_SMTP_DEFAULT_FROM": from_address,
            "PENPOT_SMTP_DEFAULT_REPLY_TO": from_address,
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
        """Get penpot s3 credentials from the s3 integration.

        Returns:
            Penpot s3 credentials.
        """
        relation = self.model.get_relation("s3")
        if not relation or not relation.app:
            return None
        s3_data = self.s3.get_s3_connection_info()
        if not s3_data or "access-key" not in s3_data:
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
        """Get penpot public URI.

        Returns:
            Penpot public URI.
        """
        return self.ingress.url

    def _get_penpot_frontend_options(self) -> list[str]:
        """Retrieve the penpot options for the penpot frontend.

        Returns:
            Penpot frontend options.
        """
        return sorted(
            [
                "enable-login-with-password",
                "disable-registration",
                "disable-onboarding-questions",
            ]
        )

    def _get_penpot_backend_options(self) -> list[str]:
        """Retrieve the penpot options for the penpot backend.

        Returns:
            Penpot backend options.
        """
        return sorted(
            [
                "enable-login-with-password",
                "enable-prepl-server",
                "disable-registration",
                "disable-telemetry",
                "disable-onboarding-questions",
                "disable-log-emails",
                ("enable" if self._get_smtp_credentials() else "disable") + "-smtp",
            ]
        )

    def _get_local_resolver(self) -> str:
        """Retrieve the current nameserver address being used.

        Returns:
            The address of the nameserver.
        """
        kube_dns = f"kube-dns.kube-system.svc.{self._get_kubernetes_cluster_domain()}"
        try:
            dns.resolver.resolve(kube_dns, search=True)
            return kube_dns
        except dns.exception.DNSException:
            # resolvers like dns-over-https, not likely to happen in Kubernetes
            return typing.cast(str, dns.resolver.Resolver().nameservers[0])

    def _get_penpot_exporter_unit(self) -> str:
        """Retrieve the name of the unit designated to run the penpot exporter.

        Returns:
            Exporter unit name.
        """
        relation = typing.cast(ops.Relation, self.model.get_relation("penpot_peer"))
        units = list(relation.units)
        units.append(self.unit)
        return sorted(units, key=lambda u: int(u.name.split("/")[-1]))[0].name

    def _get_penpot_exporter_uri(self) -> str:
        """Retrieve the address of the unit designated to run the penpot exporter.

        Returns:
            Exporter unit address.
        """
        unit_name = self._get_penpot_exporter_unit().replace("/", "-")
        k8s_domain = self._get_kubernetes_cluster_domain()
        hostname = f"{unit_name}.{self.app.name}-endpoints.{self.model.name}.svc.{k8s_domain}"
        return f"http://{hostname}:6061"

    def _get_kubernetes_cluster_domain(self) -> str:
        """Get Kubernetes cluster domain name.

        Returns:
            Kubernetes cluster domain name.
        """
        try:
            answers = dns.resolver.resolve("kubernetes.default.svc", search=True)
        except dns.exception.DNSException:
            return "cluster.local"
        return answers.qname.to_text().removeprefix("kubernetes.default.svc").strip(".")


if __name__ == "__main__":  # pragma: nocover
    ops.main.main(PenpotCharm)
