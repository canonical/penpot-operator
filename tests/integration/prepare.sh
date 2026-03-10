#!/usr/bin/env bash

# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

set -exo pipefail

if command -v k8s >/dev/null 2>&1; then
	sudo k8s enable ingress
fi

tox -e playwright-install