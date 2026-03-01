# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Clusterd models for Sunbeam."""

import typing

import pydantic

from sunbeam import utils


class StorageBackend(pydantic.BaseModel):
    """Storage backend model."""

    model_config = pydantic.ConfigDict(
        alias_generator=pydantic.AliasGenerator(
            validation_alias=utils.to_kebab,
            serialization_alias=utils.to_kebab,
        ),
    )
    name: str
    type: str
    config: pydantic.Json[dict[str, typing.Any]]
    principal: str
    model_uuid: str


class StorageBackends(pydantic.RootModel[list[StorageBackend]]):
    """Storage backends model."""


class FeatureGate(pydantic.BaseModel):
    """Feature gate model."""

    model_config = pydantic.ConfigDict(
        alias_generator=pydantic.AliasGenerator(
            validation_alias=utils.to_kebab,
            serialization_alias=utils.to_kebab,
        ),
    )
    gate_key: str
    enabled: bool


class FeatureGates(pydantic.RootModel[list[FeatureGate]]):
    """Feature gates model."""
