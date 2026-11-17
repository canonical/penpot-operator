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

```{important}
Revisions before 96 use the `redis` integration. If you upgrade from one of them, follow
[How to migrate from Redis to Valkey](./migrate-to-valkey.md).
```
