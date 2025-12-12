# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import json
from unittest.mock import ANY, Mock, patch

import pytest

from sunbeam.core.common import ResultType
from sunbeam.core.juju import (
    ActionFailedException,
    JujuWaitException,
)
from sunbeam.core.openstack import OPENSTACK_MODEL
from sunbeam.core.terraform import (
    TerraformException,
)
from sunbeam.features.baremetal import constants, steps
from sunbeam.features.baremetal import feature as ironic_feature
from sunbeam.features.interface.v1.openstack import OPENSTACK_TERRAFORM_VARS


@pytest.fixture()
def deployment():
    deploy = Mock()
    client = deploy.get_client.return_value
    client._cluster_config = {}

    def get_config(key):
        return json.dumps(client._cluster_config.get(key, {}))

    client.cluster.get_config.side_effect = get_config

    yield deploy


class TestBaremetalCommands:
    def test_run_set_temp_url_secret_failed(self):
        jhelper = Mock()
        jhelper.run_action.side_effect = ActionFailedException("expected")
        step = steps.RunSetTempUrlSecretStep(deployment, jhelper)

        result = step.run()

        assert result.result_type == ResultType.FAILED
        jhelper.get_leader_unit.assert_called_once_with(
            "ironic-conductor", OPENSTACK_MODEL
        )
        jhelper.run_action.assert_called_once_with(
            jhelper.get_leader_unit.return_value,
            OPENSTACK_MODEL,
            "set-temp-url-secret",
        )

    def test_run_set_temp_url_secret_timeout(self):
        jhelper = Mock()
        jhelper.wait_until_active.side_effect = JujuWaitException
        step = steps.RunSetTempUrlSecretStep(deployment, jhelper)

        result = step.run()

        assert result.result_type == ResultType.FAILED
        jhelper.wait_until_active.assert_called_once_with(
            OPENSTACK_MODEL,
            ["ironic-conductor"],
            timeout=constants.IRONIC_APP_TIMEOUT,
            queue=ANY,
        )

    def test_deploy_nova_ironic_shards_already_exists(self, deployment):
        ironic = ironic_feature.BaremetalFeature()
        ironic._manifest = Mock()
        client = deployment.get_client.return_value
        client._cluster_config[OPENSTACK_TERRAFORM_VARS] = {
            constants.NOVA_IRONIC_SHARDS_TFVAR: {"foo": {}},
        }
        step = steps.DeployNovaIronicShardsStep(deployment, ironic, ["foo"], False)

        result = step.run()

        assert result.result_type == ResultType.FAILED
        tfhelper = deployment.get_tfhelper.return_value
        tfhelper.update_tfvars_and_apply_tf.assert_not_called()

    def test_deploy_nova_ironic_shards_apply_failed(self, deployment):
        ironic = ironic_feature.BaremetalFeature()
        ironic._manifest = Mock()
        tfhelper = deployment.get_tfhelper.return_value
        tfhelper.apply.side_effect = TerraformException("expected to fail.")
        step = steps.DeployNovaIronicShardsStep(deployment, ironic, ["foo"])

        result = step.run()

        assert result.result_type == ResultType.FAILED
        expected_tfvars = {
            constants.NOVA_IRONIC_SHARDS_TFVAR: {"foo": {"shard": "foo"}},
        }
        tfhelper.update_tfvars_and_apply_tf.assert_called_once_with(
            deployment.get_client.return_value,
            ironic._manifest,
            tfvar_config=OPENSTACK_TERRAFORM_VARS,
            override_tfvars=expected_tfvars,
        )

    @patch.object(steps, "JujuHelper")
    def test_deploy_nova_ironic_shards_timeout(self, mock_JujuHelper, deployment):
        ironic = ironic_feature.BaremetalFeature()
        ironic._manifest = Mock()
        jhelper = mock_JujuHelper.return_value
        jhelper.wait_until_desired_status.side_effect = JujuWaitException
        step = steps.DeployNovaIronicShardsStep(deployment, ironic, ["foo"])

        result = step.run()

        assert result.result_type == ResultType.FAILED
        jhelper.wait_until_desired_status.assert_called_once_with(
            OPENSTACK_MODEL,
            ["nova-ironic-foo"],
            timeout=constants.IRONIC_APP_TIMEOUT,
            queue=ANY,
            status=["active"],
        )

    @patch.object(steps, "JujuHelper")
    def test_deploy_nova_ironic_shards(self, mock_JujuHelper, deployment):
        ironic = ironic_feature.BaremetalFeature()
        ironic._manifest = Mock()
        step = steps.DeployNovaIronicShardsStep(deployment, ironic, ["foo"])

        result = step.run()

        assert result.result_type == ResultType.COMPLETED
        expected_tfvars = {
            constants.NOVA_IRONIC_SHARDS_TFVAR: {"foo": {"shard": "foo"}},
        }
        tfhelper = deployment.get_tfhelper.return_value
        tfhelper.update_tfvars_and_apply_tf.assert_called_once_with(
            deployment.get_client.return_value,
            ironic._manifest,
            tfvar_config=OPENSTACK_TERRAFORM_VARS,
            override_tfvars=expected_tfvars,
        )

        jhelper = mock_JujuHelper.return_value
        jhelper.wait_until_desired_status.assert_called_once_with(
            OPENSTACK_MODEL,
            ["nova-ironic-foo"],
            timeout=constants.IRONIC_APP_TIMEOUT,
            queue=ANY,
            status=["active"],
        )

    @patch.object(steps, "JujuHelper")
    def test_deploy_nova_ironic_shards_replace(self, mock_JujuHelper, deployment):
        ironic = ironic_feature.BaremetalFeature()
        ironic._manifest = Mock()
        client = deployment.get_client.return_value
        client._cluster_config[OPENSTACK_TERRAFORM_VARS] = {
            constants.NOVA_IRONIC_SHARDS_TFVAR: {"foo": {}},
        }
        step = steps.DeployNovaIronicShardsStep(deployment, ironic, ["lish"], True)

        result = step.run()

        assert result.result_type == ResultType.COMPLETED
        expected_tfvars = {
            constants.NOVA_IRONIC_SHARDS_TFVAR: {"lish": {"shard": "lish"}},
        }
        tfhelper = deployment.get_tfhelper.return_value
        tfhelper.update_tfvars_and_apply_tf.assert_called_once_with(
            deployment.get_client.return_value,
            ironic._manifest,
            tfvar_config=OPENSTACK_TERRAFORM_VARS,
            override_tfvars=expected_tfvars,
        )

        jhelper = mock_JujuHelper.return_value
        jhelper.wait_until_desired_status.assert_called_once_with(
            OPENSTACK_MODEL,
            ["nova-ironic-lish"],
            timeout=constants.IRONIC_APP_TIMEOUT,
            queue=ANY,
            status=["active"],
        )

    @patch.object(steps.console, "print")
    def test_nova_ironic_shards_list(self, console_print, deployment):
        # Has no shard.
        ironic = ironic_feature.BaremetalFeature()
        ironic._manifest = Mock()
        step = steps.ListNovaIronicShardsStep(deployment, ironic)

        result = step.run()

        assert result.result_type == ResultType.COMPLETED
        console_print.assert_not_called()

        # Has a shard.
        client = deployment.get_client.return_value
        client._cluster_config[OPENSTACK_TERRAFORM_VARS] = {
            constants.NOVA_IRONIC_SHARDS_TFVAR: {"foo": {}},
        }

        result = step.run()

        assert result.result_type == ResultType.COMPLETED
        console_print.assert_called_once_with("foo")

    def test_nova_ironic_shards_delete_not_found(self, deployment):
        ironic = ironic_feature.BaremetalFeature()
        ironic._manifest = Mock()
        step = steps.DeleteNovaIronicShardStep(deployment, ironic, "foo")

        result = step.run()

        assert result.result_type == ResultType.FAILED
        tfhelper = deployment.get_tfhelper.return_value
        tfhelper.update_tfvars_and_apply_tf.assert_not_called()

    def test_nova_ironic_shards_delete_apply_failed(self, deployment):
        ironic = ironic_feature.BaremetalFeature()
        ironic._manifest = Mock()
        client = deployment.get_client.return_value
        client._cluster_config[OPENSTACK_TERRAFORM_VARS] = {
            constants.NOVA_IRONIC_SHARDS_TFVAR: {"foo": {}},
        }
        tfhelper = deployment.get_tfhelper.return_value
        tfhelper.apply.side_effect = TerraformException("expected to fail.")
        step = steps.DeleteNovaIronicShardStep(deployment, ironic, "foo")

        result = step.run()

        assert result.result_type == ResultType.FAILED
        expected_tfvars = {
            constants.NOVA_IRONIC_SHARDS_TFVAR: {},
        }
        tfhelper.update_tfvars_and_apply_tf.assert_called_once_with(
            deployment.get_client.return_value,
            ironic._manifest,
            tfvar_config=OPENSTACK_TERRAFORM_VARS,
            override_tfvars=expected_tfvars,
        )

    @patch.object(steps, "JujuHelper")
    def test_nova_ironic_shards_delete_timeout(self, mock_JujuHelper, deployment):
        ironic = ironic_feature.BaremetalFeature()
        ironic._manifest = Mock()
        client = deployment.get_client.return_value
        client._cluster_config[OPENSTACK_TERRAFORM_VARS] = {
            constants.NOVA_IRONIC_SHARDS_TFVAR: {"foo": {}},
        }
        jhelper = mock_JujuHelper.return_value
        jhelper.wait_application_gone.side_effect = JujuWaitException
        step = steps.DeleteNovaIronicShardStep(deployment, ironic, "foo")

        result = step.run()

        assert result.result_type == ResultType.FAILED
        jhelper.wait_application_gone.assert_called_once_with(
            ["nova-ironic-foo"],
            OPENSTACK_MODEL,
            timeout=constants.IRONIC_APP_TIMEOUT,
        )

    @patch.object(steps, "JujuHelper")
    def test_nova_ironic_shards_delete(self, mock_JujuHelper, deployment):
        ironic = ironic_feature.BaremetalFeature()
        ironic._manifest = Mock()
        client = deployment.get_client.return_value
        client._cluster_config[OPENSTACK_TERRAFORM_VARS] = {
            constants.NOVA_IRONIC_SHARDS_TFVAR: {"foo": {}},
        }
        step = steps.DeleteNovaIronicShardStep(deployment, ironic, "foo")

        result = step.run()

        assert result.result_type == ResultType.COMPLETED
        expected_tfvars = {
            constants.NOVA_IRONIC_SHARDS_TFVAR: {},
        }
        tfhelper = deployment.get_tfhelper.return_value
        tfhelper.update_tfvars_and_apply_tf.assert_called_once_with(
            deployment.get_client.return_value,
            ironic._manifest,
            tfvar_config=OPENSTACK_TERRAFORM_VARS,
            override_tfvars=expected_tfvars,
        )

        jhelper = mock_JujuHelper.return_value
        jhelper.wait_application_gone.assert_called_once_with(
            ["nova-ironic-foo"],
            OPENSTACK_MODEL,
            timeout=constants.IRONIC_APP_TIMEOUT,
        )
