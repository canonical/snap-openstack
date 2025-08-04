# SPDX-FileCopyrightText: 2024 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import json
from unittest.mock import Mock

import pytest

from sunbeam.features.shared_filesystem import feature as manila_feature


@pytest.fixture()
def deployment():
    deploy = Mock()
    client = deploy.get_client.return_value

    client.cluster.get_config.return_value = json.dumps(
        {
            "database": "multi",
            "horizon-plugins": ["foo"],
        }
    )

    yield deploy


class TestSharedFilesystemFeature:
    def test_set_application_names(self, deployment):
        manila = manila_feature.SharedFilesystemFeature()

        apps = manila.set_application_names(deployment)

        expected_apps = [
            "manila",
            "manila-mysql-router",
            "manila-cephfs",
            "manila-cephfs-mysql-router",
            "manila-mysql",
        ]
        assert expected_apps == apps

    def test_set_tfvars_on_enable(self, deployment):
        manila = manila_feature.SharedFilesystemFeature()
        feature_config = Mock()

        extra_tfvars = manila.set_tfvars_on_enable(deployment, feature_config)

        expected_tfvars = {
            "enable-manila": True,
            "enable-manila-cephfs": True,
            "enable-ceph-nfs": True,
            "horizon-plugins": ["foo", "manila"],
        }
        assert extra_tfvars == expected_tfvars

    def test_set_tfvars_on_disable(self, deployment):
        manila = manila_feature.SharedFilesystemFeature()

        extra_tfvars = manila.set_tfvars_on_disable(deployment)

        expected_tfvars = {
            "enable-manila": False,
            "enable-manila-cephfs": False,
            "enable-ceph-nfs": False,
            "horizon-plugins": ["foo"],
        }
        assert extra_tfvars == expected_tfvars
