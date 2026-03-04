# SPDX-FileCopyrightText: 2023 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

from unittest.mock import Mock, call, patch

from sunbeam.core.common import ResultType
from sunbeam.core.juju import JujuWaitException
from sunbeam.core.openstack import OPENSTACK_MODEL
from sunbeam.steps.upgrades.intra_channel import LatestInChannel

_INTRA_CHANNEL = "sunbeam.steps.upgrades.intra_channel"


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
        model = OPENSTACK_MODEL

        # No manifest entry
        self.manifest.core.software.charms = {}

        # Mock wait to timeout
        self.jhelper.wait_until_desired_status = Mock(
            side_effect=TimeoutError("timed out")
        )

        with patch(f"{_INTRA_CHANNEL}.update_status_background"):
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

    def test_refresh_apps_pre_refresh_status_fetched(self):
        """Test that pre-refresh status is fetched before charm_refresh calls."""
        apps = {"nova": ("nova-k8s", "2024.1/stable", 123)}
        model = OPENSTACK_MODEL
        self.manifest.core.software.charms = {}

        # snapshot_workload_status returns nova as "waiting"
        self.jhelper.snapshot_workload_status.return_value = {"nova": "waiting"}

        captured_overlay = {}

        def capture_overlay(*args, **kwargs):
            captured_overlay.update(kwargs.get("overlay", {}))

        self.jhelper.wait_until_desired_status.side_effect = capture_overlay

        with patch(f"{_INTRA_CHANNEL}.update_status_background"):
            result = self.upgrader.refresh_apps(apps, model)

        # snapshot_workload_status must be called before charm_refresh
        assert self.jhelper.snapshot_workload_status.call_count == 1
        assert result.result_type == ResultType.COMPLETED
        # "waiting" must appear in the accepted statuses for nova
        nova_overlay = captured_overlay.get("nova", {})
        accepted = nova_overlay.get("status", [])
        assert "waiting" in accepted
        assert "active" in accepted

    def test_refresh_apps_status_snapshot_exception_graceful(self):
        """Test graceful degradation when snapshot_workload_status raises."""
        apps = {"nova": ("nova-k8s", "2024.1/stable", 123)}
        model = OPENSTACK_MODEL
        self.manifest.core.software.charms = {}

        # snapshot raises — should not propagate
        self.jhelper.snapshot_workload_status.side_effect = Exception(
            "connection error"
        )

        with patch(f"{_INTRA_CHANNEL}.update_status_background"):
            result = self.upgrader.refresh_apps(apps, model)

        # charm_refresh still called, result still COMPLETED
        self.jhelper.charm_refresh.assert_called_once_with("nova", model)
        assert result.result_type == ResultType.COMPLETED


class TestWaitAfterRefresh:
    """Unit tests for LatestInChannel._wait_after_refresh."""

    def setup_method(self):
        """Set up test fixtures."""
        self.deployment = Mock()
        self.jhelper = Mock()
        self.manifest = Mock()
        self.manifest.core.software.charms = {}
        self.manifest.get_features.return_value = []
        self.upgrader = LatestInChannel(self.deployment, self.jhelper, self.manifest)

    def test_empty_refreshed_apps_returns_completed(self):
        """Empty refreshed_apps list short-circuits to COMPLETED."""
        result = self.upgrader._wait_after_refresh([], OPENSTACK_MODEL, {})

        assert result.result_type == ResultType.COMPLETED
        self.jhelper.wait_until_desired_status.assert_not_called()
        self.jhelper.wait_application_ready.assert_not_called()

    def test_k8s_model_app_was_active(self):
        """K8s path: app previously active → overlay status is ['active']."""
        pre = {"nova": "active"}
        captured = {}

        def capture(*args, **kwargs):
            captured.update(kwargs.get("overlay", {}))

        self.jhelper.wait_until_desired_status.side_effect = capture

        with patch(f"{_INTRA_CHANNEL}.update_status_background"):
            with patch(f"{_INTRA_CHANNEL}.build_overlay_dict", return_value={}):
                result = self.upgrader._wait_after_refresh(
                    ["nova"], OPENSTACK_MODEL, pre
                )

        assert result.result_type == ResultType.COMPLETED
        assert set(captured["nova"]["status"]) == {"active"}

    def test_k8s_model_app_was_waiting(self):
        """K8s path: app previously waiting → overlay accepts waiting and active."""
        pre = {"nova": "waiting"}
        captured = {}

        def capture(*args, **kwargs):
            captured.update(kwargs.get("overlay", {}))

        self.jhelper.wait_until_desired_status.side_effect = capture

        with patch(f"{_INTRA_CHANNEL}.update_status_background"):
            with patch(f"{_INTRA_CHANNEL}.build_overlay_dict", return_value={}):
                result = self.upgrader._wait_after_refresh(
                    ["nova"], OPENSTACK_MODEL, pre
                )

        assert result.result_type == ResultType.COMPLETED
        accepted = set(captured["nova"]["status"])
        assert "waiting" in accepted
        assert "active" in accepted

    def test_k8s_model_app_not_in_pre_refresh_defaults_to_active(self):
        """K8s path: app absent from pre_refresh_status defaults to 'active'."""
        captured = {}

        def capture(*args, **kwargs):
            captured.update(kwargs.get("overlay", {}))

        self.jhelper.wait_until_desired_status.side_effect = capture

        with patch(f"{_INTRA_CHANNEL}.update_status_background"):
            with patch(f"{_INTRA_CHANNEL}.build_overlay_dict", return_value={}):
                result = self.upgrader._wait_after_refresh(
                    ["nova"], OPENSTACK_MODEL, {}
                )

        assert result.result_type == ResultType.COMPLETED
        assert set(captured["nova"]["status"]) == {"active"}

    def test_k8s_model_build_overlay_dict_takes_precedence(self):
        """K8s path: apps already covered by build_overlay_dict keep their overlay."""
        # build_overlay_dict returns a "status" for traefik
        traefik_overlay = {"status": ["active", "waiting/idle"]}
        captured = {}

        def capture(*args, **kwargs):
            captured.update(kwargs.get("overlay", {}))

        self.jhelper.wait_until_desired_status.side_effect = capture

        with patch(f"{_INTRA_CHANNEL}.update_status_background"):
            with patch(
                f"{_INTRA_CHANNEL}.build_overlay_dict",
                return_value={"traefik": traefik_overlay},
            ):
                result = self.upgrader._wait_after_refresh(
                    ["traefik"], OPENSTACK_MODEL, {"traefik": "blocked"}
                )

        assert result.result_type == ResultType.COMPLETED
        # build_overlay_dict result must NOT be overwritten with pre-refresh status
        assert captured["traefik"]["status"] == ["active", "waiting/idle"]

    def test_k8s_model_juju_wait_exception_returns_failed(self):
        """K8s path: JujuWaitException propagates as FAILED result."""
        self.jhelper.wait_until_desired_status.side_effect = JujuWaitException(
            "charm error"
        )

        with patch(f"{_INTRA_CHANNEL}.update_status_background"):
            with patch(f"{_INTRA_CHANNEL}.build_overlay_dict", return_value={}):
                result = self.upgrader._wait_after_refresh(
                    ["nova"], OPENSTACK_MODEL, {}
                )

        assert result.result_type == ResultType.FAILED
        assert "charm error" in result.message

    def test_k8s_model_timeout_returns_failed(self):
        """K8s path: TimeoutError propagates as FAILED result."""
        self.jhelper.wait_until_desired_status.side_effect = TimeoutError("timeout!")

        with patch(f"{_INTRA_CHANNEL}.update_status_background"):
            with patch(f"{_INTRA_CHANNEL}.build_overlay_dict", return_value={}):
                result = self.upgrader._wait_after_refresh(
                    ["nova"], OPENSTACK_MODEL, {}
                )

        assert result.result_type == ResultType.FAILED
        assert "timeout!" in result.message

    def test_machine_model_app_was_active(self):
        """Machine path: app previously active → accepted includes active/unknown."""
        captured_calls = []
        self.jhelper.wait_application_ready.side_effect = lambda *a, **kw: (
            captured_calls.append(kw.get("accepted_status", []))
        )

        result = self.upgrader._wait_after_refresh(
            ["nova-compute"], "openstack-machines", {"nova-compute": "active"}
        )

        assert result.result_type == ResultType.COMPLETED
        assert len(captured_calls) == 1
        accepted = set(captured_calls[0])
        assert "active" in accepted
        assert "unknown" in accepted

    def test_machine_model_app_was_blocked(self):
        """Machine path: app previously blocked → blocked in accepted statuses."""
        captured_calls = []
        self.jhelper.wait_application_ready.side_effect = lambda *a, **kw: (
            captured_calls.append(kw.get("accepted_status", []))
        )

        result = self.upgrader._wait_after_refresh(
            ["nova-compute"], "openstack-machines", {"nova-compute": "blocked"}
        )

        assert result.result_type == ResultType.COMPLETED
        accepted = set(captured_calls[0])
        assert "blocked" in accepted
        assert "active" in accepted
        assert "unknown" in accepted

    def test_machine_model_app_not_in_pre_status_defaults_to_active(self):
        """Machine path: app absent from pre_refresh_status defaults to 'active'."""
        captured_calls = []
        self.jhelper.wait_application_ready.side_effect = lambda *a, **kw: (
            captured_calls.append(kw.get("accepted_status", []))
        )

        result = self.upgrader._wait_after_refresh(
            ["nova-compute"], "openstack-machines", {}
        )

        assert result.result_type == ResultType.COMPLETED
        accepted = set(captured_calls[0])
        assert "active" in accepted
        assert "unknown" in accepted

    def test_machine_model_timeout_returns_failed(self):
        """Machine path: TimeoutError propagates as FAILED result."""
        self.jhelper.wait_application_ready.side_effect = TimeoutError("too slow")

        result = self.upgrader._wait_after_refresh(
            ["nova-compute"], "openstack-machines", {}
        )

        assert result.result_type == ResultType.FAILED
        assert "too slow" in result.message

    def test_machine_model_multiple_apps_each_waited_individually(self):
        """Machine path: each app is waited on individually."""
        self.jhelper.wait_application_ready.return_value = None

        result = self.upgrader._wait_after_refresh(
            ["nova-compute", "cinder"], "openstack-machines", {}
        )

        assert result.result_type == ResultType.COMPLETED
        assert self.jhelper.wait_application_ready.call_count == 2
        apps_waited = {
            c.args[0] for c in self.jhelper.wait_application_ready.call_args_list
        }
        assert apps_waited == {"nova-compute", "cinder"}


class TestIsTrackChanged:
    """Unit tests for LatestInChannel.is_track_changed_for_any_charm."""

    def setup_method(self):
        """Set up test fixtures."""
        self.deployment = Mock()
        self.jhelper = Mock()
        self.manifest = Mock()
        self.manifest.core.software.charms = {}
        self.manifest.get_features.return_value = []
        self.upgrader = LatestInChannel(self.deployment, self.jhelper, self.manifest)

    def test_same_track_returns_false(self):
        """Returns False when manifest and deployed tracks match."""
        charm_manifest = Mock()
        charm_manifest.channel = "2024.1/stable"
        self.manifest.core.software.charms = {"nova-k8s": charm_manifest}

        apps = {"nova": ("nova-k8s", "2024.1/edge", 123)}
        result = self.upgrader.is_track_changed_for_any_charm(apps)

        assert result is False

    def test_different_track_returns_true(self):
        """Returns True when manifest track differs from deployed track."""
        charm_manifest = Mock()
        charm_manifest.channel = "2025.1/stable"
        self.manifest.core.software.charms = {"nova-k8s": charm_manifest}

        apps = {"nova": ("nova-k8s", "2024.1/stable", 123)}
        result = self.upgrader.is_track_changed_for_any_charm(apps)

        assert result is True

    def test_charm_not_in_manifest_skipped(self):
        """Charms absent from manifest are skipped; returns False if none differ."""
        self.manifest.core.software.charms = {}
        self.manifest.get_features.return_value = []

        apps = {"nova": ("nova-k8s", "2024.1/stable", 123)}
        result = self.upgrader.is_track_changed_for_any_charm(apps)

        assert result is False

    def test_charm_in_feature_manifest_track_differs(self):
        """Detects track change when charm is in a feature manifest."""
        self.manifest.core.software.charms = {}

        feature_manifest = Mock()
        charm_manifest = Mock()
        charm_manifest.channel = "2025.1/stable"
        feature_manifest.software.charms = {"barbican-k8s": charm_manifest}
        self.manifest.get_features.return_value = [("barbican", feature_manifest)]

        apps = {"barbican": ("barbican-k8s", "2024.1/stable", 123)}
        result = self.upgrader.is_track_changed_for_any_charm(apps)

        assert result is True

    def test_one_app_differs_returns_true(self):
        """Returns True on first track mismatch even if others are the same."""
        nova_charm = Mock()
        nova_charm.channel = "2024.1/stable"
        glance_charm = Mock()
        glance_charm.channel = "2025.1/stable"  # different track
        self.manifest.core.software.charms = {
            "nova-k8s": nova_charm,
            "glance-k8s": glance_charm,
        }

        apps = {
            "nova": ("nova-k8s", "2024.1/stable", 1),
            "glance": ("glance-k8s", "2024.1/stable", 2),
        }
        result = self.upgrader.is_track_changed_for_any_charm(apps)

        assert result is True
