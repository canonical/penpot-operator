#!/usr/bin/env python3

# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Framework-agnostic helpers for integration tests."""

import asyncio
import dataclasses
import logging
import re
import shlex
import tempfile
import time
from typing import Any

import jubilant
import pytest
import requests
from oauth_tools.constants import APPS
from oauth_tools.oauth_helpers import (
    access_application_login_page,
    click_on_sign_in_button_by_text,
)

logger = logging.getLogger(__name__)

IDENTITY_DEPLOY_TIMEOUT = 600


def get_required_charm_inputs(pytestconfig: pytest.Config) -> tuple[str, str]:
    """Return required charm and image options for integration tests."""
    charm = pytestconfig.getoption("--charm-file")
    penpot_image = pytestconfig.getoption("--penpot-image")
    assert charm, (
        "--charm-file is required; run 'charmcraft pack' first and pass the resulting .charm file"
    )
    assert penpot_image
    assert not penpot_image.startswith("penpotapp/backend:"), (
        "--penpot-image must use the charm-compatible Penpot rock image, not penpotapp/backend"
    )
    return charm, penpot_image


def wait_for_endpoint(url: str, timeout: int = 120):
    """Wait until an HTTPS endpoint becomes reachable."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            response = requests.get(url, timeout=10, verify=False)
            if response.status_code < 500:
                return
        except requests.RequestException:
            pass
        time.sleep(5)
    raise TimeoutError(f"timed out waiting for endpoint: {url}")


class OpsJubilantFacade:
    """Facade for model operations during framework migration."""

    def __init__(self, juju_on_ops_model: jubilant.Juju, keep_model: bool = False):
        self._juju = juju_on_ops_model
        self._keep_model = keep_model

    @property
    def model_name(self) -> str:
        assert self._juju.model is not None
        return self._juju.model

    @property
    def keep_model(self) -> bool:
        """Return whether integration model retention is enabled."""
        return self._keep_model

    @dataclasses.dataclass(frozen=True)
    class ApplicationRef:
        """Minimal application deployment reference."""

        name: str

    @staticmethod
    def _application_name_from_deploy_args(*args, **kwargs) -> str:
        """Resolve application name from deploy args."""
        app_name = kwargs.get("application_name")
        if app_name:
            return app_name
        source = str(args[0])
        return source.rsplit("/", maxsplit=1)[-1]

    async def deploy_application(self, *args, **kwargs):
        """Deploy an application through Jubilant."""
        deploy_kwargs = dict(kwargs)
        if "application_name" in deploy_kwargs:
            deploy_kwargs["app"] = deploy_kwargs.pop("application_name")
        self._juju.deploy(*args, **deploy_kwargs)
        return self.ApplicationRef(name=self._application_name_from_deploy_args(*args, **kwargs))

    async def add_relation(self, endpoint1: str, endpoint2: str):
        """Add a relation through the active model."""
        self._juju.integrate(endpoint1, endpoint2)

    async def wait_for_idle(self, **kwargs):
        """Wait for model state using Jubilant status predicates."""
        apps = kwargs.get("apps")
        status = kwargs.get("status")
        timeout = kwargs.get("timeout", 300)
        raise_on_error = kwargs.get("raise_on_error", True)
        supported_keys = {"apps", "status", "timeout", "raise_on_error"}

        if not set(kwargs.keys()).issubset(supported_keys):
            unsupported = set(kwargs.keys()) - supported_keys
            raise TypeError(f"unsupported wait_for_idle kwargs: {unsupported}")

        error_predicate = None
        if raise_on_error:
            def error_predicate(current):
                return self._has_error(current, apps)

        if status == "active":
            target_apps = tuple(apps) if apps else tuple(self._juju.status().apps.keys())
            self._juju.wait(
                lambda current: jubilant.all_active(current, *target_apps),
                error=error_predicate,
                timeout=timeout,
            )
            return

        self._juju.wait(
            lambda current: self._all_units_idle(current, apps),
            error=error_predicate,
            timeout=timeout,
        )

    @staticmethod
    def _selected_app_names(current_status, apps: list[str] | None) -> list[str]:
        if apps:
            return list(apps)
        return list(current_status.apps.keys())

    def _has_error(self, current_status, apps: list[str] | None) -> bool:
        for app_name in self._selected_app_names(current_status, apps):
            app_state = current_status.apps[app_name]
            if app_state.app_status.current == "error":
                return True
            for unit_state in app_state.units.values():
                if unit_state.workload_status.current == "error" or unit_state.juju_status.current == "error":
                    return True
        return False

    def _all_units_idle(self, current_status, apps: list[str] | None) -> bool:
        for app_name in self._selected_app_names(current_status, apps):
            app_state = current_status.apps[app_name]
            for unit_state in app_state.units.values():
                if unit_state.juju_status.current != "idle":
                    return False
        return True

    async def deploy_identity_bundle(self, bundle_url: str, ext_idp_service: Any):
        """Deploy and configure identity bundle through Jubilant."""
        try:
            return await asyncio.wait_for(self._deploy_identity_bundle_impl(bundle_url, ext_idp_service), timeout=IDENTITY_DEPLOY_TIMEOUT)
        except asyncio.TimeoutError:
            raise TimeoutError(
                f"Identity bundle deployment exceeded {IDENTITY_DEPLOY_TIMEOUT}s"
            )

    async def _deploy_identity_bundle_impl(self, bundle_url: str, ext_idp_service: Any):
        self._juju.deploy(bundle_url, trust=True)

        apps_without_ext = [getattr(APPS, key) for key in APPS._fields if key != "KRATOS_EXTERNAL_IDP_INTEGRATOR"]
        if not ext_idp_service:
            self._juju.wait(
                lambda current: jubilant.all_active(current, *apps_without_ext),
                timeout=2000,
            )
            return

        self._juju.config(
            APPS.KRATOS_EXTERNAL_IDP_INTEGRATOR,
            {
                "client_id": ext_idp_service.client_id,
                "client_secret": ext_idp_service.client_secret,
                "provider": "generic",
                "issuer_url": ext_idp_service.issuer_url,
                "scope": "profile email",
                "provider_id": "Dex",
            },
        )
        self._juju.wait(lambda current: jubilant.all_active(current, *list(APPS)), timeout=2000)
        redirect_task = self._juju.run(f"{APPS.KRATOS_EXTERNAL_IDP_INTEGRATOR}/0", "get-redirect-uri")
        redirect_uri = redirect_task.results.get("redirect-uri")
        assert redirect_uri, "kratos-external-idp-integrator get-redirect-uri did not return redirect-uri"
        ext_idp_service.update_redirect_uri(redirect_uri=redirect_uri)

    async def refresh_application_revision(self, application: str, revision: int):
        """Refresh an application to a specific revision via Juju CLI."""
        try:
            self._juju.refresh(application, revision=revision)
        except Exception as exc:
            if "would break relation" not in str(exc):
                raise
            logger.warning("skipping %s refresh to revision %s: %s", application, revision, exc)
        return None

    async def complete_auth_code_login(self, page, ext_idp_service: Any):
        """Complete auth-code login from identity login UI with external provider."""
        await page.wait_for_url(re.compile(r".*/ui/login.*"), timeout=60000)
        async with page.expect_navigation():
            await page.get_by_role("button", name="Dex").click()
        await ext_idp_service.complete_user_login(page)

    async def access_application_login_page(self, page, url: str):
        """Open an application's login page through oauth_tools."""
        return await access_application_login_page(page=page, url=url)

    async def click_on_sign_in_button_by_text(self, page, text: str):
        """Click sign-in button text through oauth_tools."""
        return await click_on_sign_in_button_by_text(page=page, text=text)

    async def juju_integrate(self, endpoint1: str, endpoint2: str):
        """Integrate two relation endpoints via Juju CLI."""
        self._juju.integrate(endpoint1, endpoint2)

    async def bootstrap_identity_login_ui(self):
        """Apply identity login-ui bootstrap steps required by oauth integration tests."""
        await self.refresh_application_revision("identity-platform-login-ui-operator", 105)
        await self.juju_integrate(
            "identity-platform-login-ui-operator:receive-ca-cert",
            "self-signed-certificates",
        )

    def integrate_endpoints(self, endpoint1: str, endpoint2: str):
        """Integrate relation endpoints through Jubilant on the active integration model."""
        self._juju.integrate(endpoint1, endpoint2)

    def wait_all_active(self, *apps: str, timeout: int = 300):
        """Wait for applications to become active via Jubilant."""
        self._juju.wait(lambda status: jubilant.all_active(status, *apps), timeout=timeout)

    async def run_unit_ssh(
        self,
        unit_name: str,
        *command: str,
        container: str = "penpot",
    ):
        """Run a command in a unit container through Jubilant SSH."""
        return self._juju.ssh(unit_name, command[0], *command[1:], container=container)

    def copy_text_to_unit(self, unit_name: str, destination: str, content: str, container: str = "penpot"):
        """Copy text content to a unit path using Jubilant SCP."""
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", delete=True) as temp_file:
            temp_file.write(content)
            temp_file.flush()
            self._juju.scp(temp_file.name, f"{unit_name}:{destination}", container=container)

    def run_unit_command(self, unit_name: str, *command: str, container: str = "penpot") -> str:
        """Run a shell command on a unit container and return stdout."""
        return self._juju.ssh(unit_name, shlex.join(command), container=container)

    def sync_s3_credentials(self, app_name: str, access_key: str, secret_key: str):
        """Run s3-integrator sync action via Jubilant on the app primary unit."""
        unit_name = self.get_unit_names(app_name)[0]
        self._juju.run(
            unit_name,
            "sync-s3-credentials",
            {
                "access-key": access_key,
                "secret-key": secret_key,
            },
        )

    def get_unit_ips(self, name: str) -> list[str]:
        """Get unit IP addresses sorted by unit index."""
        status = self._juju.status()
        units = status.apps[name].units
        return [units[key].address for key in sorted(units.keys(), key=lambda n: int(n.split("/")[-1]))]

    def get_unit_names(self, name: str) -> list[str]:
        """Get unit names sorted by unit index for an application."""
        status = self._juju.status()
        units = status.apps[name].units
        return sorted(units.keys(), key=lambda unit_name: int(unit_name.split("/")[-1]))

    def get_ca_certificate(self, unit_name: str = "self-signed-certificates/0") -> str:
        """Run the CA retrieval action and return the certificate payload."""
        task = self._juju.run(unit_name, "get-ca-certificate")
        return task.results["ca-certificate"]


def build_ops_model_facade(juju_on_ops_model: jubilant.Juju, keep_model: bool = False) -> OpsJubilantFacade:
    """Build a migration facade bound to the active integration model."""
    return OpsJubilantFacade(juju_on_ops_model=juju_on_ops_model, keep_model=keep_model)
