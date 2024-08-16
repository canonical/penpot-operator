#!/usr/bin/env bash

set -exo pipefail

sudo bash -c "echo 127.0.0.1 penpot.local >> /etc/hosts"
pip install pytest-playwright
playwright install --with-deps chromium
