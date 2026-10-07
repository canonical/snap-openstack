# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

from unittest.mock import Mock, patch

import click
import pytest
from click.testing import CliRunner

from sunbeam.core.common import run_plan
from sunbeam.core.manifest import FeatureConfig
from sunbeam.features.telemetry import feature as telemetry_feature


@pytest.fixture()
def deployment():
    deploy = Mock()
    deploy.openstack_machines_model = "openstack"
    deploy.juju_controller = "test-controller"

    client = deploy.get_client.return_value
    client.cluster.list_nodes_by_role.return_value = [{"name": "node1", "machineid": 1}]

    return deploy


@pytest.fixture()
def mock_storage_backends():
    """Mock storage backends with different principal applications."""
    backend1 = Mock()
    backend1.name = "backend1"
    backend1.type = "type1"
    backend1.principal = "cinder-volume-noha"

    backend2 = Mock()
    backend2.name = "backend2"
    backend2.type = "type2"
    backend2.principal = "cinder-volume-noha"  # Same principal as backend1

    backend3 = Mock()
    backend3.name = "backend3"
    backend3.type = "type3"
    backend3.principal = "cinder-volume"  # Different principal

    return [backend1, backend2, backend3]


@pytest.fixture()
def mock_backend_instances():
    """Mock backend instances from StorageBackendManager."""
    instance1 = Mock()
    instance1.principal_application = "cinder-volume-noha"

    instance2 = Mock()
    instance2.principal_application = "cinder-volume-noha"

    instance3 = Mock()
    instance3.principal_application = "cinder-volume"

    return {
        "type1": instance1,
        "type2": instance2,
        "type3": instance3,
    }


class TestTelemetryFeatureDeduplication:
    """Test deduplication logic in telemetry feature enable/disable plans."""

    @patch("sunbeam.features.telemetry.feature.JujuHelper")
    @patch("sunbeam.features.telemetry.feature.StorageBackendManager")
    @patch("sunbeam.features.telemetry.feature.DeploySpecificCinderVolumeStep")
    @patch("sunbeam.features.telemetry.feature.run_plan")
    def test_run_enable_plans_deduplicates_shared_principals(
        self,
        mock_run_plan,
        mock_deploy_step_class,
        mock_storage_manager_class,
        mock_jhelper_class,
        deployment,
        mock_storage_backends,
        mock_backend_instances,
    ):
        """Test that enable plans deduplicates backends sharing the same principal."""
        # Setup mocks
        client = deployment.get_client.return_value
        storage_backends_root = Mock()
        storage_backends_root.root = mock_storage_backends
        client.cluster.get_storage_backends.return_value = storage_backends_root

        # Mock StorageBackendManager
        mock_storage_manager = mock_storage_manager_class.return_value
        mock_storage_manager.backends.return_value = mock_backend_instances

        # Mock tfhelpers
        tfhelper = Mock()
        tfhelper_openstack = Mock()
        tfhelper_openstack.output.return_value = {"ceilometer-offer-url": "url"}
        tfhelper_hypervisor = Mock()
        tfhelper_cinder_volume = Mock()
        tfhelper_storage = Mock()

        deployment.get_tfhelper.side_effect = lambda plan: {
            "telemetry-plan": tfhelper,
            "openstack-plan": tfhelper_openstack,
            "hypervisor-plan": tfhelper_hypervisor,
            "cinder-volume-plan": tfhelper_cinder_volume,
            "storage-backend-plan": tfhelper_storage,
        }[plan]

        # Create feature and run enable plans
        feature = telemetry_feature.TelemetryFeature()
        feature._manifest = Mock()
        feature.run_enable_plans(deployment, Mock(), False)

        # Verify DeploySpecificCinderVolumeStep was called only twice
        # (once for cinder-volume-noha, once for cinder-volume)
        # NOT three times (which would be without deduplication)
        assert mock_deploy_step_class.call_count == 2

        # Verify the storage-backend plan was registered before being fetched.
        # The plan is registered dynamically by the backend instance (it is not in
        # versions.TERRAFORM_DIR_NAMES), so register_terraform_plan must run first.
        registering_instances = [
            inst
            for inst in mock_backend_instances.values()
            if inst.register_terraform_plan.called
        ]
        assert len(registering_instances) >= 1
        for inst in registering_instances:
            inst.register_terraform_plan.assert_called_with(deployment)

        # Verify the principals that were processed
        principals_processed = set()
        for call in mock_deploy_step_class.call_args_list:
            backend_instance = call[0][6]  # 7th positional arg is backend_instance
            principals_processed.add(backend_instance.principal_application)

        assert principals_processed == {"cinder-volume-noha", "cinder-volume"}

    @patch("sunbeam.features.telemetry.feature.JujuHelper")
    @patch("sunbeam.features.telemetry.feature.StorageBackendManager")
    @patch("sunbeam.features.telemetry.feature.DeploySpecificCinderVolumeStep")
    @patch("sunbeam.features.telemetry.feature.run_plan")
    def test_run_disable_plans_deduplicates_shared_principals(
        self,
        mock_run_plan,
        mock_deploy_step_class,
        mock_storage_manager_class,
        mock_jhelper_class,
        deployment,
        mock_storage_backends,
        mock_backend_instances,
    ):
        """Test that disable plans deduplicates backends sharing the same principal."""
        # Setup mocks
        client = deployment.get_client.return_value
        storage_backends_root = Mock()
        storage_backends_root.root = mock_storage_backends
        client.cluster.get_storage_backends.return_value = storage_backends_root

        # Mock StorageBackendManager
        mock_storage_manager = mock_storage_manager_class.return_value
        mock_storage_manager.backends.return_value = mock_backend_instances

        # Mock tfhelpers
        tfhelper = Mock()
        tfhelper.state_list.return_value = []
        tfhelper_openstack = Mock()
        tfhelper_hypervisor = Mock()
        tfhelper_cinder_volume = Mock()
        tfhelper_storage = Mock()

        deployment.get_tfhelper.side_effect = lambda plan: {
            "telemetry-plan": tfhelper,
            "openstack-plan": tfhelper_openstack,
            "hypervisor-plan": tfhelper_hypervisor,
            "cinder-volume-plan": tfhelper_cinder_volume,
            "storage-backend-plan": tfhelper_storage,
        }[plan]

        # Create feature and run disable plans
        feature = telemetry_feature.TelemetryFeature()
        feature._manifest = Mock()
        feature.run_disable_plans(deployment, False)

        # Verify DeploySpecificCinderVolumeStep was called only twice
        # (once for cinder-volume-noha, once for cinder-volume)
        assert mock_deploy_step_class.call_count == 2

        # Verify the storage-backend plan was registered before being fetched.
        registering_instances = [
            inst
            for inst in mock_backend_instances.values()
            if inst.register_terraform_plan.called
        ]
        assert len(registering_instances) >= 1
        for inst in registering_instances:
            inst.register_terraform_plan.assert_called_with(deployment)

        # Verify the principals that were processed
        principals_processed = set()
        for call in mock_deploy_step_class.call_args_list:
            backend_instance = call[0][6]  # 7th positional arg is backend_instance
            principals_processed.add(backend_instance.principal_application)

        assert principals_processed == {"cinder-volume-noha", "cinder-volume"}

    @patch("sunbeam.features.telemetry.feature.JujuHelper")
    @patch("sunbeam.features.telemetry.feature.StorageBackendManager")
    @patch("sunbeam.features.telemetry.feature.run_plan")
    def test_run_enable_plans_no_storage_backends(
        self,
        mock_run_plan,
        mock_storage_manager_class,
        mock_jhelper_class,
        deployment,
    ):
        """Test that enable plans works when there are no storage backends."""
        # Setup mocks
        client = deployment.get_client.return_value
        storage_backends_root = Mock()
        storage_backends_root.root = []  # No backends
        client.cluster.get_storage_backends.return_value = storage_backends_root

        # Mock tfhelpers
        tfhelper = Mock()
        tfhelper_openstack = Mock()
        tfhelper_openstack.output.return_value = {"ceilometer-offer-url": "url"}
        tfhelper_hypervisor = Mock()
        tfhelper_cinder_volume = Mock()

        deployment.get_tfhelper.side_effect = lambda plan: {
            "telemetry-plan": tfhelper,
            "openstack-plan": tfhelper_openstack,
            "hypervisor-plan": tfhelper_hypervisor,
            "cinder-volume-plan": tfhelper_cinder_volume,
        }[plan]

        # Create feature and run enable plans
        feature = telemetry_feature.TelemetryFeature()
        feature._manifest = Mock()
        feature.run_enable_plans(deployment, Mock(), False)

        # No backend plan is needed; the third call checks final readiness.
        assert mock_run_plan.call_count == 3
        final_plan = mock_run_plan.call_args.args[0]
        assert len(final_plan) == 1
        assert isinstance(final_plan[0], telemetry_feature.WaitForFeatureReadyStep)

    @patch("sunbeam.features.telemetry.feature.JujuHelper")
    @patch("sunbeam.features.telemetry.feature.StorageBackendManager")
    @patch("sunbeam.features.telemetry.feature.DeploySpecificCinderVolumeStep")
    @patch("sunbeam.features.telemetry.feature.run_plan")
    def test_run_enable_plans_passes_extra_tfvars(
        self,
        mock_run_plan,
        mock_deploy_step_class,
        mock_storage_manager_class,
        mock_jhelper_class,
        deployment,
        mock_storage_backends,
        mock_backend_instances,
    ):
        """Test that enable plans passes correct extra_tfvars to steps."""
        # Setup mocks
        client = deployment.get_client.return_value
        storage_backends_root = Mock()
        storage_backends_root.root = mock_storage_backends
        client.cluster.get_storage_backends.return_value = storage_backends_root

        # Mock StorageBackendManager
        mock_storage_manager = mock_storage_manager_class.return_value
        mock_storage_manager.backends.return_value = mock_backend_instances

        # Mock tfhelpers
        tfhelper = Mock()
        tfhelper_openstack = Mock()
        tfhelper_openstack.output.return_value = {"ceilometer-offer-url": "url"}
        tfhelper_hypervisor = Mock()
        tfhelper_cinder_volume = Mock()
        tfhelper_storage = Mock()

        deployment.get_tfhelper.side_effect = lambda plan: {
            "telemetry-plan": tfhelper,
            "openstack-plan": tfhelper_openstack,
            "hypervisor-plan": tfhelper_hypervisor,
            "cinder-volume-plan": tfhelper_cinder_volume,
            "storage-backend-plan": tfhelper_storage,
        }[plan]

        # Create feature and run enable plans
        feature = telemetry_feature.TelemetryFeature()
        feature._manifest = Mock()
        feature.run_enable_plans(deployment, Mock(), False)

        # Verify all DeploySpecificCinderVolumeStep calls have correct extra_tfvars
        for call in mock_deploy_step_class.call_args_list:
            extra_tfvars = call[1]["extra_tfvars"]
            assert extra_tfvars == {"enable-telemetry-notifications": True}

    @patch("sunbeam.features.telemetry.feature.JujuHelper")
    @patch("sunbeam.features.telemetry.feature.StorageBackendManager")
    @patch("sunbeam.features.telemetry.feature.DeploySpecificCinderVolumeStep")
    @patch("sunbeam.features.telemetry.feature.run_plan")
    def test_run_disable_plans_passes_extra_tfvars(
        self,
        mock_run_plan,
        mock_deploy_step_class,
        mock_storage_manager_class,
        mock_jhelper_class,
        deployment,
        mock_storage_backends,
        mock_backend_instances,
    ):
        """Test that disable plans passes correct extra_tfvars to steps."""
        # Setup mocks
        client = deployment.get_client.return_value
        storage_backends_root = Mock()
        storage_backends_root.root = mock_storage_backends
        client.cluster.get_storage_backends.return_value = storage_backends_root

        # Mock StorageBackendManager
        mock_storage_manager = mock_storage_manager_class.return_value
        mock_storage_manager.backends.return_value = mock_backend_instances

        # Mock tfhelpers
        tfhelper = Mock()
        tfhelper.state_list.return_value = []
        tfhelper_openstack = Mock()
        tfhelper_hypervisor = Mock()
        tfhelper_cinder_volume = Mock()
        tfhelper_storage = Mock()

        deployment.get_tfhelper.side_effect = lambda plan: {
            "telemetry-plan": tfhelper,
            "openstack-plan": tfhelper_openstack,
            "hypervisor-plan": tfhelper_hypervisor,
            "cinder-volume-plan": tfhelper_cinder_volume,
            "storage-backend-plan": tfhelper_storage,
        }[plan]

        # Create feature and run disable plans
        feature = telemetry_feature.TelemetryFeature()
        feature._manifest = Mock()
        feature.run_disable_plans(deployment, False)

        # Verify all DeploySpecificCinderVolumeStep calls have correct extra_tfvars
        for call in mock_deploy_step_class.call_args_list:
            extra_tfvars = call[1]["extra_tfvars"]
            assert extra_tfvars == {"enable-telemetry-notifications": False}


@pytest.mark.parametrize("reenable", [False, True])
def test_final_gate_failure_reaches_cli_before_success(mocker, deployment, reenable):
    feature = telemetry_feature.TelemetryFeature()
    feature._manifest = Mock()
    mocker.patch.object(feature, "pre_enable")
    mocker.patch.object(feature, "post_enable")
    previous_enabled = "true" if reenable else "false"
    feature_info = {"enabled": previous_enabled}
    write_feature_info = mocker.patch.object(
        feature,
        "update_feature_info",
        side_effect=lambda client, info: feature_info.update(info),
    )
    mocker.patch.object(feature, "_readiness_requirements", return_value={})
    helper = mocker.patch("sunbeam.features.telemetry.feature.JujuHelper").return_value
    helper.wait_until_models_ready.side_effect = TimeoutError(
        "ceilometer: logging integration incomplete"
    )
    client = deployment.get_client.return_value
    client.cluster.get_storage_backends.return_value.root = []
    deployment.get_tfhelper.return_value.output.return_value = {}

    def run_final_only(plan, console, show_hints):
        final = [
            step
            for step in plan
            if isinstance(step, telemetry_feature.WaitForFeatureReadyStep)
        ]
        if final:
            return run_plan(final, console, show_hints)
        return {}

    mocker.patch(
        "sunbeam.features.telemetry.feature.run_plan", side_effect=run_final_only
    )

    @click.command()
    def enable():
        feature.enable_feature(deployment, FeatureConfig(), False)

    result = CliRunner().invoke(enable)
    assert result.exit_code == 1
    assert "logging integration incomplete" in result.output
    assert "application enabled" not in result.output
    write_feature_info.assert_not_called()
    assert feature_info["enabled"] == previous_enabled
    feature.post_enable.assert_not_called()
    helper.wait_until_models_ready.assert_called_once()


def test_noop_enablement_still_runs_final_gate(mocker, deployment):
    feature = telemetry_feature.TelemetryFeature()
    feature._manifest = Mock()
    mocker.patch.object(feature, "_readiness_requirements", return_value={})
    helper = mocker.patch("sunbeam.features.telemetry.feature.JujuHelper").return_value
    client = deployment.get_client.return_value
    client.cluster.get_storage_backends.return_value.root = []
    deployment.get_tfhelper.return_value.output.return_value = {}

    def run_final_only(plan, console, show_hints):
        final = [
            step
            for step in plan
            if isinstance(step, telemetry_feature.WaitForFeatureReadyStep)
        ]
        if final:
            return run_plan(final, console, show_hints)
        return {}

    mocker.patch(
        "sunbeam.features.telemetry.feature.run_plan", side_effect=run_final_only
    )
    feature.run_enable_plans(deployment, FeatureConfig(), False)
    helper.wait_until_models_ready.assert_called_once()
    assert helper.wait_until_models_ready.call_args.args[1] == 1800


def test_telemetry_enablement_has_its_own_timeout(deployment):
    feature = telemetry_feature.TelemetryFeature()
    assert feature.set_application_timeout_on_enable(deployment) == 1800
    assert feature.set_application_timeout_on_disable(deployment) == 900


@pytest.mark.parametrize("database_topology", ["single", "multi"])
def test_final_scope_preserves_distinct_storage_placements(
    mocker, deployment, mock_storage_backends, database_topology
):
    feature = telemetry_feature.TelemetryFeature()
    feature._manifest = Mock()
    deployment.openstack_machines_model = "machines"
    client = deployment.get_client.return_value
    client.cluster.get_config.return_value = f'{{"database": "{database_topology}"}}'
    client.cluster.list_nodes_by_role.return_value = [
        {"name": f"node{number}", "machineid": number} for number in range(3)
    ]
    client.cluster.get_storage_backends.return_value.root = mock_storage_backends

    def state(apps):
        helper = Mock()
        helper.pull_state.return_value = {
            "resources": [
                {
                    "mode": "managed",
                    "type": "juju_application",
                    "instances": [
                        {
                            "attributes": {
                                "name": app,
                                "units": len(machines) if machines is not None else 3,
                                "machines": machines,
                            }
                        }
                    ],
                }
                for app, machines in apps.items()
            ]
        }
        return helper

    helpers = {
        feature.tfplan: state(dict.fromkeys(feature.set_application_names(deployment))),
        "hypervisor-plan": state({"openstack-hypervisor": ["0", "1", "2"]}),
        "cinder-volume-plan": state({"cinder-volume": ["0", "1", "2"]}),
        "storage-backend-plan": state({"cinder-volume-noha": ["2"]}),
    }
    deployment.get_tfhelper.side_effect = helpers.__getitem__
    accepted_status = ["active", "unknown", "blocked"]
    accepted = mocker.patch.object(
        telemetry_feature.DeployCinderVolumeApplicationStep,
        "get_accepted_application_status",
        return_value=accepted_status,
    )
    requirements = feature._readiness_requirements(deployment, Mock())
    assert set(requirements["openstack"]) == set(
        feature.set_application_names(deployment)
    )
    machines = requirements["machines"]
    assert machines["cinder-volume"].machines == ["0", "1", "2"]
    assert machines["cinder-volume"].status == accepted_status
    assert machines["cinder-volume-noha"].machines == ["2"]
    assert machines["cinder-volume-noha"].status == ["active", "blocked"]
    assert machines["openstack-hypervisor"].agent_status == ("idle",)
    accepted.assert_called_once()
