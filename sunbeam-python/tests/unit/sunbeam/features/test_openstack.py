# SPDX-FileCopyrightText: 2023 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

from unittest.mock import Mock, patch

import pytest

import sunbeam.features.interface.v1.openstack as openstack
from sunbeam.core.common import ResultType
from sunbeam.core.terraform import TerraformException


@pytest.fixture()
def osfeature():
    with patch(
        "sunbeam.features.interface.v1.openstack.OpenStackControlPlaneFeature"
    ) as p:
        yield p


class TestEnableOpenStackApplicationStep:
    def test_run(self, deployment, tfhelper, jhelper, osfeature):
        step = openstack.EnableOpenStackApplicationStep(
            deployment, Mock(), tfhelper, jhelper, osfeature
        )
        result = step.run()

        tfhelper.update_tfvars_and_apply_tf.assert_called_once()
        jhelper.wait_until_desired_status.assert_called_once()
        assert result.result_type == ResultType.COMPLETED

    def test_run_tf_apply_failed(self, deployment, jhelper, tfhelper, osfeature):
        tfhelper.update_tfvars_and_apply_tf.side_effect = TerraformException(
            "apply failed..."
        )

        step = openstack.EnableOpenStackApplicationStep(
            deployment, Mock(), tfhelper, jhelper, osfeature
        )
        result = step.run()

        tfhelper.update_tfvars_and_apply_tf.assert_called_once()
        jhelper.wait_until_desired_status.assert_not_called()
        assert result.result_type == ResultType.FAILED
        assert result.message == "apply failed..."

    def test_run_waiting_timed_out(self, deployment, jhelper, tfhelper, osfeature):
        jhelper.wait_until_desired_status.side_effect = TimeoutError("timed out")

        step = openstack.EnableOpenStackApplicationStep(
            deployment, Mock(), tfhelper, jhelper, osfeature
        )
        result = step.run()

        tfhelper.update_tfvars_and_apply_tf.assert_called_once()
        jhelper.wait_until_desired_status.assert_called_once()
        assert result.result_type == ResultType.FAILED
        assert result.message == "timed out"

    def test_run_with_agent_desired_status(
        self, deployment, tfhelper, jhelper, osfeature
    ):
        """Test that agent_desired_status is passed to wait_until_desired_status."""
        step = openstack.EnableOpenStackApplicationStep(
            deployment,
            Mock(),
            tfhelper,
            jhelper,
            osfeature,
            app_desired_status=["active"],
            agent_desired_status=["idle"],
        )
        result = step.run()

        tfhelper.update_tfvars_and_apply_tf.assert_called_once()
        jhelper.wait_until_desired_status.assert_called_once()
        call_kwargs = jhelper.wait_until_desired_status.call_args[1]
        assert call_kwargs["status"] == ["active"]
        assert call_kwargs["agent_status"] == ["idle"]
        assert result.result_type == ResultType.COMPLETED

    def test_run_without_agent_desired_status(
        self, deployment, tfhelper, jhelper, osfeature
    ):
        """Test backward compatibility when agent_desired_status is not provided."""
        step = openstack.EnableOpenStackApplicationStep(
            deployment,
            Mock(),
            tfhelper,
            jhelper,
            osfeature,
            app_desired_status=["active"],
        )
        result = step.run()

        tfhelper.update_tfvars_and_apply_tf.assert_called_once()
        jhelper.wait_until_desired_status.assert_called_once()
        call_kwargs = jhelper.wait_until_desired_status.call_args[1]
        assert call_kwargs["status"] == ["active"]
        assert call_kwargs["agent_status"] is None
        assert result.result_type == ResultType.COMPLETED


class TestDisableOpenStackApplicationStep:
    def test_run(self, deployment, tfhelper, jhelper, osfeature):
        step = openstack.DisableOpenStackApplicationStep(
            deployment, tfhelper, jhelper, osfeature
        )
        result = step.run()

        tfhelper.update_tfvars_and_apply_tf.assert_called_once()
        assert result.result_type == ResultType.COMPLETED

    def test_run_tf_apply_failed(self, deployment, tfhelper, jhelper, osfeature):
        tfhelper.update_tfvars_and_apply_tf.side_effect = TerraformException(
            "apply failed..."
        )

        step = openstack.DisableOpenStackApplicationStep(
            deployment, tfhelper, jhelper, osfeature
        )
        result = step.run()

        tfhelper.update_tfvars_and_apply_tf.assert_called_once()
        assert result.result_type == ResultType.FAILED
        assert result.message == "apply failed..."

    def test_run_waiting_timed_out(self, deployment, tfhelper, jhelper, osfeature):
        jhelper.wait_application_gone.side_effect = TimeoutError("timed out")

        step = openstack.DisableOpenStackApplicationStep(
            deployment, tfhelper, jhelper, osfeature
        )
        result = step.run()

        tfhelper.update_tfvars_and_apply_tf.assert_called_once()
        jhelper.wait_application_gone.assert_called_once()
        assert result.result_type == ResultType.FAILED
        assert result.message == "timed out"

    def test_calls_set_application_timeout_on_disable_with_deployment(
        self, deployment, tfhelper, jhelper, osfeature
    ):
        """Test that set_application_timeout_on_disable is called with deployment."""
        osfeature.set_application_timeout_on_disable.return_value = 1800

        step = openstack.DisableOpenStackApplicationStep(
            deployment, tfhelper, jhelper, osfeature
        )
        step.run()

        osfeature.set_application_timeout_on_disable.assert_called_once_with(deployment)


class TestUpgradeOpenStackApplicationStep:
    def test_run(
        self,
        deployment,
        tfhelper,
        jhelper,
        osfeature,
    ):
        jhelper.get_model_status.return_value = Mock(
            apps={
                "keystone": Mock(
                    charm="keystone-k8s",
                    charm_channel="2023.2/stable",
                )
            }
        )

        step = openstack.UpgradeOpenStackApplicationStep(
            deployment, tfhelper, jhelper, osfeature
        )
        result = step.run()

        tfhelper.update_partial_tfvars_and_apply_tf.assert_called_once()
        jhelper.wait_until_desired_status.assert_called_once()
        assert result.result_type == ResultType.COMPLETED

    def test_run_tf_apply_failed(self, deployment, tfhelper, jhelper, osfeature):
        tfhelper.update_partial_tfvars_and_apply_tf.side_effect = TerraformException(
            "apply failed..."
        )

        jhelper.get_model_status.return_value = Mock(
            apps={
                "keystone": Mock(
                    charm="keystone-k8s",
                    charm_channel="2023.2/stable",
                )
            }
        )

        step = openstack.UpgradeOpenStackApplicationStep(
            deployment, tfhelper, jhelper, osfeature
        )
        result = step.run()

        tfhelper.update_partial_tfvars_and_apply_tf.assert_called_once()
        jhelper.wait_until_desired_status.assert_not_called()
        assert result.result_type == ResultType.FAILED
        assert result.message == "apply failed..."

    def test_run_waiting_timed_out(self, deployment, tfhelper, jhelper, osfeature):
        jhelper.wait_until_desired_status.side_effect = TimeoutError("timed out")

        jhelper.get_model_status.return_value = Mock(
            apps={
                "keystone": Mock(
                    charm="keystone-k8s",
                    charm_channel="2023.2/stable",
                )
            }
        )
        step = openstack.UpgradeOpenStackApplicationStep(
            deployment, tfhelper, jhelper, osfeature
        )
        result = step.run()

        tfhelper.update_partial_tfvars_and_apply_tf.assert_called_once()
        jhelper.wait_until_desired_status.assert_called_once()
        assert result.result_type == ResultType.FAILED
        assert result.message == "timed out"

    def test_calls_set_application_timeout_on_enable_with_deployment(
        self, deployment, tfhelper, jhelper, osfeature
    ):
        """Test that set_application_timeout_on_enable is called with deployment."""
        jhelper.get_model_status.return_value = Mock(
            apps={
                "keystone": Mock(
                    charm="keystone-k8s",
                    charm_channel="2023.2/stable",
                )
            }
        )
        osfeature.set_application_timeout_on_enable.return_value = 1800

        step = openstack.UpgradeOpenStackApplicationStep(
            deployment, tfhelper, jhelper, osfeature
        )
        step.run()

        osfeature.set_application_timeout_on_enable.assert_called_once_with(deployment)


class TestOpenStackControlPlaneFeatureTimeouts:
    """Test timeout methods for OpenStackControlPlaneFeature."""

    def test_set_application_timeout_on_enable_default(self, deployment):
        """Test default timeout on enable."""
        feature = Mock(spec=openstack.OpenStackControlPlaneFeature)
        feature.set_application_timeout_on_enable = (
            openstack.OpenStackControlPlaneFeature.set_application_timeout_on_enable
        )
        timeout = feature.set_application_timeout_on_enable(feature, deployment)
        assert timeout == openstack.APPLICATION_DEPLOY_TIMEOUT

    def test_set_application_timeout_on_disable_default(self, deployment):
        """Test default timeout on disable."""
        feature = Mock(spec=openstack.OpenStackControlPlaneFeature)
        feature.set_application_timeout_on_disable = (
            openstack.OpenStackControlPlaneFeature.set_application_timeout_on_disable
        )
        timeout = feature.set_application_timeout_on_disable(feature, deployment)
        assert timeout == openstack.APPLICATION_DEPLOY_TIMEOUT
