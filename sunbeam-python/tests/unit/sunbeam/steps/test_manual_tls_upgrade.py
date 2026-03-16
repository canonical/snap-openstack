# SPDX-FileCopyrightText: 2024 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

from unittest.mock import Mock

import pytest

from sunbeam.core.common import ResultType
from sunbeam.core.juju import (
    ApplicationNotFoundException,
    JujuException,
)
from sunbeam.steps.manual_tls import (
    CHARM_NAME,
    MANUAL_CERT_AUTH_CHANNEL,
    MANUAL_TLS_UPGRADE_TIMEOUT,
    ManualTLSCharmUpgradeStep,
)


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
    channel=MANUAL_CERT_AUTH_CHANNEL, rev=10, base_name="ubuntu", base_channel="22.04"
):
    base = Mock(name=base_name, channel=base_channel)
    return Mock(charm_channel=channel, charm_rev=rev, base=base)


class TestManualTLSCharmUpgradeStepIsSkip:
    def test_skip_when_application_not_deployed(self, step, basic_jhelper):
        basic_jhelper.get_application.side_effect = ApplicationNotFoundException()

        result = step.is_skip()

        assert result.result_type == ResultType.SKIPPED
        assert "not been deployed" in result.message

    def test_skip_when_revision_pinned_and_already_deployed(
        self, step, basic_jhelper, basic_manifest
    ):
        basic_jhelper.get_application.return_value = _app(rev=5)
        charm_mock = Mock(revision=5, channel=MANUAL_CERT_AUTH_CHANNEL)
        basic_manifest.core.software.charms = {CHARM_NAME: charm_mock}
        basic_manifest.get_features.return_value = []

        result = step.is_skip()

        assert result.result_type == ResultType.SKIPPED
        assert "already at manifest-pinned" in result.message
        basic_jhelper.get_available_charm_revision.assert_not_called()

    def test_not_skipped_when_revision_pinned_but_differs_from_deployed(
        self, step, basic_jhelper, basic_manifest
    ):
        basic_jhelper.get_application.return_value = _app(rev=3)
        charm_mock = Mock(revision=5, channel=MANUAL_CERT_AUTH_CHANNEL)
        basic_manifest.core.software.charms = {CHARM_NAME: charm_mock}
        basic_manifest.get_features.return_value = []

        result = step.is_skip()

        assert result.result_type == ResultType.COMPLETED
        basic_jhelper.get_available_charm_revision.assert_not_called()

    def test_skip_when_already_at_latest_revision_on_same_channel(
        self, step, basic_jhelper, basic_manifest
    ):
        basic_jhelper.get_application.return_value = _app(
            channel=MANUAL_CERT_AUTH_CHANNEL, rev=42
        )
        basic_jhelper.get_available_charm_revision.return_value = 42
        basic_manifest.core.software.charms = {}
        basic_manifest.get_features.return_value = []

        result = step.is_skip()

        assert result.result_type == ResultType.SKIPPED
        assert "already on channel" in result.message

    def test_not_skipped_when_newer_revision_available_same_channel(
        self, step, basic_jhelper, basic_manifest
    ):
        basic_jhelper.get_application.return_value = _app(
            channel=MANUAL_CERT_AUTH_CHANNEL, rev=10
        )
        basic_jhelper.get_available_charm_revision.return_value = 11
        basic_manifest.core.software.charms = {}
        basic_manifest.get_features.return_value = []

        result = step.is_skip()

        assert result.result_type == ResultType.COMPLETED

    def test_not_skipped_when_channel_differs_cross_track(
        self, step, basic_jhelper, basic_manifest
    ):
        # deployed on 2/stable, manifest says 1/stable — cross-track, should NOT skip
        basic_jhelper.get_application.return_value = _app(channel="2/stable", rev=10)
        basic_jhelper.get_available_charm_revision.return_value = 42
        charm_mock = Mock(revision=None, channel="1/stable")
        basic_manifest.core.software.charms = {CHARM_NAME: charm_mock}
        basic_manifest.get_features.return_value = []

        result = step.is_skip()

        assert result.result_type == ResultType.COMPLETED

    def test_not_skipped_when_channel_changed_same_track(
        self, step, basic_jhelper, basic_manifest
    ):
        basic_jhelper.get_application.return_value = _app(channel="1/stable", rev=10)
        basic_jhelper.get_available_charm_revision.return_value = 15
        charm_mock = Mock(revision=None, channel="1/edge")
        basic_manifest.core.software.charms = {CHARM_NAME: charm_mock}
        basic_manifest.get_features.return_value = []

        result = step.is_skip()

        assert result.result_type == ResultType.COMPLETED

    def test_proceeds_when_revision_lookup_fails(
        self, step, basic_jhelper, basic_manifest
    ):
        basic_jhelper.get_application.return_value = _app()
        basic_jhelper.get_available_charm_revision.side_effect = JujuException(
            "lookup failed"
        )
        basic_manifest.core.software.charms = {}
        basic_manifest.get_features.return_value = []

        result = step.is_skip()

        assert result.result_type == ResultType.COMPLETED


class TestManualTLSCharmUpgradeStepRun:
    def test_run_success(self, step, basic_jhelper, basic_manifest):
        basic_manifest.core.software.charms = {}
        basic_manifest.get_features.return_value = []
        basic_jhelper.snapshot_workload_status.return_value = {CHARM_NAME: "active"}

        result = step.run()

        assert result.result_type == ResultType.COMPLETED
        basic_jhelper.charm_refresh.assert_called_once_with(
            CHARM_NAME,
            "openstack",
            channel=MANUAL_CERT_AUTH_CHANNEL,
            revision=None,
        )
        basic_jhelper.wait_application_ready.assert_called_once_with(
            CHARM_NAME,
            "openstack",
            accepted_status=["active"],
            timeout=MANUAL_TLS_UPGRADE_TIMEOUT,
        )

    def test_run_uses_pre_refresh_status_in_wait(
        self, step, basic_jhelper, basic_manifest
    ):
        basic_manifest.core.software.charms = {}
        basic_manifest.get_features.return_value = []
        basic_jhelper.snapshot_workload_status.return_value = {CHARM_NAME: "waiting"}

        step.run()

        _, kwargs = basic_jhelper.wait_application_ready.call_args
        assert "waiting" in kwargs["accepted_status"]
        assert "active" in kwargs["accepted_status"]

    def test_run_uses_channel_and_revision_from_manifest(
        self, step, basic_jhelper, basic_manifest
    ):
        charm_mock = Mock(revision=42, channel="2/stable")
        basic_manifest.core.software.charms = {CHARM_NAME: charm_mock}
        basic_manifest.get_features.return_value = []
        basic_jhelper.snapshot_workload_status.return_value = {}

        step.run()

        basic_jhelper.charm_refresh.assert_called_once_with(
            CHARM_NAME,
            "openstack",
            channel="2/stable",
            revision=42,
        )

    def test_run_charm_refresh_fails(self, step, basic_jhelper, basic_manifest):
        basic_manifest.core.software.charms = {}
        basic_manifest.get_features.return_value = []
        basic_jhelper.snapshot_workload_status.return_value = {}
        basic_jhelper.charm_refresh.side_effect = JujuException("refresh failed")

        result = step.run()

        assert result.result_type == ResultType.FAILED
        assert "refresh failed" in result.message
        basic_jhelper.wait_application_ready.assert_not_called()

    def test_run_wait_times_out(self, step, basic_jhelper, basic_manifest):
        basic_manifest.core.software.charms = {}
        basic_manifest.get_features.return_value = []
        basic_jhelper.snapshot_workload_status.return_value = {}
        basic_jhelper.wait_application_ready.side_effect = TimeoutError("timed out")

        result = step.run()

        assert result.result_type == ResultType.FAILED
        assert "timed out" in result.message

    def test_run_updates_tfvars_with_channel(
        self, step, basic_jhelper, basic_tfhelper, basic_manifest
    ):
        basic_manifest.core.software.charms = {}
        basic_manifest.get_features.return_value = []
        basic_jhelper.snapshot_workload_status.return_value = {}

        step.run()

        basic_tfhelper.update_tfvars_and_apply_tf.assert_called_once()
        _, kwargs = basic_tfhelper.update_tfvars_and_apply_tf.call_args
        assert kwargs["override_tfvars"] == {
            "manual-tls-certificates-channel": MANUAL_CERT_AUTH_CHANNEL
        }
