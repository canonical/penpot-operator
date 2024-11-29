# Penpot Charm

[![CharmHub Badge](https://charmhub.io/penpot/badge.svg)](https://charmhub.io/penpot)
[![Publish to edge](https://github.com/canonical/penpot-operator/actions/workflows/publish_charm.yaml/badge.svg)](https://github.com/canonical/penpot-operator/actions/workflows/publish_charm.yaml)
[![Promote charm](https://github.com/canonical/penpot-operator/actions/workflows/promote_charm.yaml/badge.svg)](https://github.com/canonical/penpot-operator/actions/workflows/promote_charm.yaml)
[![Discourse Status](https://img.shields.io/discourse/status?server=https%3A%2F%2Fdiscourse.charmhub.io&style=flat&label=CharmHub%20Discourse)](https://discourse.charmhub.io)

A [Juju](https://juju.is/) [charm](https://juju.is/docs/olm/charmed-operators)
for deploying and managing the [penpot](https://penpot.app) open-source
design tool for design and code collaboration in your systems.

This charm simplifies the configuration and maintenance of penpot across a 
range of environments, allowing designers to create stunning designs and interactive prototypes, 
design systems at scale, and make their workflow easy and fast with ready-to-use code.

## Get started
In this section, we will deploy the penpot charm.  
You’ll need a workstation, e.g., a laptop, with sufficient resources to launch a virtual machine with 4 CPUs, 8 GB RAM, and 50 GB disk space.

### Set up
You can follow the tutorial [here](https://juju.is/docs/juju/set-up--tear-down-your-test-environment#heading--set-up-automatically) to set up a test environment for Juju with LXD.

### Deploy
From inside the virtual machine, deploy penpot charm's dependencies using the `juju deploy` command.

```
juju deploy minio --config access-key=minioadmin --config secret-key=minioadmin
juju deploy postgresql-k8s --channel 14/stable --trust
juju deploy redis-k8s --channel latest/edge
juju deploy s3-integrator --config "endpoint=http://minio-endpoints.penpot-test.svc.cluster.local:9000" --config bucket=penpot
juju deploy nginx-ingress-integrator --trust --config service-hostname=penpot.local --config path-routes=/
juju deploy self-signed-certificates
juju integrate self-signed-certificates nginx-ingress-integrator
```

Configure minio to provide a S3 compatible storage for the penpot charm.

```
export AWS_ACCESS_KEY_ID=minioadmin
export AWS_SECRET_ACCESS_KEY=minioadmin
export AWS_ENDPOINT_URL=http://$(juju status --format=json | jq -r '.applications.minio.units."minio/0".address'):9000
aws s3 mb s3://penpot
juju run s3-integrator/0 sync-s3-credentials --string-args access-key=minioadmin secret-key=minioadmin
```

Deploy the penpot charm and integrate the penpot charm with all its dependencies.

```
juju deploy penpot --channel latest/edge
juju integrate penpot postgresql-k8s
juju integrate penpot redis-k8s
juju integrate penpot s3-integrator
juju integrate penpot nginx-ingress-integrator
```

### Basic operations
When the penpot charm has completed deployment and installation, you can access penpot from a browser.  
First, we need to modify the `/etc/hosts` file to point the `penpot.local` domain to the IP address of the virtual machine.  
After that, we can access the penpot instance in the browser using the address `https://penpot.local`.  
Note that `https` is required for penpot to function. You may need to bypass the certificate security warning in the browser, as we are using a self-signed certificate.  

Inside the virtual machine, run the following command to create a penpot account, and use the returned credentials to log in with this account.

```
juju run penpot/0 create-profile --string-args email=example@example.com fullname="John Doe"
```

## Integrations

### `smtp` integration
[`smtp-integrator`](https://charmhub.io/smtp-integrator) and other charms implementing the `smtp` integration can 
provide SMTP credentials to enable the penpot charm to send email notifications and other email-related functionality.

### `oauth`
[Identity Platform bundle](https://charmhub.io/identity-platform) and other charms implementing the `oauth` 
integration can provide OpenID Connect settings for the Penpot charm. 
This will disable the user/password login in the penpot charm and switch to using the OpenID Connect login flow.

## Learn more
* [Read more](https://charmhub.io/penpot)
* [Official webpage](https://penpot.app/)
* [Troubleshooting](https://matrix.to/#/#charmhub-charmdev:ubuntu.com)

## Project and community
* [Issues](https://github.com/canonical/penpot-operator/issues)
* [Contributing](https://charmhub.io/penpot/docs/how-to-contribute)
* [Matrix](https://matrix.to/#/#charmhub-charmdev:ubuntu.com)
