# SPDX-FileCopyrightText: 2024 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import json
from unittest.mock import ANY, Mock, patch

import click
import pytest

from sunbeam.core.juju import (
    JujuWaitException,
)
from sunbeam.core.openstack import OPENSTACK_MODEL
from sunbeam.core.terraform import (
    TerraformException,
)
from sunbeam.features.baremetal import commands, constants
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
    def test_baremetal_resource_add_already_exists(self, deployment):
        ironic = ironic_feature.BaremetalFeature()
        client = deployment.get_client.return_value
        client._cluster_config[OPENSTACK_TERRAFORM_VARS] = {
            constants.NOVA_IRONIC_SHARDS_TFVAR: {"foo": {}},
        }

        with pytest.raises(click.ClickException):
            commands._baremetal_resource_add(
                ironic,
                deployment,
                constants.NOVA_IRONIC_SHARDS_TFVAR,
                {"foo": {"shard": "foo"}},
                "nova-ironic",
            )

        tfhelper = deployment.get_tfhelper.return_value
        tfhelper.write_tfvars.assert_not_called()

    def test_baremetal_resource_add_apply_failed(self, deployment):
        ironic = ironic_feature.BaremetalFeature()
        tfhelper = deployment.get_tfhelper.return_value
        tfhelper.apply.side_effect = TerraformException("expected to fail.")

        with pytest.raises(click.ClickException):
            commands._baremetal_resource_add(
                ironic,
                deployment,
                constants.NOVA_IRONIC_SHARDS_TFVAR,
                {"foo": {"shard": "foo"}},
                "nova-ironic",
            )

        expected_tfvars = {
            constants.NOVA_IRONIC_SHARDS_TFVAR: {"foo": {"shard": "foo"}},
        }
        tfhelper.write_tfvars.assert_called_once_with(expected_tfvars)
        tfhelper.apply.assert_called_once()

    @patch.object(commands, "JujuHelper")
    def test_baremetal_resource_add_timeout(self, mock_JujuHelper, deployment):
        ironic = ironic_feature.BaremetalFeature()
        jhelper = mock_JujuHelper.return_value
        jhelper.wait_until_active.side_effect = JujuWaitException

        with pytest.raises(click.ClickException):
            commands._baremetal_resource_add(
                ironic,
                deployment,
                constants.NOVA_IRONIC_SHARDS_TFVAR,
                {"foo": {"shard": "foo"}},
                "nova-ironic",
            )

        jhelper.wait_until_active.assert_called_once_with(
            OPENSTACK_MODEL,
            ["nova-ironic-foo"],
            timeout=constants.IRONIC_APP_TIMEOUT,
            queue=ANY,
        )

    @patch.object(commands, "JujuHelper")
    def test_baremetal_resource_add(self, mock_JujuHelper, deployment):
        ironic = ironic_feature.BaremetalFeature()

        commands._baremetal_resource_add(
            ironic,
            deployment,
            constants.NOVA_IRONIC_SHARDS_TFVAR,
            {"foo": {"shard": "foo"}},
            "nova-ironic",
        )

        expected_tfvars = {
            constants.NOVA_IRONIC_SHARDS_TFVAR: {"foo": {"shard": "foo"}},
        }
        tfhelper = deployment.get_tfhelper.return_value
        tfhelper.write_tfvars.assert_called_once_with(expected_tfvars)
        tfhelper.apply.assert_called_once()

        jhelper = mock_JujuHelper.return_value
        jhelper.wait_until_active.assert_called_once_with(
            OPENSTACK_MODEL,
            ["nova-ironic-foo"],
            timeout=constants.IRONIC_APP_TIMEOUT,
            queue=ANY,
        )

    @patch.object(commands, "JujuHelper")
    def test_baremetal_resource_add_replace(self, mock_JujuHelper, deployment):
        ironic = ironic_feature.BaremetalFeature()
        client = deployment.get_client.return_value
        client._cluster_config[OPENSTACK_TERRAFORM_VARS] = {
            constants.NOVA_IRONIC_SHARDS_TFVAR: {"foo": {}},
        }

        commands._baremetal_resource_add(
            ironic,
            deployment,
            constants.NOVA_IRONIC_SHARDS_TFVAR,
            {"lish": {"shard": "lish"}},
            "nova-ironic",
            replace=True,
        )

        expected_tfvars = {
            constants.NOVA_IRONIC_SHARDS_TFVAR: {"lish": {"shard": "lish"}},
        }
        tfhelper = deployment.get_tfhelper.return_value
        tfhelper.write_tfvars.assert_called_once_with(expected_tfvars)
        tfhelper.apply.assert_called_once()

        jhelper = mock_JujuHelper.return_value
        jhelper.wait_until_active.assert_called_once_with(
            OPENSTACK_MODEL,
            ["nova-ironic-lish"],
            timeout=constants.IRONIC_APP_TIMEOUT,
            queue=ANY,
        )

    @patch.object(commands.console, "print")
    def test_baremetal_resource_list(self, console_print, deployment):
        # Has no shard.
        ironic = ironic_feature.BaremetalFeature()

        commands._baremetal_resource_list(
            ironic,
            deployment,
            constants.NOVA_IRONIC_SHARDS_TFVAR,
        )

        console_print.assert_not_called()

        # Has a shard.
        client = deployment.get_client.return_value
        client._cluster_config[OPENSTACK_TERRAFORM_VARS] = {
            constants.NOVA_IRONIC_SHARDS_TFVAR: {"foo": {}},
        }

        commands._baremetal_resource_list(
            ironic,
            deployment,
            constants.NOVA_IRONIC_SHARDS_TFVAR,
        )

        console_print.assert_called_once_with("foo")

    def test_baremetal_resource_delete_not_found(self, deployment):
        ironic = ironic_feature.BaremetalFeature()

        with pytest.raises(click.ClickException):
            commands._baremetal_resource_delete(
                ironic,
                deployment,
                constants.NOVA_IRONIC_SHARDS_TFVAR,
                "foo",
                "nova-ironic-foo",
            )

        tfhelper = deployment.get_tfhelper.return_value
        tfhelper.write_tfvars.assert_not_called()

    def test_baremetal_resource_delete_apply_failed(self, deployment):
        ironic = ironic_feature.BaremetalFeature()
        client = deployment.get_client.return_value
        client._cluster_config[OPENSTACK_TERRAFORM_VARS] = {
            constants.NOVA_IRONIC_SHARDS_TFVAR: {"foo": {}},
        }
        tfhelper = deployment.get_tfhelper.return_value
        tfhelper.apply.side_effect = TerraformException("expected to fail.")

        with pytest.raises(click.ClickException):
            commands._baremetal_resource_delete(
                ironic,
                deployment,
                constants.NOVA_IRONIC_SHARDS_TFVAR,
                "foo",
                "nova-ironic-foo",
            )

        expected_tfvars = {
            constants.NOVA_IRONIC_SHARDS_TFVAR: {},
        }
        tfhelper.write_tfvars.assert_called_once_with(expected_tfvars)
        tfhelper.apply.assert_called_once()

    @patch.object(commands, "JujuHelper")
    def test_baremetal_resource_delete_timeout(self, mock_JujuHelper, deployment):
        ironic = ironic_feature.BaremetalFeature()
        client = deployment.get_client.return_value
        client._cluster_config[OPENSTACK_TERRAFORM_VARS] = {
            constants.NOVA_IRONIC_SHARDS_TFVAR: {"foo": {}},
        }
        jhelper = mock_JujuHelper.return_value
        jhelper.wait_application_gone.side_effect = JujuWaitException

        with pytest.raises(click.ClickException):
            commands._baremetal_resource_delete(
                ironic,
                deployment,
                constants.NOVA_IRONIC_SHARDS_TFVAR,
                "foo",
                "nova-ironic-foo",
            )

        jhelper.wait_application_gone.assert_called_once_with(
            ["nova-ironic-foo"],
            OPENSTACK_MODEL,
            timeout=constants.IRONIC_APP_TIMEOUT,
        )

    @patch.object(commands, "JujuHelper")
    def test_baremetal_resource_delete(self, mock_JujuHelper, deployment):
        ironic = ironic_feature.BaremetalFeature()
        client = deployment.get_client.return_value
        client._cluster_config[OPENSTACK_TERRAFORM_VARS] = {
            constants.NOVA_IRONIC_SHARDS_TFVAR: {"foo": {}},
        }

        commands._baremetal_resource_delete(
            ironic,
            deployment,
            constants.NOVA_IRONIC_SHARDS_TFVAR,
            "foo",
            "nova-ironic-foo",
        )

        expected_tfvars = {
            constants.NOVA_IRONIC_SHARDS_TFVAR: {},
        }
        tfhelper = deployment.get_tfhelper.return_value
        tfhelper.write_tfvars.assert_called_once_with(expected_tfvars)
        tfhelper.apply.assert_called_once()

        jhelper = mock_JujuHelper.return_value
        jhelper.wait_application_gone.assert_called_once_with(
            ["nova-ironic-foo"],
            OPENSTACK_MODEL,
            timeout=constants.IRONIC_APP_TIMEOUT,
        )
