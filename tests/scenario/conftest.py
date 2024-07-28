# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Scenario test fixtures."""

import json

from scenario import Secret, Relation


class Helper:
    @property
    def postgresql_relation(self) -> Relation:
        return Relation("postgresql")

    @property
    def redis_relation(self) -> Relation:
        return Relation("redis")

    @property
    def smtp_relation(self) -> Relation:
        return Relation("smtp")

    @property
    def s3_relation(self) -> Relation:
        return Relation("s3")

    @property
    def ingress_relation(self) -> Relation:
        return Relation("ingress")
