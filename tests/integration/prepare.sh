#!/usr/bin/env bash

# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

set -exo pipefail

sudo k8s enable ingress

sudo bash -c "echo 127.0.0.1 penpot.local >> /etc/hosts"

tox -e playwright-install