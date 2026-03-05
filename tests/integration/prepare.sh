#!/usr/bin/env bash

# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

set -exo pipefail

ingress_ip="$(sudo k8s kubectl get node -o jsonpath='{.items[0].status.addresses[?(@.type=="InternalIP")].address}')"

if [[ -z "$ingress_ip" ]]; then
	ingress_ip="127.0.0.1"
fi

sudo sed -i '/[[:space:]]penpot\.local$/d' /etc/hosts
echo "$ingress_ip penpot.local" | sudo tee -a /etc/hosts >/dev/null

getent hosts penpot.local

tox -e playwright-install