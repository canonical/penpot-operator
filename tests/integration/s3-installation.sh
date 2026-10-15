#!/bin/bash
# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.
set -euo pipefail

S3_ACCESS_KEY="penpot-test-access-key"
S3_SECRET_KEY="penpot-test-secret-key"

sudo snap install microceph
sudo microceph cluster bootstrap
sudo microceph disk add loop,1G,3
sudo microceph enable rgw --port 7480 --wait

curl --connect-timeout 2 --max-time 3 --retry 30 --retry-delay 2 --retry-connrefused -s http://127.0.0.1:7480 > /dev/null

sudo microceph.radosgw-admin user create --uid ci-user --display-name "CI User" \
    --access-key "${S3_ACCESS_KEY}" --secret-key "${S3_SECRET_KEY}"
