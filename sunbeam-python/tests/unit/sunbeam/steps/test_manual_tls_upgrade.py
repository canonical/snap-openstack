# SPDX-FileCopyrightText: 2024 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

from unittest.mock import Mock

import pytest

from sunbeam.core.common import Result, ResultType
from sunbeam.core.juju import (
    ApplicationNotFoundException,
    JujuException,
    JujuWaitException,
)
from sunbeam.steps.charm_upgrade import CharmRefreshDecision
from sunbeam.steps.manual_tls import (
    CHARM_NAME,
    MANUAL_TLS_UPGRADE_TIMEOUT,
    ManualTLSCharmUpgradeStep,
)
from sunbeam.versions import MANUAL_TLS_CERTIFICATES_CHANNEL


@pytest.fixture
def step(basic_deployment, basic_client, basic_manifest, basic_jhelper, basic_tfhelper):
    return ManualTLSCharmUpgradeStep(
        basic_deployment,
        basic_client,
        basic_manifest,
        basic_jhelper,
        basic_tfhelper,
    )


def _app(
    channel=MANUAL_TLS_CERTIFICATES_CHANNEL,
    rev=10,
    base_name="ubuntu",
    base_channel="22.04",
):
    base = Mock(name=base_name, channel=base_channel)
    return Mock(charm_channel=channel, charm_rev=rev, base=base)


class TestManualTLSCharmUpgradeStepIsSkip:
    def test_skip_when_application_not_deployed(
        self, step, basic_jhelper, step_context
    ):
        basic_jhelper.get_application.side_effect = ApplicationNotFoundException()

        result = step.is_skip(step_context)

        assert result.result_type == ResultType.SKIPPED
        assert "not been deployed" in result.message

    def test_skip_when_revision_pinned_and_already_deployed(
        self, step, basic_jhelper, basic_manifest, step_context
    ):
        basic_jhelper.get_application.return_value = _app(rev=5)
        basic_manifest.find_charm.return_value = Mock(
            revision=5, channel=MANUAL_TLS_CERTIFICATES_CHANNEL
        )

        result = step.is_skip(step_context)

        assert result.result_type == ResultType.SKIPPED
        assert "manifest pinned revision" in result.message
        basic_jhelper.get_available_charm_revisions.assert_not_called()

    def test_not_skipped_when_revision_pinned_but_differs_from_deployed(
        self, step, basic_jhelper, basic_manifest, step_context
    ):
        basic_jhelper.get_application.return_value = _app(rev=3)
        basic_manifest.find_charm.return_value = Mock(
            revision=5, channel=MANUAL_TLS_CERTIFICATES_CHANNEL
        )

        result = step.is_skip(step_context)

        assert result.result_type == ResultType.COMPLETED
        basic_jhelper.get_available_charm_revisions.assert_not_called()

    def test_skip_when_already_at_latest_revision_on_same_channel(
        self, step, basic_jhelper, basic_manifest, step_context
    ):
        basic_jhelper.get_application.return_value = _app(
            channel=MANUAL_TLS_CERTIFICATES_CHANNEL, rev=42
        )
        basic_jhelper.get_available_charm_revisions.return_value = {"amd64": 42}
        basic_manifest.find_charm.return_value = None

        result = step.is_skip(step_context)

        assert result.result_type == ResultType.SKIPPED

    def test_not_skipped_when_newer_revision_available_same_channel(
        self, step, basic_jhelper, basic_manifest, step_context
    ):
        basic_jhelper.get_application.return_value = _app(
            channel=MANUAL_TLS_CERTIFICATES_CHANNEL, rev=10
        )
        basic_jhelper.get_available_charm_revisions.return_value = {"amd64": 11}
        basic_manifest.find_charm.return_value = None

        result = step.is_skip(step_context)

        assert result.result_type == ResultType.COMPLETED

    def test_not_skipped_when_channel_changed_same_track(
        self, step, basic_jhelper, basic_manifest, step_context
    ):
        basic_jhelper.get_application.return_value = _app(channel="1/stable", rev=10)
        basic_manifest.find_charm.return_value = Mock(revision=None, channel="1/edge")

        result = step.is_skip(step_context)

        assert result.result_type == ResultType.COMPLETED
        basic_jhelper.get_available_charm_revisions.assert_not_called()

    def test_not_skipped_when_cross_track_channel_in_manifest(
        self, step, basic_jhelper, basic_manifest, step_context
    ):
        basic_jhelper.get_application.return_value = _app(channel="1/stable", rev=10)
        basic_manifest.find_charm.return_value = Mock(revision=None, channel="2/stable")

        result = step.is_skip(step_context)

        assert result.result_type == ResultType.COMPLETED

    def test_proceeds_when_revision_lookup_fails(
        self, step, basic_jhelper, basic_manifest, step_context
    ):
        basic_jhelper.get_application.return_value = _app()
        basic_jhelper.get_available_charm_revisions.side_effect = JujuException(
            "lookup failed"
        )
        basic_manifest.find_charm.return_value = None

        result = step.is_skip(step_context)

        assert result.result_type == ResultType.COMPLETED


class TestManualTLSCharmUpgradeStepRun:
    def test_run_success(self, step, basic_jhelper, step_context):
        step._decision = CharmRefreshDecision(
            result=Result(ResultType.COMPLETED),
            effective_channel=MANUAL_TLS_CERTIFICATES_CHANNEL,
            needs_channel_flag=False,
        )

        result = step.run(step_context)

        assert result.result_type == ResultType.COMPLETED
        basic_jhelper.charm_refresh.assert_called_once_with(
            CHARM_NAME,
            "openstack",
            channel=None,
            revision=None,
        )
        basic_jhelper.wait_until_active.assert_called_once_with(
            "openstack",
            apps=[CHARM_NAME],
            timeout=MANUAL_TLS_UPGRADE_TIMEOUT,
        )

    def test_run_passes_channel_when_needs_channel_flag(
        self, step, basic_jhelper, step_context
    ):
        step._decision = CharmRefreshDecision(
            result=Result(ResultType.COMPLETED),
            effective_channel="1/edge",
            needs_channel_flag=True,
        )

        step.run(step_context)

        basic_jhelper.charm_refresh.assert_called_once_with(
            CHARM_NAME,
            "openstack",
            channel="1/edge",
            revision=None,
        )

    def test_run_uses_channel_and_revision_from_decision(
        self, step, basic_jhelper, step_context
    ):
        step._decision = CharmRefreshDecision(
            result=Result(ResultType.COMPLETED),
            effective_channel="2/stable",
            needs_channel_flag=True,
            effective_revision=42,
        )

        step.run(step_context)

        basic_jhelper.charm_refresh.assert_called_once_with(
            CHARM_NAME,
            "openstack",
            channel="2/stable",
            revision=42,
        )

    def test_run_charm_refresh_fails(self, step, basic_jhelper, step_context):
        step._decision = CharmRefreshDecision(
            result=Result(ResultType.COMPLETED),
            effective_channel=MANUAL_TLS_CERTIFICATES_CHANNEL,
            needs_channel_flag=False,
        )
        basic_jhelper.charm_refresh.side_effect = JujuException("refresh failed")

        result = step.run(step_context)

        assert result.result_type == ResultType.FAILED
        assert "refresh failed" in result.message
        basic_jhelper.wait_until_active.assert_not_called()

    def test_run_wait_times_out(self, step, basic_jhelper, step_context):
        step._decision = CharmRefreshDecision(
            result=Result(ResultType.COMPLETED),
            effective_channel=MANUAL_TLS_CERTIFICATES_CHANNEL,
            needs_channel_flag=False,
        )
        basic_jhelper.wait_until_active.side_effect = JujuWaitException("timed out")

        result = step.run(step_context)

        assert result.result_type == ResultType.FAILED
        assert "timed out" in result.message

    def test_run_updates_tfvars_with_channel(
        self, step, basic_jhelper, basic_tfhelper, step_context
    ):
        step._decision = CharmRefreshDecision(
            result=Result(ResultType.COMPLETED),
            effective_channel=MANUAL_TLS_CERTIFICATES_CHANNEL,
            needs_channel_flag=False,
        )

        step.run(step_context)

        basic_tfhelper.update_tfvars_and_apply_tf.assert_called_once()
        _, kwargs = basic_tfhelper.update_tfvars_and_apply_tf.call_args
        assert kwargs["override_tfvars"] == {
            "manual-tls-certificates-channel": MANUAL_TLS_CERTIFICATES_CHANNEL
        }
