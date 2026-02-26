# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Fixtures for charm tests."""

import os


def pytest_addoption(parser):
    """Parse additional pytest options.

    Args:
        parser: Pytest parser.
    """
    parser.addoption("--charm-file", action="store")
    parser.addoption("--kube-config", action="store")
    parser.addoption("--penpot-image", action="store")
    parser.addoption("--ingress-address", action="store")


def pytest_configure(config):
    """Configure environment for shared test fixtures.

    Args:
        config: Pytest config object.
    """
    kube_config = config.getoption("--kube-config")
    if kube_config and not os.environ.get("TESTING_KUBECONFIG"):
        os.environ["TESTING_KUBECONFIG"] = kube_config
