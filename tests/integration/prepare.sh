#!/usr/bin/env bash

# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

set -exo pipefail

if command -v k8s >/dev/null 2>&1; then
	sudo k8s enable ingress
elif command -v microk8s >/dev/null 2>&1; then
	sudo microk8s enable ingress
else
	echo "Neither 'k8s' nor 'microk8s' command is available" >&2
	exit 1
fi

sudo sed -i '/[[:space:]]penpot\.local$/d' /etc/hosts
echo "127.0.0.1 penpot.local" | sudo tee -a /etc/hosts >/dev/null

tox -e playwright-install