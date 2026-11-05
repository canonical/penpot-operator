# Upgrade

Before updating the charm you need to back up the database using the PostgreSQL charm’s `create-backup` action.

```bash
juju run postgresql-k8s/leader create-backup
```

Additional information can be found about backing up in the [PostgreSQL charm's documentation](https://canonical-charmed-postgresql.readthedocs-hosted.com/16/how-to/back-up-and-restore/).

Then upgrade the Penpot charm to the latest revision:

```bash
juju refresh penpot
```

## Migrate from Redis to Valkey

Penpot charm revisions released after 2026-11-05 use [Valkey](https://charmhub.io/valkey)
through the `valkey` integration instead of the `redis` integration. Penpot only stores
transient data (job queues, rate limits and messages) in it, so no data migration is needed.

Juju refuses to refresh a charm that would remove an endpoint in use, so remove the Redis
integration first:

```bash
juju deploy valkey --channel 9/edge --trust
juju remove-relation penpot redis-k8s
juju refresh penpot
juju integrate penpot:valkey valkey:valkey-client
juju remove-application redis-k8s
```
