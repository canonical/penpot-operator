# Tutorial: Deploy the Penpot charm

## What you'll do
This tutorial will walk you through deploying the penpot charm; you will:
1. Deploy and configure the penpot charm's dependencies (PostgreSQL, Redis, s3, etc.)
2. Deploy Penpot and integrate it with its dependencies
3. Create a user
4. Login to the web interface for your deployed Penpot instance

## Prerequisites
* A kubernetes cluster.
* A host machine with juju version 3.4 or above.

## Create a juju model
1. Create a juju model
```
juju add-model penpot-test
```

## Deploy and configure the penpot charm's dependencies
1. Deploy and configure some dependencies
```
juju deploy minio --config access-key=minioadmin --config secret-key=minioadmin
juju deploy postgresql-k8s --channel 14/stable --trust
juju deploy redis-k8s --channel latest/edge
juju deploy s3-integrator --config "endpoint=http://minio-endpoints.penpot-test.svc.cluster.local:9000" --config bucket=penpot
juju deploy nginx-ingress-integrator --trust --config service-hostname=penpot.local --config path-routes=/
juju deploy self-signed-certificates
```

## Configure Minio
1. Once the `minio/0` unit is `active/idle`, create the penpot bucket (need `pip install boto3`):
```
python3 << 'EOF'
import json
import subprocess

import boto3
import botocore.client

status = json.loads(subprocess.check_output(["juju","status", "--format",
"json"]))
ip = status["applications"]["minio"]["units"]["minio/0"]["address"]
key = "minioadmin"
s3 = boto3.client(
    "s3",
    endpoint_url=f"http://{ip}:9000",
    aws_access_key_id="minioadmin",
    aws_secret_access_key="minioadmin",
    config=botocore.client.Config(signature_version="s3v4"),
)
s3.create_bucket(Bucket="penpot")
EOF
```

## Configure s3-integrator
1. Once the `s3-integrator/0` unit is `active/idle`:
```
juju run s3-integrator/0 sync-s3-credentials --string-args access-key=minioadmin secret-key=minioadmin
```

## Deploy penpot and integrate it with its dependencies
```
juju deploy penpot --channel latest/edge

juju integrate penpot postgresql-k8s
juju integrate penpot redis-k8s
juju integrate penpot s3-integrator
juju integrate penpot nginx-ingress-integrator
```

## Create a user to login with
```
juju run penpot/0 create-profile --string-args email=test@test.com fullname=test
```

## Now login to your deployed Penpot application

1. Update your `/etc/hosts` file so `penpot.local` resolves to `127.0.0.1`.
2. Visit `https://penpot.local` in a private browser window.
3. Accept the self-signed certificate to proceed.
4. Login with the credentials from the `create-profile` action above.
