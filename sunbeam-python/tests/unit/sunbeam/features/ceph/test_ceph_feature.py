# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import json
from unittest.mock import ANY, Mock, patch

import click
import pytest

from sunbeam.clusterd.service import ConfigItemNotFoundException
from sunbeam.core.ceph import CephDeploymentMode, SetCephProviderStep
from sunbeam.core.terraform import TerraformInitStep
from sunbeam.feature_manager import FeatureManager
from sunbeam.features.ceph import feature as ceph_feature
from sunbeam.features.microceph.steps import (
    ConfigureMicrocephOSDStep,
    DeployMicrocephApplicationStep,
    DestroyMicrocephApplicationStep,
)
from sunbeam.provider.maas.steps import MaasConfigureMicrocephOSDStep
from sunbeam.steps.openstack import DeployControlPlaneStep
from sunbeam.storage.steps import (
    BaseStorageBackendDeployStep,
    BaseStorageBackendDestroyStep,
    DeploySpecificCinderVolumeStep,
    DestroySpecificCinderVolumeStep,
)


class TestCephFeature:
    def test_feature_is_discovered(self):
        manager = FeatureManager()

        assert "ceph" in manager.features()
        assert isinstance(manager.features()["ceph"], ceph_feature.CephFeature)

    @patch.object(ceph_feature, "run_plan")
    @patch.object(ceph_feature, "JujuHelper")
    @patch.object(ceph_feature, "click", Mock())
    @patch.object(ceph_feature, "update_config")
    def test_run_enable_plans(self, mock_update_config, _mock_jhelper, mock_run_plan):
        deployment = Mock()
        deployment.openstack_machines_model = "openstack"
        deployment.get_manifest.return_value = Mock()
        deployment.get_client.return_value = Mock()
        deployment.get_client.return_value.cluster.list_nodes_by_role.return_value = [
            {"machineid": "0"},
            {"machineid": "1"},
            {"machineid": "2"},
        ]

        feature = ceph_feature.CephFeature()
        with patch.object(
            feature,
            "_get_internal_ceph_backend",
        ) as mock_get_backend:
            mock_backend = Mock()
            mock_backend.tfplan = "storage-backend-plan"
            mock_backend.config_key.return_value = "Storage-internal-ceph"
            mock_backend.create_deploy_step.return_value = Mock(
                spec=BaseStorageBackendDeployStep
            )
            mock_get_backend.return_value = mock_backend

            feature.run_enable_plans(deployment, Mock(), False)

        steps = mock_run_plan.call_args.args[0]

        # First 3 steps: microceph deployment
        assert isinstance(steps[0], SetCephProviderStep)
        assert steps[0].wanted_mode == CephDeploymentMode.MICROCEPH
        assert isinstance(steps[1], TerraformInitStep)
        assert isinstance(steps[2], DeployMicrocephApplicationStep)

        # Next 5 steps: internal-ceph backend registration
        assert isinstance(steps[3], TerraformInitStep)  # storage backend tf init
        assert isinstance(steps[4], TerraformInitStep)  # openstack tf init
        assert isinstance(steps[5], DeploySpecificCinderVolumeStep)
        assert isinstance(steps[6], BaseStorageBackendDeployStep)
        assert isinstance(steps[7], DeployControlPlaneStep)

        assert len(steps) == 8

    @patch.object(ceph_feature, "run_plan")
    @patch.object(ceph_feature, "JujuHelper")
    @patch.object(ceph_feature, "click", Mock())
    @patch.object(ceph_feature, "update_config")
    def test_run_enable_plans_stores_config(
        self, mock_update_config, _mock_jhelper, mock_run_plan
    ):
        """Verify that enable stores the InternalCephConfig in clusterd."""
        deployment = Mock()
        deployment.openstack_machines_model = "openstack"
        deployment.get_manifest.return_value = Mock()
        client = Mock()
        client.cluster.list_nodes_by_role.return_value = [
            {"machineid": "0"},
            {"machineid": "1"},
        ]
        deployment.get_client.return_value = client

        feature = ceph_feature.CephFeature()
        with patch.object(
            feature,
            "_get_internal_ceph_backend",
        ) as mock_get_backend:
            mock_backend = Mock()
            mock_backend.tfplan = "storage-backend-plan"
            mock_backend.config_key.return_value = "Storage-internal-ceph"
            mock_backend.create_deploy_step.return_value = Mock(
                spec=BaseStorageBackendDeployStep
            )
            mock_get_backend.return_value = mock_backend

            feature.run_enable_plans(deployment, Mock(), False)

        # ceph_replica_scale(2) == 2, stored with kebab-case alias
        mock_update_config.assert_called_once_with(
            client,
            "Storage-internal-ceph",
            {"ceph-osd-replication-count": 2},
        )

    @patch.object(ceph_feature, "run_plan")
    @patch.object(ceph_feature, "JujuHelper")
    @patch.object(ceph_feature, "click", Mock())
    def test_run_disable_plans(self, _mock_jhelper, mock_run_plan):
        deployment = Mock()
        deployment.openstack_machines_model = "openstack"
        deployment.get_manifest.return_value = Mock()
        deployment.get_client.return_value = Mock()

        feature = ceph_feature.CephFeature()
        with patch.object(
            feature,
            "_get_internal_ceph_backend",
        ) as mock_get_backend:
            mock_backend = Mock()
            mock_backend.tfplan = "storage-backend-plan"
            mock_backend.create_destroy_step.return_value = Mock(
                spec=BaseStorageBackendDestroyStep
            )
            mock_get_backend.return_value = mock_backend

            feature.run_disable_plans(deployment, False)

        # Phase 1: destroy backend (mode still MICROCEPH)
        assert mock_run_plan.call_count == 4
        destroy_steps = mock_run_plan.call_args_list[0].args[0]
        assert isinstance(destroy_steps[0], TerraformInitStep)
        assert isinstance(destroy_steps[1], BaseStorageBackendDestroyStep)
        assert isinstance(destroy_steps[2], DestroySpecificCinderVolumeStep)

        # Phase 2a: set mode to NONE
        mode_steps = mock_run_plan.call_args_list[1].args[0]
        assert len(mode_steps) == 1
        assert isinstance(mode_steps[0], SetCephProviderStep)
        assert mode_steps[0].wanted_mode == CephDeploymentMode.NONE

        # Phase 2b: reapply control plane (now sees NoCephProvider)
        cp_steps = mock_run_plan.call_args_list[2].args[0]
        assert isinstance(cp_steps[0], TerraformInitStep)
        assert isinstance(cp_steps[1], DeployControlPlaneStep)

        # Phase 3: destroy MicroCeph
        mc_steps = mock_run_plan.call_args_list[3].args[0]
        assert isinstance(mc_steps[0], TerraformInitStep)
        assert isinstance(mc_steps[1], DestroyMicrocephApplicationStep)

    @patch.object(ceph_feature.CephFeature, "run_enable_plans")
    def test_enable_default_storage_skips_when_mode_does_not_require_internal_ceph(
        self, mock_run_enable_plans
    ):
        deployment = Mock()
        client = Mock()
        client.cluster.get_config.return_value = json.dumps(
            {"mode": CephDeploymentMode.NONE}
        )
        deployment.get_client.return_value = client

        feature = ceph_feature.CephFeature()
        with patch.object(feature, "get_feature_info", return_value={}):
            with patch.object(feature, "update_feature_info") as mock_update:
                feature.enable_default_storage(deployment, False)

        mock_run_enable_plans.assert_not_called()
        mock_update.assert_not_called()

    @patch.object(ceph_feature.CephFeature, "run_enable_plans")
    def test_enable_default_storage_reconciles_when_mode_requires_internal_ceph(
        self, mock_run_enable_plans
    ):
        deployment = Mock()
        client = Mock()
        client.cluster.get_config.return_value = json.dumps(
            {"mode": CephDeploymentMode.MICROCEPH}
        )
        deployment.get_client.return_value = client

        feature = ceph_feature.CephFeature()
        with patch.object(feature, "get_feature_info", return_value={}):
            with patch.object(feature, "update_feature_info") as mock_update:
                feature.enable_default_storage(deployment, False)

        mock_run_enable_plans.assert_called_once_with(deployment, ANY, False)
        mock_update.assert_called_once_with(
            client,
            {
                "enabled": "true",
                ceph_feature.DEFAULT_STORAGE_RECONCILED_KEY: "true",
            },
        )

    @patch.object(ceph_feature.CephFeature, "run_enable_plans")
    def test_enable_default_storage_reconciles_optimistic_enabled_state(
        self, mock_run_enable_plans
    ):
        deployment = Mock()
        client = Mock()
        client.cluster.get_config.return_value = json.dumps(
            {"mode": CephDeploymentMode.MICROCEPH}
        )
        deployment.get_client.return_value = client

        feature = ceph_feature.CephFeature()
        with patch.object(
            feature,
            "get_feature_info",
            return_value={"enabled": "true"},
        ):
            with patch.object(feature, "update_feature_info") as mock_update:
                feature.enable_default_storage(deployment, False)

        mock_run_enable_plans.assert_called_once_with(deployment, ANY, False)
        mock_update.assert_called_once_with(
            client,
            {
                "enabled": "true",
                ceph_feature.DEFAULT_STORAGE_RECONCILED_KEY: "true",
            },
        )

    @patch.object(ceph_feature.CephFeature, "run_enable_plans")
    def test_enable_default_storage_skips_when_fully_reconciled(
        self, mock_run_enable_plans
    ):
        deployment = Mock()
        client = Mock()
        client.cluster.get_config.return_value = json.dumps(
            {"mode": CephDeploymentMode.MICROCEPH}
        )
        deployment.get_client.return_value = client

        feature = ceph_feature.CephFeature()
        with patch.object(
            feature,
            "get_feature_info",
            return_value={
                "enabled": "true",
                ceph_feature.DEFAULT_STORAGE_RECONCILED_KEY: "true",
            },
        ):
            with patch.object(feature, "update_feature_info") as mock_update:
                feature.enable_default_storage(deployment, False)

        mock_run_enable_plans.assert_not_called()
        mock_update.assert_not_called()

    @patch.object(ceph_feature.CephFeature, "run_enable_plans")
    def test_enable_feature_marks_default_storage_reconciled(
        self, mock_run_enable_plans
    ):
        deployment = Mock()
        client = Mock()
        client.cluster.get_config.return_value = json.dumps(
            {"mode": CephDeploymentMode.MICROCEPH}
        )
        deployment.get_client.return_value = client

        feature = ceph_feature.CephFeature()
        feature_state = {}
        click_context = Mock()
        click_context.parent = None

        def fake_update_feature_info(_client, info):
            feature_state.update(info)

        with patch.object(feature, "pre_enable"):
            with patch.object(
                feature,
                "get_feature_info",
                side_effect=lambda _client: feature_state.copy(),
            ):
                with patch.object(
                    feature,
                    "update_feature_info",
                    side_effect=fake_update_feature_info,
                ):
                    with patch(
                        "sunbeam.features.interface.v1.base.click.get_current_context",
                        return_value=click_context,
                    ):
                        feature.enable_feature(deployment, Mock(), False)
                    feature.enable_default_storage(deployment, False)

        assert feature_state == {
            "enabled": "true",
            ceph_feature.DEFAULT_STORAGE_RECONCILED_KEY: "true",
        }
        assert mock_run_enable_plans.call_count == 1

    @patch.object(ceph_feature, "run_plan")
    @patch.object(ceph_feature, "JujuHelper")
    @patch.object(ceph_feature, "click", Mock())
    @patch.object(ceph_feature, "update_config")
    def test_enable_default_storage_local_includes_local_disk_step(
        self, _mock_update_config, _mock_jhelper, mock_run_plan
    ):
        deployment = Mock()
        deployment.type = "local"
        deployment.openstack_machines_model = "openstack"
        deployment.get_manifest.return_value = Mock()
        client = Mock()
        client.cluster.list_nodes_by_role.return_value = [{"machineid": "0"}]
        client.cluster.get_config.side_effect = ConfigItemNotFoundException("missing")
        deployment.get_client.return_value = client

        feature = ceph_feature.CephFeature()
        with patch.object(feature, "_get_internal_ceph_backend") as mock_get_backend:
            mock_backend = Mock()
            mock_backend.tfplan = "storage-backend-plan"
            mock_backend.config_key.return_value = "Storage-internal-ceph"
            mock_backend.create_deploy_step.return_value = Mock(
                spec=BaseStorageBackendDeployStep
            )
            mock_get_backend.return_value = mock_backend

            feature.enable_default_storage(
                deployment,
                False,
                node_name="node-1",
                accept_defaults=True,
            )

        steps = mock_run_plan.call_args.args[0]
        assert any(isinstance(step, ConfigureMicrocephOSDStep) for step in steps)

    @patch.object(ceph_feature, "run_plan")
    @patch.object(ceph_feature, "JujuHelper")
    @patch.object(ceph_feature, "click", Mock())
    @patch.object(ceph_feature, "update_config")
    def test_enable_default_storage_maas_includes_maas_disk_step(
        self, _mock_update_config, _mock_jhelper, mock_run_plan
    ):
        deployment = Mock()
        deployment.type = "maas"
        deployment.openstack_machines_model = "openstack"
        deployment.get_manifest.return_value = Mock()
        client = Mock()
        client.cluster.list_nodes_by_role.return_value = [{"machineid": "0"}]
        client.cluster.get_config.side_effect = ConfigItemNotFoundException("missing")
        deployment.get_client.return_value = client

        feature = ceph_feature.CephFeature()
        with patch.object(feature, "_get_internal_ceph_backend") as mock_get_backend:
            mock_backend = Mock()
            mock_backend.tfplan = "storage-backend-plan"
            mock_backend.config_key.return_value = "Storage-internal-ceph"
            mock_backend.create_deploy_step.return_value = Mock(
                spec=BaseStorageBackendDeployStep
            )
            mock_get_backend.return_value = mock_backend

            feature.enable_default_storage(
                deployment,
                False,
                maas_client=Mock(),
                storage=["node-1"],
            )

        steps = mock_run_plan.call_args.args[0]
        assert any(isinstance(step, MaasConfigureMicrocephOSDStep) for step in steps)


class TestCephFeatureDisableForceFlag:
    """Tests for the --force flag on disable_cmd."""

    def _make_click_context(self, deployment):
        """Create a click context with the deployment as obj."""
        ctx = click.Context(click.Command("test"), obj=deployment)
        return ctx

    def test_disable_without_force_raises_error(self):
        """disable_cmd without --force should raise ClickException."""
        feature = ceph_feature.CephFeature()
        deployment = Mock()

        with self._make_click_context(deployment):
            with pytest.raises(click.ClickException, match="data loss"):
                # pass_method_obj injects deployment from click context
                feature.disable_cmd.callback(feature, force=False, show_hints=False)

    def test_disable_with_force_proceeds(self):
        """disable_cmd with --force should call disable_feature."""
        feature = ceph_feature.CephFeature()
        deployment = Mock()

        with self._make_click_context(deployment):
            with patch.object(feature, "disable_feature") as mock_disable:
                # pass_method_obj injects deployment from click context
                feature.disable_cmd.callback(feature, force=True, show_hints=False)
                mock_disable.assert_called_once_with(deployment, False)

    def test_disable_cmd_has_force_option(self):
        """disable_cmd should have a --force flag."""
        feature = ceph_feature.CephFeature()
        cmd = feature.disable_cmd
        param_names = [p.name for p in cmd.params]
        assert "force" in param_names


class TestCephFeatureOnJoin:
    """Tests for the on_join hook."""

    @patch.object(ceph_feature, "run_plan")
    @patch.object(ceph_feature, "JujuHelper")
    @patch.object(ceph_feature, "update_config")
    def test_on_join_reconciles_storage_when_already_enabled(
        self, _mock_update_config, _mock_jhelper, mock_run_plan
    ):
        """on_join reapplies storage backend when feature is already reconciled."""
        deployment = Mock()
        deployment.openstack_machines_model = "openstack"
        deployment.get_manifest.return_value = Mock()
        client = Mock()
        deployment.get_client.return_value = client
        client.cluster.list_nodes_by_role.return_value = [
            {"machineid": "0"},
            {"machineid": "1"},
        ]

        feature = ceph_feature.CephFeature()
        # Mark feature as already reconciled
        feature.get_feature_info = Mock(
            return_value={"enabled": "true", "default_storage_reconciled": "true"}
        )

        with patch.object(feature, "_get_internal_ceph_backend") as mock_get_backend:
            mock_backend = Mock()
            mock_backend.tfplan = "storage-backend-plan"
            mock_backend.config_key.return_value = "Storage-internal-ceph"
            mock_backend.create_deploy_step.return_value = Mock(
                spec=BaseStorageBackendDeployStep
            )
            mock_get_backend.return_value = mock_backend

            feature.on_join(
                deployment,
                {"name": "node-2", "role": ["storage"]},
                roles=["storage"],
            )

        # Should be called twice: once for MicroCeph, once for storage reconciliation
        assert mock_run_plan.call_count == 2

        # Second call should include storage backend steps
        reconcile_steps = mock_run_plan.call_args_list[1].args[0]
        assert any(
            isinstance(s, DeploySpecificCinderVolumeStep) for s in reconcile_steps
        )
        assert any(isinstance(s, DeployControlPlaneStep) for s in reconcile_steps)

    @patch.object(ceph_feature, "run_plan")
    @patch.object(ceph_feature, "JujuHelper")
    def test_on_join_skips_reconciliation_when_not_yet_enabled(
        self, _mock_jhelper, mock_run_plan
    ):
        """on_join does not reconcile storage when feature not yet reconciled."""
        deployment = Mock()
        deployment.openstack_machines_model = "openstack"
        deployment.get_manifest.return_value = Mock()
        client = Mock()
        deployment.get_client.return_value = client

        feature = ceph_feature.CephFeature()
        # Feature not yet reconciled
        feature.get_feature_info = Mock(return_value={})

        feature.on_join(
            deployment,
            {"name": "node-1", "role": ["storage"]},
            roles=["storage"],
        )

        # Only the MicroCeph plan should run
        assert mock_run_plan.call_count == 1
