# SPDX-FileCopyrightText: 2023 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

from unittest.mock import Mock, call

from sunbeam.core.common import ResultType
from sunbeam.steps.upgrades.intra_channel import LatestInChannel


class TestLatestInChannel:
    def setup_method(self):
        """Set up test fixtures."""
        self.deployment = Mock()
        self.jhelper = Mock()
        self.manifest = Mock()

        # Set up default manifest structure
        self.manifest.core.software.charms = {}
        self.manifest.get_features.return_value = []

        self.upgrader = LatestInChannel(self.deployment, self.jhelper, self.manifest)

    def test_refresh_apps_no_manifest_entry(self):
        """Test refresh when charm has no manifest entry."""
        # Setup
        apps = {
            "nova": ("nova-k8s", "2024.1/stable", 123),
        }
        model = "openstack"

        # No manifest charm entry
        self.manifest.core.software.charms = {}
        self.manifest.get_features.return_value = []

        # Mock wait methods
        self.jhelper.wait_until_active = Mock()

        # Execute
        result = self.upgrader.refresh_apps(apps, model)

        # Verify charm_refresh was called without channel/revision
        self.jhelper.charm_refresh.assert_called_once_with("nova", model)
        assert result.result_type == ResultType.COMPLETED

    def test_refresh_apps_with_manifest_channel_and_revision(self):
        """Test refresh when manifest has channel and revision."""
        # Setup
        apps = {
            "nova": ("nova-k8s", "2024.1/stable", 123),
        }
        model = "openstack"

        # Manifest charm with channel and revision
        manifest_charm = Mock()
        manifest_charm.channel = "2024.1/stable"
        manifest_charm.revision = 150
        self.manifest.core.software.charms = {"nova-k8s": manifest_charm}

        # Mock wait methods
        self.jhelper.wait_until_active = Mock()

        # Execute
        result = self.upgrader.refresh_apps(apps, model)

        # Verify charm_refresh was called with channel and revision
        self.jhelper.charm_refresh.assert_called_once_with(
            "nova",
            model,
            channel="2024.1/stable",
            revision=150,
        )
        assert result.result_type == ResultType.COMPLETED

    def test_refresh_apps_with_manifest_channel_only(self):
        """Test refresh when manifest has only channel (no revision)."""
        # Setup
        apps = {
            "nova": ("nova-k8s", "2024.1/stable", 123),
        }
        model = "openstack"

        # Manifest charm with channel but no revision
        manifest_charm = Mock()
        manifest_charm.channel = "2024.1/stable"
        manifest_charm.revision = None
        self.manifest.core.software.charms = {"nova-k8s": manifest_charm}

        # Mock wait methods
        self.jhelper.wait_until_active = Mock()

        # Execute
        result = self.upgrader.refresh_apps(apps, model)

        # Verify charm_refresh was called with channel but None revision
        self.jhelper.charm_refresh.assert_called_once_with(
            "nova",
            model,
            channel="2024.1/stable",
            revision=None,
        )
        assert result.result_type == ResultType.COMPLETED

    def test_refresh_apps_multiple_apps_mixed_manifest(self):
        """Test refresh with multiple apps, some in manifest, some not."""
        # Setup
        apps = {
            "nova": ("nova-k8s", "2024.1/stable", 123),
            "neutron": ("neutron-k8s", "2024.1/stable", 456),
            "cinder": ("cinder-k8s", "2024.1/stable", 789),
        }
        model = "openstack"

        # Only nova in manifest
        manifest_charm = Mock()
        manifest_charm.channel = "2024.1/candidate"
        manifest_charm.revision = 200
        self.manifest.core.software.charms = {"nova-k8s": manifest_charm}

        # Mock wait methods
        self.jhelper.wait_until_active = Mock()

        # Execute
        result = self.upgrader.refresh_apps(apps, model)

        # Verify charm_refresh was called for all apps
        assert self.jhelper.charm_refresh.call_count == 3

        # Check calls
        calls = self.jhelper.charm_refresh.call_args_list

        # Nova should be called with manifest config
        assert call("nova", model, channel="2024.1/candidate", revision=200) in calls

        # Neutron and Cinder should be called without channel/revision
        assert call("neutron", model) in calls
        assert call("cinder", model) in calls

        assert result.result_type == ResultType.COMPLETED

    def test_refresh_apps_from_feature_manifest(self):
        """Test refresh when charm is in feature manifest, not core."""
        # Setup
        apps = {
            "barbican": ("barbican-k8s", "2024.1/stable", 123),
        }
        model = "openstack"

        # Not in core manifest
        self.manifest.core.software.charms = {}

        # In feature manifest
        feature_manifest = Mock()
        manifest_charm = Mock()
        manifest_charm.channel = "2024.1/stable"
        manifest_charm.revision = 100
        feature_manifest.software.charms = {"barbican-k8s": manifest_charm}
        self.manifest.get_features.return_value = [("barbican", feature_manifest)]

        # Mock wait methods
        self.jhelper.wait_until_active = Mock()

        # Execute
        result = self.upgrader.refresh_apps(apps, model)

        # Verify charm_refresh was called with feature manifest config
        self.jhelper.charm_refresh.assert_called_once_with(
            "barbican",
            model,
            channel="2024.1/stable",
            revision=100,
        )
        assert result.result_type == ResultType.COMPLETED

    def test_refresh_apps_machine_model(self):
        """Test refresh for machine model apps."""
        # Setup
        apps = {
            "nova-compute": ("nova-compute", "2024.1/stable", 123),
        }
        model = "openstack-machines"

        # No manifest entry
        self.manifest.core.software.charms = {}

        # Mock wait methods
        self.jhelper.wait_application_ready = Mock()

        # Execute
        result = self.upgrader.refresh_apps(apps, model)

        # Verify charm_refresh was called
        self.jhelper.charm_refresh.assert_called_once_with("nova-compute", model)

        # Verify wait_application_ready was called (not wait_until_active)
        self.jhelper.wait_application_ready.assert_called_once()
        assert result.result_type == ResultType.COMPLETED

    def test_refresh_apps_timeout_k8s_model(self):
        """Test refresh fails when timeout occurs for k8s apps."""
        # Setup
        apps = {
            "nova": ("nova-k8s", "2024.1/stable", 123),
        }
        model = "openstack"

        # No manifest entry
        self.manifest.core.software.charms = {}

        # Mock wait to timeout
        self.jhelper.wait_until_active = Mock(side_effect=TimeoutError("timed out"))

        # Execute
        result = self.upgrader.refresh_apps(apps, model)

        # Verify failed result
        assert result.result_type == ResultType.FAILED
        assert "timed out" in result.message

    def test_refresh_apps_timeout_machine_model(self):
        """Test refresh fails when timeout occurs for machine apps."""
        # Setup
        apps = {
            "nova-compute": ("nova-compute", "2024.1/stable", 123),
        }
        model = "openstack-machines"

        # No manifest entry
        self.manifest.core.software.charms = {}

        # Mock wait to timeout
        self.jhelper.wait_application_ready = Mock(
            side_effect=TimeoutError("timed out")
        )

        # Execute
        result = self.upgrader.refresh_apps(apps, model)

        # Verify failed result
        assert result.result_type == ResultType.FAILED
        assert "timed out" in result.message
