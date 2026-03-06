# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Framework-agnostic helpers for integration tests."""

import requests
from tenacity import RetryError, Retrying, retry_if_exception_type, stop_after_delay, wait_fixed


def wait_for_endpoint(url: str, timeout: int = 120, headers: dict | None = None):
    """Wait until an HTTPS endpoint becomes reachable."""
    try:
        for attempt in Retrying(
            stop=stop_after_delay(timeout),
            wait=wait_fixed(5),
            retry=retry_if_exception_type((requests.RequestException, RuntimeError)),
        ):
            with attempt:
                response = requests.get(  # nosec: B501
                    url,
                    timeout=10,
                    verify=False,
                    headers=headers,
                )
                if response.status_code >= 500:
                    raise RuntimeError(f"endpoint not ready: {response.status_code}")
    except RetryError as error:
        raise TimeoutError(f"timed out waiting for endpoint: {url}") from error
