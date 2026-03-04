# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Fixtures for charm tests."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pytest


def pytest_addoption(parser: "pytest.Parser"):
    """Parse additional pytest options."""
    parser.addoption("--charm-file", action="store")
    parser.addoption("--kube-config", action="store")
    parser.addoption("--penpot-image", action="store")
    parser.addoption("--ingress-address", action="store")
