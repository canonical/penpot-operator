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

Penpot charm revision 96 and later replace the `redis` integration with the `valkey`
integration. Juju doesn't refresh a charm that drops an endpoint still in use, so you must remove
the `redis` integration first. Penpot is unavailable from that step until the end of the
migration.

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

Penpot doesn't migrate data from Redis to Valkey. Penpot uses it as a broker and cache, so
active sessions or queued background jobs are lost during the switch.
