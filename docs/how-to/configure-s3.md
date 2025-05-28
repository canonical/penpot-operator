# How to configure S3

First, follow [the tutorial of `s3-integrator`](https://charmhub.io/s3-integrator)
to deploy and configure the `s3-integrator` charm.

Then run the following command to integrate the `s3-integrator` charm
and the `penpot` charm to supply the S3 configuration to the `penpot`
charm.

```
juju integrate penpot:s3 s3-integrator
```
