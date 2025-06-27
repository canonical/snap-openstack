# SPDX-FileCopyrightText: 2024 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

from unittest.mock import MagicMock, Mock, patch

import pytest

from sunbeam.core.common import ResultType
from sunbeam.core.deployment import Networks
from sunbeam.core.terraform import TerraformException
from sunbeam.features.instance_recovery import consul as consul_feature


@pytest.fixture()
def tfhelper():
    yield Mock()


@pytest.fixture()
def jhelper():
    yield Mock()


@pytest.fixture()
def deployment():
    yield Mock()


@pytest.fixture()
def manifest():
    yield MagicMock()


@pytest.fixture()
def consulfeature():
    with patch("sunbeam.features.instance_recovery.consul.ConsulFeature") as p:
        yield p


@pytest.fixture()
def update_config():
    with patch("sunbeam.features.instance_recovery.consul.update_config") as p:
        yield p


class TestDeployConsulClientStep:
    def test_run(self, deployment, tfhelper, jhelper, consulfeature):
        step = consul_feature.DeployConsulClientStep(
            deployment, tfhelper, tfhelper, jhelper, manifest
        )
        result = step.run()

        tfhelper.update_tfvars_and_apply_tf.assert_called_once()
        jhelper.wait_until_desired_status.assert_called_once()
        assert result.result_type == ResultType.COMPLETED

    def test_run_tf_apply_failed(self, deployment, tfhelper, jhelper, consulfeature):
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

    def test_run_waiting_timed_out(self, deployment, tfhelper, jhelper, consulfeature):
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
