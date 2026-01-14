# Upgrade

Before updating the charm you need to back up the database using the PostgreSQL charm’s create-backup action.

```bash
juju run postgresql-k8s/leader create-backup
```

Additional information can be found about backing up in the [PostgreSQL charm's documentation](https://canonical-charmed-postgresql.readthedocs-hosted.com/16/how-to/back-up-and-restore/).

Then you can upgrade the Penpot charm:

```bash
juju refresh penpot
```