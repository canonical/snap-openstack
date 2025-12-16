# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import pydantic

from sunbeam.core.manifest import (
    FeatureConfig,
)


class BaremetalFeatureConfig(FeatureConfig):
    shards: list[str] = pydantic.Field(examples=["foo", "bar"], default=[])

    @pydantic.field_validator("shards")
    @classmethod
    def validate_shards(cls, v: list):
        """Validate shards."""
        if len(v) == 0:
            return v

        if len(v) != len(set(v)):
            raise ValueError("Shards must be unique.")

        return v
