# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import pydantic

from sunbeam.core.manifest import (
    FeatureConfig,
)


class BaremetalFeatureConfig(FeatureConfig):
    shards: list[str] = pydantic.Field(examples=["foo", "bar"], default=[])

    conductor_groups: list[str] = pydantic.Field(
        examples=["foo", "bar"],
        alias="conductor-groups",
        validation_alias="conductor_groups",
        default=[],
    )

    @pydantic.field_validator("shards")
    @classmethod
    def validate_shards(cls, v: list):
        """Validate shards."""
        if len(v) == 0:
            return v

        if len(v) != len(set(v)):
            raise ValueError("Shards must be unique.")

        return v

    @pydantic.field_validator("conductor_groups")
    @classmethod
    def validate_conductor_groups(cls, v: list):
        """Validate conductor_groups."""
        if len(v) == 0:
            return v

        if len(v) != len(set(v)):
            raise ValueError("Conductor groups must be unique.")

        return v
