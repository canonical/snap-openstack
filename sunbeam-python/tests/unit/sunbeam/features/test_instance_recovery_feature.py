# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

from unittest.mock import Mock, patch

import pytest

from sunbeam.core.openstack import OPENSTACK_MODEL
from sunbeam.features.instance_recovery.feature import InstanceRecoveryFeature


@pytest.fixture
def deployment():
    deploy = Mock()
    deploy.openstack_machines_model = "openstack-machines"
    deploy.juju_controller = "test-controller"
    deploy.get_client.return_value = Mock()

    tfhelper_hypervisor = Mock()
    tfhelper_consul_client = Mock()
    tfhelper_openstack = Mock()

    def _get_tfhelper(plan: str):
        return {
            "openstack-plan": tfhelper_openstack,
            "hypervisor-plan": tfhelper_hypervisor,
            "consul-client-plan": tfhelper_consul_client,
        }[plan]

    deploy.get_tfhelper.side_effect = _get_tfhelper
    return deploy


@patch("sunbeam.features.instance_recovery.feature.JujuHelper")
@patch("sunbeam.features.instance_recovery.feature.RemoveSaasApplicationsStep")
@patch("sunbeam.features.instance_recovery.feature.run_plan")
@patch("sunbeam.features.instance_recovery.consul.ConsulFeature.set_application_names")
@patch.object(InstanceRecoveryFeature, "get_database_topology", return_value="single")
def test_disable_purges_consul_and_masakari_saas_apps(
    mock_get_database_topology,
    mock_consul_app_names,
    mock_run_plan,
    mock_remove_saas,
    _mock_jhelper_class,
    deployment,
):
    consul_apps = [
        "consul-management",
        "consul-storage",
        "consul-tenant",
    ]
    mock_consul_app_names.return_value = consul_apps.copy()
    mock_jhelper = Mock()
    _mock_jhelper_class.return_value = mock_jhelper

    feature = InstanceRecoveryFeature()
    feature._manifest = Mock()
    feature.run_disable_plans(deployment, show_hints=False)

    expected_saas_apps = consul_apps + ["masakari", "masakari-mysql-router"]

    assert mock_run_plan.call_count == 1

    mock_remove_saas.assert_called_once()
    assert (
        mock_remove_saas.call_args.kwargs["saas_apps_to_delete"] == expected_saas_apps
    )

    assert mock_remove_saas.call_args.args[0] == mock_jhelper
    assert mock_remove_saas.call_args.args[1] == "openstack-machines"
    assert mock_remove_saas.call_args.args[2] == OPENSTACK_MODEL

    mock_get_database_topology.assert_called_once_with(deployment)
