# How to configure Single Sign-On (SSO)

The Penpot charm supports Single Sign-On (SSO) for user authentication 
using OpenID Connect.

You can follow the tutorial [here](https://charmhub.io/topics/canonical-identity-platform/tutorials/e2e-tutorial)
to get started with OpenID Connect in the Juju system using the 
Canonical IAM bundle.

After deploying the IAM bundle, run the following command to integrate 
Penpot with the IAM bundle and enable SSO in Penpot.

```
juju integrate penpot:oauth hydra
```
