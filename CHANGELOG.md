# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

Each revision is versioned by the date of the revision.

## 2026-11-16

### Changed

- Terraform: the charm and product modules use Valkey instead of Redis (breaking, module
  version 2). The charm module exposes `requires.valkey` instead of `requires.redis`. The product
  module deploys Charmed Valkey (`9/<risk>`) and replaces `redis_k8s` and `redis_integration` with
  `valkey` and `valkey_integration`. See the upgrade guide for the migration steps.

## 2026-11-05

### Changed

- Replaced the `redis` integration with the `valkey` integration (`valkey_client` interface,
  [Charmed Valkey](https://charmhub.io/valkey) `9/edge`). See the upgrade guide for the
  migration steps.
