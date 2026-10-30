# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

from unittest.mock import Mock, patch

from sunbeam.clusterd.service import ConfigItemNotFoundException
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
        self.tfhelper.update_tfvars.assert_not_called()
        self.tfhelper.apply.assert_not_called()

    def test_run_applies_with_targets_and_waits_traefik(self, step_context):
        self.tfhelper.state_list = Mock(
            return_value=["juju_application.traefik", "juju_application.keystone"]
        )
        self.tfhelper.plan_json = Mock(return_value={"resource_changes": []})
        self.step.get_apps_filter_by_charms = Mock(
            return_value=["traefik", "traefik-public"]
        )

        result = self.step.run(step_context)

        assert result.result_type == ResultType.COMPLETED
        targets = ["-target=juju_application.traefik"]
        self.tfhelper.update_tfvars.assert_called_once()
        assert self.tfhelper.update_tfvars.call_args.kwargs["tfvar_config"] == (
            OPENSTACK_TERRAFORM_VARS
        )
        self.tfhelper.plan_json.assert_called_once_with(targets)
        self.tfhelper.apply.assert_called_once_with(
            targets, reporter=step_context.reporter
        )
        waited_apps = set(self.jhelper.wait_until_active.call_args.kwargs["apps"])
        assert waited_apps == {"traefik", "traefik-public"}

    def test_run_terraform_failure(self, step_context):
        self.tfhelper.state_list = Mock(return_value=["juju_application.traefik"])
        self.tfhelper.plan_json = Mock(return_value={"resource_changes": []})
        self.tfhelper.apply = Mock(side_effect=TerraformException("apply failed..."))

        result = self.step.run(step_context)

        assert result.result_type == ResultType.FAILED
        assert result.message == "apply failed..."

    def test_run_wait_timeout(self, step_context):
        self.tfhelper.state_list = Mock(return_value=["juju_application.traefik"])
        self.tfhelper.plan_json = Mock(return_value={"resource_changes": []})
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
        self.tfhelper.update_tfvars.assert_not_called()
        self.tfhelper.apply.assert_not_called()

    def test_run_fails_when_plan_touches_other_apps(self, step_context):
        """Targeting an integration also selects its dependencies.

        A targeted plan that would change a non-traefik application
        (e.g. an observability application behind a traefik
        integration) must fail instead of upgrading it here.
        """
        self.tfhelper.state_list = Mock(
            return_value=[
                "juju_application.traefik",
                "juju_integration.traefik-internal-to-metrics-endpoint",
            ]
        )
        self.tfhelper.plan_json = Mock(
            return_value={
                "resource_changes": [
                    {
                        "address": "juju_application.traefik",
                        "change": {"actions": ["update"]},
                    },
                    {
                        "address": "juju_application.observability-agent-infra",
                        "change": {"actions": ["update"]},
                    },
                ]
            }
        )

        result = self.step.run(step_context)

        assert result.result_type == ResultType.FAILED
        assert "observability-agent-infra" in result.message
        self.tfhelper.apply.assert_not_called()


class TestIngressChannelRevisionOverrides:
    """The terraform reapply pins traefik channel/revision to the decision.

    Without the pin the plan default (latest/stable) would flip a channel
    the charm refresh deliberately kept, e.g. after an out-of-band change
    or a revision-only manifest pin.
    """

    def setup_method(self):
        self.deployment = Mock()
        self.client = Mock()
        self.tfhelper = Mock()
        self.jhelper = Mock()
        self.manifest = Mock()
        self.refresh_step = Mock()

    def _step(self):
        return ReapplyIngressTerraformPlanStep(
            self.deployment,
            self.client,
            self.tfhelper,
            self.jhelper,
            self.manifest,
            refresh_step=self.refresh_step,
        )

    def _run(self, step, step_context):
        step.tfhelper = self.tfhelper
        self.tfhelper.state_list = Mock(return_value=["juju_application.traefik"])
        self.tfhelper.plan_json = Mock(return_value={"resource_changes": []})
        step.get_apps_filter_by_charms = Mock(return_value=["traefik"])
        return step.run(step_context)

    def test_channel_and_revision_pinned_from_decision(self, step_context):
        self.refresh_step.decisions = {
            "traefik": CharmRefreshDecision(
                result=Result(ResultType.COMPLETED),
                effective_channel="latest/candidate",
                effective_revision=479,
            )
        }

        result = self._run(self._step(), step_context)

        assert result.result_type == ResultType.COMPLETED
        kwargs = self.tfhelper.update_tfvars.call_args.kwargs
        assert kwargs["override_tfvars"] == {
            "traefik-channel": "latest/candidate",
            "traefik-revision": 479,
        }

    def test_revision_omitted_when_not_pinned(self, step_context):
        """Revision unset in the decision must not become a None tfvar."""
        self.refresh_step.decisions = {
            "traefik": CharmRefreshDecision(
                result=Result(ResultType.COMPLETED),
                effective_channel="latest/stable",
            )
        }
        step = self._step()
        with patch(
            f"{_INGRESS}.read_config",
            Mock(side_effect=ConfigItemNotFoundException("no config")),
        ):
            result = self._run(step, step_context)

        assert result.result_type == ResultType.COMPLETED
        kwargs = self.tfhelper.update_tfvars.call_args.kwargs
        assert kwargs["override_tfvars"] == {"traefik-channel": "latest/stable"}

    def test_unpinned_refresh_clears_stale_computed_revision(self, step_context):
        """Two successive refreshes: pin, then remove the pin.

        The first refresh persists traefik-revision as a computed key;
        when the manifest later drops the pin, the stale computed value
        must be cleared instead of silently re-applied.
        """
        self.refresh_step.decisions = {
            "traefik": CharmRefreshDecision(
                result=Result(ResultType.COMPLETED),
                effective_channel="latest/stable",
            )
        }
        step = self._step()
        stored = {
            "traefik-channel": "latest/stable",
            "traefik-revision": 479,
            "_computed_keys": ["traefik-channel", "traefik-revision"],
        }
        with (
            patch(f"{_INGRESS}.read_config", Mock(return_value=stored)),
            patch(f"{_INGRESS}.update_config") as mock_update_config,
        ):
            result = self._run(step, step_context)

        assert result.result_type == ResultType.COMPLETED
        mock_update_config.assert_called_once()
        saved = mock_update_config.call_args.args[2]
        assert "traefik-revision" not in saved
        assert "traefik-revision" not in saved["_computed_keys"]

    def test_keeps_out_of_band_channel(self, step_context):
        """Up-to-date apps on a non-default channel: the apply keeps it."""
        self.refresh_step.decisions = {
            "traefik": CharmRefreshDecision(
                result=Result(ResultType.SKIPPED, "already at latest revision"),
                effective_channel="latest/candidate",
            )
        }
        step = self._step()
        with patch(
            f"{_INGRESS}.read_config",
            Mock(side_effect=ConfigItemNotFoundException("no config")),
        ):
            result = self._run(step, step_context)

        assert result.result_type == ResultType.COMPLETED
        kwargs = self.tfhelper.update_tfvars.call_args.kwargs
        # The plan default (latest/stable) must not win:
        assert kwargs["override_tfvars"]["traefik-channel"] == "latest/candidate"

    def test_differing_channels_fail_instead_of_switching(self, step_context):
        """Each application gets its own decision.

        With a revision-only manifest and traefik on latest/stable while
        traefik-public is on latest/candidate, the decisions correctly
        retain both channels; applying one shared traefik-channel to all
        applications would silently switch the other. Fail instead.
        """
        self.refresh_step.decisions = {
            "traefik": CharmRefreshDecision(
                result=Result(ResultType.SKIPPED, "already at latest revision"),
                effective_channel="latest/stable",
            ),
            "traefik-public": CharmRefreshDecision(
                result=Result(ResultType.SKIPPED, "already at latest revision"),
                effective_channel="latest/candidate",
            ),
        }

        result = self._run(self._step(), step_context)

        assert result.result_type == ResultType.FAILED
        assert "different channels" in result.message
        self.tfhelper.apply.assert_not_called()

    def test_undeployed_app_does_not_weigh_in_on_channel_pin(self, step_context):
        """An app that vanished between is_skip and run must not fail the pin.

        Its decision carries the default channel (latest/stable) while
        the deployed app keeps its out-of-band channel; the undeployed
        app is not in terraform state and never touched by the apply.
        """
        self.refresh_step.decisions = {
            "traefik": CharmRefreshDecision(
                result=Result(ResultType.SKIPPED, "already at latest revision"),
                effective_channel="latest/candidate",
            ),
            "traefik-public": CharmRefreshDecision(
                result=Result(
                    ResultType.SKIPPED,
                    "'traefik-public' application has not been deployed yet",
                ),
                effective_channel="latest/stable",
                app_not_deployed=True,
            ),
        }
        step = self._step()
        with patch(
            f"{_INGRESS}.read_config",
            Mock(side_effect=ConfigItemNotFoundException("no config")),
        ):
            result = self._run(step, step_context)

        assert result.result_type == ResultType.COMPLETED
        kwargs = self.tfhelper.update_tfvars.call_args.kwargs
        assert kwargs["override_tfvars"]["traefik-channel"] == "latest/candidate"

    def test_abbreviated_manifest_channel_matches_deployed_channel(self, step_context):
        """Abbreviated manifest channels must not count as a disagreement.

        The manifest side is raw input (e.g. "stable") while Juju-derived
        channels are always normalised ("latest/stable").
        """
        self.refresh_step.decisions = {
            "traefik": CharmRefreshDecision(
                result=Result(ResultType.COMPLETED),
                effective_channel="stable",
            ),
            "traefik-public": CharmRefreshDecision(
                result=Result(ResultType.SKIPPED, "already at latest revision"),
                effective_channel="latest/stable",
            ),
        }
        step = self._step()
        with patch(
            f"{_INGRESS}.read_config",
            Mock(side_effect=ConfigItemNotFoundException("no config")),
        ):
            result = self._run(step, step_context)

        assert result.result_type == ResultType.COMPLETED
        kwargs = self.tfhelper.update_tfvars.call_args.kwargs
        assert kwargs["override_tfvars"]["traefik-channel"] == "latest/stable"

    def test_no_decisions_falls_back_to_manifest(self, step_context):
        """Without a refresh step the apply relies on the manifest alone."""
        step = ReapplyIngressTerraformPlanStep(
            self.deployment,
            self.client,
            self.tfhelper,
            self.jhelper,
            self.manifest,
        )
        result = self._run(step, step_context)

        assert result.result_type == ResultType.COMPLETED
        kwargs = self.tfhelper.update_tfvars.call_args.kwargs
        assert kwargs["override_tfvars"] is None
