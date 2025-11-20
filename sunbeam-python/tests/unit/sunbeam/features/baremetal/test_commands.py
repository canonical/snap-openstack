# SPDX-FileCopyrightText: 2024 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import json
from unittest.mock import ANY, Mock, call, patch

import click
import pytest
from rich import box
from rich.table import Column

from sunbeam.core.juju import (
    ActionFailedException,
    JujuSecretNotFound,
    JujuWaitException,
)
from sunbeam.core.openstack import OPENSTACK_MODEL
from sunbeam.core.terraform import (
    TerraformException,
)
from sunbeam.features.baremetal import commands, constants
from sunbeam.features.baremetal import feature as ironic_feature
from sunbeam.features.interface.v1.openstack import OPENSTACK_TERRAFORM_VARS
from tests.unit.sunbeam.features.baremetal import test_feature_config


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
        jhelper.wait_until_desired_status.side_effect = JujuWaitException

        with pytest.raises(click.ClickException):
            commands._baremetal_resource_add(
                ironic,
                deployment,
                constants.NOVA_IRONIC_SHARDS_TFVAR,
                {"foo": {"shard": "foo"}},
                "nova-ironic",
            )

        jhelper.wait_until_desired_status.assert_called_once_with(
            OPENSTACK_MODEL,
            ["nova-ironic-foo"],
            timeout=constants.IRONIC_APP_TIMEOUT,
            queue=ANY,
            status=["active"],
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
        jhelper.wait_until_desired_status.assert_called_once_with(
            OPENSTACK_MODEL,
            ["nova-ironic-foo"],
            timeout=constants.IRONIC_APP_TIMEOUT,
            queue=ANY,
            status=["active"],
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
        jhelper.wait_until_desired_status.assert_called_once_with(
            OPENSTACK_MODEL,
            ["nova-ironic-lish"],
            timeout=constants.IRONIC_APP_TIMEOUT,
            queue=ANY,
            status=["active"],
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

    @patch.object(commands, "JujuHelper")
    def test_run_set_temp_url_secret_failed(self, mock_JujuHelper):
        jhelper = mock_JujuHelper.return_value
        jhelper.run_action.side_effect = ActionFailedException("expected")

        with pytest.raises(click.ClickException):
            commands._run_set_temp_url_secret(Mock(), Mock(), ["foo"])

        jhelper.get_leader_unit.assert_called_once_with("foo", OPENSTACK_MODEL)
        jhelper.run_action.assert_called_once_with(
            jhelper.get_leader_unit.return_value,
            OPENSTACK_MODEL,
            "set-temp-url-secret",
        )

    @patch.object(commands, "JujuHelper")
    def test_run_set_temp_url_secret_timeout(self, mock_JujuHelper):
        jhelper = mock_JujuHelper.return_value
        jhelper.wait_until_active.side_effect = JujuWaitException

        with pytest.raises(click.ClickException):
            commands._run_set_temp_url_secret(Mock(), Mock(), ["foo"])

        jhelper.wait_until_active.assert_called_once_with(
            OPENSTACK_MODEL,
            ["foo"],
            timeout=constants.IRONIC_APP_TIMEOUT,
            queue=ANY,
        )

    @patch.object(commands, "JujuHelper")
    def test_switch_config_add_already_exists(self, mock_JujuHelper, deployment):
        ironic = ironic_feature.BaremetalFeature()
        jhelper = mock_JujuHelper.return_value
        jhelper.secret_exists.return_value = True

        with pytest.raises(click.ClickException):
            commands._switch_config_add(
                ironic,
                deployment,
                protocol="netconf",
                name="foo",
                config="config-file",
                additional_file=[],
                show_hints=False,
            )

        jhelper.add_secret.assert_not_called()

    @patch.object(commands, "JujuHelper")
    def test_switch_config_add_duplicate_file(self, mock_JujuHelper, deployment):
        ironic = ironic_feature.BaremetalFeature()
        jhelper = mock_JujuHelper.return_value
        jhelper.secret_exists.return_value = False

        with pytest.raises(click.ClickException):
            commands._switch_config_add(
                ironic,
                deployment,
                protocol="netconf",
                name="foo",
                config="config-file",
                additional_file=[("foo", Mock()), ("foo", Mock())],
                show_hints=False,
            )

        jhelper.add_secret.assert_not_called()

    @patch.object(commands, "JujuHelper")
    def test_switch_config_add(self, mock_JujuHelper, deployment):
        ironic = ironic_feature.BaremetalFeature()

        jhelper = mock_JujuHelper.return_value
        jhelper.secret_exists.return_value = False
        jhelper.add_secret.return_value = "secret_id"
        client = deployment.get_client.return_value
        client._cluster_config[OPENSTACK_TERRAFORM_VARS] = {}
        netconf = test_feature_config._get_netconf_sample_config("foo")
        config = Mock()
        config.read.return_value = netconf
        additional_file = Mock()
        additional_file.read.return_value = "some-cool-key-here"

        commands._switch_config_add(
            ironic,
            deployment,
            protocol="netconf",
            name="foo",
            config=config,
            additional_file=[("foo-key", additional_file)],
            show_hints=False,
        )

        secret_name = "switch-config-foo"
        secret_data = {
            "conf": netconf,
            "foo-key": additional_file.read.return_value,
        }
        jhelper.add_secret.assert_called_once_with(
            OPENSTACK_MODEL,
            secret_name,
            secret_data,
            ANY,
        )
        calls = [
            call(OPENSTACK_MODEL, secret_name, "neutron"),
            call(OPENSTACK_MODEL, secret_name, "neutron-baremetal-switch-config"),
        ]
        jhelper.grant_secret.assert_has_calls(calls)

        expected_tfvars = {
            constants.NEUTRON_BAREMETAL_SWITCH_CONF_SECRETS_TFVAR: "secret_id",
            constants.NEUTRON_SWITCH_CONF_SECRETS_TFVAR: {"netconf": [secret_name]},
        }
        tfhelper = deployment.get_tfhelper.return_value
        tfhelper.write_tfvars.assert_called_once_with(expected_tfvars)
        tfhelper.apply.assert_called_once()

        jhelper.wait_until_desired_status.assert_called_once_with(
            OPENSTACK_MODEL,
            ["neutron-baremetal-switch-config", "neutron"],
            timeout=constants.IRONIC_APP_TIMEOUT,
            queue=ANY,
            status=["active"],
        )

    @patch.object(commands, "Table")
    @patch.object(commands.console, "print")
    def test_switch_config_list(self, console_print, mock_Table, deployment):
        ironic = ironic_feature.BaremetalFeature()

        client = deployment.get_client.return_value
        client._cluster_config[OPENSTACK_TERRAFORM_VARS] = {
            constants.NEUTRON_SWITCH_CONF_SECRETS_TFVAR: {
                "netconf": ["switch-config-foo", "other"],
                "generic": ["switch-config-lish"],
            },
        }

        commands._switch_config_list(
            ironic,
            deployment,
        )

        mock_Table.assert_called_once_with(
            Column("Protocol"),
            Column("Name"),
            box=box.SIMPLE,
        )
        table = mock_Table.return_value
        table.add_row.assert_has_calls(
            [
                call("netconf", "foo"),
                call("generic", "lish"),
            ]
        )
        console_print.assert_called_once_with(table)

    @patch.object(commands, "JujuHelper")
    def test_switch_config_update_not_found(self, mock_JujuHelper, deployment):
        ironic = ironic_feature.BaremetalFeature()
        jhelper = mock_JujuHelper.return_value
        jhelper.secret_exists.return_value = False

        with pytest.raises(click.ClickException):
            commands._switch_config_update(
                ironic,
                deployment,
                protocol="netconf",
                name="foo",
                config="config-file",
                additional_file=[],
                show_hints=False,
            )

        jhelper.update_secret.assert_not_called()

    @patch.object(commands, "JujuHelper")
    def test_switch_config_update(self, mock_JujuHelper, deployment):
        ironic = ironic_feature.BaremetalFeature()

        jhelper = mock_JujuHelper.return_value
        jhelper.secret_exists.return_value = True
        netconf = test_feature_config._get_netconf_sample_config("foo")
        config = Mock()
        config.read.return_value = netconf
        additional_file = Mock()
        additional_file.read.return_value = "some-cool-key-here"

        commands._switch_config_update(
            ironic,
            deployment,
            protocol="netconf",
            name="foo",
            config=config,
            additional_file=[("foo-key", additional_file)],
            show_hints=False,
        )

        secret_name = "switch-config-foo"
        secret_data = {
            "conf": netconf,
            "foo-key": additional_file.read.return_value,
        }
        jhelper.update_secret.assert_called_once_with(
            OPENSTACK_MODEL,
            secret_name,
            secret_data,
        )

    @patch.object(commands, "JujuHelper")
    def test_switch_config_delete_not_found(self, mock_JujuHelper, deployment):
        ironic = ironic_feature.BaremetalFeature()
        jhelper = mock_JujuHelper.return_value
        jhelper.show_secret.side_effect = JujuSecretNotFound

        with pytest.raises(click.ClickException):
            commands._switch_config_delete(
                ironic,
                deployment,
                "foo",
                False,
            )

        jhelper.remove_secret.assert_not_called()

    @patch.object(commands, "JujuHelper")
    def test_switch_config_delete(self, mock_JujuHelper, deployment):
        ironic = ironic_feature.BaremetalFeature()

        jhelper = mock_JujuHelper.return_value
        secret = jhelper.show_secret.return_value
        secret.uri.unique_identifier = "secret_id"

        client = deployment.get_client.return_value
        client._cluster_config[OPENSTACK_TERRAFORM_VARS] = {
            constants.NEUTRON_GENERIC_SWITCH_CONF_SECRETS_TFVAR: "secret_id",
            constants.NEUTRON_SWITCH_CONF_SECRETS_TFVAR: {
                "generic": ["switch-config-foo"],
            },
        }

        commands._switch_config_delete(
            ironic,
            deployment,
            name="foo",
            show_hints=False,
        )

        secret_name = "switch-config-foo"
        jhelper.show_secret.assert_called_once_with(OPENSTACK_MODEL, secret_name)
        jhelper.remove_secret.assert_called_once_with(OPENSTACK_MODEL, secret_name)

        expected_tfvars = {
            constants.NEUTRON_GENERIC_SWITCH_CONF_SECRETS_TFVAR: "",
            constants.NEUTRON_SWITCH_CONF_SECRETS_TFVAR: {
                "generic": [],
            },
        }
        tfhelper = deployment.get_tfhelper.return_value
        tfhelper.write_tfvars.assert_called_once_with(expected_tfvars)
        tfhelper.apply.assert_called_once()

        jhelper.wait_until_desired_status.assert_called_once_with(
            OPENSTACK_MODEL,
            ["neutron-generic-switch-config", "neutron"],
            timeout=constants.IRONIC_APP_TIMEOUT,
            queue=ANY,
            status=["active", "blocked"],
        )
