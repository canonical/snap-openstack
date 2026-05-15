# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

from unittest.mock import Mock

import pytest

from sunbeam.core.common import ResultType
from sunbeam.core.juju import ApplicationNotFoundException, JujuException
from sunbeam.steps.charm_upgrade import check_charm_needs_refresh

DEFAULT_CHANNEL = "1.18/stable"
CHARM_NAME = "test-charm"
MODEL = "test-model"
APPLICATION = "test-app"


@pytest.fixture
def jhelper():
    return Mock()


@pytest.fixture
def manifest():
    m = Mock()
    m.find_charm.return_value = None
    return m


class TestCheckCharmNeedsRefreshAppNotDeployed:
    def test_skipped_when_app_not_found(self, jhelper, manifest):
        jhelper.get_application.side_effect = ApplicationNotFoundException()

        decision = check_charm_needs_refresh(
            jhelper, manifest, CHARM_NAME, MODEL, APPLICATION, DEFAULT_CHANNEL
        )

        assert decision.result.result_type == ResultType.SKIPPED
        assert "not been deployed" in decision.result.message
        assert decision.app_not_deployed is True


class TestCheckCharmNeedsRefreshMinimumTrack:
    def test_failed_when_manifest_channel_below_minimum(self, jhelper, manifest):
        app = Mock(charm_rev=50, charm_channel="1.16/stable", base=None)
        jhelper.get_application.return_value = app
        manifest.find_charm.return_value = Mock(channel="1.16/stable", revision=None)

        decision = check_charm_needs_refresh(
            jhelper,
            manifest,
            CHARM_NAME,
            MODEL,
            APPLICATION,
            DEFAULT_CHANNEL,
        )

        assert decision.result.result_type == ResultType.FAILED
        assert "minimum supported track" in decision.result.message

    def test_passes_when_manifest_channel_at_minimum(self, jhelper, manifest):
        app = Mock(charm_rev=50, charm_channel="1.18/stable", base=None)
        jhelper.get_application.return_value = app
        jhelper.get_available_charm_revisions.return_value = {"amd64": 100}
        manifest.find_charm.return_value = Mock(channel="1.18/stable", revision=None)

        decision = check_charm_needs_refresh(
            jhelper,
            manifest,
            CHARM_NAME,
            MODEL,
            APPLICATION,
            DEFAULT_CHANNEL,
        )

        assert decision.result.result_type != ResultType.FAILED

    def test_passes_when_manifest_channel_above_minimum(self, jhelper, manifest):
        # scenario: track upgrades allowed (e.g. vault)
        app = Mock(charm_rev=50, charm_channel="1.18/stable", base=None)
        jhelper.get_application.return_value = app
        jhelper.get_available_charm_revisions.return_value = {"amd64": 100}
        manifest.find_charm.return_value = Mock(channel="1.19/stable", revision=None)

        decision = check_charm_needs_refresh(
            jhelper,
            manifest,
            CHARM_NAME,
            MODEL,
            APPLICATION,
            DEFAULT_CHANNEL,
            support_track_upgrades=True,
        )

        assert decision.result.result_type != ResultType.FAILED


class TestCheckCharmNeedsRefreshEffectiveChannel:
    def test_no_manifest_stays_on_deployed_channel_when_above_minimum(
        self, jhelper, manifest
    ):
        """No manifest: uses deployed channel when track >= minimum."""
        app = Mock(charm_rev=50, charm_channel="1.18/edge", base=None)
        jhelper.get_application.return_value = app
        jhelper.get_available_charm_revisions.return_value = {"amd64": 100}
        manifest.find_charm.return_value = None

        decision = check_charm_needs_refresh(
            jhelper,
            manifest,
            CHARM_NAME,
            MODEL,
            APPLICATION,
            DEFAULT_CHANNEL,
        )

        assert decision.effective_channel == "1.18/edge"

    def test_no_manifest_falls_back_to_default_when_below_minimum_and_upgrades_allowed(
        self, jhelper, manifest
    ):
        """Falls back to default_channel when deployed track < minimum.

        Applies when track upgrades are allowed.
        """
        # scenario: vault (support_track_upgrades=True)
        app = Mock(charm_rev=50, charm_channel="1.16/stable", base=None)
        jhelper.get_application.return_value = app
        jhelper.get_available_charm_revisions.return_value = {"amd64": 100}
        manifest.find_charm.return_value = None

        decision = check_charm_needs_refresh(
            jhelper,
            manifest,
            CHARM_NAME,
            MODEL,
            APPLICATION,
            DEFAULT_CHANNEL,
            support_track_upgrades=True,
        )

        assert decision.effective_channel == DEFAULT_CHANNEL

    def test_no_manifest_stays_on_deployed_track_when_below_minimum_and_upgrades_not_allowed(
        self, jhelper, manifest
    ):
        """Warns and stays on deployed track when deployed track < minimum.

        Applies when track upgrades are not allowed.
        """
        # scenario: k8s (support_track_upgrades=False)
        app = Mock(charm_rev=50, charm_channel="1.16/stable", base=None)
        jhelper.get_application.return_value = app
        jhelper.get_available_charm_revisions.return_value = {"amd64": 100}
        manifest.find_charm.return_value = None

        decision = check_charm_needs_refresh(
            jhelper,
            manifest,
            CHARM_NAME,
            MODEL,
            APPLICATION,
            DEFAULT_CHANNEL,
            support_track_upgrades=False,
        )

        assert decision.result.result_type != ResultType.FAILED
        assert decision.effective_channel == "1.16/stable"

    def test_no_manifest_above_default_track_stays_on_deployed_channel(
        self, jhelper, manifest
    ):
        """No manifest: deployed track above default_channel track stays on deployed."""
        app = Mock(charm_rev=50, charm_channel="1.36/edge", base=None)
        jhelper.get_application.return_value = app
        jhelper.get_available_charm_revisions.return_value = {"amd64": 100}
        manifest.find_charm.return_value = None

        decision = check_charm_needs_refresh(
            jhelper,
            manifest,
            CHARM_NAME,
            MODEL,
            APPLICATION,
            "1.35/stable",
        )

        assert decision.effective_channel == "1.36/edge"

    def test_manifest_channel_wins_over_deployed(self, jhelper, manifest):
        app = Mock(charm_rev=50, charm_channel="1.18/stable", base=None)
        jhelper.get_application.return_value = app
        jhelper.get_available_charm_revisions.return_value = {"amd64": 100}
        manifest.find_charm.return_value = Mock(channel="1.19/stable", revision=None)

        decision = check_charm_needs_refresh(
            jhelper,
            manifest,
            CHARM_NAME,
            MODEL,
            APPLICATION,
            DEFAULT_CHANNEL,
            support_track_upgrades=True,
        )

        assert decision.effective_channel == "1.19/stable"


class TestCheckCharmNeedsRefreshRevisionOnly:
    def test_skipped_when_already_at_revision(self, jhelper, manifest):
        app = Mock(charm_rev=500, charm_channel="1.18/stable", base=None)
        jhelper.get_application.return_value = app
        manifest.find_charm.return_value = Mock(channel=None, revision=500)

        decision = check_charm_needs_refresh(
            jhelper, manifest, CHARM_NAME, MODEL, APPLICATION, DEFAULT_CHANNEL
        )

        assert decision.result.result_type == ResultType.SKIPPED
        assert "500" in decision.result.message

    def test_completed_when_revision_differs(self, jhelper, manifest):
        app = Mock(charm_rev=499, charm_channel="1.18/stable", base=None)
        jhelper.get_application.return_value = app
        jhelper.get_charm_channel_for_revision.return_value = "1.18/stable"
        manifest.find_charm.return_value = Mock(channel=None, revision=500)

        decision = check_charm_needs_refresh(
            jhelper, manifest, CHARM_NAME, MODEL, APPLICATION, DEFAULT_CHANNEL
        )

        assert decision.result.result_type == ResultType.COMPLETED
        assert decision.effective_revision == 500

    def test_failed_when_revision_belongs_to_different_track(self, jhelper, manifest):
        """Track change via revision is blocked when support_track_upgrades=False."""
        app = Mock(charm_rev=499, charm_channel="1.18/stable", base=None)
        jhelper.get_application.return_value = app
        jhelper.get_charm_channel_for_revision.return_value = "1.20/stable"
        manifest.find_charm.return_value = Mock(channel=None, revision=500)

        decision = check_charm_needs_refresh(
            jhelper,
            manifest,
            CHARM_NAME,
            MODEL,
            APPLICATION,
            DEFAULT_CHANNEL,
            support_track_upgrades=False,
        )

        assert decision.result.result_type == ResultType.FAILED
        assert "1.18" in decision.result.message
        assert "1.20" in decision.result.message

    def test_proceeds_when_track_lookup_fails(self, jhelper, manifest):
        """Revision-only: proceed rather than block when CharmHub is unreachable."""
        app = Mock(charm_rev=499, charm_channel="1.18/stable", base=None)
        jhelper.get_application.return_value = app
        jhelper.get_charm_channel_for_revision.side_effect = JujuException(
            "network error"
        )
        manifest.find_charm.return_value = Mock(channel=None, revision=500)

        decision = check_charm_needs_refresh(
            jhelper,
            manifest,
            CHARM_NAME,
            MODEL,
            APPLICATION,
            DEFAULT_CHANNEL,
            support_track_upgrades=False,
        )

        assert decision.result.result_type == ResultType.COMPLETED

    def test_no_track_check_when_support_track_upgrades_true(self, jhelper, manifest):
        """Revision from a different track is allowed when upgrades are supported."""
        app = Mock(charm_rev=499, charm_channel="1.18/stable", base=None)
        jhelper.get_application.return_value = app
        manifest.find_charm.return_value = Mock(channel=None, revision=500)

        decision = check_charm_needs_refresh(
            jhelper,
            manifest,
            CHARM_NAME,
            MODEL,
            APPLICATION,
            DEFAULT_CHANNEL,
            support_track_upgrades=True,
        )

        assert decision.result.result_type == ResultType.COMPLETED
        jhelper.get_charm_channel_for_revision.assert_not_called()


class TestCheckCharmNeedsRefreshTrackChange:
    def test_failed_on_track_change_when_not_supported(self, jhelper, manifest):
        app = Mock(charm_rev=100, charm_channel="1.18/stable", base=None)
        jhelper.get_application.return_value = app
        manifest.find_charm.return_value = Mock(channel="1.20/stable", revision=None)

        decision = check_charm_needs_refresh(
            jhelper,
            manifest,
            CHARM_NAME,
            MODEL,
            APPLICATION,
            DEFAULT_CHANNEL,
            support_track_upgrades=False,
        )

        assert decision.result.result_type == ResultType.FAILED
        assert "track change" in decision.result.message

    def test_completed_on_track_change_when_supported(self, jhelper, manifest):
        app = Mock(charm_rev=100, charm_channel="1.18/stable", base=None)
        jhelper.get_application.return_value = app
        manifest.find_charm.return_value = Mock(channel="1.20/stable", revision=None)

        decision = check_charm_needs_refresh(
            jhelper,
            manifest,
            CHARM_NAME,
            MODEL,
            APPLICATION,
            DEFAULT_CHANNEL,
            support_track_upgrades=True,
        )

        assert decision.result.result_type == ResultType.COMPLETED
        assert decision.needs_channel_flag is True


class TestCheckCharmNeedsRefreshRiskChange:
    def test_completed_with_channel_flag_on_risk_change(self, jhelper, manifest):
        app = Mock(charm_rev=100, charm_channel="1.18/stable", base=None)
        jhelper.get_application.return_value = app
        manifest.find_charm.return_value = Mock(channel="1.18/edge", revision=None)

        decision = check_charm_needs_refresh(
            jhelper, manifest, CHARM_NAME, MODEL, APPLICATION, DEFAULT_CHANNEL
        )

        assert decision.result.result_type == ResultType.COMPLETED
        assert decision.needs_channel_flag is True
        assert decision.effective_channel == "1.18/edge"


class TestCheckCharmNeedsRefreshRevisionAndChannel:
    def test_skipped_when_exact_channel_and_revision_match(self, jhelper, manifest):
        app = Mock(charm_rev=42, charm_channel="1.18/stable", base=None)
        jhelper.get_application.return_value = app
        manifest.find_charm.return_value = Mock(channel="1.18/stable", revision=42)

        decision = check_charm_needs_refresh(
            jhelper, manifest, CHARM_NAME, MODEL, APPLICATION, DEFAULT_CHANNEL
        )

        assert decision.result.result_type == ResultType.SKIPPED
        assert "42" in decision.result.message

    def test_completed_when_revision_differs(self, jhelper, manifest):
        app = Mock(charm_rev=41, charm_channel="1.18/stable", base=None)
        jhelper.get_application.return_value = app
        manifest.find_charm.return_value = Mock(channel="1.18/stable", revision=42)

        decision = check_charm_needs_refresh(
            jhelper, manifest, CHARM_NAME, MODEL, APPLICATION, DEFAULT_CHANNEL
        )

        assert decision.result.result_type == ResultType.COMPLETED
        assert decision.effective_revision == 42


class TestCheckCharmNeedsRefreshPatchUpgrade:
    def test_skipped_when_at_latest_revision(self, jhelper, manifest):
        app = Mock(charm_rev=200, charm_channel="1.18/stable", base=None)
        jhelper.get_application.return_value = app
        jhelper.get_available_charm_revisions.return_value = {"amd64": 200}
        manifest.find_charm.return_value = None

        decision = check_charm_needs_refresh(
            jhelper, manifest, CHARM_NAME, MODEL, APPLICATION, DEFAULT_CHANNEL
        )

        assert decision.result.result_type == ResultType.SKIPPED
        assert "200" in decision.result.message

    def test_completed_when_newer_revision_available(self, jhelper, manifest):
        app = Mock(charm_rev=199, charm_channel="1.18/stable", base=None)
        jhelper.get_application.return_value = app
        jhelper.get_available_charm_revisions.return_value = {"amd64": 200}
        manifest.find_charm.return_value = None

        decision = check_charm_needs_refresh(
            jhelper, manifest, CHARM_NAME, MODEL, APPLICATION, DEFAULT_CHANNEL
        )

        assert decision.result.result_type == ResultType.COMPLETED

    def test_completed_when_revision_lookup_fails(self, jhelper, manifest):
        """Proceed rather than skip when CharmHub is unreachable."""
        app = Mock(charm_rev=200, charm_channel="1.18/stable", base=None)
        jhelper.get_application.return_value = app
        jhelper.get_available_charm_revisions.side_effect = JujuException("timeout")
        manifest.find_charm.return_value = None

        decision = check_charm_needs_refresh(
            jhelper, manifest, CHARM_NAME, MODEL, APPLICATION, DEFAULT_CHANNEL
        )

        assert decision.result.result_type == ResultType.COMPLETED

    def test_passes_base_to_revision_lookup(self, jhelper, manifest):
        base = Mock()
        base.name = "ubuntu"
        base.channel = "24.04"
        app = Mock(charm_rev=199, charm_channel="1.18/stable", base=base)
        jhelper.get_application.return_value = app
        jhelper.get_available_charm_revisions.return_value = {"amd64": 200}
        manifest.find_charm.return_value = None

        check_charm_needs_refresh(
            jhelper, manifest, CHARM_NAME, MODEL, APPLICATION, DEFAULT_CHANNEL
        )

        jhelper.get_available_charm_revisions.assert_called_once_with(
            CHARM_NAME, "1.18/stable", "ubuntu@24.04"
        )

    def test_completed_on_branch_channel(self, jhelper, manifest):
        """Branch channels proceed with refresh to pick up any new revision."""
        app = Mock(
            charm_rev=200, charm_channel="1.32/edge/hue-fix-kubelet-0405-1", base=None
        )
        jhelper.get_application.return_value = app
        manifest.find_charm.return_value = None

        decision = check_charm_needs_refresh(
            jhelper, manifest, CHARM_NAME, MODEL, APPLICATION, DEFAULT_CHANNEL
        )

        assert decision.result.result_type == ResultType.COMPLETED
        jhelper.get_available_charm_revisions.assert_not_called()
