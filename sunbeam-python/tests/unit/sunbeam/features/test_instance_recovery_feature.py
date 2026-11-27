# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

from unittest.mock import Mock, patch

import pytest

from sunbeam.core.deployment import Networks
from sunbeam.core.openstack import OPENSTACK_MODEL
from sunbeam.features.instance_recovery import consul
from sunbeam.features.instance_recovery.feature import InstanceRecoveryFeature
from sunbeam.provider.maas.steps import MaasCreateLoadBalancerIPPoolsStep
from sunbeam.steps.k8s import (
    EnsureCiliumDeviceByHostStep,
    EnsureL2AdvertisementByHostStep,
)
from sunbeam.steps.openstack import ValidateInfraAppsStep


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


@patch("sunbeam.features.instance_recovery.feature.MaasClient")
@patch("sunbeam.features.instance_recovery.feature.JujuHelper")
@patch("sunbeam.features.instance_recovery.feature.run_plan")
@patch(
    "sunbeam.features.instance_recovery.feature.is_maas_deployment", return_value=True
)
def test_enable_reconciles_storage_network_before_assigning_consul_vip(
    _mock_is_maas, mock_run_plan, _mock_jhelper, _mock_maas_client, deployment
):
    deployment.get_tfhelper.side_effect = None
    deployment.public_ip_pool = "test-public-api"
    deployment.storage_ip_pool = "test-storage-ippool"
    feature = InstanceRecoveryFeature()
    feature._manifest = Mock()

    feature.run_enable_plans(deployment, Mock(), show_hints=False)

    pool_plan = next(
        call.args[0]
        for call in mock_run_plan.call_args_list
        if isinstance(call.args[0][0], MaasCreateLoadBalancerIPPoolsStep)
    )
    create_pool, cilium, public_l2, storage_l2, assign_pool, assign_ip, *_ = pool_plan
    assert isinstance(create_pool, MaasCreateLoadBalancerIPPoolsStep)
    assert isinstance(cilium, EnsureCiliumDeviceByHostStep)
    assert isinstance(public_l2, EnsureL2AdvertisementByHostStep)
    assert public_l2.network == Networks.PUBLIC
    assert public_l2.pool == deployment.public_ip_pool
    assert public_l2.optional_if_pool_missing
    assert public_l2.remove_legacy
    assert isinstance(storage_l2, EnsureL2AdvertisementByHostStep)
    assert storage_l2.network == Networks.STORAGE
    assert storage_l2.pool == deployment.storage_ip_pool
    assert storage_l2.optional_if_pool_missing
    assert storage_l2.remove_legacy
    assert isinstance(assign_pool, consul.PatchConsulStorageLoadBalancerIPPoolStep)
    assert isinstance(assign_ip, consul.PatchConsulStorageLoadBalancerIPStep)


@pytest.mark.parametrize(
    "db_topology, expected_extra_masakari_apps",
    [
        ("single", ["masakari", "masakari-mysql-router"]),
        ("multi", ["masakari", "masakari-mysql-router", "masakari-mysql"]),
    ],
)
@patch("sunbeam.features.instance_recovery.feature.JujuHelper")
@patch("sunbeam.features.instance_recovery.feature.RemoveSaasApplicationsStep")
@patch("sunbeam.features.instance_recovery.feature.run_plan")
@patch("sunbeam.features.instance_recovery.consul.ConsulFeature.set_application_names")
def test_disable_purges_consul_and_masakari_saas_apps(
    mock_consul_app_names,
    mock_run_plan,
    mock_remove_saas,
    _mock_jhelper_class,
    deployment,
    db_topology,
    expected_extra_masakari_apps,
):
    consul_apps = [
        "consul-management",
        "consul-storage",
        "consul-tenant",
    ]
    mock_consul_app_names.return_value = consul_apps.copy()
    mock_jhelper = Mock()
    _mock_jhelper_class.return_value = mock_jhelper

    with patch.object(
        InstanceRecoveryFeature,
        "get_database_topology",
        return_value=db_topology,
    ) as mock_get_database_topology:
        feature = InstanceRecoveryFeature()
        feature._manifest = Mock()
        feature.run_disable_plans(deployment, show_hints=False)

        mock_get_database_topology.assert_called_once_with(deployment)

    expected_saas_apps = consul_apps + expected_extra_masakari_apps

    # The INFRA_APPS gate runs as its own plan before the teardown plan.
    assert mock_run_plan.call_count == 2
    first_plan = mock_run_plan.call_args_list[0].args[0]
    assert any(isinstance(step, ValidateInfraAppsStep) for step in first_plan), (
        "the INFRA_APPS gate must run before the teardown plan"
    )

    mock_remove_saas.assert_called_once()
    assert (
        mock_remove_saas.call_args.kwargs["saas_apps_to_delete"] == expected_saas_apps
    )

    assert mock_remove_saas.call_args.args[0] == mock_jhelper
    assert mock_remove_saas.call_args.args[1] == "openstack-machines"
    assert mock_remove_saas.call_args.args[2] == OPENSTACK_MODEL
