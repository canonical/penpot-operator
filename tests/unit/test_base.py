# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

# Learn more about testing at: https://juju.is/docs/sdk/testing

# pylint: disable=duplicate-code,missing-function-docstring
"""Unit tests."""

import unittest

import ops
import ops.testing

from charm import PenpotCharm


class TestCharm(unittest.TestCase):
    """Test class."""

    def setUp(self):
        """Set up the testing environment."""
        self.harness = ops.testing.Harness(PenpotCharm)
        self.addCleanup(self.harness.cleanup)
        self.harness.begin()

    def test_config_changed_valid(self):
        # Trigger a config-changed event with an updated value
        self.harness.update_config({"log-level": "debug"})
        self.assertEqual(self.harness.model.unit.status, ops.ActiveStatus())

    def test_config_changed_invalid(self):
        # Trigger a config-changed event with an updated value
        self.harness.update_config({"log-level": "foobar"})
        # Check the charm is in BlockedStatus
        self.assertIsInstance(self.harness.model.unit.status, ops.BlockedStatus)
