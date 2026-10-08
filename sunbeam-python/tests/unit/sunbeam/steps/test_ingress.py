# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

from unittest.mock import Mock, patch

from sunbeam.core.common import Result, ResultType
from sunbeam.core.openstack import OPENSTACK_MODEL
from sunbeam.core.terraform import TerraformException
from sunbeam.features.interface.v1.openstack import OPENSTACK_TERRAFORM_VARS
from sunbeam.steps.charm_upgrade import CharmRefreshDecision
from sunbeam.steps.ingress import (
    IngressCharmRefreshStep,
    ReapplyIngressTerraformPlanStep,
)

_INGRESS = "sunbeam.steps.ingress"


class TestIngressCharmRefreshStep:
    """Tests for IngressCharmRefreshStep (`sunbeam cluster refresh ingress`)."""

    def setup_method(self):
        self.deployment = Mock()
        self.jhelper = Mock()
        self.manifest = Mock()
        self.manifest.find_charm = Mock(return_value=None)
        self.step = IngressCharmRefreshStep(
            self.deployment, self.jhelper, self.manifest
        )

    def test_is_skip_no_traefik_deployed(self, step_context):
        self.step.get_charm_deployed_versions = Mock(
            return_value={"keystone": ("keystone-k8s", "2024.1/stable", 123)}
        )
        result = self.step.is_skip(step_context)
        assert result.result_type == ResultType.SKIPPED

    def test_is_skip_traefik_deployed(self, step_context):
        self.step.get_charm_deployed_versions = Mock(
            return_value={"traefik": ("traefik-k8s", "latest/stable", 100)}
        )
        result = self.step.is_skip(step_context)
        assert result.result_type == ResultType.COMPLETED

    @patch(f"{_INGRESS}.check_charm_needs_refresh")
    def test_run_refreshes_traefik_apps(self, mock_check, step_context):
        """Each traefik app is refreshed with its decision and awaited."""
        decision = CharmRefreshDecision(
            result=Result(ResultType.COMPLETED),
            effective_channel="latest/stable",
            effective_revision=None,
        )
        mock_check.return_value = decision
        self.step.traefik_apps = ["traefik", "traefik-public"]

        result = self.step.run(step_context)

        assert result.result_type == ResultType.COMPLETED
        assert self.jhelper.charm_refresh.call_count == 2
        self.jhelper.charm_refresh.assert_any_call(
            "traefik", OPENSTACK_MODEL, channel="latest/stable", revision=None
        )
        self.jhelper.charm_refresh.assert_any_call(
            "traefik-public", OPENSTACK_MODEL, channel="latest/stable", revision=None
        )
        waited_apps = set(self.jhelper.wait_until_active.call_args.kwargs["apps"])
        assert waited_apps == {"traefik", "traefik-public"}
        # Decision taken per application
        assert mock_check.call_count == 2

    @patch(f"{_INGRESS}.check_charm_needs_refresh")
    def test_run_skipped_when_all_up_to_date(self, mock_check, step_context):
        """No refresh and no wait when every app is already up to date."""
        mock_check.return_value = CharmRefreshDecision(
            result=Result(ResultType.SKIPPED, "already at latest revision"),
            effective_channel="latest/stable",
        )
        self.step.traefik_apps = ["traefik"]

        result = self.step.run(step_context)

        assert result.result_type == ResultType.COMPLETED
        self.jhelper.charm_refresh.assert_not_called()
        self.jhelper.wait_until_active.assert_not_called()

    @patch(f"{_INGRESS}.check_charm_needs_refresh")
    def test_run_propagates_failed_decision(self, mock_check, step_context):
        """A FAILED decision (e.g. unsupported track change) stops the step."""
        mock_check.return_value = CharmRefreshDecision(
            result=Result(ResultType.FAILED, "track change not supported"),
            effective_channel="latest/stable",
        )
        self.step.traefik_apps = ["traefik"]

        result = self.step.run(step_context)

        assert result.result_type == ResultType.FAILED
        assert result.message == "track change not supported"
        self.jhelper.charm_refresh.assert_not_called()


class TestReapplyIngressTerraformPlanStep:
    """Tests for ReapplyIngressTerraformPlanStep (`sunbeam cluster refresh ingress`)."""

    def setup_method(self):
        self.deployment = Mock()
        self.client = Mock()
        self.tfhelper = Mock()
        self.jhelper = Mock()
        self.manifest = Mock()
        self.step = ReapplyIngressTerraformPlanStep(
            self.deployment,
            self.client,
            self.tfhelper,
            self.jhelper,
            self.manifest,
        )

    def test_targets_discovered_from_state(self):
        """Only traefik resources in terraform state are targeted."""
        self.tfhelper.state_list = Mock(
            return_value=[
                "juju_application.traefik",
                "juju_application.traefik-public",
                "juju_integration.traefik-public-to-tls-provider",
                "juju_application.keystone",
                "juju_model.sunbeam",
            ]
        )
        assert self.step._get_ingress_terraform_targets() == [
            "-target=juju_application.traefik",
            "-target=juju_application.traefik-public",
            "-target=juju_integration.traefik-public-to-tls-provider",
        ]

    def test_run_no_targets_skips(self, step_context):
        """No traefik resources in state means a full apply must NOT run."""
        self.tfhelper.state_list = Mock(return_value=["juju_application.keystone"])

        result = self.step.run(step_context)

        assert result.result_type == ResultType.SKIPPED
        self.tfhelper.update_tfvars_and_apply_tf.assert_not_called()

    def test_run_applies_with_targets_and_waits_traefik(self, step_context):
        self.tfhelper.state_list = Mock(
            return_value=["juju_application.traefik", "juju_application.keystone"]
        )
        self.step.get_apps_filter_by_charms = Mock(
            return_value=["traefik", "traefik-public"]
        )

        result = self.step.run(step_context)

        assert result.result_type == ResultType.COMPLETED
        self.tfhelper.update_tfvars_and_apply_tf.assert_called_once()
        apply_kwargs = self.tfhelper.update_tfvars_and_apply_tf.call_args.kwargs
        assert apply_kwargs["tf_apply_extra_args"] == [
            "-target=juju_application.traefik"
        ]
        assert apply_kwargs["tfvar_config"] == OPENSTACK_TERRAFORM_VARS
        waited_apps = set(self.jhelper.wait_until_active.call_args.kwargs["apps"])
        assert waited_apps == {"traefik", "traefik-public"}

    def test_run_terraform_failure(self, step_context):
        self.tfhelper.state_list = Mock(return_value=["juju_application.traefik"])
        self.tfhelper.update_tfvars_and_apply_tf.side_effect = TerraformException(
            "apply failed..."
        )

        result = self.step.run(step_context)

        assert result.result_type == ResultType.FAILED
        assert result.message == "apply failed..."

    def test_run_wait_timeout(self, step_context):
        self.tfhelper.state_list = Mock(return_value=["juju_application.traefik"])
        self.step.get_apps_filter_by_charms = Mock(return_value=["traefik"])
        self.jhelper.wait_until_active.side_effect = TimeoutError("timed out")

        result = self.step.run(step_context)

        assert result.result_type == ResultType.FAILED
        assert result.message == "timed out"

    def test_run_state_list_failure_fails(self, step_context):
        """A terraform state listing failure must not look like a skip.

        Healthy deployments always have traefik resources in state, so an
        empty listing only happens when state_list failed or traefik is
        genuinely absent; the former must surface as an error.
        """
        self.tfhelper.state_list = Mock(
            side_effect=TerraformException("state list failed...")
        )

        result = self.step.run(step_context)

        assert result.result_type == ResultType.FAILED
        assert result.message == "state list failed..."
        self.tfhelper.update_tfvars_and_apply_tf.assert_not_called()
