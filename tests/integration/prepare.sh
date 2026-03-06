#!/usr/bin/env bash

# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

set -exo pipefail

sudo k8s enable ingress

ingress_ip=""
for _ in $(seq 1 20); do
	ingress_ip="$(sudo k8s kubectl -n kube-system get svc cilium-ingress -o jsonpath='{.status.loadBalancer.ingress[0].ip}' 2>/dev/null || true)"
	if [[ -n "$ingress_ip" ]]; then
		break
	fi
	sleep 3
done

if [[ -z "$ingress_ip" ]]; then
	echo "ERROR: cilium-ingress Service has no load balancer IP" >&2
	echo "ERROR: cannot map penpot.local for ingress tests" >&2
	exit 1
fi

echo "$ingress_ip penpot.local" | sudo tee -a /etc/hosts >/dev/null

getent hosts penpot.local

tox -e playwright-install