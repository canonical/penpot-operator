# Tutorial: Deploy the Penpot charm

## What you'll do
This tutorial will walk you through deploying the penpot charm; you will:
1. Deploy and configure the Penpot charm's dependencies (PostgreSQL, Redis, s3, etc.)
2. Deploy Penpot and integrate it with its dependencies
3. Create a user
4. Login to the web interface for your deployed Penpot instance

## Prerequisites
* MicroK8s deployed with the `ingress`, `dns`, and `storage` plugins enabled.
* A host machine with Juju version 3.4 or above and a Juju controller bootstrapped.
* The AWS command line interface, which can be installed with `sudo snap install aws-cli --classic`.

## Create a Juju model
1. Create a Juju model
```
juju add-model penpot-test
```

## Deploy and configure the Penpot charm's dependencies
1. Deploy and configure some dependencies
```
juju deploy minio --config access-key=minioadmin --config secret-key=minioadmin
juju deploy postgresql-k8s --channel 14/stable --trust
juju deploy redis-k8s --channel latest/edge
juju deploy s3-integrator --config "endpoint=http://minio-endpoints.penpot-test.svc.cluster.local:9000" --config bucket=penpot
juju deploy nginx-ingress-integrator --trust --config service-hostname=penpot.local --config path-routes=/
juju deploy self-signed-certificates
juju integrate self-signed-certificates nginx-ingress-integrator
```

## Configure Minio
1. Run `juju status` to confirm the `minio/0` unit is `active/idle` (may take a few minutes), create the `penpot` bucket:
```
export AWS_ACCESS_KEY_ID=minioadmin
export AWS_SECRET_ACCESS_KEY=minioadmin
export AWS_ENDPOINT_URL=http://$(juju status --format=json | jq -r '.applications.minio.units."minio/0".address'):9000
aws s3 mb s3://penpot
```

## Configure s3-integrator
1. Run `juju status` to confirm the `s3-integrator/0` unit is `active/idle` (may take a few minutes):
```
juju run s3-integrator/0 sync-s3-credentials --string-args access-key=minioadmin secret-key=minioadmin
```

## Deploy Penpot and integrate it with its dependencies
```
juju deploy penpot --channel latest/edge

juju integrate penpot postgresql-k8s
juju integrate penpot redis-k8s
juju integrate penpot s3-integrator
juju integrate penpot nginx-ingress-integrator
```

## Confirm all applications are deployed
1. Run `juju status`. You should see something like this (IPs will be different):
```
Model        Controller          Cloud/Region        Version  SLA          Timestamp
penpot-test  microk8s-localhost  microk8s/localhost  3.5.3    unsupported  16:14:03+02:00

App                       Version                Status  Scale  Charm                     Channel        Rev  Address         Exposed  Message
minio                     res:oci-image@1755999  active      1  minio                     latest/stable  277  10.152.183.128  no
nginx-ingress-integrator  24.2.0                 active      1  nginx-ingress-integrator  latest/stable  101  10.152.183.77   no       Ingress IP(s): 127.0.0.1
penpot                                           active      1  penpot                    latest/edge      3  10.152.183.24   no
postgresql-k8s            14.11                  active      1  postgresql-k8s            14/stable      281  10.152.183.22   no
redis-k8s                 7.2.5                  active      1  redis-k8s                 latest/edge     34  10.152.183.82   no
s3-integrator                                    active      1  s3-integrator             latest/stable   24  10.152.183.78   no
self-signed-certificates                         active      1  self-signed-certificates  latest/stable  155  10.152.183.89   no

Unit                         Workload  Agent  Address       Ports          Message
minio/0*                     active    idle   10.1.129.147  9000-9001/TCP
nginx-ingress-integrator/0*  active    idle   10.1.129.140                 Ingress IP(s): 127.0.0.1
penpot/0*                    active    idle   10.1.129.139
postgresql-k8s/0*            active    idle   10.1.129.142                 Primary
redis-k8s/0*                 active    idle   10.1.129.144
s3-integrator/0*             active    idle   10.1.129.148
self-signed-certificates/0*  active    idle   10.1.129.149
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

## To remove all applications and data
1. Run `juju destroy-model penpot-test`. Follow the prompt to confirm you want to go ahead.
