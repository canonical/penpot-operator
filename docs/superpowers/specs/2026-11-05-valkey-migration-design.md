# Migrate Penpot charm from redis-k8s to Valkey — design

## Goal

Replace the `redis-k8s` dependency of the Penpot charm with the Charmed Valkey
operator (`valkey`, channel `9/edge`), keeping Penpot's runtime behaviour
(message bus, worker queues, rate limiting) unchanged.

## Background / key differences

| | redis-k8s (today) | valkey (target) |
|---|---|---|
| Charm / channel | `redis-k8s` `latest/edge` rev 42 | `valkey` `9/edge` (one charm for VM + K8s, deployed with `--trust`, base `ubuntu@26.04`) |
| Provider endpoint / interface | `redis` / `redis` | `valkey-client` / `valkey_client` |
| Library | vendored `charms.redis_k8s.v0.redis` | PyPI `dpcharmlibs-interfaces` (data-interfaces v1) |
| Data provided | `hostname`, `port` (unit databag) | `username`, `password` (Juju secret), `endpoints` (primary, `host:port`), `read_only_endpoints`, `sentinel_endpoints`, `mode`, `tls`, `tls_ca`, `version` |
| Auth | none | per-relation ACL user |
| ACL scope | none | `~<resource> &<resource>` (keys **and** pub/sub channels), commands: `+@read +@write +@keyspace +@pubsub +@transaction +@scripting +info +ping +echo +role +time +select +client|id +client|tracking` |
| TLS | not supported | optional (`tls=True`, port 6380, `tls_ca`) |

Penpot 2.14.3 Redis usage: keys `penpot.worker.queue:*`, `penpot.rlimit.*`
(Lua via EVALSHA), pub/sub channels `<PENPOT_TENANT>.<topic>` (tenant defaults
to `default`). Penpot reads a single `PENPOT_REDIS_URI` (Lettuce client),
consumed by both the backend and the exporter services.

## Decisions

1. **Replace, don't coexist.** Remove the `redis` endpoint; add a required
   `valkey` endpoint (`interface: valkey_client`, `limit: 1`). Juju refuses to
   change the interface of an endpoint that has an active relation, hence a new
   endpoint name. This is a breaking change, documented in the upgrade how-to
   and CHANGELOG.
2. **Requested resource `*`.** Valkey's single ACL pattern covers keys and
   channels; Penpot keys (`penpot.*`) and channels (`default.*`) differ, so the
   charm requests `*`. Valkey does not validate the pattern. No Penpot config
   change (no `PENPOT_TENANT` change, avoiding orphaned queued tasks).
3. **TLS out of scope.** Redis integration has no TLS today, and proper support
   needs `rediss://` plus importing `tls_ca` into the JVM truststore — tracked
   as a separate follow-up PR. If Valkey reports `tls=True`, the charm sets
   `BlockedStatus("valkey client TLS is not supported")` and does not configure
   Penpot.
4. **Library: `dpcharmlibs-interfaces`** (`ResourceRequirerEventHandler` +
   `RequirerCommonModel` + `ValkeyResponseModel`) — the provider's documented
   path; compatible with `ops==3.7`, `pydantic==2.14`.

## Charm changes

- `charmcraft.yaml`: `requires.redis` → `requires.valkey: {interface: valkey_client, limit: 1}`.
- `pyproject.toml` / `uv.lock`: add `dpcharmlibs-interfaces` to runtime deps
  (via `uv add`).
- Delete `lib/charms/redis_k8s/`.
- `src/charm.py`:
  - drop `RedisRelationCharmEvents` (`on = ...`) and `RedisRequires`;
  - `self.valkey = ResourceRequirerEventHandler(self, relation_name="valkey",
    requests=[RequirerCommonModel(resource="*")], response_model=ValkeyResponseModel)`;
  - observe `self.valkey.on.resource_created`, `.endpoints_changed`,
    `.authentication_updated`, and `self.on.valkey_relation_broken` → `_reconcile`;
  - `_get_redis_credentials()` → `_get_valkey_credentials()`:
    - no relation / no remote app → `{}`;
    - build the response model via
      `self.valkey.interface.build_model(relation.id, component=relation.app)`;
      take the first request response; if `username`, `password` or
      `endpoints` missing → `{}`;
    - return `{"PENPOT_REDIS_URI": f"redis://{quote(user, safe='')}:{quote(password, safe='')}@{endpoints.split(',')[0]}/0"}`;
  - helper `_is_valkey_tls_enabled()` reading the same response;
  - `_check_ready()`: rename requirement `"redis"` → `"valkey"`; before the
    generic check, if TLS is enabled set the Blocked status above and return
    `False`.
- Failover: `endpoints` is the primary; on failover the provider updates it,
  `endpoints_changed` fires, reconcile rewrites env and Pebble replans
  (restarting backend/exporter).

## Tests

### Unit (`tox -e unit`, plus `lint`, `static`)

- Replace `redis_relation` fixture with `valkey_relation` building a
  `testing.Relation(endpoint="valkey", remote_app_name="valkey", ...)` whose
  `remote_app_data` follows the data-interfaces v1 contract, with the user
  credentials in a `testing.Secret` (exact databag shape derived from the
  library's `DataContractV1` / `ValkeyResponseModel` serialisation).
- Update existing tests: expected env
  `PENPOT_REDIS_URI = redis://<user>:<pass>@valkey-0.valkey-endpoints:6379/0`.
- New tests: password requiring URL-quoting; relation present but response
  incomplete → `Blocked("waiting for valkey")`; `tls=True` → Blocked TLS
  message; credential change (authentication updated) → new URI.

### Integration (jubilant, run inside VM `penpot-valkey`)

- `tests/integration/conftest.py`: replace `REDIS_REVISION` / `_deploy_redis`
  with `VALKEY_REVISION` (pinned `9/edge` amd64 revision, currently 156) and
  `juju.deploy("valkey", channel="9/edge", revision=VALKEY_REVISION, trust=True)`
  (no Juju 4 resource workaround needed — single resource);
  `juju.integrate("penpot:valkey", "valkey:valkey-client")`; deployed-apps set
  `redis-k8s` → `valkey`.
- Penpot image: use the published rock from Charmhub (`penpot` charm
  `latest/edge`, resource `penpot-image` rev 84) instead of packing the rock,
  since `rockcraft.yaml` is unchanged. Supplied via pytest-opcli CLI-flag mode
  (`--charm-file penpot=<path> --resource-image penpot-image=<ref>`).

## Terraform, docs, changelog

- `terraform/charm/outputs.tf`: `requires.redis` → `requires.valkey`.
- `terraform/product`: `redis_k8s` / `redis_integration` → `valkey` /
  `valkey_integration` (charm `valkey`, channel default `9/edge`, `trust = true`,
  endpoint `valkey-client`); update README/tests; bump `MAJOR_VERSION` of both
  charm and product modules (breaking interface change).
- Docs: `tutorial/getting-started.md`, `reference/charm-architecture.md`,
  `how-to/upgrade.md` (migration: deploy valkey, remove redis relation, refresh
  charm, integrate `penpot:valkey valkey:valkey-client`), `CHANGELOG.md`.

## Validation workflow (small feedback loops)

1. Host: `tox -e lint`, `tox -e unit`, `tox -e static`; `charmcraft pack`.
   Rock is **not** packed.
2. VM `penpot-valkey` (Juju 3.6.29, K8s; repo at
   `/home/ubuntu/projects/penpot-operator`). Before **every** test run: verify
   no applications exist in any non-controller model; run exactly one test at
   a time.
3. Spike (risk check, manual): deploy `valkey` alone (base 26.04 on Juju 3.6),
   then Penpot + Valkey relation, confirm Penpot backend connects under the
   restricted ACL (Lettuce `HELLO` / `CLIENT SETINFO` are not granted
   explicitly), confirm the Charmhub image reference deploys.
4. Single integration tests via `tox -e integration -- <file>::<test>` (opcli
   CLI flags), escalating to whole modules one at a time.
5. Only when all green: one local spread run via `opcli spread run`.

## Risks

- `valkey` on base `ubuntu@26.04` with Juju 3.6 / Juju 4 K8s — verified in the spike.
- Lettuce connection handshake under Valkey ACL (missing `+@connection` /
  `+hello`) — verified in the spike; mitigation if it fails: raise with the
  Valkey team / force RESP2 via URI if Penpot supports it.
- Valkey resource usage on the limited VM (sentinel mode, 1 unit).
- Pulling the Charmhub registry image requires credentials from the resource
  download endpoint — fallback: `skopeo copy` into a local registry.

## Out of scope

- Valkey client TLS / mTLS (follow-up PR).
- Read-replica or sentinel-aware Penpot connections.
- Data migration from Redis (Penpot's Redis data is ephemeral: queues are
  re-dispatched from PostgreSQL, rate-limit/pubsub state is transient).
