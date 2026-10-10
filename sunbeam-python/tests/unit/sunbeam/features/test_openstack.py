# SPDX-FileCopyrightText: 2023 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

from unittest.mock import Mock, patch

import pytest

import sunbeam.features.interface.v1.openstack as openstack
from sunbeam.core.common import ResultType
from sunbeam.core.terraform import TerraformException


@pytest.fixture()
def osfeature():
    with patch(
        "sunbeam.features.interface.v1.openstack.OpenStackControlPlaneFeature"
    ) as p:
        yield p


class TestEnableOpenStackApplicationStep:
    def test_run(self, deployment, tfhelper, jhelper, osfeature, step_context):
        step = openstack.EnableOpenStackApplicationStep(
            deployment, Mock(), tfhelper, jhelper, osfeature
        )
        result = step.run(step_context)

        tfhelper.update_tfvars.assert_called_once()
        tfhelper.apply.assert_called_once()
        jhelper.wait_until_desired_status.assert_called_once()
        assert result.result_type == ResultType.COMPLETED

    def test_run_tf_apply_failed(
        self, deployment, jhelper, tfhelper, osfeature, step_context
    ):
        tfhelper.apply.side_effect = TerraformException("apply failed...")

        step = openstack.EnableOpenStackApplicationStep(
            deployment, Mock(), tfhelper, jhelper, osfeature
        )
        result = step.run(step_context)

        tfhelper.update_tfvars.assert_called_once()
        tfhelper.apply.assert_called_once()
        jhelper.wait_until_desired_status.assert_not_called()
        assert result.result_type == ResultType.FAILED
        assert result.message == "apply failed..."

    def test_run_waiting_timed_out(
        self, deployment, jhelper, tfhelper, osfeature, step_context
    ):
        jhelper.wait_until_desired_status.side_effect = TimeoutError("timed out")

        step = openstack.EnableOpenStackApplicationStep(
            deployment, Mock(), tfhelper, jhelper, osfeature
        )
        result = step.run(step_context)

        tfhelper.update_tfvars.assert_called_once()
        tfhelper.apply.assert_called_once()
        jhelper.wait_until_desired_status.assert_called_once()
        assert result.result_type == ResultType.FAILED
        assert result.message == "timed out"

    def test_run_with_agent_desired_status(
        self,
        deployment,
        tfhelper,
        jhelper,
        osfeature,
        step_context,
    ):
        """Test that agent_desired_status is passed to wait_until_desired_status."""
        step = openstack.EnableOpenStackApplicationStep(
            deployment,
            Mock(),
            tfhelper,
            jhelper,
            osfeature,
            app_desired_status=["active"],
            agent_desired_status=["idle"],
        )
        result = step.run(step_context)

        tfhelper.update_tfvars.assert_called_once()
        tfhelper.apply.assert_called_once()
        jhelper.wait_until_desired_status.assert_called_once()
        call_kwargs = jhelper.wait_until_desired_status.call_args[1]
        assert call_kwargs["status"] == ["active"]
        assert call_kwargs["agent_status"] == ["idle"]
        assert result.result_type == ResultType.COMPLETED

    def test_run_without_agent_desired_status(
        self,
        deployment,
        tfhelper,
        jhelper,
        osfeature,
        step_context,
    ):
        """Test backward compatibility when agent_desired_status is not provided."""
        step = openstack.EnableOpenStackApplicationStep(
            deployment,
            Mock(),
            tfhelper,
            jhelper,
            osfeature,
            app_desired_status=["active"],
        )
        result = step.run(step_context)

        tfhelper.update_tfvars.assert_called_once()
        tfhelper.apply.assert_called_once()
        jhelper.wait_until_desired_status.assert_called_once()
        call_kwargs = jhelper.wait_until_desired_status.call_args[1]
        assert call_kwargs["status"] == ["active"]
        assert call_kwargs["agent_status"] is None
        assert result.result_type == ResultType.COMPLETED


class TestDisableOpenStackApplicationStep:
    def test_run(self, deployment, tfhelper, jhelper, osfeature, step_context):
        step = openstack.DisableOpenStackApplicationStep(
            deployment, tfhelper, jhelper, osfeature
        )
        result = step.run(step_context)

        tfhelper.update_tfvars.assert_called_once()
        tfhelper.apply.assert_called_once()
        assert result.result_type == ResultType.COMPLETED

    def test_run_tf_apply_failed(
        self, deployment, tfhelper, jhelper, osfeature, step_context
    ):
        tfhelper.apply.side_effect = TerraformException("apply failed...")

        step = openstack.DisableOpenStackApplicationStep(
            deployment, tfhelper, jhelper, osfeature
        )
        result = step.run(step_context)

        tfhelper.update_tfvars.assert_called_once()
        tfhelper.apply.assert_called_once()
        assert result.result_type == ResultType.FAILED
        assert result.message == "apply failed..."

    def test_run_waiting_timed_out(
        self, deployment, tfhelper, jhelper, osfeature, step_context
    ):
        jhelper.wait_application_gone.side_effect = TimeoutError("timed out")

        step = openstack.DisableOpenStackApplicationStep(
            deployment, tfhelper, jhelper, osfeature
        )
        result = step.run(step_context)

        tfhelper.update_tfvars.assert_called_once()
        tfhelper.apply.assert_called_once()
        jhelper.wait_application_gone.assert_called_once()
        assert result.result_type == ResultType.FAILED
        assert result.message == "timed out"

    def test_calls_set_application_timeout_on_disable_with_deployment(
        self,
        deployment,
        tfhelper,
        jhelper,
        osfeature,
        step_context,
    ):
        """Test that set_application_timeout_on_disable is called with deployment."""
        osfeature.set_application_timeout_on_disable.return_value = 1800

        step = openstack.DisableOpenStackApplicationStep(
            deployment, tfhelper, jhelper, osfeature
        )
        step.run(step_context)

        osfeature.set_application_timeout_on_disable.assert_called_once_with(deployment)


class TestUpgradeOpenStackApplicationStep:
    def test_run(
        self,
        deployment,
        tfhelper,
        jhelper,
        osfeature,
        step_context,
    ):
        jhelper.get_model_status.return_value = Mock(
            apps={
                "keystone": Mock(
                    charm="keystone-k8s",
                    charm_channel="2023.2/stable",
                )
            }
        )

        step = openstack.UpgradeOpenStackApplicationStep(
            deployment, tfhelper, jhelper, osfeature
        )
        result = step.run(step_context)

        tfhelper.update_partial_tfvars_and_apply_tf.assert_called_once()
        jhelper.wait_until_desired_status.assert_called_once()
        assert result.result_type == ResultType.COMPLETED

    def test_run_tf_apply_failed(
        self, deployment, tfhelper, jhelper, osfeature, step_context
    ):
        tfhelper.update_partial_tfvars_and_apply_tf.side_effect = TerraformException(
            "apply failed..."
        )

        jhelper.get_model_status.return_value = Mock(
            apps={
                "keystone": Mock(
                    charm="keystone-k8s",
                    charm_channel="2023.2/stable",
                )
            }
        )

        step = openstack.UpgradeOpenStackApplicationStep(
            deployment, tfhelper, jhelper, osfeature
        )
        result = step.run(step_context)

        tfhelper.update_partial_tfvars_and_apply_tf.assert_called_once()
        jhelper.wait_until_desired_status.assert_not_called()
        assert result.result_type == ResultType.FAILED
        assert result.message == "apply failed..."

    def test_run_waiting_timed_out(
        self, deployment, tfhelper, jhelper, osfeature, step_context
    ):
        jhelper.wait_until_desired_status.side_effect = TimeoutError("timed out")

        jhelper.get_model_status.return_value = Mock(
            apps={
                "keystone": Mock(
                    charm="keystone-k8s",
                    charm_channel="2023.2/stable",
                )
            }
        )
        step = openstack.UpgradeOpenStackApplicationStep(
            deployment, tfhelper, jhelper, osfeature
        )
        result = step.run(step_context)

        tfhelper.update_partial_tfvars_and_apply_tf.assert_called_once()
        jhelper.wait_until_desired_status.assert_called_once()
        assert result.result_type == ResultType.FAILED
        assert result.message == "timed out"

    def test_calls_set_application_timeout_on_enable_with_deployment(
        self,
        deployment,
        tfhelper,
        jhelper,
        osfeature,
        step_context,
    ):
        """Test that set_application_timeout_on_enable is called with deployment."""
        jhelper.get_model_status.return_value = Mock(
            apps={
                "keystone": Mock(
                    charm="keystone-k8s",
                    charm_channel="2023.2/stable",
                )
            }
        )
        osfeature.set_application_timeout_on_enable.return_value = 1800

        step = openstack.UpgradeOpenStackApplicationStep(
            deployment, tfhelper, jhelper, osfeature
        )
        step.run(step_context)

        osfeature.set_application_timeout_on_enable.assert_called_once_with(deployment)


class TestOpenStackControlPlaneFeatureTimeouts:
    """Test timeout methods for OpenStackControlPlaneFeature."""

    def test_set_application_timeout_on_enable_default(self, deployment):
        """Test default timeout on enable."""
        feature = Mock(spec=openstack.OpenStackControlPlaneFeature)
        feature.set_application_timeout_on_enable = (
            openstack.OpenStackControlPlaneFeature.set_application_timeout_on_enable
        )
        timeout = feature.set_application_timeout_on_enable(feature, deployment)
        assert timeout == openstack.APPLICATION_DEPLOY_TIMEOUT

    def test_set_application_timeout_on_disable_default(self, deployment):
        """Test default timeout on disable."""
        feature = Mock(spec=openstack.OpenStackControlPlaneFeature)
        feature.set_application_timeout_on_disable = (
            openstack.OpenStackControlPlaneFeature.set_application_timeout_on_disable
        )
        timeout = feature.set_application_timeout_on_disable(feature, deployment)
        assert timeout == openstack.APPLICATION_DEPLOY_TIMEOUT


class TestInfraValidationGate:
    """Tests for the INFRA_APPS validation gate in the feature base class."""

    @pytest.fixture()
    def deployment(self):
        deploy = Mock()
        deploy.openstack_machines_model = "openstack-machines"
        deploy.get_tfhelper.side_effect = lambda plan: Mock()
        return deploy

    class _Feature(openstack.OpenStackControlPlaneFeature):
        """Concrete feature using the openstack plan."""

        name = "fake"
        tf_plan_location = openstack.TerraformPlanLocation.SUNBEAM_TERRAFORM_REPO
        tfplan = "openstack-plan"

        def manifest_attributes_tfvar_map(self) -> dict:
            return {}

        def set_application_names(self, deployment) -> list:
            return []

        def set_tfvars_on_enable(self, deployment, config) -> dict:
            return {}

        def set_tfvars_on_disable(self, deployment) -> dict:
            return {}

        def set_tfvars_on_resize(self, deployment) -> dict:
            return {}

    def _feature(self):
        feature = self._Feature()
        feature.user_manifest = Mock()
        feature._manifest = Mock()
        return feature

    def test_own_infra_charms_vault(self):
        """The vault feature owns vault-k8s: it must not be gated on it."""
        from sunbeam.features.vault.feature import VaultFeature

        assert VaultFeature()._own_infra_charms == {"vault-k8s"}

    def test_own_infra_charms_non_infra_feature(self):
        """Features without INFRA_APP charms own nothing to skip."""
        from sunbeam.features.telemetry.feature import TelemetryFeature

        assert TelemetryFeature()._own_infra_charms == set()

    @patch("sunbeam.features.interface.v1.openstack.EnableOpenStackApplicationStep")
    @patch("sunbeam.features.interface.v1.openstack.run_plan")
    def test_enable_gates_before_manifest_storage(
        self, run_plan, _enable_step, deployment
    ):
        """The INFRA_APPS gate runs before the user manifest is stored.

        A drifted manifest must be refused before AddManifestStep persists
        it in the cluster database.
        """
        from sunbeam.steps.openstack import ValidateInfraAppsStep

        feature = self._feature()
        feature.run_enable_plans(deployment, Mock(), False)

        # First run_plan call is the gate; the second is the enable plan,
        # which stores the user manifest only after the gate passed.
        assert run_plan.call_count == 2
        gate_plan = run_plan.call_args_list[0][0][0]
        assert gate_plan[0].__class__.__name__ == "TerraformInitStep"
        assert isinstance(gate_plan[1], ValidateInfraAppsStep)
        # The gate received the feature's own charms to skip.
        assert gate_plan[1].skip_charms == feature._own_infra_charms
        enable_plan = run_plan.call_args_list[1][0][0]
        assert enable_plan[0].__class__.__name__ == "AddManifestStep"
        # Followed by the (patched) enable step.
        _enable_step.assert_called_once()

    @patch("sunbeam.features.interface.v1.openstack.AddManifestStep")
    @patch("sunbeam.features.interface.v1.openstack.EnableOpenStackApplicationStep")
    def test_enable_gate_failure_skips_manifest_storage(
        self, _enable_step, add_manifest_step, deployment
    ):
        """A failed gate must not store the manifest or run the enable plan."""
        import click

        from sunbeam.core.common import Result

        feature = self._feature()
        with patch(
            "sunbeam.steps.openstack.ValidateInfraAppsStep.run",
            return_value=Result(ResultType.FAILED, "pending infra changes"),
        ):
            # TerraformInitStep.run calls tfhelper.init() (a Mock here) and
            # the failed gate makes run_plan raise.
            with pytest.raises(click.ClickException, match="pending infra"):
                feature.run_enable_plans(deployment, Mock(), False)

        # The drifted manifest was never stored and the enable plan was
        # never built.
        add_manifest_step.assert_not_called()
        _enable_step.assert_not_called()


class TestInfraAppDriftBackstop:
    """The enable/disable steps gate INFRA_APPS drift themselves.

    Backstop so features that override run_enable_plans/run_disable_plans
    (observability, loadbalancer, dns, caas, baremetal, ...) cannot apply
    INFRA_APP channel or revision changes by constructing these steps.
    """

    @pytest.fixture()
    def osfeature(self):
        feature = Mock()
        feature.tfplan = "openstack-plan"
        feature._own_infra_charms = set()
        return feature

    @pytest.fixture()
    def deployment(self):
        deploy = Mock()
        deploy.openstack_machines_model = "openstack-machines"
        return deploy

    @pytest.fixture()
    def tfhelper(self):
        return Mock(plan="openstack-plan")

    def test_enable_step_blocks_infra_drift(
        self, deployment, tfhelper, jhelper, osfeature, step_context
    ):
        tfhelper.plan_resource_changes.return_value = {
            "juju_application.traefik": {
                "actions": ["update"],
                "type": "juju_application",
                "before": {
                    "name": "traefik",
                    "charm": [{"name": "traefik-k8s", "channel": "latest/stable"}],
                },
                "after": {
                    "name": "traefik",
                    "charm": [{"name": "traefik-k8s", "channel": "latest/candidate"}],
                },
            }
        }

        step = openstack.EnableOpenStackApplicationStep(
            deployment, Mock(), tfhelper, jhelper, osfeature
        )
        result = step.run(step_context)

        tfhelper.apply.assert_not_called()
        assert result.result_type == ResultType.FAILED
        assert "sunbeam cluster refresh ingress" in result.message

    def test_enable_step_skips_own_charms(
        self, deployment, tfhelper, jhelper, osfeature, step_context
    ):
        osfeature._own_infra_charms = {"vault-k8s"}
        tfhelper.plan_resource_changes.return_value = {
            "juju_application.vault[0]": {
                "actions": ["update"],
                "type": "juju_application",
                "before": {
                    "name": "vault",
                    "charm": [{"name": "vault-k8s", "channel": "1.18/stable"}],
                },
                "after": {
                    "name": "vault",
                    "charm": [{"name": "vault-k8s", "channel": "1.18/candidate"}],
                },
            }
        }

        osfeature.set_application_names.return_value = []
        step = openstack.EnableOpenStackApplicationStep(
            deployment, Mock(), tfhelper, jhelper, osfeature
        )
        result = step.run(step_context)

        tfhelper.apply.assert_called_once()
        assert result.result_type == ResultType.COMPLETED

    def test_enable_step_ignores_feature_repo_plans(
        self, deployment, tfhelper, jhelper, osfeature, step_context
    ):
        """Feature-repo plans (cos, cni, ...) manage no INFRA_APPS."""
        tfhelper.plan = "cos-plan"
        tfhelper.plan_resource_changes.return_value = {}

        osfeature.set_application_names.return_value = []
        step = openstack.EnableOpenStackApplicationStep(
            deployment, Mock(), tfhelper, jhelper, osfeature
        )
        result = step.run(step_context)

        tfhelper.plan_resource_changes.assert_not_called()
        tfhelper.apply.assert_called_once()
        assert result.result_type == ResultType.COMPLETED

    def test_disable_step_blocks_infra_drift(
        self, deployment, tfhelper, jhelper, osfeature, step_context
    ):
        osfeature.tf_plan_location = (
            openstack.TerraformPlanLocation.SUNBEAM_TERRAFORM_REPO
        )
        tfhelper.plan_resource_changes.return_value = {
            "juju_application.mysql": {
                "actions": ["update"],
                "type": "juju_application",
                "before": {
                    "name": "mysql",
                    "charm": [{"name": "mysql-k8s", "channel": "8.0/stable"}],
                },
                "after": {
                    "name": "mysql",
                    "charm": [{"name": "mysql-k8s", "channel": "8.0/candidate"}],
                },
            }
        }

        step = openstack.DisableOpenStackApplicationStep(
            deployment, tfhelper, jhelper, osfeature
        )
        result = step.run(step_context)

        tfhelper.apply.assert_not_called()
        assert result.result_type == ResultType.FAILED
        assert "sunbeam cluster refresh mysql" in result.message
