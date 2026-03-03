#!/usr/bin/env python3

# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Framework-agnostic helpers for integration tests."""

import asyncio
import logging
import time
from typing import Any

import jubilant
import pytest
import requests
from oauth_tools.oauth_helpers import (
    access_application_login_page,
    click_on_sign_in_button_by_text,
    complete_auth_code_login,
    deploy_identity_bundle,
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

    def __init__(self, ops_test: Any, juju_on_ops_model: jubilant.Juju):
        self._ops_test = ops_test
        self._juju = juju_on_ops_model

    @property
    def model_name(self) -> str:
        assert self._juju.model is not None
        return self._juju.model

    @property
    def keep_model(self) -> bool:
        """Return whether integration model retention is enabled."""
        return bool(getattr(self._ops_test, "keep_model", False))

    async def deploy_application(self, *args, **kwargs):
        """Deploy an application through the active model."""
        assert self._ops_test.model
        return await self._ops_test.model.deploy(*args, **kwargs)

    async def add_relation(self, endpoint1: str, endpoint2: str):
        """Add a relation through the active model."""
        self._juju.integrate(endpoint1, endpoint2)

    async def wait_for_idle(self, **kwargs):
        """Wait for model idle through the active model."""
        apps = kwargs.get("apps")
        status = kwargs.get("status")
        timeout = kwargs.get("timeout", 300)
        raise_on_error = kwargs.get("raise_on_error", True)
        supported_keys = {"apps", "status", "timeout", "raise_on_error"}

        if (
            set(kwargs.keys()).issubset(supported_keys)
            and apps
            and status == "active"
            and raise_on_error is True
        ):
            self._juju.wait(lambda current: jubilant.all_active(current, *apps), timeout=timeout)
            return

        assert self._ops_test.model
        return await self._ops_test.model.wait_for_idle(**kwargs)

    async def deploy_identity_bundle(self, bundle_url: str, ext_idp_service: Any):
        """Deploy the oauth identity bundle using the oauth_tools compatibility path."""
        try:
            return await asyncio.wait_for(
                deploy_identity_bundle(
                    ops_test=self._ops_test,
                    bundle_url=bundle_url,
                    ext_idp_service=ext_idp_service,
                ),
                timeout=IDENTITY_DEPLOY_TIMEOUT,
            )
        except asyncio.TimeoutError:
            raise TimeoutError(
                f"Identity bundle deployment exceeded {IDENTITY_DEPLOY_TIMEOUT}s"
            )

    async def refresh_application_revision(self, application: str, revision: int):
        """Refresh an application to a specific revision via Juju CLI."""
        return await self._ops_test.juju("refresh", application, "--revision", str(revision))

    async def complete_auth_code_login(self, page, ext_idp_service: Any):
        """Complete oauth_tools auth-code login using the underlying harness."""
        return await complete_auth_code_login(
            page=page,
            ops_test=self._ops_test,
            ext_idp_service=ext_idp_service,
        )

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
        stdin: str | bytes | None = None,
    ):
        """Run an ssh command in a unit container through the harness Juju CLI."""
        return await self._ops_test.juju(
            "ssh",
            "--container",
            container,
            unit_name,
            *command,
            stdin=stdin,
        )

    def get_application(self, name: str):
        """Fetch an application from the active model by name."""
        assert self._ops_test.model
        return self._ops_test.model.applications[name]

    def get_unit(self, app_name: str, unit_index: int = 0):
        """Fetch an application unit by index from the active model."""
        return self.get_application(app_name).units[unit_index]

    async def run_unit_action(self, unit, action_name: str, **params):
        """Run an action on a unit and wait for completion."""
        action = await unit.run_action(action_name, **params)
        await action.wait()
        return action

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


def build_ops_model_facade(ops_test: Any) -> OpsJubilantFacade:
    """Build a migration facade bound to the active integration model."""
    assert ops_test.model
    juju_on_current_model = jubilant.Juju(model=ops_test.model.name)
    return OpsJubilantFacade(ops_test=ops_test, juju_on_ops_model=juju_on_current_model)
