<!-- vale off -->
# How to configure SMTP
<!-- vale on -->

First, follow [the tutorial of `smtp-integrator`](https://charmhub.io/smtp-integrator/docs/tutorial-getting-started)
to deploy and configure the `smtp-integrator` charm.

Then run the following command to integrate the `smtp-integrator` charm
and the `penpot` charm to supply the SMTP configuration to the `penpot`
charm.

```
juju integrate penpot:smtp smtp-integrator
```
