# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

from unittest.mock import Mock, patch

import pytest

from sunbeam.features.secrets import feature as secrets_feature


@pytest.fixture()
def deployment():
    deploy = Mock()
    deploy.openstack_machines_model = "openstack"
    deploy.juju_controller = "test-controller"

    client = deploy.get_client.return_value
    client.cluster.list_nodes_by_role.return_value = [{"name": "node1", "machineid": 1}]

    return deploy


class TestSecretsFeatureRunEnablePlans:
    @pytest.fixture()
    def tfhelpers(self, deployment):
        tfhelper_openstack = Mock()
        tfhelper_openstack.output.return_value = {
            "barbican-offer-url": "admin/openstack.barbican"
        }
        tfhelper_hypervisor = Mock()

        deployment.get_tfhelper.side_effect = lambda plan: {
            "openstack-plan": tfhelper_openstack,
            "hypervisor-plan": tfhelper_hypervisor,
        }[plan]

        return {
            "openstack": tfhelper_openstack,
            "hypervisor": tfhelper_hypervisor,
        }

    @patch("sunbeam.features.secrets.feature.JujuHelper")
    @patch("sunbeam.features.secrets.feature.ReapplyHypervisorTerraformPlanStep")
    @patch("sunbeam.features.secrets.feature.run_plan")
    def test_passes_barbican_offer_url_to_hypervisor(
        self,
        mock_run_plan,
        mock_reapply_step_class,
        mock_jhelper_class,
        deployment,
        tfhelpers,
    ):
        """barbican-offer-url from openstack output is forwarded to hypervisor plan."""
        feature = secrets_feature.SecretsFeature()
        feature._manifest = Mock()
        feature.run_enable_plans(deployment, Mock(), False)

        mock_reapply_step_class.assert_called_once()
        assert mock_reapply_step_class.call_args.kwargs["extra_tfvars"] == {
            "barbican-offer-url": "admin/openstack.barbican"
        }

    @patch("sunbeam.features.secrets.feature.JujuHelper")
    @patch("sunbeam.features.secrets.feature.ReapplyHypervisorTerraformPlanStep")
    @patch("sunbeam.features.secrets.feature.run_plan")
    def test_passes_none_when_offer_url_absent(
        self,
        mock_run_plan,
        mock_reapply_step_class,
        mock_jhelper_class,
        deployment,
    ):
        """Missing barbican-offer-url in openstack output is forwarded as None."""
        tfhelper_openstack = Mock()
        tfhelper_openstack.output.return_value = {}

        deployment.get_tfhelper.side_effect = lambda plan: {
            "openstack-plan": tfhelper_openstack,
            "hypervisor-plan": Mock(),
        }[plan]

        feature = secrets_feature.SecretsFeature()
        feature._manifest = Mock()
        feature.run_enable_plans(deployment, Mock(), False)

        mock_reapply_step_class.assert_called_once()
        assert mock_reapply_step_class.call_args.kwargs["extra_tfvars"] == {
            "barbican-offer-url": None
        }

    @patch("sunbeam.features.secrets.feature.JujuHelper")
    @patch("sunbeam.features.secrets.feature.ReapplyHypervisorTerraformPlanStep")
    @patch("sunbeam.features.secrets.feature.run_plan")
    def test_runs_two_plans(
        self,
        mock_run_plan,
        mock_reapply_step_class,
        mock_jhelper_class,
        deployment,
        tfhelpers,
    ):
        """Enable runs exactly two plans: control plane deploy then hypervisor reapply.

        Ensures the sequential structure of the enable flow is preserved.
        """
        feature = secrets_feature.SecretsFeature()
        feature._manifest = Mock()
        feature.run_enable_plans(deployment, Mock(), False)

        assert mock_run_plan.call_count == 2


class TestSecretsFeatureRunDisablePlans:
    @pytest.fixture()
    def tfhelpers(self, deployment):
        tfhelper = Mock()
        tfhelper_hypervisor = Mock()

        deployment.get_tfhelper.side_effect = lambda plan: {
            "openstack-plan": tfhelper,
            "hypervisor-plan": tfhelper_hypervisor,
        }[plan]

        return {"openstack": tfhelper, "hypervisor": tfhelper_hypervisor}

    @patch("sunbeam.features.secrets.feature.JujuHelper")
    @patch("sunbeam.features.secrets.feature.ReapplyHypervisorTerraformPlanStep")
    @patch("sunbeam.features.secrets.feature.RemoveSaasApplicationsStep")
    @patch("sunbeam.features.secrets.feature.run_plan")
    def test_clears_barbican_offer_url(
        self,
        mock_run_plan,
        mock_remove_saas_class,
        mock_reapply_step_class,
        mock_jhelper_class,
        deployment,
        tfhelpers,
    ):
        """Disable passes None for barbican-offer-url to tear down the CMR."""
        feature = secrets_feature.SecretsFeature()
        feature._manifest = Mock()
        feature.run_disable_plans(deployment, False)

        mock_reapply_step_class.assert_called_once()
        assert mock_reapply_step_class.call_args.kwargs["extra_tfvars"] == {
            "barbican-offer-url": None
        }

    @patch("sunbeam.features.secrets.feature.JujuHelper")
    @patch("sunbeam.features.secrets.feature.ReapplyHypervisorTerraformPlanStep")
    @patch("sunbeam.features.secrets.feature.RemoveSaasApplicationsStep")
    @patch("sunbeam.features.secrets.feature.run_plan")
    def test_removes_barbican_saas(
        self,
        mock_run_plan,
        mock_remove_saas_class,
        mock_reapply_step_class,
        mock_jhelper_class,
        deployment,
        tfhelpers,
    ):
        """Disable removes the barbican SAAS application from the machines model."""
        feature = secrets_feature.SecretsFeature()
        feature._manifest = Mock()
        feature.run_disable_plans(deployment, False)

        mock_remove_saas_class.assert_called_once()
        assert mock_remove_saas_class.call_args.kwargs["saas_apps_to_delete"] == [
            "barbican"
        ]
