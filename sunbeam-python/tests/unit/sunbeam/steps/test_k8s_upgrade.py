# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

from unittest.mock import Mock

import pytest

from sunbeam.core.common import Result, ResultType
from sunbeam.core.juju import (
    ApplicationNotFoundException,
    JujuException,
    JujuWaitException,
    LeaderNotFoundException,
)
from sunbeam.steps.charm_upgrade import CharmRefreshDecision
from sunbeam.steps.k8s_upgrade import CHARM_NAME, K8S_CHANNEL, K8SCharmUpgradeStep


@pytest.fixture
def step(basic_deployment, basic_client, basic_manifest, basic_jhelper):
    basic_deployment.openstack_machines_model = "openstack-machines"
    return K8SCharmUpgradeStep(
        basic_deployment, basic_client, basic_manifest, basic_jhelper
    )


class TestK8SCharmUpgradeStepIsSkip:
    def test_skip_when_application_not_deployed(
        self, step, basic_jhelper, step_context
    ):
        basic_jhelper.get_application.side_effect = ApplicationNotFoundException()

        result = step.is_skip(step_context)

        assert result.result_type == ResultType.SKIPPED
        assert "not been deployed" in result.message

    def test_skip_when_already_at_pinned_revision(
        self, step, basic_jhelper, basic_manifest, step_context
    ):
        app = Mock(charm_rev=100, charm_channel="1.32/stable", base=None)
        basic_jhelper.get_application.return_value = app
        basic_manifest.find_charm.return_value = Mock(
            channel="1.32/stable", revision=100
        )

        result = step.is_skip(step_context)

        assert result.result_type == ResultType.SKIPPED
        assert "pinned revision" in result.message

    def test_not_skipped_when_revision_differs_from_pinned(
        self, step, basic_jhelper, basic_manifest, step_context
    ):
        app = Mock(charm_rev=99, charm_channel="1.32/stable", base=None)
        basic_jhelper.get_application.return_value = app
        basic_manifest.find_charm.return_value = Mock(
            channel="1.32/stable", revision=100
        )

        result = step.is_skip(step_context)

        assert result.result_type == ResultType.COMPLETED

    def test_skip_when_already_at_revision_only(
        self, step, basic_jhelper, basic_manifest, step_context
    ):
        """Revision-only manifest: skip when already at that revision."""
        app = Mock(charm_rev=500, charm_channel="1.32/stable", base=None)
        basic_jhelper.get_application.return_value = app
        basic_manifest.find_charm.return_value = Mock(channel=None, revision=500)

        result = step.is_skip(step_context)

        assert result.result_type == ResultType.SKIPPED
        assert "500" in result.message
        assert step._decision.needs_channel_flag is False

    def test_not_skipped_when_revision_only_differs(
        self, step, basic_jhelper, basic_manifest, step_context
    ):
        """Revision-only manifest: proceed when revision differs, no --channel."""
        app = Mock(charm_rev=499, charm_channel="1.32/stable", base=None)
        basic_jhelper.get_application.return_value = app
        basic_jhelper.get_charm_channel_for_revision.return_value = "1.32/stable"
        basic_manifest.find_charm.return_value = Mock(channel=None, revision=500)

        result = step.is_skip(step_context)

        assert result.result_type == ResultType.COMPLETED
        assert step._decision.needs_channel_flag is False

    def test_revision_only_proceeds_when_channel_not_found(
        self, step, basic_jhelper, basic_manifest, step_context
    ):
        """Revision-only: if revision is not found in any channel, proceed."""
        app = Mock(charm_rev=499, charm_channel="1.32/stable", base=None)
        basic_jhelper.get_application.return_value = app
        basic_jhelper.get_charm_channel_for_revision.return_value = None
        basic_manifest.find_charm.return_value = Mock(channel=None, revision=500)

        result = step.is_skip(step_context)

        assert result.result_type == ResultType.COMPLETED
        assert step._decision.needs_channel_flag is False

    def test_revision_only_fails_for_track_change(
        self, step, basic_jhelper, basic_manifest, step_context
    ):
        """Revision-only: FAILED when revision belongs to a different track."""
        app = Mock(charm_rev=499, charm_channel="1.32/stable", base=None)
        basic_jhelper.get_application.return_value = app
        basic_jhelper.get_charm_channel_for_revision.return_value = "1.35/stable"
        basic_manifest.find_charm.return_value = Mock(channel=None, revision=500)

        result = step.is_skip(step_context)

        assert result.result_type == ResultType.FAILED
        assert "1.32" in result.message
        assert "1.35" in result.message
        assert "500" in result.message

    def test_revision_only_proceeds_when_channel_lookup_fails(
        self, step, basic_jhelper, basic_manifest, step_context
    ):
        """Revision-only: if CharmHub lookup fails, proceed rather than block."""
        app = Mock(charm_rev=499, charm_channel="1.32/stable", base=None)
        basic_jhelper.get_application.return_value = app
        basic_jhelper.get_charm_channel_for_revision.side_effect = JujuException(
            "network error"
        )
        basic_manifest.find_charm.return_value = Mock(channel=None, revision=500)

        result = step.is_skip(step_context)

        assert result.result_type == ResultType.COMPLETED

    def test_no_manifest_uses_deployed_channel(
        self, step, basic_jhelper, basic_manifest, step_context
    ):
        """With no manifest, refresh in the same channel/risk as deployed."""
        # Use a non-K8S_CHANNEL value so the test catches any accidental fallback.
        app = Mock(charm_rev=199, charm_channel="1.32/edge", base=None)
        basic_jhelper.get_application.return_value = app
        basic_jhelper.get_available_charm_revisions.return_value = {"amd64": 200}
        basic_manifest.find_charm.return_value = None

        result = step.is_skip(step_context)

        assert result.result_type == ResultType.COMPLETED
        assert step._decision.needs_channel_flag is False
        # Charmhub lookup must use the *deployed* channel, not K8S_CHANNEL
        basic_jhelper.get_available_charm_revisions.assert_called_once_with(
            CHARM_NAME, "1.32/edge"
        )

    def test_no_manifest_skips_when_already_at_latest(
        self, step, basic_jhelper, basic_manifest, step_context
    ):
        """No manifest: SKIPPED when already at latest revision in deployed channel."""
        app = Mock(charm_rev=200, charm_channel="1.32/edge", base=None)
        basic_jhelper.get_application.return_value = app
        basic_jhelper.get_available_charm_revisions.return_value = {"amd64": 200}
        basic_manifest.find_charm.return_value = None

        result = step.is_skip(step_context)

        assert result.result_type == ResultType.SKIPPED
        assert "1.32/edge" in result.message

    def test_manifest_without_k8s_entry_uses_deployed_channel(
        self, step, basic_jhelper, basic_manifest, step_context
    ):
        """Manifest present but k8s not listed: behaves same as no manifest.

        find_charm returns None in both cases, so the deployed channel is used
        and no channel switch is attempted.
        """
        app = Mock(charm_rev=199, charm_channel="1.32/stable", base=None)
        basic_jhelper.get_application.return_value = app
        basic_jhelper.get_available_charm_revisions.return_value = {"amd64": 200}
        # Simulate a manifest that exists but has no k8s entry
        basic_manifest.find_charm.return_value = None

        result = step.is_skip(step_context)

        assert result.result_type == ResultType.COMPLETED
        assert step._decision.needs_channel_flag is False
        basic_jhelper.get_available_charm_revisions.assert_called_once_with(
            CHARM_NAME, "1.32/stable"
        )

    def test_fails_for_track_change(
        self, step, basic_jhelper, basic_manifest, step_context
    ):
        """When the channel track changes (e.g. 1.31 -> 1.32), return FAILED."""
        app = Mock(charm_rev=100, charm_channel="1.31/stable", base=None)
        basic_jhelper.get_application.return_value = app
        basic_manifest.find_charm.return_value = Mock(
            channel="1.32/stable", revision=None
        )

        result = step.is_skip(step_context)

        assert result.result_type == ResultType.FAILED
        assert "track change" in result.message
        assert "1.31" in result.message
        assert "1.32" in result.message

    def test_not_skipped_for_risk_change(
        self, step, basic_jhelper, basic_manifest, step_context
    ):
        """When only the risk level changes (same track), proceed with --channel."""
        app = Mock(charm_rev=100, charm_channel="1.32/stable", base=None)
        basic_jhelper.get_application.return_value = app
        basic_manifest.find_charm.return_value = Mock(
            channel="1.32/edge", revision=None
        )

        result = step.is_skip(step_context)

        assert result.result_type == ResultType.COMPLETED
        assert step._decision.needs_channel_flag is True

    def test_skip_when_already_at_latest_patch_revision(
        self, step, basic_jhelper, basic_manifest, step_context
    ):
        app = Mock(charm_rev=200, charm_channel=K8S_CHANNEL, base=None)
        basic_jhelper.get_application.return_value = app
        basic_jhelper.get_available_charm_revisions.return_value = {"amd64": 200}
        basic_manifest.find_charm.return_value = None

        result = step.is_skip(step_context)

        assert result.result_type == ResultType.SKIPPED
        assert "already at the latest revision" in result.message

    def test_not_skipped_when_newer_patch_revision_available(
        self, step, basic_jhelper, basic_manifest, step_context
    ):
        app = Mock(charm_rev=199, charm_channel=K8S_CHANNEL, base=None)
        basic_jhelper.get_application.return_value = app
        basic_jhelper.get_available_charm_revisions.return_value = {"amd64": 200}
        basic_manifest.find_charm.return_value = None

        result = step.is_skip(step_context)

        assert result.result_type == ResultType.COMPLETED
        assert step._decision.needs_channel_flag is False

    def test_proceeds_when_available_revision_lookup_fails(
        self, step, basic_jhelper, basic_manifest, step_context
    ):
        """If charmhub can't be reached, proceed rather than skip."""
        app = Mock(charm_rev=200, charm_channel=K8S_CHANNEL, base=None)
        basic_jhelper.get_application.return_value = app
        basic_jhelper.get_available_charm_revisions.side_effect = JujuException(
            "network error"
        )
        basic_manifest.find_charm.return_value = None

        result = step.is_skip(step_context)

        assert result.result_type == ResultType.COMPLETED

    def test_uses_base_when_available_for_revision_lookup(
        self, step, basic_jhelper, basic_manifest, step_context
    ):
        base = Mock()
        base.name = "ubuntu"
        base.channel = "24.04"
        app = Mock(charm_rev=199, charm_channel=K8S_CHANNEL, base=base)
        basic_jhelper.get_application.return_value = app
        basic_jhelper.get_available_charm_revisions.return_value = {"amd64": 200}
        basic_manifest.find_charm.return_value = None

        step.is_skip(step_context)

        basic_jhelper.get_available_charm_revisions.assert_called_once_with(
            CHARM_NAME, K8S_CHANNEL, "ubuntu@24.04"
        )


class TestK8SCharmUpgradeStepRun:
    @pytest.fixture(autouse=True)
    def default_decision(self, step):
        """Set a sensible default _decision so run() tests don't need is_skip()."""
        step._decision = CharmRefreshDecision(
            result=Result(ResultType.COMPLETED),
            effective_channel=K8S_CHANNEL,
        )

    def test_run_patch_upgrade_success(
        self, step, basic_jhelper, basic_manifest, step_context
    ):
        """Patch upgrade: no channel passed, wait_until_active called."""
        basic_jhelper.get_leader_unit.return_value = "k8s/0"
        basic_manifest.find_charm.return_value = None

        result = step.run(step_context)

        assert result.result_type == ResultType.COMPLETED
        basic_jhelper.run_action.assert_called_once_with(
            "k8s/0", "openstack-machines", "pre-upgrade-check"
        )
        basic_jhelper.charm_refresh.assert_called_once_with(
            "k8s",
            "openstack-machines",
            channel=None,
            revision=None,
        )
        basic_jhelper.wait_until_active.assert_called_once()

    def test_run_risk_change_uses_new_channel(self, step, basic_jhelper, step_context):
        """Risk-level change: charm_refresh called with the target channel."""
        basic_jhelper.get_leader_unit.return_value = "k8s/0"
        step._decision = CharmRefreshDecision(
            result=Result(ResultType.COMPLETED),
            effective_channel="1.32/edge",
            needs_channel_flag=True,
        )

        result = step.run(step_context)

        assert result.result_type == ResultType.COMPLETED
        basic_jhelper.charm_refresh.assert_called_once_with(
            "k8s",
            "openstack-machines",
            channel="1.32/edge",
            revision=None,
        )

    def test_run_uses_pinned_revision_from_manifest(
        self, step, basic_jhelper, step_context
    ):
        basic_jhelper.get_leader_unit.return_value = "k8s/0"
        step._decision = CharmRefreshDecision(
            result=Result(ResultType.COMPLETED),
            effective_channel="1.32/stable",
            needs_channel_flag=False,
            effective_revision=42,
        )

        result = step.run(step_context)

        assert result.result_type == ResultType.COMPLETED
        basic_jhelper.charm_refresh.assert_called_once_with(
            "k8s",
            "openstack-machines",
            channel=None,
            revision=42,
        )

    def test_run_fails_when_no_leader_found(self, step, basic_jhelper, step_context):
        basic_jhelper.get_leader_unit.side_effect = LeaderNotFoundException()

        result = step.run(step_context)

        assert result.result_type == ResultType.FAILED
        assert "k8s upgrade failed" in result.message
        assert "leader unit" in result.message
        basic_jhelper.run_action.assert_not_called()

    def test_run_fails_when_pre_upgrade_check_fails(
        self, step, basic_jhelper, basic_manifest, step_context
    ):
        basic_jhelper.get_leader_unit.return_value = "k8s/0"
        basic_jhelper.run_action.side_effect = JujuException("cluster not ready")
        basic_manifest.find_charm.return_value = None

        result = step.run(step_context)

        assert result.result_type == ResultType.FAILED
        assert "k8s upgrade failed" in result.message
        assert "pre-upgrade-check" in result.message
        assert "cluster not ready" in result.message
        basic_jhelper.charm_refresh.assert_not_called()

    def test_run_fails_when_charm_refresh_fails(
        self, step, basic_jhelper, step_context
    ):
        basic_jhelper.get_leader_unit.return_value = "k8s/0"
        basic_jhelper.run_action.return_value = {}
        basic_jhelper.charm_refresh.side_effect = JujuException("bad channel")

        result = step.run(step_context)

        assert result.result_type == ResultType.FAILED
        assert "k8s upgrade failed" in result.message
        assert "bad channel" in result.message
        basic_jhelper.wait_until_active.assert_not_called()

    def test_run_fails_on_wait_timeout(self, step, basic_jhelper, step_context):
        basic_jhelper.get_leader_unit.return_value = "k8s/0"
        basic_jhelper.run_action.return_value = {}
        basic_jhelper.charm_refresh.return_value = None
        basic_jhelper.wait_until_active.side_effect = TimeoutError("timed out")

        result = step.run(step_context)

        assert result.result_type == ResultType.FAILED
        assert "k8s upgrade failed" in result.message
        assert "Timed out" in result.message

    def test_run_fails_on_juju_wait_exception(self, step, basic_jhelper, step_context):
        basic_jhelper.get_leader_unit.return_value = "k8s/0"
        basic_jhelper.run_action.return_value = {}
        basic_jhelper.charm_refresh.return_value = None
        basic_jhelper.wait_until_active.side_effect = JujuWaitException("wait error")

        result = step.run(step_context)

        assert result.result_type == ResultType.FAILED
        assert "k8s upgrade failed" in result.message

    def test_run_error_messages_include_model_name(
        self, step, basic_jhelper, basic_manifest, step_context
    ):
        """Error messages should include the model name for actionable juju commands."""
        basic_jhelper.get_leader_unit.return_value = "k8s/0"
        basic_jhelper.run_action.side_effect = JujuException("not ready")
        basic_manifest.find_charm.return_value = None

        result = step.run(step_context)

        assert "openstack-machines" in result.message
