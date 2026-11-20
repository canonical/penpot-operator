<!-- vale off -->
# Deploy the Penpot charm
<!-- vale on -->

[Penpot](https://penpot.app) is an open-source, web-based design and prototyping tool. The
Penpot charm deploys it on Kubernetes and requires four relations to run: PostgreSQL for
data, Valkey as a message broker, S3-compatible object storage for uploaded files, and an
ingress that provides its public HTTPS URL. In this tutorial you deploy the charm with each of
these, and learn what each one does.

## What you'll do

1. Set up Juju and Canonical Kubernetes
2. Deploy the services Penpot depends on: a database, a cache, object storage, and ingress with TLS
3. Deploy Penpot and integrate it with those services
4. Create a user and log in to the web interface
5. Clean up the environment

### Charms you'll deploy

The Penpot charm stays blocked until each required relation is in place. This tutorial
deploys the following charms:

| Application | Required? | What it's used for |
|---|---|---|
| `postgresql-k8s` | Yes | Stores Penpot's data: users, projects, and files |
| `valkey` | Yes | Brokers messages between Penpot components |
| `s3-integrator` | Yes | Connects Penpot to an S3-compatible object store for uploaded assets. This tutorial uses MicroCeph, installed on your working station, as the store |
| `gateway-api-integrator` | Yes | Exposes Penpot outside the cluster. Penpot needs its public URL to work |
| `ingress-configurator` | Yes | Tells the integrator which hostname and paths to serve Penpot on |
| `self-signed-certificates` | Required for HTTPS | Issues the TLS certificate for the ingress |

Penpot also supports optional relations, such as SMTP for email, SSO for login, and
COS for monitoring. This tutorial doesn't use them. See [Next steps](#next-steps).

## What you'll need

<!-- vale Canonical.013-Spell-out-numbers-below-10 = NO -->
You will need a working station, for example a laptop, with AMD64 architecture. Your working
station should have at least 4 CPU cores, 8 GB of RAM, and 50 GB of disk space.
<!-- vale Canonical.013-Spell-out-numbers-below-10 = YES -->

````{tip}
You can use [Multipass](https://canonical.com/multipass) to create an isolated environment by running:
```
multipass launch 24.04 --name charm-tutorial-vm --cpus 4 --memory 8G --disk 50G
```

To work inside the Multipass VM, log in with:
```
multipass shell charm-tutorial-vm
```
````

This tutorial requires the following software, either on your working station or in the
Multipass VM:

- Juju 3
- Canonical Kubernetes 1.32 or later
- MicroCeph, to provide S3-compatible object storage
- The AWS command line interface, to create a storage bucket
- `jq`

Use [Concierge](https://github.com/canonical/concierge) to set up Juju and Canonical Kubernetes,
and to install MicroCeph and the AWS command line interface:

```bash
sudo snap install --classic concierge
sudo concierge prepare -p k8s --extra-snaps microceph,aws-cli
```

For this tutorial, Juju must be bootstrapped to a Canonical Kubernetes controller. Concierge
does this for you. You can verify by running `juju controllers`.

If Concierge did not bootstrap Juju, run:

```bash
juju bootstrap k8s tutorial-controller
```

Set the Kubernetes load balancer IP to the IP of your working station or Multipass VM. This
lets you reach Penpot from a browser without extra network configuration:

```bash
PREFSRC=$(ip -4 -j route get 2.2.2.2 | jq -r '.[] | .prefsrc')
sudo k8s set load-balancer.cidrs=$PREFSRC/32
```

## Create a Juju model

Create a model to keep this tutorial's workload separate from your other work:

```bash
juju add-model penpot-test
```

## Deploy the storage services

Deploy the database and cache:

```bash
juju deploy postgresql-k8s --channel 14/stable --trust
juju deploy valkey --channel 9/edge --trust
```

Penpot stores uploaded files in S3-compatible object storage. For this tutorial you'll
run [MicroCeph](https://canonical-microceph.readthedocs-hosted.com/) on your working station,
which provides a Ceph S3 gateway. The `s3-integrator` charm passes its connection details to Penpot.

### Set up MicroCeph

Concierge already installed the MicroCeph snap. Bootstrap a cluster, enable its S3 gateway on
port 7480, and create an S3 user:

```bash
sudo microceph cluster bootstrap
sudo microceph disk add loop,1G,3
sudo microceph enable rgw --port 7480 --wait

sudo microceph.radosgw-admin user create --uid penpot-user --display-name "Penpot User" \
    --access-key penpot-access-key --secret-key penpot-secret-key
```

Create the `penpot` bucket:

```bash
export AWS_ACCESS_KEY_ID=penpot-access-key
export AWS_SECRET_ACCESS_KEY=penpot-secret-key
export AWS_DEFAULT_REGION=us-east-1
export AWS_ENDPOINT_URL=http://$PREFSRC:7480
aws s3 mb s3://penpot
```

### Deploy and configure S3

Deploy `s3-integrator` pointing at the MicroCeph gateway:

```bash
juju deploy s3-integrator --config "endpoint=http://$PREFSRC:7480" --config bucket=penpot
```

Run `juju status` and wait until the `s3-integrator/0` unit is `blocked` waiting for
credentials, then give it the credentials:

```bash
juju run s3-integrator/0 sync-s3-credentials --string-args access-key=penpot-access-key secret-key=penpot-secret-key
```

## Deploy ingress with TLS

Penpot has to be reachable at the root of a hostname, and over HTTPS. Two charms provide this capability:

- `gateway-api-integrator` creates the Kubernetes Gateway API resources that route traffic into
  the cluster.
- `ingress-configurator` tells the integrator which hostname and paths to serve for Penpot. The
  `ingress` relation of the integrator would publish Penpot under a path prefix, such as
  `/penpot-test-penpot`, which Penpot can't serve. The configurator lets Penpot be served
  from `/` on a hostname you choose.

Deploy the integrator with the `--trust` flag, which lets the charm manage the Gateway API resources,
and set the gateway class shipped with Canonical Kubernetes:

```bash
juju deploy gateway-api-integrator --trust --channel 1/edge
juju config gateway-api-integrator gateway-class=ck-gateway
```

The charm stays blocked until it has a TLS certificate. The
[self-signed certificates charm](https://charmhub.io/self-signed-certificates) is the simplest
way to provide one, but you shouldn't use self-signed certificates in production environments.
For this tutorial, deploy it and integrate it with the integrator:

```bash
juju deploy self-signed-certificates
juju integrate self-signed-certificates:certificates gateway-api-integrator:certificates
```

Deploy the configurator with a hostname that resolves to your working station.
[sslip.io](https://sslip.io) is a public DNS service that resolves names like
`10.0.0.5.sslip.io` to the IP in the name, so you don't need to edit `/etc/hosts`:

```bash
juju deploy ingress-configurator --trust
juju config ingress-configurator hostname=$PREFSRC.sslip.io paths=/
juju integrate ingress-configurator gateway-api-integrator
```

## Deploy Penpot and integrate it with its dependencies

Deploy Penpot and integrate it with each service:

```bash
juju deploy penpot --channel latest/edge

juju integrate penpot postgresql-k8s
juju integrate penpot:valkey valkey:valkey-client
juju integrate penpot s3-integrator
juju integrate penpot ingress-configurator
```

## Confirm all applications are deployed

Run `juju status --relations` and wait until every application is `active`. This may take
several minutes. You should see something like this (addresses will differ):

```text
Model        Controller     Cloud/Region  Version  SLA          Timestamp
penpot-test  concierge-k8s  k8s           3.6.29   unsupported  19:42:22+01:00

App                       Version  Status  Scale  Charm                     Channel        Rev  Address         Exposed  Message
gateway-api-integrator             active      1  gateway-api-integrator    1/edge         192  10.152.183.38   no       Gateway addresses: 10.76.109.198
ingress-configurator               active      1  ingress-configurator      latest/stable  111  10.152.183.178  no       Ready
penpot                             active      1  penpot                    latest/edge     99  10.152.183.182  no
postgresql-k8s            14.24    active      1  postgresql-k8s            14/stable      960  10.152.183.116  no
s3-integrator                      active      1  s3-integrator             1/stable       562  10.152.183.137  no
self-signed-certificates           active      1  self-signed-certificates  1/stable       710  10.152.183.163  no
valkey                    9.0.4    active      1  valkey                    9/edge         167  10.152.183.109  no
```

## Create a user to log in with

```bash
juju run penpot/0 create-profile --string-args email=test@test.com fullname=test
```

Note the password in the output.

## Log in to Penpot

Get the hostname you configured:

```bash
echo https://$PREFSRC.sslip.io
```

Open that URL in a private browser window. Your browser may display a warning about the
connection's security because we used a self-signed certificate. You can safely ignore this
warning.

Log in with the credentials from the `create-profile` action. After logging in, you should
see the Penpot dashboard, where you can create your first project.

If you're using a Multipass VM, the sslip.io hostname resolves to the IP of the VM, so the URL is
reachable from your host machine without further setup.

## Clean up the environment

Congratulations! You deployed Penpot with a database, a cache, object storage, and HTTPS
ingress, and logged in to it.

To remove all applications and data, run `juju destroy-model penpot-test` and follow the
prompt to confirm. For more details, see
[Tear down your test environment](https://canonical.com/juju/docs/juju-cli/3.6/howto/manage-your-juju-deployment/tear-down-your-juju-deployment-local-testing-and-development/).

## Next steps

You now have a basic Penpot deployment. Check out these pages to learn more:

- [Manage users](../how-to/manage-users.md)
- [Configure S3](../how-to/configure-s3.md) to use a production object store instead of the single-node MicroCeph
- [Configure SMTP](../how-to/configure-smtp.md) so Penpot can send email
- [Configure SSO](../how-to/configure-sso.md) to let users log in with your identity provider
- [Integrate with COS](../how-to/integrate-with-cos.md) to monitor your deployment
- [Upgrade](../how-to/upgrade.md) Penpot
- Review the [charm architecture](../reference/charm-architecture.md)
