# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import json
from unittest.mock import Mock, patch

import pytest

from sunbeam.clusterd.service import ConfigItemNotFoundException
from sunbeam.core.common import ResultType
from sunbeam.features.telemetry import feature as telemetry_feature


@pytest.fixture()
def deployment():
    deploy = Mock()
    deploy.openstack_machines_model = "openstack"
    deploy.juju_controller = "test-controller"

    client = deploy.get_client.return_value
    client.cluster.list_nodes_by_role.return_value = [{"name": "node1", "machineid": 1}]
    # Return empty config for metrics backend (no S3 offer configured)
    client.cluster.get_config.return_value = json.dumps({})

    return deploy


class TestUpdateCinderVolumeTelemetryTfvarsStep:
    """Test the UpdateCinderVolumeTelemetryTfvarsStep."""

    @patch("sunbeam.features.telemetry.feature.read_config")
    @patch("sunbeam.features.telemetry.feature.update_config")
    def test_run_enables_telemetry_on_all_cinder_volumes(
        self,
        mock_update_config,
        mock_read_config,
        step_context,
    ):
        """Enabling telemetry should set flag=True on every cinder-volume entry."""
        client = Mock()
        mock_read_config.return_value = {
            "backends": {"backend-a": {}},
            "cinder-volumes": {
                "cinder-volume": {"application_name": "cinder-volume"},
                "cinder-volume-noha": {"application_name": "cinder-volume-noha"},
            },
        }

        step = telemetry_feature.UpdateCinderVolumeTelemetryTfvarsStep(
            client, enable=True
        )
        result = step.run(step_context)

        assert result.result_type == ResultType.COMPLETED
        written = mock_update_config.call_args[0][2]
        for entry in written["cinder-volumes"].values():
            assert entry["enable-telemetry-notifications"] is True

    @patch("sunbeam.features.telemetry.feature.read_config")
    @patch("sunbeam.features.telemetry.feature.update_config")
    def test_run_disables_telemetry_on_all_cinder_volumes(
        self,
        mock_update_config,
        mock_read_config,
        step_context,
    ):
        """Disabling telemetry should set flag=False on every cinder-volume entry."""
        client = Mock()
        mock_read_config.return_value = {
            "backends": {"backend-a": {}},
            "cinder-volumes": {
                "cinder-volume": {
                    "application_name": "cinder-volume",
                    "enable-telemetry-notifications": True,
                },
            },
        }

        step = telemetry_feature.UpdateCinderVolumeTelemetryTfvarsStep(
            client, enable=False
        )
        result = step.run(step_context)

        assert result.result_type == ResultType.COMPLETED
        written = mock_update_config.call_args[0][2]
        assert (
            written["cinder-volumes"]["cinder-volume"]["enable-telemetry-notifications"]
            is False
        )

    @patch("sunbeam.features.telemetry.feature.read_config")
    def test_is_skip_when_no_config(self, mock_read_config, step_context):
        """Step should skip when no storage backend config exists."""
        client = Mock()
        mock_read_config.side_effect = ConfigItemNotFoundException("not found")

        step = telemetry_feature.UpdateCinderVolumeTelemetryTfvarsStep(
            client, enable=True
        )
        result = step.is_skip(step_context)
        assert result.result_type == ResultType.SKIPPED

    @patch("sunbeam.features.telemetry.feature.read_config")
    def test_is_skip_when_no_cinder_volumes(self, mock_read_config, step_context):
        """Step should skip when cinder-volumes is empty."""
        client = Mock()
        mock_read_config.return_value = {
            "backends": {"backend-a": {}},
            "cinder-volumes": {},
        }

        step = telemetry_feature.UpdateCinderVolumeTelemetryTfvarsStep(
            client, enable=True
        )
        result = step.is_skip(step_context)
        assert result.result_type == ResultType.SKIPPED

    @patch("sunbeam.features.telemetry.feature.read_config")
    def test_is_skip_returns_completed_when_entries_exist(
        self, mock_read_config, step_context
    ):
        """Step should not skip when cinder-volume entries exist."""
        client = Mock()
        mock_read_config.return_value = {
            "cinder-volumes": {"cinder-volume": {"application_name": "cinder-volume"}},
        }

        step = telemetry_feature.UpdateCinderVolumeTelemetryTfvarsStep(
            client, enable=True
        )
        result = step.is_skip(step_context)
        assert result.result_type == ResultType.COMPLETED

    @patch("sunbeam.features.telemetry.feature.read_config")
    @patch("sunbeam.features.telemetry.feature.update_config")
    def test_run_completes_when_no_cinder_volumes(
        self, mock_update_config, mock_read_config, step_context
    ):
        """Run should complete gracefully when no cinder-volume entries."""
        client = Mock()
        mock_read_config.return_value = {"backends": {}, "cinder-volumes": {}}

        step = telemetry_feature.UpdateCinderVolumeTelemetryTfvarsStep(
            client, enable=True
        )
        result = step.run(step_context)

        assert result.result_type == ResultType.COMPLETED
        mock_update_config.assert_not_called()


class TestTelemetryFeatureEnableDisablePlans:
    """Test that enable/disable plans use the correct storage backend plan name."""

    @patch("sunbeam.features.telemetry.feature.StorageBackendBase.__init__")
    @patch("sunbeam.features.telemetry.feature.StorageBackendBase.__new__")
    @patch("sunbeam.features.telemetry.feature.JujuHelper")
    @patch("sunbeam.features.telemetry.feature.run_plan")
    def test_run_enable_plans_uses_storage_backend_plan(
        self,
        mock_run_plan,
        mock_jhelper_class,
        mock_sbb_new,
        mock_sbb_init,
        deployment,
    ):
        """Enable plans should use storage-backend-plan, not storage-plan."""
        mock_sbb_instance = Mock()
        mock_sbb_instance.tfplan = "storage-backend-plan"
        mock_sbb_new.return_value = mock_sbb_instance
        mock_sbb_init.return_value = None

        tfhelper = Mock()
        tfhelper_openstack = Mock()
        tfhelper_openstack.output.return_value = {"ceilometer-offer-url": "url"}
        tfhelper_hypervisor = Mock()
        tfhelper_storage = Mock()

        deployment.get_tfhelper.side_effect = lambda plan: {
            "telemetry-plan": tfhelper,
            "openstack-plan": tfhelper_openstack,
            "hypervisor-plan": tfhelper_hypervisor,
            "storage-backend-plan": tfhelper_storage,
        }[plan]

        feature = telemetry_feature.TelemetryFeature()
        feature._manifest = Mock()
        feature.run_enable_plans(deployment, Mock(), False)

        # Verify that get_tfhelper was called with "storage-backend-plan"
        # (it should NOT raise KeyError for "storage-plan")
        calls = deployment.get_tfhelper.call_args_list
        plan_names = [call[0][0] for call in calls]
        assert "storage-backend-plan" in plan_names
        assert "storage-plan" not in plan_names

    @patch("sunbeam.features.telemetry.feature.StorageBackendBase.__init__")
    @patch("sunbeam.features.telemetry.feature.StorageBackendBase.__new__")
    @patch("sunbeam.features.telemetry.feature.JujuHelper")
    @patch("sunbeam.features.telemetry.feature.run_plan")
    def test_run_disable_plans_uses_storage_backend_plan(
        self,
        mock_run_plan,
        mock_jhelper_class,
        mock_sbb_new,
        mock_sbb_init,
        deployment,
    ):
        """Disable plans should use storage-backend-plan, not storage-plan."""
        mock_sbb_instance = Mock()
        mock_sbb_instance.tfplan = "storage-backend-plan"
        mock_sbb_new.return_value = mock_sbb_instance
        mock_sbb_init.return_value = None

        tfhelper = Mock()
        tfhelper.state_list.return_value = []
        tfhelper_openstack = Mock()
        tfhelper_hypervisor = Mock()
        tfhelper_storage = Mock()

        deployment.get_tfhelper.side_effect = lambda plan: {
            "telemetry-plan": tfhelper,
            "openstack-plan": tfhelper_openstack,
            "hypervisor-plan": tfhelper_hypervisor,
            "storage-backend-plan": tfhelper_storage,
        }[plan]

        feature = telemetry_feature.TelemetryFeature()
        feature._manifest = Mock()
        feature.run_disable_plans(deployment, False)

        calls = deployment.get_tfhelper.call_args_list
        plan_names = [call[0][0] for call in calls]
        assert "storage-backend-plan" in plan_names
        assert "storage-plan" not in plan_names

    @patch("sunbeam.features.telemetry.feature.StorageBackendBase.__init__")
    @patch("sunbeam.features.telemetry.feature.StorageBackendBase.__new__")
    @patch("sunbeam.features.telemetry.feature.JujuHelper")
    @patch("sunbeam.features.telemetry.feature.run_plan")
    def test_run_enable_plans_includes_update_and_reapply_steps(
        self,
        mock_run_plan,
        mock_jhelper_class,
        mock_sbb_new,
        mock_sbb_init,
        deployment,
    ):
        """Enable plan3 should include update and reapply steps.

        Checks for UpdateCinderVolumeTelemetryTfvarsStep and
        ReapplyStorageBackendTerraformPlanStep.
        """
        from sunbeam.storage.steps import ReapplyStorageBackendTerraformPlanStep

        mock_sbb_instance = Mock()
        mock_sbb_instance.tfplan = "storage-backend-plan"
        mock_sbb_new.return_value = mock_sbb_instance
        mock_sbb_init.return_value = None

        tfhelper = Mock()
        tfhelper_openstack = Mock()
        tfhelper_openstack.output.return_value = {"ceilometer-offer-url": "url"}
        tfhelper_hypervisor = Mock()
        tfhelper_storage = Mock()

        deployment.get_tfhelper.side_effect = lambda plan: {
            "telemetry-plan": tfhelper,
            "openstack-plan": tfhelper_openstack,
            "hypervisor-plan": tfhelper_hypervisor,
            "storage-backend-plan": tfhelper_storage,
        }[plan]

        feature = telemetry_feature.TelemetryFeature()
        feature._manifest = Mock()
        feature.run_enable_plans(deployment, Mock(), False)

        # run_plan is called 3 times: plan1, plan2, plan3
        assert mock_run_plan.call_count == 3

        # plan3 is the last call
        plan3_steps = mock_run_plan.call_args_list[2][0][0]
        step_types = [type(s) for s in plan3_steps]
        assert telemetry_feature.UpdateCinderVolumeTelemetryTfvarsStep in step_types
        assert ReapplyStorageBackendTerraformPlanStep in step_types

        # Verify the update step has enable=True
        update_steps = [
            s
            for s in plan3_steps
            if isinstance(s, telemetry_feature.UpdateCinderVolumeTelemetryTfvarsStep)
        ]
        assert len(update_steps) == 1
        assert update_steps[0].enable is True

    @patch("sunbeam.features.telemetry.feature.StorageBackendBase.__init__")
    @patch("sunbeam.features.telemetry.feature.StorageBackendBase.__new__")
    @patch("sunbeam.features.telemetry.feature.JujuHelper")
    @patch("sunbeam.features.telemetry.feature.run_plan")
    def test_run_disable_plans_includes_update_and_reapply_steps(
        self,
        mock_run_plan,
        mock_jhelper_class,
        mock_sbb_new,
        mock_sbb_init,
        deployment,
    ):
        """Disable plan2 should include update and reapply steps.

        Checks for UpdateCinderVolumeTelemetryTfvarsStep and
        ReapplyStorageBackendTerraformPlanStep.
        """
        from sunbeam.storage.steps import ReapplyStorageBackendTerraformPlanStep

        mock_sbb_instance = Mock()
        mock_sbb_instance.tfplan = "storage-backend-plan"
        mock_sbb_new.return_value = mock_sbb_instance
        mock_sbb_init.return_value = None

        tfhelper = Mock()
        tfhelper.state_list.return_value = []
        tfhelper_openstack = Mock()
        tfhelper_hypervisor = Mock()
        tfhelper_storage = Mock()

        deployment.get_tfhelper.side_effect = lambda plan: {
            "telemetry-plan": tfhelper,
            "openstack-plan": tfhelper_openstack,
            "hypervisor-plan": tfhelper_hypervisor,
            "storage-backend-plan": tfhelper_storage,
        }[plan]

        feature = telemetry_feature.TelemetryFeature()
        feature._manifest = Mock()
        feature.run_disable_plans(deployment, False)

        # run_plan is called: plan (disable main), plan2 (storage update)
        assert mock_run_plan.call_count == 2

        # plan2 is the last call
        plan2_steps = mock_run_plan.call_args_list[1][0][0]
        step_types = [type(s) for s in plan2_steps]
        assert telemetry_feature.UpdateCinderVolumeTelemetryTfvarsStep in step_types
        assert ReapplyStorageBackendTerraformPlanStep in step_types

        # Verify the update step has enable=False
        update_steps = [
            s
            for s in plan2_steps
            if isinstance(s, telemetry_feature.UpdateCinderVolumeTelemetryTfvarsStep)
        ]
        assert len(update_steps) == 1
        assert update_steps[0].enable is False
