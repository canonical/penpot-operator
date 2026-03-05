#!/usr/bin/env bash

# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

set -exo pipefail

ingress_ip="$(sudo k8s kubectl -n ingress get pod -l name=nginx-ingress-microk8s --no-headers -o custom-columns=IP:.status.podIP | head -n1)"

sudo bash -c "echo $ingress_ip penpot.local >> /etc/hosts"

tox -e playwright-install