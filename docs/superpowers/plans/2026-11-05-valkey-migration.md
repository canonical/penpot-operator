# Migrate Penpot from redis-k8s to Valkey — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **Also REQUIRED:** read `~/.agents/skills/redis-to-valkey-migration/SKILL.md` before starting, and **update it** whenever you hit a non-obvious problem, find a better command, or confirm/refute one of its "unverified" items. Future agents migrating other charms depend on it.

**Goal:** Replace the Penpot charm's `redis` (redis-k8s) integration with a `valkey` integration (Charmed Valkey `9/edge`, `valkey_client` interface) with all unit, lint, static and integration tests green.

**Architecture:** The charm gets a new required `valkey` endpoint served by the PyPI `dpcharmlibs-interfaces` requirer (`ResourceRequirerEventHandler`, data-interfaces v1). It requests the resource `*` and turns the response (username/password secret, primary endpoint) into the existing `PENPOT_REDIS_URI` env var. Valkey client TLS is reported as Blocked (out of scope). Integration fixtures, Terraform modules and docs switch from `redis-k8s` to `valkey`.

**Tech Stack:** Python 3.12, ops 3.7 / ops.testing (scenario), pydantic 2.14, dpcharmlibs-interfaces 1.2.2, jubilant 1.9, pytest-opcli (canonical/charm-ci), tox 4 + tox-uv, charmcraft, Juju 3.6 K8s (concierge), Terraform.

**Spec:** `docs/superpowers/specs/2026-11-05-valkey-migration-design.md`

## Global Constraints

- Valkey charm: `valkey`, channel `9/edge`, revision `156` (amd64), deployed with `--trust`, base `ubuntu@26.04`.
- Charm endpoint: `valkey`, `interface: valkey_client`, `limit: 1`, required. The `redis` endpoint is removed.
- Valkey provider endpoint: `valkey-client`.
- Requested resource: `*`.
- Env var stays `PENPOT_REDIS_URI`; format `redis://<quote(username)>:<quote(password)>@<first endpoint>/0` (`urllib.parse.quote(..., safe="")`).
- TLS reported by Valkey → `BlockedStatus("valkey client TLS is not supported")`.
- Missing/incomplete Valkey data → existing generic status `BlockedStatus("waiting for valkey")` (alphabetical list with other missing requirements).
- Runtime dep pin: `dpcharmlibs-interfaces==1.2.2` (repo pins exact versions).
- Do **not** pack the rock (`penpot_rock/rockcraft.yaml` is unchanged). Use the published image: penpot charm `latest/edge`, resource `penpot-image` rev 84 (Charmhub registry).
- Integration tests run **only inside multipass VM `penpot-valkey`**, one test at a time, never in parallel, only after confirming no applications exist in any non-controller model. No spread until everything is green; then exactly one local spread run.
- Host repo: `/home/fouad.atwi@canonical.com/projects/penpot-operator`. VM repo (same files, mounted): `/home/ubuntu/projects/penpot-operator`.
- In the VM always pass `--workdir /home/ubuntu/.cache/penpot-tox` to tox so the VM never touches the host's `.tox/`.
- Never commit credentials (the Charmhub image pull token lives only in `/home/ubuntu/penpot-image.json` inside the VM).
- Commit trailer on every commit: `Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>`.

## Verified facts (from prototyping — rely on them)

- The Valkey provider writes to its app databag: `version: "v1"` and `requests` = JSON list of responses with keys `resource`, `request-id`, `salt`, `endpoints`, `read-only-endpoints`, `secret-user`, `secret-tls`, `mode`, `version`, ...
- `secret-user` secret content: `{"username": ..., "password": ...}`; `secret-tls` content: `{"tls": "true"|"false"}`.
- Secret IDs **must** match `secret:.*` or `build_model` raises `pydantic.ValidationError`.
- `self.valkey.interface.build_model(relation.id, component=relation.app)` reads the response on leader and non-leader units, without any local request data. Empty databag → `requests == []`.
- The library's custom events (`resource_created`, …) are emitted only when the requirer has local request data; therefore the charm **also** observes `valkey_relation_changed` directly.
- Valkey client ACL: `-@all +@read +@write +@keyspace -migrate +@pubsub +@transaction +info +ping +echo +role +time +select +client|id +client|tracking +eval +evalsha +@scripting ~<resource> &<resource>`.

## File map

| File | Change |
|---|---|
| `charmcraft.yaml` | `requires.redis` → `requires.valkey` |
| `pyproject.toml`, `uv.lock` | add `dpcharmlibs-interfaces==1.2.2` |
| `lib/charms/redis_k8s/` | delete |
| `src/charm.py` | Valkey requirer, credentials, TLS block |
| `tests/unit/conftest.py` | `valkey_relation()`, `valkey_secrets()`, constants |
| `tests/unit/test_charm.py` | migrate 8 tests, add 5 Valkey tests |
| `tests/integration/conftest.py` | deploy/integrate `valkey` instead of `redis-k8s` |
| `terraform/charm/outputs.tf`, `terraform/charm/MAJOR_VERSION` | `requires.valkey`, bump to 2 |
| `terraform/product/{variables,locals,main,outputs}.tf`, `README.md`, `tests/main.tftest.hcl`, `MAJOR_VERSION` | `valkey` / `valkey_integration`, bump to 2 |
| `docs/tutorial/getting-started.md`, `docs/reference/charm-architecture.md`, `docs/how-to/upgrade.md`, `CHANGELOG.md` | Valkey docs |
| `~/.agents/skills/redis-to-valkey-migration/SKILL.md` | keep updated (outside repo) |

---

### Task 1: Charm Valkey requirer (code + unit tests)

**Files:**
- Modify: `charmcraft.yaml` (the `requires:` block)
- Modify: `pyproject.toml`, `uv.lock` (via `uv add`)
- Delete: `lib/charms/redis_k8s/`
- Modify: `src/charm.py` (imports, `PenpotCharm.on`, `__init__`, `_get_redis_credentials`, `_check_ready`, pebble plan env)
- Modify: `tests/unit/conftest.py`
- Modify: `tests/unit/test_charm.py`

**Interfaces:**
- Produces (tests): `valkey_relation(*, with_credentials: bool = True) -> Relation`, `valkey_secrets(*, password: str = VALKEY_PASSWORD, tls: bool = False) -> set[Secret]`, constants `VALKEY_USERNAME`, `VALKEY_PASSWORD`, `VALKEY_ENDPOINT`, `VALKEY_USER_SECRET_ID`, `VALKEY_TLS_SECRET_ID`, `VALKEY_REDIS_URI`.
- Produces (charm): `PenpotCharm.valkey: ResourceRequirerEventHandler`, `PenpotCharm._get_valkey_response() -> ValkeyResponseModel | None`, `PenpotCharm._get_valkey_credentials() -> dict[str, str]`, module constant `VALKEY_RESOURCE = "*"`.

- [ ] **Step 1: Add the dependency**

Run (host, repo root):
```bash
uv add "dpcharmlibs-interfaces==1.2.2"
```
Expected: `pyproject.toml` `[project].dependencies` gains `"dpcharmlibs-interfaces==1.2.2"`, `uv.lock` updated (pulls `cryptography`).

- [ ] **Step 2: Add Valkey fixtures to `tests/unit/conftest.py`**

Change the import line `from ops.testing import Exec, PeerRelation, Relation` to:
```python
from ops.testing import Exec, PeerRelation, Relation, Secret
```
Below `S3_SECRET_KEY = token_hex(16)` add:
```python
VALKEY_USERNAME = "relation-5-penpot"
VALKEY_PASSWORD = token_hex(16)
VALKEY_ENDPOINT = "valkey-0.valkey-endpoints:6379"
# data-interfaces v1 rejects secret IDs that do not start with "secret:".
VALKEY_USER_SECRET_ID = f"secret:{token_hex(10)}"
VALKEY_TLS_SECRET_ID = f"secret:{token_hex(10)}"
VALKEY_REDIS_URI = f"redis://{VALKEY_USERNAME}:{VALKEY_PASSWORD}@{VALKEY_ENDPOINT}/0"
```
Replace the whole `redis_relation()` function with:
```python
def valkey_secrets(*, password: str = VALKEY_PASSWORD, tls: bool = False) -> set[Secret]:
    """Secrets shared by the valkey charm over the valkey_client integration."""
    return {
        Secret(
            id=VALKEY_USER_SECRET_ID,
            tracked_content={"username": VALKEY_USERNAME, "password": password},
        ),
        Secret(id=VALKEY_TLS_SECRET_ID, tracked_content={"tls": "true" if tls else "false"}),
    }


def valkey_relation(*, with_credentials: bool = True) -> Relation:
    """Valkey integration as written by the valkey charm (data-interfaces v1)."""
    response = {
        "resource": "*",
        "request-id": "penpot-valkey-request",
        "salt": "penpot-valkey-salt",
        "endpoints": VALKEY_ENDPOINT,
        "secret-user": VALKEY_USER_SECRET_ID if with_credentials else None,
        "secret-tls": VALKEY_TLS_SECRET_ID,
        "mode": "sentinel",
        "version": "9.0.0",
    }
    return Relation(
        endpoint="valkey",
        remote_app_name="valkey",
        remote_app_data={"version": "v1", "requests": json.dumps([response])},
    )
```

- [ ] **Step 3: Migrate existing unit tests**

In `tests/unit/test_charm.py` every `secrets=` line belongs to a test that also uses `redis_relation()` (lines 46, 73, 98, 129, 163, 199, 308), so a mechanical replace is safe:
```bash
sed -i \
  -e 's/redis_relation()/valkey_relation()/' \
  -e 's/secrets={peer_secret}/secrets={peer_secret, *valkey_secrets()}/' \
  -e 's/secrets={peer_secret, smtp_secret}/secrets={peer_secret, smtp_secret, *valkey_secrets()}/' \
  tests/unit/test_charm.py
grep -n "redis" tests/unit/test_charm.py
```
Then edit by hand:
1. Import block from `tests.unit.conftest`: remove `redis_relation,`; add `VALKEY_ENDPOINT,`, `VALKEY_REDIS_URI,`, `VALKEY_USER_SECRET_ID,`, `VALKEY_USERNAME,`, `valkey_relation,`, `valkey_secrets,` (keep the alphabetical style: constants first, then functions). Also change `from ops.testing import Exec, Secret` to `from ops.testing import Exec, Relation, Secret`.
2. In `test_penpot_pebble_layer` replace both `"PENPOT_REDIS_URI": "redis://redis-hostname:6379",` with `"PENPOT_REDIS_URI": VALKEY_REDIS_URI,`.
3. Replace the whole `test_redis_config` function with the Valkey tests below.

- [ ] **Step 4: Write the new Valkey tests (replace `test_redis_config`)**

```python
def _valkey_ready_state(valkey: Relation, valkey_secret_set: set[Secret]) -> testing.State:
    """State with every requirement fulfilled except for the given valkey integration."""
    peer_secret = Secret(tracked_content={"penpot-secret-key": token_hex(16)}, id=PEER_SECRET_ID)
    return testing.State(
        relations={
            peer_relation(secret_id=peer_secret.id),
            postgresql_relation(),
            valkey,
            s3_relation(),
            ingress_relation(),
        },
        secrets={peer_secret, *valkey_secret_set},
        containers={penpot_container()},
    )


def test_valkey_config(monkeypatch: pytest.MonkeyPatch, context: testing.Context[PenpotCharm]):
    """
    arrange: initialize the testing context with required integrations.
    act: run reconcile via config-changed and retrieve the output state.
    assert: the valkey URI is set in the backend and exporter environments.
    """
    monkeypatch.setattr(PenpotCharm, "_check_penpot_backend_ready", lambda self: True)
    state = _valkey_ready_state(valkey_relation(), valkey_secrets())
    out = context.run(context.on.config_changed(), state)
    assert out.unit_status == testing.ActiveStatus()
    services = out.get_container("penpot").plan.services
    assert services["backend"].environment["PENPOT_REDIS_URI"] == VALKEY_REDIS_URI
    assert services["exporter"].environment["PENPOT_REDIS_URI"] == VALKEY_REDIS_URI


def test_valkey_password_is_url_quoted(
    monkeypatch: pytest.MonkeyPatch, context: testing.Context[PenpotCharm]
):
    """
    arrange: valkey shares a password containing URI reserved characters.
    act: run reconcile via config-changed.
    assert: the password is percent-encoded in the URI.
    """
    monkeypatch.setattr(PenpotCharm, "_check_penpot_backend_ready", lambda self: True)
    state = _valkey_ready_state(valkey_relation(), valkey_secrets(password="p@ss/w:rd"))
    out = context.run(context.on.config_changed(), state)
    backend_env = out.get_container("penpot").plan.services["backend"].environment
    assert backend_env["PENPOT_REDIS_URI"] == (
        f"redis://{VALKEY_USERNAME}:p%40ss%2Fw%3Ard@{VALKEY_ENDPOINT}/0"
    )


def test_valkey_waiting_for_credentials(context: testing.Context[PenpotCharm]):
    """
    arrange: valkey integration exists but credentials were not shared yet.
    act: run reconcile via config-changed.
    assert: the unit is blocked waiting for valkey.
    """
    state = _valkey_ready_state(valkey_relation(with_credentials=False), valkey_secrets())
    out = context.run(context.on.config_changed(), state)
    assert out.unit_status == testing.BlockedStatus("waiting for valkey")


def test_valkey_tls_not_supported(context: testing.Context[PenpotCharm]):
    """
    arrange: valkey reports client TLS as enabled.
    act: run reconcile via config-changed.
    assert: the unit is blocked because valkey client TLS is not supported.
    """
    state = _valkey_ready_state(valkey_relation(), valkey_secrets(tls=True))
    out = context.run(context.on.config_changed(), state)
    assert out.unit_status == testing.BlockedStatus("valkey client TLS is not supported")


def test_valkey_credentials_rotation(
    monkeypatch: pytest.MonkeyPatch, context: testing.Context[PenpotCharm]
):
    """
    arrange: valkey rotated the password stored in the user secret.
    act: run the secret-changed event for the user secret.
    assert: the URI is updated with the new password.
    """
    monkeypatch.setattr(PenpotCharm, "_check_penpot_backend_ready", lambda self: True)
    new_password = token_hex(16)
    state = _valkey_ready_state(valkey_relation(), valkey_secrets(password=new_password))
    user_secret = next(s for s in state.secrets if s.id == VALKEY_USER_SECRET_ID)
    out = context.run(context.on.secret_changed(user_secret), state)
    backend_env = out.get_container("penpot").plan.services["backend"].environment
    assert backend_env["PENPOT_REDIS_URI"] == (
        f"redis://{VALKEY_USERNAME}:{new_password}@{VALKEY_ENDPOINT}/0"
    )
```

- [ ] **Step 5: Run unit tests to verify they fail**

Run: `tox -e unit`
Expected: FAIL — scenario errors such as `relation endpoint 'valkey' is not declared in metadata` / missing `PENPOT_REDIS_URI` with valkey values.

- [ ] **Step 6: Update `charmcraft.yaml`**

Replace:
```yaml
  redis:
    interface: redis
    limit: 1
```
with:
```yaml
  valkey:
    interface: valkey_client
    limit: 1
```

- [ ] **Step 7: Delete the redis library**

```bash
git rm -r lib/charms/redis_k8s
```

- [ ] **Step 8: Implement the requirer in `src/charm.py`**

Imports — remove `from charms.redis_k8s.v0.redis import RedisRelationCharmEvents, RedisRequires`; add (keep isort order — third-party block, ruff `I` will verify):
```python
import pydantic
from dpcharmlibs.interfaces import (
    RequirerCommonModel,
    ResourceRequirerEventHandler,
    ValkeyResponseModel,
)
```
After `logger = logging.getLogger(__name__)` add:
```python
# Valkey scopes both keys and pub/sub channels to this pattern. Penpot keys use the
# "penpot." prefix but its channels use the tenant prefix ("default."), so request all.
VALKEY_RESOURCE = "*"
```
In `class PenpotCharm`, delete the line `    on = RedisRelationCharmEvents()` (and the blank line after it).

In `__init__` replace `self.redis = RedisRequires(self, "redis")` with:
```python
        self.valkey = ResourceRequirerEventHandler(
            self,
            relation_name="valkey",
            requests=[RequirerCommonModel(resource=VALKEY_RESOURCE)],
            response_model=ValkeyResponseModel,
        )
```
Replace the two redis observers:
```python
        self.framework.observe(self.redis.charm.on.redis_relation_updated, self._reconcile)
        self.framework.observe(self.on.redis_relation_broken, self._reconcile)
```
with:
```python
        self.framework.observe(self.valkey.on.resource_created, self._reconcile)
        self.framework.observe(self.valkey.on.endpoints_changed, self._reconcile)
        self.framework.observe(self.valkey.on.authentication_updated, self._reconcile)
        self.framework.observe(self.on.valkey_relation_changed, self._reconcile)
        self.framework.observe(self.on.valkey_relation_broken, self._reconcile)
```
Replace the whole `_get_redis_credentials` method with:
```python
    def _get_valkey_response(self) -> ValkeyResponseModel | None:
        """Get the response shared by valkey over the valkey integration.

        Returns:
            The valkey response, or None if valkey has not responded yet.
        """
        relation = self.model.get_relation("valkey")
        if not relation or not relation.app:
            return None
        try:
            model = self.valkey.interface.build_model(relation.id, component=relation.app)
        except pydantic.ValidationError:
            logger.exception("invalid data in the valkey integration")
            return None
        return model.requests[0] if model.requests else None

    def _get_valkey_credentials(self) -> dict[str, str]:
        """Get penpot redis credentials from the valkey integration.

        Returns:
            Penpot redis credentials.
        """
        response = self._get_valkey_response()
        if (
            not response
            or response.tls
            or not response.username
            or not response.password
            or not response.endpoints
        ):
            return {}
        username = urllib.parse.quote(response.username, safe="")
        password = urllib.parse.quote(response.password, safe="")
        endpoint = response.endpoints.split(",")[0]
        return {"PENPOT_REDIS_URI": f"redis://{username}:{password}@{endpoint}/0"}
```
Replace both `**self._get_redis_credentials(),` (backend and exporter env) with `**self._get_valkey_credentials(),`.

In `_check_ready`, insert right after `public_uri = self._get_public_uri()`:
```python
        valkey_response = self._get_valkey_response()
        if valkey_response and valkey_response.tls:
            self.unit.status = ops.BlockedStatus("valkey client TLS is not supported")
            return False
```
and change the requirement entry `"redis": self._get_redis_credentials(),` to `"valkey": self._get_valkey_credentials(),`.

- [ ] **Step 9: Run unit tests to verify they pass**

Run: `tox -e unit`
Expected: all tests PASS (pydantic `UnsupportedFieldAttributeWarning` warnings from dpcharmlibs are expected and harmless). Coverage report printed.

- [ ] **Step 10: Run lint and static**

Run: `tox -e lint && tox -e static`
Expected: both succeed. If ruff complains about import order run `tox -e fmt` and re-run lint. If mypy complains about `response.username`/`password` types (`UserSecretStr`), narrow with local variables (`username = response.username`) before the truthiness checks — do not add `type: ignore`.

- [ ] **Step 11: Grep for leftovers**

Run: `grep -rn -i "redis" src/ tests/unit/ charmcraft.yaml`
Expected: only `PENPOT_REDIS_URI` and the `redis://` scheme / docstring "Penpot redis credentials".

- [ ] **Step 12: Commit**

```bash
git add -A charmcraft.yaml pyproject.toml uv.lock lib/charms src/charm.py tests/unit
git commit -m "feat!: replace redis integration with valkey

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

- [ ] **Step 13: Update the skill** with anything surprising (library API, scenario quirks, mypy narrowing).

---

### Task 2: Integration fixtures use Valkey

**Files:**
- Modify: `tests/integration/conftest.py` (constants at top, `_deploy_redis`, `deployment_fixture`)

**Interfaces:**
- Consumes: charm endpoint `valkey` (Task 1).
- Produces: `deployment` fixture returns app set containing `"valkey"` instead of `"redis-k8s"`.

- [ ] **Step 1: Replace constants and deploy helper**

Replace `REDIS_REVISION = 42` with:
```python
VALKEY_CHANNEL = "9/edge"
VALKEY_REVISION = 156
```
Delete the whole `_deploy_redis` function. In `deployment_fixture` replace `_deploy_redis(juju, Path(charm_file).parent)` with:
```python
    juju.deploy("valkey", channel=VALKEY_CHANNEL, revision=VALKEY_REVISION, trust=True)
```
Replace `juju.integrate("penpot:redis", "redis-k8s")` with:
```python
    juju.integrate("penpot:valkey", "valkey:valkey-client")
```
In `deployed_apps` replace `"redis-k8s",` with `"valkey",`.

- [ ] **Step 2: Remove now-unused imports**

Run: `tox -e lint`
Expected: ruff reports unused imports (likely `zipfile`, `yaml`, `Path`) — delete exactly those it reports, re-run until `tox -e lint` passes.

- [ ] **Step 3: Commit**

```bash
git add tests/integration/conftest.py
git commit -m "test: deploy valkey in integration tests

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

---

### Task 3: Terraform modules

**Files:**
- Modify: `terraform/charm/outputs.tf`, `terraform/charm/MAJOR_VERSION`
- Modify: `terraform/product/variables.tf`, `locals.tf`, `main.tf`, `outputs.tf`, `README.md`, `tests/main.tftest.hcl`, `MAJOR_VERSION`

- [ ] **Step 1: Charm module output**

In `terraform/charm/outputs.tf` replace the `redis = { ... endpoint = "redis" }` block with:
```hcl
    valkey = {
      kind     = "endpoint"
      name     = juju_application.penpot.name
      endpoint = "valkey"
    }
```
Keep the map keys alphabetically sorted (move the block after `s3`/`smtp` entries if the file is sorted). Set `terraform/charm/MAJOR_VERSION` content to `2`.

- [ ] **Step 2: Product variables**

In `terraform/product/variables.tf` replace the `redis_integration` and `redis_k8s` variables with:
```hcl
variable "valkey" {
  description = "Configuration of the bundled valkey charm, deployed unless `valkey_integration` is set. When `channel` is null it defaults to `9/<risk>`."
  type = object({
    app_name    = optional(string, "valkey")
    base        = optional(string, "ubuntu@26.04")
    channel     = optional(string)
    config      = optional(map(string), {})
    constraints = optional(string)
    resources   = optional(map(string), {})
    revision    = optional(number)
    units       = optional(number, 1)
  })
  default = {}
}

variable "valkey_integration" {
  description = "Existing `valkey_client` provider to integrate penpot with, given as an application `name` in `model_uuid` or an `offer_url`. When set, the bundled valkey is not deployed."
  type = object({
    name      = optional(string)
    offer_url = optional(string)
    endpoint  = optional(string)
  })
  default = null

  validation {
    condition     = var.valkey_integration == null || ((try(var.valkey_integration.name, null) == null) != (try(var.valkey_integration.offer_url, null) == null))
    error_message = "Set exactly one of name or offer_url in valkey_integration."
  }
}
```
Place them where alphabetical ordering of variables puts them (after `s3_integrator`/`s3_integration`). Update the `model_uuid` description: `redis-k8s` → `valkey`.

- [ ] **Step 3: Product locals, main, outputs**

`locals.tf`: replace `redis_k8s_channel = coalesce(var.redis_k8s.channel, "latest/${var.risk}")` with `valkey_channel = coalesce(var.valkey.channel, "9/${var.risk}")`; `deploy_redis_k8s = var.redis_integration == null` with `deploy_valkey = var.valkey_integration == null`; and the `redis_target` block with:
```hcl
  valkey_target = local.deploy_valkey ? {
    name      = one(juju_application.valkey[*].name)
    offer_url = null
    endpoint  = "valkey-client"
  } : var.valkey_integration
```
Run `terraform fmt` afterwards to realign `=`.

`main.tf`: replace `resource "juju_application" "redis_k8s"` with:
```hcl
resource "juju_application" "valkey" {
  count = local.deploy_valkey ? 1 : 0

  name       = var.valkey.app_name
  model_uuid = var.model_uuid
  trust      = true

  charm {
    name     = "valkey"
    channel  = local.valkey_channel
    revision = var.valkey.revision
    base     = var.valkey.base
  }

  config      = var.valkey.config
  constraints = var.valkey.constraints
  resources   = var.valkey.resources
  units       = var.valkey.units
}
```
and `resource "juju_integration" "penpot_redis"` with:
```hcl
resource "juju_integration" "penpot_valkey" {
  model_uuid = var.model_uuid

  application {
    name     = module.penpot.application.name
    endpoint = module.penpot.requires.valkey.endpoint
  }

  application {
    name      = local.valkey_target.name
    offer_url = local.valkey_target.offer_url
    endpoint  = local.valkey_target.endpoint
  }
}
```
`outputs.tf`: replace `{ for app in juju_application.redis_k8s : "redis_k8s" => app },` with `{ for app in juju_application.valkey : "valkey" => app },` and move it after the `s3_integrator` line so `keys()` stays alphabetical.

- [ ] **Step 4: Product tests and README**

`tests/main.tftest.hcl`:
- `external_integrations`: `redis_integration = { name = "redis" }` → `valkey_integration = { name = "valkey" }`; assert `length(juju_application.valkey) == 0 && length(juju_application.s3_integrator) == 0`, message `"the bundled valkey and s3-integrator should not be deployed when their integrations are set"`.
- `basic_deploy`: replace the `redis_k8s` block with
```hcl
    valkey = {
      # renovate: depName="valkey"
      revision = 156
    }
```
  and the components assert with `keys(output.models.penpot.components) == ["penpot", "s3_integrator", "valkey"]`, message `"the penpot model should contain penpot, s3-integrator and valkey"`.

`README.md`: replace `redis-k8s` with `valkey` and `redis_integration` with `valkey_integration`; the "published on `edge` only" sentence becomes: "Penpot is published on `edge` only, so set `risk = \"edge\"` until it is released to `stable`." Set `terraform/product/MAJOR_VERSION` to `2`.

- [ ] **Step 5: Validate**

```bash
cd terraform/product && terraform fmt -recursive -check .. ; terraform init -backend=false >/dev/null && terraform validate
cd ../charm && terraform init -backend=false >/dev/null && terraform validate
grep -rn -i redis ..
```
Expected: fmt clean, `Success! The configuration is valid.` twice, grep finds nothing. (`terraform test` needs a controller — left to CI.) Remove any `.terraform/` and `.terraform.lock.hcl` created by init if they are not tracked (`git status`).

- [ ] **Step 6: Commit**

```bash
git add terraform
git commit -m "feat(terraform)!: deploy and integrate valkey instead of redis-k8s

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

---

### Task 4: Pack the charm (host)

- [ ] **Step 1: Pack**

Run (host, repo root): `charmcraft pack`
Expected: `penpot_amd64.charm` refreshed in repo root (visible in the VM at `/home/ubuntu/projects/penpot-operator/penpot_amd64.charm`). Do **not** run rockcraft.

- [ ] **Step 2: Sanity-check the packed metadata**

```bash
unzip -p penpot_amd64.charm metadata.yaml | grep -A2 -E "valkey|redis"
unzip -l penpot_amd64.charm | grep -c "dpcharmlibs/interfaces"
```
Expected: `valkey:` with `interface: valkey_client`, no `redis`; count > 0.

---

### Task 5: Prepare the VM and spike Valkey (risk check)

All commands run inside the VM: prefix with `multipass exec penpot-valkey -- bash -lc '<cmd>'`, or open `multipass shell penpot-valkey`.

- [ ] **Step 1: Confirm the VM is idle (run before EVERY deployment/test)**

```bash
juju models --format json | python3 -c '
import json, subprocess, sys
for m in json.load(sys.stdin)["models"]:
    name = m["short-name"]
    if name == "controller":
        continue
    st = json.loads(subprocess.check_output(["juju", "status", "-m", name, "--format", "json"]))
    print(name, "apps:", sorted(st.get("applications", {})))
'
```
Expected: every model shows `apps: []`. If any leftover test model has apps, ask the user before destroying it (`juju destroy-model <name> --no-prompt --destroy-storage --force`).

- [ ] **Step 2: Install test tooling (once)**

```bash
uv tool install tox --with tox-uv
export PATH="$HOME/.local/bin:$PATH"; tox --version
ls ~/.kube/config || sudo k8s config > ~/.kube/config
cd /home/ubuntu/projects/penpot-operator
bash tests/integration/s3-installation.sh
tox --workdir /home/ubuntu/.cache/penpot-tox -e playwright-install
```
Expected: tox ≥ 4.21; microceph RGW reachable on `127.0.0.1:7480`; chromium installed.

- [ ] **Step 3: Prepare the Penpot image resource file (credentials stay in the VM)**

```bash
curl -sL "https://api.charmhub.io/api/v1/resources/download/charm_GO27fkneNgjuKWaGWhMg3PnyqjKTA2fB.penpot-image_84" \
  | python3 -c 'import json,sys; d=json.load(sys.stdin); print(json.dumps({"registrypath": d["ImageName"], "username": d["Username"], "password": d["Password"]}))' \
  > /home/ubuntu/penpot-image.json
chmod 600 /home/ubuntu/penpot-image.json
python3 -c 'import json; print(json.load(open("/home/ubuntu/penpot-image.json"))["registrypath"].split("@")[0])'
```
Expected: `registry.jujucharms.com/charm/.../penpot-image`. If later the pod shows `ErrImagePull`/unauthorized, regenerate this file (the token may expire). Fallback: `skopeo copy` the image to a local registry and pass the plain image reference.

- [ ] **Step 4: Spike — Valkey deploys and the client ACL allows Penpot's commands**

```bash
juju add-model valkey-spike
juju deploy valkey --channel 9/edge --revision 156 --trust
juju deploy data-integrator --channel latest/edge --config prefix-name='*'
juju integrate data-integrator valkey
juju wait-for application valkey --query='status=="active"' --timeout 20m
juju wait-for application data-integrator --query='status=="active"' --timeout 10m
juju run data-integrator/leader get-credentials
```
Expected: valkey goes active on Juju 3.6.29 (base 26.04); credentials show `endpoints`, `username`, `password`, `tls: "False"`. If `prefix-name` is not the config key, check `juju config data-integrator` and use the documented key.

Then, with `U`, `P`, `H` (host part of `endpoints`) from the output:
```bash
V="juju ssh --container valkey valkey/0 valkey-cli -h $H -p 6379 --user $U --pass $P --no-auth-warning"
$V HELLO 3
$V CLIENT SETINFO LIB-NAME Lettuce
$V CLIENT SETNAME penpot
$V SELECT 0
$V RPUSH penpot.worker.queue:default:test x
$V BLPOP penpot.worker.queue:default:test 1
$V PUBLISH default.test hello
$V EVAL "return redis.call('set', KEYS[1], ARGV[1])" 1 penpot.rlimit.test 1
```
Expected: data commands succeed. **Record in the skill exactly which of `HELLO`, `CLIENT SETINFO`, `CLIENT SETNAME` return `NOPERM`.** If `valkey-cli` is not in the container, find the binary with `juju ssh --container valkey valkey/0 ls /usr/bin /usr/local/bin | grep -i cli`.

- [ ] **Step 5: Tear down the spike**

```bash
juju destroy-model valkey-spike --no-prompt --destroy-storage
```
Re-run Step 1 → all models empty.

- [ ] **Step 6: Update the skill** with spike results (deploy time, base 26.04 on Juju 3.6 result, ACL results, image-resource handling).

---

### Task 6: Integration tests in the VM (one at a time)

- [ ] **Step 1: Idle check** — run Task 5 Step 1. All models empty.

- [ ] **Step 2: Run `test_create_profile` alone**

```bash
cd /home/ubuntu/projects/penpot-operator && export PATH="$HOME/.local/bin:$PATH"
tox --workdir /home/ubuntu/.cache/penpot-tox -e integration -- \
  "tests/integration/test_charm.py::test_create_profile" \
  --charm-file penpot=/home/ubuntu/projects/penpot-operator/penpot_amd64.charm \
  --resource-image penpot-image=/home/ubuntu/penpot-image.json \
  2>&1 | tee /home/ubuntu/it-create-profile.log
```
Run it in async mode and wait for completion (it takes tens of minutes); do not start anything else meanwhile.
Expected: `1 passed`.

On failure (keep loops small):
- Re-run once with `--keep-models` appended, then inspect: `juju status -m <model>`, `juju debug-log -m <model> --replay --include penpot --limit 300`, `juju ssh -m <model> --container penpot penpot/0 pebble logs backend -n 200`.
- Look specifically for Lettuce `NOPERM` / `WRONGPASS` / connection errors.
- Fix → `tox -e unit,lint` on the host → `charmcraft pack` on the host → destroy the kept model → Step 1 → rerun only this test.
- Record root cause + fix in the skill.

- [ ] **Step 3: Capture real status output for the docs**

While a kept model exists (or after a passing run with `--keep-models`), save `juju status` output for the `valkey` application/unit rows to `/home/ubuntu/valkey-status.txt` for Task 7, then destroy the model.

- [ ] **Step 4: Idle check, then run `test_oauth_login` alone**

```bash
tox --workdir /home/ubuntu/.cache/penpot-tox -e integration -- \
  "tests/integration/test_charm.py::test_oauth_login" \
  --charm-file penpot=/home/ubuntu/projects/penpot-operator/penpot_amd64.charm \
  --resource-image penpot-image=/home/ubuntu/penpot-image.json \
  2>&1 | tee /home/ubuntu/it-oauth-login.log
```
Expected: `1 passed`. Same failure loop as Step 2.

- [ ] **Step 5: Idle check, then run the whole module once**

```bash
tox --workdir /home/ubuntu/.cache/penpot-tox -e integration -- tests/integration/test_charm.py \
  --charm-file penpot=/home/ubuntu/projects/penpot-operator/penpot_amd64.charm \
  --resource-image penpot-image=/home/ubuntu/penpot-image.json \
  2>&1 | tee /home/ubuntu/it-all.log
```
Expected: `2 passed`.

- [ ] **Step 6: Commit any fixes** made during this task (each with the trailer), and update the skill.

---

### Task 7: Documentation and changelog

**Files:**
- Modify: `docs/tutorial/getting-started.md`, `docs/reference/charm-architecture.md`, `docs/how-to/upgrade.md`, `CHANGELOG.md`

- [ ] **Step 1: Tutorial**

In `docs/tutorial/getting-started.md`:
- Step list item 1: `(PostgreSQL, Redis, s3, and so on)` → `(PostgreSQL, Valkey, s3, and so on)`.
- `juju deploy redis-k8s --channel latest/edge` → `juju deploy valkey --channel 9/edge --trust`.
- `juju integrate penpot redis-k8s` → `juju integrate penpot:valkey valkey:valkey-client`.
- In the sample `juju status` output replace the `redis-k8s` app row and `redis-k8s/0*` unit row with the real rows captured in `/home/ubuntu/valkey-status.txt` (Task 6 Step 3), aligned to the existing columns.

- [ ] **Step 2: Architecture reference**

In `docs/reference/charm-architecture.md` replace:
```
Container_Boundary(redis, "Redis Charm") {
    ComponentDb(penpot-broker, "Redis", "Penpot broker","")
}
```
with:
```
Container_Boundary(valkey, "Valkey Charm") {
    ComponentDb(penpot-broker, "Valkey", "Penpot broker","")
}
```
and the `### \`redis\` integration` section with:
```markdown
### `valkey` integration

The [`valkey`](https://charmhub.io/valkey) charm provides the Valkey key-value store, a
Redis-compatible database, required for Penpot to run. Penpot requests access to all keys and
Pub/Sub channels. Valkey client TLS isn't supported yet; the charm is blocked if it's enabled.
```
Check the file for any other `redis` mention (`grep -n -i redis docs/reference/charm-architecture.md`) and update it the same way.

- [ ] **Step 3: Upgrade how-to**

Append to `docs/how-to/upgrade.md`:
````markdown
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
````

- [ ] **Step 4: Changelog**

Under the header text in `CHANGELOG.md` add:
```markdown
## 2026-11-05

### Changed

- Replaced the `redis` integration with the `valkey` integration (`valkey_client` interface,
  [Charmed Valkey](https://charmhub.io/valkey) `9/edge`). See the upgrade guide for the
  migration steps. The Terraform charm and product modules move to major version 2.
```

- [ ] **Step 5: Lint and commit**

```bash
tox -e lint
grep -rn -i "redis" docs/ README.md | grep -v -i "redis-compatible\|PENPOT_REDIS\|from redis to valkey\|redis integration\|redis-k8s"
git add docs CHANGELOG.md
git commit -m "docs: document the valkey integration and migration

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```
Expected: lint (codespell) passes; grep prints nothing unexpected (the remaining mentions are the intentional migration ones).

---

### Task 8: Final full check, then one local spread run

- [ ] **Step 1: Everything green on the host**

Run: `tox -e lint,unit,static`
Expected: all three succeed. Confirm Task 6 Step 5 passed with the current charm (re-pack + re-run only if the charm code changed since).

- [ ] **Step 2: Prepare `build/artifacts.build.yaml` without packing the rock**

The local rock `penpot_rock/penpot_2.14.3_amd64.rock` already matches `rockcraft.yaml` (`version: 2.14.3`, unchanged). Create (host, repo root; `build/` is gitignored):
```bash
mkdir -p build
cat > build/artifacts.build.yaml <<'EOF'
version: 1
rocks:
  - name: penpot
    rockcraft-yaml: penpot_rock/rockcraft.yaml
    builds:
      - arch: amd64
        file: ./penpot_rock/penpot_2.14.3_amd64.rock
charms: []
EOF
opcli artifacts build --charm penpot
cat build/artifacts.build.yaml
```
Expected: the file lists the rock `file:` build **and** the charm build `path:`. If opcli dropped the rock entry, re-add it by hand, keeping the charm entry opcli wrote (including its `resources` mapping `penpot-image` → rock `penpot`).

- [ ] **Step 3: Single local spread run**

```bash
opcli spread run -- -list
opcli spread run
```
Run in async mode; it provisions VMs and takes a long time. Expected: all tasks on `integration-test` (Juju 3.6) and `integration-test-juju4` PASS. If the user prefers to limit to Juju 3, use the selector printed by `-list` for `integration-test:ubuntu-24.04:tests/integration/...`.

- [ ] **Step 4: Final skill update and hand-off**

Update the skill with final lessons, then report to the user: commits, test evidence (log tails), and the follow-up TLS PR.

## Not covered (explicit)

- Valkey client TLS (follow-up PR).
- Automated upgrade test from a redis-backed deployment (the documented manual steps are not exercised by CI).
- `terraform test` (needs a controller; runs in CI on PR).
