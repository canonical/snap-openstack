# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import pydantic
import pytest

from sunbeam.features.baremetal import feature_config


class TestBaremetalFeatureConfig:
    def test_validate_shards(self):
        # must be list.
        with pytest.raises(pydantic.ValidationError):
            feature_config.BaremetalFeatureConfig(shards="foo")

        # must be strings.
        with pytest.raises(pydantic.ValidationError):
            feature_config.BaremetalFeatureConfig(shards=[5])

        # no duplicates.
        with pytest.raises(pydantic.ValidationError):
            feature_config.BaremetalFeatureConfig(shards=["foo", "foo"])

        # valid examples.
        feature_config.BaremetalFeatureConfig()
        feature_config.BaremetalFeatureConfig(shards=[])
        feature_config.BaremetalFeatureConfig(shards=["foo", "lish"])
