# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Framework-agnostic helpers for integration tests."""

import json
import urllib.parse

import jubilant
import requests
from tenacity import RetryError, Retrying, retry_if_exception_type, stop_after_delay, wait_fixed


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
