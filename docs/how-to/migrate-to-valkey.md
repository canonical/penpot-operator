# Migrate from Redis to Valkey

Penpot charm revision 96 and later replace the `redis` integration with the `valkey`
integration. Juju doesn't refresh a charm that drops an endpoint still in use, so you must remove
the `redis` integration first. Penpot is unavailable from that step until the end of the
migration.

Penpot doesn't migrate data from Redis to Valkey. Penpot uses it as a broker and cache, so
active sessions or queued background jobs are lost during the switch.

## Migrate with Juju

Deploy Charmed Valkey, then remove the Redis integration:

```bash
juju deploy valkey --channel 9/edge --trust
juju remove-relation penpot redis-k8s
```

Refresh the Penpot charm and integrate it with Valkey:

```bash
juju refresh penpot
juju integrate penpot:valkey valkey:valkey-client
```

After Penpot is `active`, remove the old Redis application:

```bash
juju remove-application redis-k8s
```

## Migrate with Terraform

Version 2 of the `terraform/charm` and `terraform/product` modules replaces Redis with Valkey:

- The charm module exposes `requires.valkey` instead of `requires.redis`.
- The product module deploys `valkey` instead of `redis-k8s`. The `redis_k8s` and `redis_integration`
  variables are replaced by `valkey` and `valkey_integration`. Setting a removed variable fails
  the plan with an error.

Terraform can't remove the Redis integration before it refreshes the Penpot charm, and Juju
refuses to refresh a charm whose removed endpoint is still integrated. Migrate in this order:

1. Remove the Redis integration with Juju:

   ```bash
   juju remove-relation penpot redis-k8s
   ```

2. Drop the integration from the Terraform state:

   ```bash
   terraform apply -refresh-only
   ```

3. Update the Penpot Terraform modules to the new version and apply the change. If you set
   `redis_k8s` or `redis_integration`, replace them with `valkey` or `valkey_integration` first.
   Set the Penpot revision to 96 or later (`penpot.revision` in the product module, `revision` in
   the charm module), even if you don't usually pin it. Terraform keeps the deployed revision
   unless a charm input changes, so the apply doesn't refresh Penpot without it. You can remove the
   setting after the apply.

   Terraform deploys Valkey, integrates it with Penpot, and removes the old bundled `redis-k8s`
   application, because the new module no longer manages it.
