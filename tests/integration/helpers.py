# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Framework-agnostic helpers for integration tests."""

import json
import logging
import time
import urllib.parse

import jubilant
import requests
from tenacity import (
    RetryError,
    Retrying,
    retry,
    retry_if_exception_type,
    retry_if_result,
    stop_after_delay,
    wait_fixed,
)

logger = logging.getLogger(__name__)

MIGRATION_APPS = ("hydra", "kratos")
MIGRATION_ACTION_TIMEOUT = 300
MIGRATION_MAX_ATTEMPTS = 2
# "Waiting for database migration" is also shown while the automatic migration is still
# running, so only act on it once it has persisted for this long.
MIGRATION_STUCK_GRACE = 300


def _migration_state(status: jubilant.Status, app: str) -> str | None:
    """Return "requested", "waiting" or None depending on the app's migration status."""
    app_status = status.apps.get(app)
    if app_status is None:
        return None
    statuses = [
        app_status.app_status,
        *(unit.workload_status for unit in app_status.units.values()),
    ]
    state = None
    for item in statuses:
        if item.current not in ("waiting", "blocked"):
            continue
        if "run-migration" in item.message:
            return "requested"
        if "database migration" in item.message.lower():
            state = "waiting"
    return state


def _run_migration(juju: jubilant.Juju, app: str, attempt: int) -> None:
    """Run the run-migration action on the app leader, logging which charm it ran for."""
    logger.info("running run-migration on %s/leader (attempt %d)", app, attempt)
    try:
        juju.run(
            f"{app}/leader",
            "run-migration",
            {"timeout": MIGRATION_ACTION_TIMEOUT},
            wait=MIGRATION_ACTION_TIMEOUT + 60,
        )
    except (jubilant.TaskError, TimeoutError) as exc:
        logger.warning("run-migration on %s failed: %s", app, exc)
    else:
        logger.info("run-migration on %s completed", app)


def wait_for_active_with_migrations(juju: jubilant.Juju, apps: set[str], timeout: int = 900):
    """Wait for all apps to be active, running hydra/kratos migrations if they get stuck.

    Hydra and kratos only auto-migrate a fresh database; if that fails on a slow runner
    they wait for the run-migration action.
    """
    attempts = dict.fromkeys(MIGRATION_APPS, 0)
    waiting_since: dict[str, float] = {}

    @retry(
        stop=stop_after_delay(timeout),
        wait=wait_fixed(10),
        retry=retry_if_result(lambda active: not active),
    )
    def check() -> bool:
        status = juju.status()
        if jubilant.all_active(status, *apps):
            return True
        now = time.monotonic()
        for app in MIGRATION_APPS:
            if app not in apps or attempts[app] >= MIGRATION_MAX_ATTEMPTS:
                continue
            state = _migration_state(status, app)
            if state is None:
                waiting_since.pop(app, None)
                continue
            stuck_for = now - waiting_since.setdefault(app, now)
            if state == "requested" or stuck_for >= MIGRATION_STUCK_GRACE:
                attempts[app] += 1
                waiting_since.pop(app, None)
                _run_migration(juju, app, attempts[app])
        return False

    try:
        check()
    except RetryError as error:
        raise TimeoutError(f"timed out after {timeout}s\n{juju.status()}") from error


def wait_for_endpoint(url: str, timeout: int = 120, headers: dict[str, str] | None = None):
    """Wait until an HTTPS endpoint becomes reachable."""
    try:
        for attempt in Retrying(
            stop=stop_after_delay(timeout),
            wait=wait_fixed(5),
            retry=retry_if_exception_type((requests.RequestException, RuntimeError)),
        ):
            with attempt:
                response = requests.get(url, headers=headers, timeout=10, verify=False)  # nosec: B501
                if response.status_code >= 500:
                    raise RuntimeError(f"endpoint not ready: {response.status_code}")
    except RetryError as error:
        raise TimeoutError(f"timed out waiting for endpoint: {url}") from error


def get_public_url(juju: jubilant.Juju, app: str = "penpot") -> str:
    """Get the public URL of an app from traefik-public proxied endpoints."""
    result = juju.run("traefik-public/0", "show-proxied-endpoints")
    endpoints = json.loads(result.results["proxied-endpoints"])
    url = endpoints.get(app, {}).get("url", "")
    if not url:
        raise RuntimeError(f"could not get {app} URL from traefik-public proxied endpoints")
    url = url.rstrip("/")
    parsed = urllib.parse.urlsplit(url)
    # In Traefik subdomain routing mode, this action can report a root
    # URL even when the real route is model-app.<external-hostname>.
    if parsed.path in ("", "/"):
        traefik_url = endpoints.get("traefik-public", {}).get("url", "")
        traefik_parsed = urllib.parse.urlsplit(traefik_url)
        if traefik_parsed.hostname:
            return f"{parsed.scheme}://{juju.model}-{app}.{traefik_parsed.hostname}"
    return url


def wait_for_config_flags(url: str, flags: list[str], timeout: int = 120) -> None:
    """Wait until the Penpot frontend config.js exposes the given flags.

    The OIDC login button only renders when the frontend has been reconfigured
    with ``enable-login-with-oidc``. Waiting for the flag here avoids a
    confusing 30s "button not visible" timeout when the frontend is still
    serving its previous config.
    """
    try:
        for attempt in Retrying(
            stop=stop_after_delay(timeout),
            wait=wait_fixed(5),
            retry=retry_if_exception_type((requests.RequestException, RuntimeError)),
        ):
            with attempt:
                response = requests.get(url, timeout=10, verify=False)  # nosec: B501
                if response.status_code >= 500:
                    raise RuntimeError(f"endpoint not ready: {response.status_code}")
                missing = [flag for flag in flags if flag not in response.text]
                if missing:
                    raise RuntimeError(f"config.js is missing flags: {missing}")
    except RetryError as error:
        raise TimeoutError(f"timed out waiting for config flags: {url}") from error
