# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

Each revision is versioned by the date of the revision.

## 2026-11-05

### Changed

- Replaced the `redis` integration with the `valkey` integration (`valkey_client` interface,
  [Charmed Valkey](https://charmhub.io/valkey) `9/edge`). See the upgrade guide for the
  migration steps. The Terraform charm and product modules move to major version 2.
