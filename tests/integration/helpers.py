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


CRASH_LOOP_MARKER = "crash loop backoff"


def _crash_loop_details(status: jubilant.Status) -> list[str]:
    """Return app/unit status messages that indicate a crash loop."""
    details = []
    for app_name, app_status in status.apps.items():
        messages = [app_status.app_status.message]
        messages.extend(unit.workload_status.message for unit in app_status.units.values())
        for message in messages:
            if message and CRASH_LOOP_MARKER in message.lower():
                details.append(f"{app_name}: {message}")
    return details


def _raise_on_crash_loop(status: jubilant.Status) -> None:
    """Raise a clear error if any app or unit is crash-looping."""
    if details := _crash_loop_details(status):
        raise RuntimeError(
            "detected a crash loop while waiting for apps to become active:\n" + "\n".join(details)
        )


def wait_for_active(juju: jubilant.Juju, apps: set[str], timeout: int = 900):
    """Wait for all apps to be active, failing fast if one is crash-looping."""

    @retry(
        stop=stop_after_delay(timeout),
        wait=wait_fixed(10),
        retry=retry_if_result(lambda active: not active),
    )
    def check() -> bool:
        status = juju.status()
        _raise_on_crash_loop(status)
        return jubilant.all_active(status, *apps)

    try:
        check()
    except RetryError as error:
        raise TimeoutError(f"timed out after {timeout}s\n{juju.status()}") from error


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
        _raise_on_crash_loop(status)
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


def wait_for_backend_ready(juju: jubilant.Juju, units: list[str], timeout: int = 300) -> None:
    """Wait until every Penpot unit's backend reports ready on /readyz."""
    for unit_name in units:
        try:
            for attempt in Retrying(
                stop=stop_after_delay(timeout),
                wait=wait_fixed(5),
                retry=retry_if_exception_type((jubilant.CLIError, RuntimeError)),
            ):
                with attempt:
                    output = juju.ssh(
                        unit_name,
                        "curl",
                        "-fsS",
                        "-m",
                        "5",
                        "http://localhost:6060/readyz",
                        container="penpot",
                    )
                    if output.strip() != "OK":
                        raise RuntimeError(f"{unit_name} backend not ready: {output!r}")
        except RetryError as error:
            raise TimeoutError(f"timed out waiting for the backend on {unit_name}") from error


def get_public_url(juju: jubilant.Juju) -> str:
    """Get the Penpot public URL from traefik-public proxied endpoints."""
    result = juju.run("traefik-public/0", "show-proxied-endpoints")
    endpoints = json.loads(result.results["proxied-endpoints"])
    url = endpoints.get("penpot", {}).get("url", "")
    if not url:
        raise RuntimeError("could not get Penpot URL from traefik-public proxied endpoints")
    url = url.rstrip("/")
    parsed = urllib.parse.urlsplit(url)
    # In Traefik subdomain routing mode, this action can report a root
    # URL even when the real route is model-app.<external-hostname>.
    if parsed.path in ("", "/"):
        traefik_url = endpoints.get("traefik-public", {}).get("url", "")
        traefik_parsed = urllib.parse.urlsplit(traefik_url)
        if traefik_parsed.hostname:
            return f"{parsed.scheme}://{juju.model}-penpot.{traefik_parsed.hostname}"
    return url


def wait_for_config_flags(
    juju: jubilant.Juju, units: list[str], flags: list[str], timeout: int = 120
) -> None:
    """Wait until every Penpot unit's config.js exposes the given flags.

    Checking each unit matters: with more than one unit the ingress can route
    the browser to a unit whose frontend has not been reconfigured yet, which
    shows up as "OIDC button did not become visible".
    """
    for unit_name in units:
        try:
            for attempt in Retrying(
                stop=stop_after_delay(timeout),
                wait=wait_fixed(5),
                retry=retry_if_exception_type((jubilant.CLIError, RuntimeError)),
            ):
                with attempt:
                    config = juju.ssh(
                        unit_name, "cat", "/var/www/app/js/config.js", container="penpot"
                    )
                    missing = [flag for flag in flags if flag not in config]
                    if missing:
                        raise RuntimeError(f"{unit_name} config.js is missing flags: {missing}")
        except RetryError as error:
            raise TimeoutError(
                f"timed out waiting for config flags on {unit_name}: {flags}"
            ) from error
