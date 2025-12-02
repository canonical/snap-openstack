# SPDX-FileCopyrightText: 2024 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

from unittest.mock import Mock, patch

import pytest

from sunbeam.core.common import ResultType
from sunbeam.core.deployment import Networks
from sunbeam.core.terraform import TerraformException
from sunbeam.features.instance_recovery import consul as consul_feature


@pytest.fixture()
def consulfeature():
    with patch("sunbeam.features.instance_recovery.consul.ConsulFeature") as p:
        yield p


@pytest.fixture()
def update_config():
    with patch("sunbeam.features.instance_recovery.consul.update_config") as p:
        yield p


class TestDeployConsulClientStep:
    def test_get_enable_tcp_check_options_with_storage(
        self, deployment, tfhelper, jhelper, manifest
    ):
        """Test TCP check option when storage network exists."""
        step = consul_feature.DeployConsulClientStep(
            deployment, tfhelper, tfhelper, jhelper, manifest
        )

        clients_to_enable = {
            consul_feature.ConsulServerNetworks.MANAGEMENT: True,
            consul_feature.ConsulServerNetworks.TENANT: True,
            consul_feature.ConsulServerNetworks.STORAGE: True,
        }

        result = step._get_enable_tcp_check_options(clients_to_enable)

        assert result[consul_feature.ConsulServerNetworks.MANAGEMENT] is False
        assert result[consul_feature.ConsulServerNetworks.TENANT] is False
        assert result[consul_feature.ConsulServerNetworks.STORAGE] is True

    def test_get_enable_tcp_check_options_without_storage(
        self, deployment, tfhelper, jhelper, manifest
    ):
        """Test TCP check option when storage network doesn't exist."""
        step = consul_feature.DeployConsulClientStep(
            deployment, tfhelper, tfhelper, jhelper, manifest
        )

        clients_to_enable = {
            consul_feature.ConsulServerNetworks.MANAGEMENT: True,
            consul_feature.ConsulServerNetworks.TENANT: True,
            consul_feature.ConsulServerNetworks.STORAGE: False,
        }

        result = step._get_enable_tcp_check_options(clients_to_enable)

        assert result[consul_feature.ConsulServerNetworks.MANAGEMENT] is True
        assert result[consul_feature.ConsulServerNetworks.TENANT] is False
        assert result[consul_feature.ConsulServerNetworks.STORAGE] is False

    def test_get_enable_tcp_check_options_with_only_management(
        self, deployment, tfhelper, jhelper, manifest
    ):
        """Test TCP check option when storage network doesn't exist."""
        step = consul_feature.DeployConsulClientStep(
            deployment, tfhelper, tfhelper, jhelper, manifest
        )

        clients_to_enable = {
            consul_feature.ConsulServerNetworks.MANAGEMENT: True,
            consul_feature.ConsulServerNetworks.TENANT: False,
            consul_feature.ConsulServerNetworks.STORAGE: False,
        }

        result = step._get_enable_tcp_check_options(clients_to_enable)

        assert result[consul_feature.ConsulServerNetworks.MANAGEMENT] is True
        assert result[consul_feature.ConsulServerNetworks.TENANT] is False
        assert result[consul_feature.ConsulServerNetworks.STORAGE] is False

    @patch(
        "sunbeam.features.instance_recovery.consul.ConsulFeature.consul_servers_to_enable"
    )
    @patch(
        "sunbeam.features.instance_recovery.consul.ConsulFeature.get_config_from_manifest"
    )
    def test_get_tfvars_with_tcp_check_defaults(
        self,
        mock_get_config,
        mock_servers_to_enable,
        deployment,
        tfhelper,
        jhelper,
        manifest,
    ):
        """Test that TCP health check is set by default when not in manifest."""
        mock_servers_to_enable.return_value = {
            consul_feature.ConsulServerNetworks.MANAGEMENT: True,
            consul_feature.ConsulServerNetworks.TENANT: False,
            consul_feature.ConsulServerNetworks.STORAGE: True,
        }
        mock_get_config.return_value = {}

        step = consul_feature.DeployConsulClientStep(
            deployment, tfhelper, tfhelper, jhelper, manifest
        )

        result = step._get_tfvars()

        # Management should be False, Storage should be True
        assert (
            result["consul-config-map"]["consul-management"]["enable-tcp-health-check"]
            is False
        )
        assert (
            result["consul-config-map"]["consul-storage"]["enable-tcp-health-check"]
            is True
        )

    @patch(
        "sunbeam.features.instance_recovery.consul.ConsulFeature.consul_servers_to_enable"
    )
    @patch(
        "sunbeam.features.instance_recovery.consul.ConsulFeature.get_config_from_manifest"
    )
    def test_get_tfvars_with_manifest_override(
        self,
        mock_get_config,
        mock_servers_to_enable,
        deployment,
        tfhelper,
        jhelper,
        manifest,
    ):
        """Test that manifest config overrides default TCP health check setting."""
        mock_servers_to_enable.return_value = {
            consul_feature.ConsulServerNetworks.MANAGEMENT: True,
            consul_feature.ConsulServerNetworks.TENANT: False,
            consul_feature.ConsulServerNetworks.STORAGE: False,
        }
        # Manifest explicitly sets enable-tcp-health-check
        mock_get_config.return_value = {"enable-tcp-health-check": False}

        step = consul_feature.DeployConsulClientStep(
            deployment, tfhelper, tfhelper, jhelper, manifest
        )

        result = step._get_tfvars()

        # Should respect manifest value (False) instead of default (True)
        assert (
            result["consul-config-map"]["consul-management"]["enable-tcp-health-check"]
            is False
        )

    @patch(
        "sunbeam.features.instance_recovery.consul.ConsulFeature.consul_servers_to_enable"
    )
    @patch(
        "sunbeam.features.instance_recovery.consul.ConsulFeature.get_config_from_manifest"
    )
    def test_get_tfvars_all_networks_enabled(
        self,
        mock_get_config,
        mock_servers_to_enable,
        deployment,
        tfhelper,
        jhelper,
        manifest,
    ):
        """Test TCP health check settings when all networks are enabled."""
        mock_servers_to_enable.return_value = {
            consul_feature.ConsulServerNetworks.MANAGEMENT: True,
            consul_feature.ConsulServerNetworks.TENANT: True,
            consul_feature.ConsulServerNetworks.STORAGE: True,
        }
        mock_get_config.return_value = {}

        step = consul_feature.DeployConsulClientStep(
            deployment, tfhelper, tfhelper, jhelper, manifest
        )

        result = step._get_tfvars()

        # Only storage should have TCP check enabled
        assert (
            result["consul-config-map"]["consul-management"]["enable-tcp-health-check"]
            is False
        )
        assert (
            result["consul-config-map"]["consul-tenant"]["enable-tcp-health-check"]
            is False
        )
        assert (
            result["consul-config-map"]["consul-storage"]["enable-tcp-health-check"]
            is True
        )

    def test_run(self, deployment, tfhelper, jhelper, consulfeature, manifest):
        step = consul_feature.DeployConsulClientStep(
            deployment, tfhelper, tfhelper, jhelper, manifest
        )
        result = step.run()

        tfhelper.update_tfvars_and_apply_tf.assert_called_once()
        jhelper.wait_until_desired_status.assert_called_once()
        assert result.result_type == ResultType.COMPLETED

    def test_run_tf_apply_failed(
        self, deployment, tfhelper, jhelper, consulfeature, manifest
    ):
        tfhelper.update_tfvars_and_apply_tf.side_effect = TerraformException(
            "apply failed..."
        )
        step = consul_feature.DeployConsulClientStep(
            deployment, tfhelper, tfhelper, jhelper, manifest
        )
        result = step.run()

        tfhelper.update_tfvars_and_apply_tf.assert_called_once()
        jhelper.wait_until_desired_status.assert_not_called()
        assert result.result_type == ResultType.FAILED
        assert result.message == "apply failed..."

    def test_run_waiting_timed_out(
        self, deployment, tfhelper, jhelper, consulfeature, manifest
    ):
        jhelper.wait_until_desired_status.side_effect = TimeoutError("timed out")

        step = consul_feature.DeployConsulClientStep(
            deployment, tfhelper, tfhelper, jhelper, manifest
        )
        result = step.run()

        tfhelper.update_tfvars_and_apply_tf.assert_called_once()
        jhelper.wait_until_desired_status.assert_called_once()
        assert result.result_type == ResultType.FAILED
        assert result.message == "timed out"


class TestRemoveConsulClientStep:
    def test_run(self, deployment, tfhelper, jhelper, update_config):
        step = consul_feature.RemoveConsulClientStep(deployment, tfhelper, jhelper)
        result = step.run()

        tfhelper.destroy.assert_called_once()
        jhelper.wait_application_gone.assert_called_once()
        assert result.result_type == ResultType.COMPLETED

    def test_run_tf_destroy_failed(self, deployment, tfhelper, jhelper, update_config):
        tfhelper.destroy.side_effect = TerraformException("destroy failed...")

        step = consul_feature.RemoveConsulClientStep(deployment, tfhelper, jhelper)
        result = step.run()

        tfhelper.destroy.assert_called_once()
        jhelper.wait_application_gone.assert_not_called()
        assert result.result_type == ResultType.FAILED
        assert result.message == "destroy failed..."

    def test_run_waiting_timed_out(self, deployment, tfhelper, jhelper, update_config):
        jhelper.wait_application_gone.side_effect = TimeoutError("timed out")

        step = consul_feature.RemoveConsulClientStep(deployment, tfhelper, jhelper)
        result = step.run()

        tfhelper.destroy.assert_called_once()
        jhelper.wait_application_gone.assert_called_once()
        assert result.result_type == ResultType.FAILED
        assert result.message == "timed out"


class TestConsulFeature:
    @pytest.mark.parametrize(
        "spaces,expected_output",
        [
            (["mgmt", "mgmt", "mgmt"], [True, False, False]),
            (["mgmt", "data", "mgmt"], [True, True, False]),
            (["mgmt", "data", "storage"], [True, True, True]),
            (["mgmt", "mgmt", "storage"], [True, False, True]),
            (["mgmt", "data", "data"], [True, False, True]),
        ],
    )
    def test_set_tfvars_on_enable(
        self, deployment, snap, spaces, expected_output, manifest
    ):
        def _get_space(network: Networks):
            if network == Networks.MANAGEMENT:
                return spaces[0]
            elif network == Networks.DATA:
                return spaces[1]
            elif network == Networks.STORAGE:
                return spaces[2]

            return spaces[0]

        deployment.get_space.side_effect = _get_space

        consul = consul_feature.ConsulFeature()
        feature_config = Mock()
        extra_tfvars = consul.set_tfvars_on_enable(deployment, feature_config, manifest)

        # Verify enable-consul-<> vars are set to true/false based on spaces defined
        for (
            index,
            server,
        ) in enumerate(
            [
                "enable-consul-management",
                "enable-consul-tenant",
                "enable-consul-storage",
            ]
        ):
            assert extra_tfvars.get(server) is expected_output[index]
