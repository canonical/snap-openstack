# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

from unittest.mock import Mock, patch

from sunbeam.core.ceph import CephDeploymentMode, SetCephProviderStep
from sunbeam.core.terraform import TerraformInitStep
from sunbeam.feature_manager import FeatureManager
from sunbeam.features.ceph import feature as ceph_feature
from sunbeam.features.microceph.steps import (
    DeployMicrocephApplicationStep,
    DestroyMicrocephApplicationStep,
)


class TestCephFeature:
    def test_feature_is_discovered(self):
        manager = FeatureManager()

        assert "ceph" in manager.features()
        assert isinstance(manager.features()["ceph"], ceph_feature.CephFeature)

    @patch.object(ceph_feature, "run_plan")
    @patch.object(ceph_feature, "JujuHelper")
    @patch.object(ceph_feature, "click", Mock())
    def test_run_enable_plans(self, _mock_jhelper, mock_run_plan):
        deployment = Mock()
        deployment.openstack_machines_model = "openstack"
        deployment.get_manifest.return_value = Mock()

        feature = ceph_feature.CephFeature()
        feature.run_enable_plans(deployment, Mock(), False)

        steps = mock_run_plan.call_args.args[0]
        assert len(steps) == 3
        assert isinstance(steps[0], SetCephProviderStep)
        assert steps[0].wanted_mode == CephDeploymentMode.MICROCEPH
        assert isinstance(steps[1], TerraformInitStep)
        assert isinstance(steps[2], DeployMicrocephApplicationStep)
        deployment.get_tfhelper.assert_called_once_with("microceph-plan")

    @patch.object(ceph_feature, "run_plan")
    @patch.object(ceph_feature, "JujuHelper")
    @patch.object(ceph_feature, "click", Mock())
    def test_run_disable_plans(self, _mock_jhelper, mock_run_plan):
        deployment = Mock()
        deployment.openstack_machines_model = "openstack"
        deployment.get_manifest.return_value = Mock()

        feature = ceph_feature.CephFeature()
        feature.run_disable_plans(deployment, False)

        steps = mock_run_plan.call_args.args[0]
        assert len(steps) == 3
        assert isinstance(steps[0], SetCephProviderStep)
        assert steps[0].wanted_mode == CephDeploymentMode.NONE
        assert isinstance(steps[1], TerraformInitStep)
        assert isinstance(steps[2], DestroyMicrocephApplicationStep)
        deployment.get_tfhelper.assert_called_once_with("microceph-plan")
