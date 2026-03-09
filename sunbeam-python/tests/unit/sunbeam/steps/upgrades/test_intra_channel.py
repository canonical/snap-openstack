# SPDX-FileCopyrightText: 2023 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

from unittest.mock import Mock, call, patch

from sunbeam.core.common import Result, ResultType
from sunbeam.core.juju import (
    ActionFailedException,
    ApplicationNotFoundException,
    JujuWaitException,
)
from sunbeam.core.openstack import OPENSTACK_MODEL
from sunbeam.steps.upgrades.intra_channel import (
    SNAP_APPS_INFRA_MODEL,
    SNAP_APPS_MACHINE_MODEL,
    LatestInChannel,
    LatestInChannelCoordinator,
    ReapplyInfraModelConfigStep,
    RefreshSnapStep,
)

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
            "nova": ("nova-k8s", "2026.1/stable", 123),
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
            "nova": ("nova-k8s", "2026.1/stable", 123),
        }
        model = "openstack"

        # Manifest charm with channel and revision
        manifest_charm = Mock()
        manifest_charm.channel = "2026.1/stable"
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
            channel="2026.1/stable",
            revision=150,
        )
        assert result.result_type == ResultType.COMPLETED

    def test_refresh_apps_with_manifest_channel_only(self):
        """Test refresh when manifest has only channel (no revision)."""
        # Setup
        apps = {
            "nova": ("nova-k8s", "2026.1/stable", 123),
        }
        model = "openstack"

        # Manifest charm with channel but no revision
        manifest_charm = Mock()
        manifest_charm.channel = "2026.1/stable"
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
            channel="2026.1/stable",
            revision=None,
        )
        assert result.result_type == ResultType.COMPLETED

    def test_refresh_apps_multiple_apps_mixed_manifest(self):
        """Test refresh with multiple apps, some in manifest, some not."""
        # Setup
        apps = {
            "nova": ("nova-k8s", "2026.1/stable", 123),
            "neutron": ("neutron-k8s", "2026.1/stable", 456),
            "cinder": ("cinder-k8s", "2026.1/stable", 789),
        }
        model = "openstack"

        # Only nova in manifest
        manifest_charm = Mock()
        manifest_charm.channel = "2026.1/candidate"
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
        assert call("nova", model, channel="2026.1/candidate", revision=200) in calls

        # Neutron and Cinder should be called without channel/revision
        assert call("neutron", model) in calls
        assert call("cinder", model) in calls

        assert result.result_type == ResultType.COMPLETED

    def test_refresh_apps_from_feature_manifest(self):
        """Test refresh when charm is in feature manifest, not core."""
        # Setup
        apps = {
            "barbican": ("barbican-k8s", "2026.1/stable", 123),
        }
        model = "openstack"

        # Not in core manifest
        self.manifest.core.software.charms = {}

        # In feature manifest
        feature_manifest = Mock()
        manifest_charm = Mock()
        manifest_charm.channel = "2026.1/stable"
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
            channel="2026.1/stable",
            revision=100,
        )
        assert result.result_type == ResultType.COMPLETED

    def test_refresh_apps_machine_model(self):
        """Test refresh for machine model apps."""
        # Setup
        apps = {
            "nova-compute": ("nova-compute", "2026.1/stable", 123),
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
            "nova": ("nova-k8s", "2026.1/stable", 123),
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
            "nova-compute": ("nova-compute", "2026.1/stable", 123),
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
        apps = {"nova": ("nova-k8s", "2026.1/stable", 123)}
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
        apps = {"nova": ("nova-k8s", "2026.1/stable", 123)}
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

    def test_k8s_model_build_overlay_dict_merged_with_pre_status(self):
        """K8s path: build_overlay_dict statuses are merged with pre-refresh status.

        If traefik was 'blocked' before the refresh, 'blocked' must appear in
        the accepted statuses alongside the base overlay's values so that the
        wait does not time-out when the charm is still blocked after refresh.
        """
        # build_overlay_dict returns a "status" for traefik
        traefik_overlay = {
            "status": ["active", "maintenance"],
            "agent_status": ["idle"],
        }
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
        # pre-refresh 'blocked' must be merged in, not dropped
        assert set(captured["traefik"]["status"]) == {
            "active",
            "maintenance",
            "blocked",
        }
        # non-status keys from build_overlay_dict must be preserved
        assert captured["traefik"]["agent_status"] == ["idle"]

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
        charm_manifest.channel = "2026.1/stable"
        self.manifest.core.software.charms = {"nova-k8s": charm_manifest}

        apps = {"nova": ("nova-k8s", "2026.1/edge", 123)}
        result = self.upgrader.is_track_changed_for_any_charm(apps)

        assert result is False

    def test_different_track_returns_true(self):
        """Returns True when manifest track differs from deployed track."""
        charm_manifest = Mock()
        charm_manifest.channel = "2026.1/stable"
        self.manifest.core.software.charms = {"nova-k8s": charm_manifest}

        apps = {"nova": ("nova-k8s", "2025.1/stable", 123)}
        result = self.upgrader.is_track_changed_for_any_charm(apps)

        assert result is True

    def test_charm_not_in_manifest_skipped(self):
        """Charms absent from manifest are skipped; returns False if none differ."""
        self.manifest.core.software.charms = {}
        self.manifest.get_features.return_value = []

        apps = {"nova": ("nova-k8s", "2026.1/stable", 123)}
        result = self.upgrader.is_track_changed_for_any_charm(apps)

        assert result is False

    def test_charm_in_feature_manifest_track_differs(self):
        """Detects track change when charm is in a feature manifest."""
        self.manifest.core.software.charms = {}

        feature_manifest = Mock()
        charm_manifest = Mock()
        charm_manifest.channel = "2026.1/stable"
        feature_manifest.software.charms = {"barbican-k8s": charm_manifest}
        self.manifest.get_features.return_value = [("barbican", feature_manifest)]

        apps = {"barbican": ("barbican-k8s", "2025.1/stable", 123)}
        result = self.upgrader.is_track_changed_for_any_charm(apps)

        assert result is True

    def test_one_app_differs_returns_true(self):
        """Returns True on first track mismatch even if others are the same."""
        nova_charm = Mock()
        nova_charm.channel = "2026.1/stable"
        glance_charm = Mock()
        glance_charm.channel = "2026.1/stable"
        self.manifest.core.software.charms = {
            "nova-k8s": nova_charm,
            "glance-k8s": glance_charm,
        }

        apps = {
            "nova": ("nova-k8s", "2025.1/stable", 1),
            "glance": ("glance-k8s", "2025.1/stable", 2),
        }
        result = self.upgrader.is_track_changed_for_any_charm(apps)

        assert result is True


class TestLatestInChannelRun:
    """Tests for the LatestInChannel.run() method, including MAAS infra model."""

    def setup_method(self):
        """Set up test fixtures."""
        self.deployment = Mock()
        self.deployment.openstack_machines_model = "openstack-machines"
        self.jhelper = Mock()
        self.manifest = Mock()
        self.manifest.core.software.charms = {}
        self.manifest.get_features.return_value = []

        self.upgrader = LatestInChannel(self.deployment, self.jhelper, self.manifest)

    @patch(f"{_INTRA_CHANNEL}.is_maas_deployment")
    def test_run_local_deployment_does_not_refresh_infra_model(self, mock_is_maas):
        """Local deployment should NOT discover or refresh infra model apps."""
        mock_is_maas.return_value = False

        k8s_apps = {"nova": ("nova-k8s", "2026.1/stable", 123)}
        machine_apps = {"sunbeam-machine": ("sunbeam-machine", "2026.1/stable", 10)}

        # Call order: k8s, machines (no infra for local)
        self.upgrader.get_charm_deployed_versions = Mock(
            side_effect=[k8s_apps, machine_apps]
        )
        self.upgrader.refresh_apps = Mock(return_value=Result(ResultType.COMPLETED))

        result = self.upgrader.run()

        assert result.result_type == ResultType.COMPLETED
        # get_charm_deployed_versions called only for k8s and machines models
        assert self.upgrader.get_charm_deployed_versions.call_count == 2
        # refresh_apps called only for k8s and machines models
        assert self.upgrader.refresh_apps.call_count == 2
        self.upgrader.refresh_apps.assert_any_call(k8s_apps, "openstack", None)
        self.upgrader.refresh_apps.assert_any_call(
            machine_apps, "openstack-machines", None
        )

    @patch(f"{_INTRA_CHANNEL}.is_maas_deployment")
    def test_run_maas_deployment_discovers_and_refreshes_infra_model(
        self, mock_is_maas
    ):
        """MAAS deployment should discover and refresh infra model apps."""
        mock_is_maas.return_value = True
        self.deployment.infra_model = "openstack-infra"

        k8s_apps = {"nova": ("nova-k8s", "2026.1/stable", 123)}
        machine_apps = {"sunbeam-machine": ("sunbeam-machine", "2026.1/stable", 10)}
        infra_apps = {
            "sunbeam-clusterd": ("sunbeam-clusterd", "2026.1/stable", 5),
            "tls-operator": ("self-signed-certificates", "latest/stable", 20),
        }

        # Call order: infra, k8s, machines
        self.upgrader.get_charm_deployed_versions = Mock(
            side_effect=[infra_apps, k8s_apps, machine_apps]
        )
        self.upgrader.refresh_apps = Mock(return_value=Result(ResultType.COMPLETED))

        result = self.upgrader.run()

        assert result.result_type == ResultType.COMPLETED
        # get_charm_deployed_versions called for infra, k8s, AND machines models
        assert self.upgrader.get_charm_deployed_versions.call_count == 3
        self.upgrader.get_charm_deployed_versions.assert_any_call("openstack-infra")
        # refresh_apps called for all 3 models: infra, k8s, machines
        assert self.upgrader.refresh_apps.call_count == 3
        self.upgrader.refresh_apps.assert_any_call(infra_apps, "openstack-infra", None)
        self.upgrader.refresh_apps.assert_any_call(k8s_apps, "openstack", None)
        self.upgrader.refresh_apps.assert_any_call(
            machine_apps, "openstack-machines", None
        )

    @patch(f"{_INTRA_CHANNEL}.is_maas_deployment")
    def test_run_maas_infra_apps_included_in_track_check(self, mock_is_maas):
        """MAAS infra model apps should be included in track change detection."""
        mock_is_maas.return_value = True
        self.deployment.infra_model = "openstack-infra"

        k8s_apps = {"nova": ("nova-k8s", "2025.1/stable", 123)}
        machine_apps = {}
        infra_apps = {
            "sunbeam-clusterd": ("sunbeam-clusterd", "2025.1/stable", 5),
        }

        # Call order: infra, k8s, machines
        self.upgrader.get_charm_deployed_versions = Mock(
            side_effect=[infra_apps, k8s_apps, machine_apps]
        )

        # Simulate track change detected (manifest track differs from deployed)
        manifest_charm = Mock()
        manifest_charm.channel = "2026.1/stable"
        self.manifest.core.software.charms = {"sunbeam-clusterd": manifest_charm}

        result = self.upgrader.run()

        assert result.result_type == ResultType.FAILED
        assert "upgrade-release" in result.message

    @patch(f"{_INTRA_CHANNEL}.is_maas_deployment")
    def test_run_maas_infra_refresh_failure_halts_execution(self, mock_is_maas):
        """If infra model refresh fails, the run should return failure."""
        mock_is_maas.return_value = True
        self.deployment.infra_model = "openstack-infra"

        k8s_apps = {"nova": ("nova-k8s", "2026.1/stable", 123)}
        machine_apps = {"sunbeam-machine": ("sunbeam-machine", "2026.1/stable", 10)}
        infra_apps = {
            "sunbeam-clusterd": ("sunbeam-clusterd", "2026.1/stable", 5),
        }

        # Call order: infra, k8s, machines
        self.upgrader.get_charm_deployed_versions = Mock(
            side_effect=[infra_apps, k8s_apps, machine_apps]
        )

        # Refresh order: infra fails immediately
        self.upgrader.refresh_apps = Mock(
            return_value=Result(ResultType.FAILED, "infra refresh timed out")
        )

        result = self.upgrader.run()

        assert result.result_type == ResultType.FAILED
        assert "infra refresh timed out" in result.message
        # Only infra refresh attempted before halting
        assert self.upgrader.refresh_apps.call_count == 1

    @patch(f"{_INTRA_CHANNEL}.is_maas_deployment")
    def test_run_maas_k8s_failure_skips_machines_refresh(self, mock_is_maas):
        """If k8s model refresh fails, machines refresh is skipped."""
        mock_is_maas.return_value = True
        self.deployment.infra_model = "openstack-infra"

        k8s_apps = {"nova": ("nova-k8s", "2026.1/stable", 123)}
        machine_apps = {"sunbeam-machine": ("sunbeam-machine", "2026.1/stable", 10)}
        infra_apps = {
            "sunbeam-clusterd": ("sunbeam-clusterd", "2026.1/stable", 5),
        }

        # Call order: infra, k8s, machines
        self.upgrader.get_charm_deployed_versions = Mock(
            side_effect=[infra_apps, k8s_apps, machine_apps]
        )

        # Refresh order: infra succeeds, k8s fails
        self.upgrader.refresh_apps = Mock(
            side_effect=[
                Result(ResultType.COMPLETED),  # infra
                Result(ResultType.FAILED, "k8s refresh failed"),  # k8s
            ]
        )

        result = self.upgrader.run()

        assert result.result_type == ResultType.FAILED
        # refresh_apps called for infra (ok) then k8s (fail), machines skipped
        assert self.upgrader.refresh_apps.call_count == 2

    @patch(f"{_INTRA_CHANNEL}.is_maas_deployment")
    def test_run_maas_empty_infra_model_is_handled(self, mock_is_maas):
        """MAAS with no apps in infra model should still succeed."""
        mock_is_maas.return_value = True
        self.deployment.infra_model = "openstack-infra"

        k8s_apps = {"nova": ("nova-k8s", "2026.1/stable", 123)}
        machine_apps = {}
        infra_apps = {}  # empty infra model

        # Call order: infra, k8s, machines
        self.upgrader.get_charm_deployed_versions = Mock(
            side_effect=[infra_apps, k8s_apps, machine_apps]
        )
        self.upgrader.refresh_apps = Mock(return_value=Result(ResultType.COMPLETED))

        result = self.upgrader.run()

        assert result.result_type == ResultType.COMPLETED
        # refresh_apps called for infra (empty), k8s, machines
        assert self.upgrader.refresh_apps.call_count == 3


class TestLatestInChannelCoordinator:
    """Tests for the LatestInChannelCoordinator.get_plan() method."""

    def setup_method(self):
        """Set up test fixtures."""
        self.deployment = Mock()
        self.deployment.openstack_machines_model = "openstack-machines"
        self.deployment.get_tfhelper = Mock(return_value=Mock())
        self.deployment.get_ovn_manager = Mock(return_value=Mock())
        ovn_manager = self.deployment.get_ovn_manager()
        ovn_manager.get_roles_for_microovn.return_value = []
        self.client = Mock()
        self.client.cluster.list_nodes_by_role.return_value = []
        self.jhelper = Mock()
        self.manifest = Mock()

    @patch(f"{_INTRA_CHANNEL}.is_maas_deployment")
    def test_get_plan_local_excludes_lb_ip_pool_step(self, mock_is_maas):
        """Local deployment plan should NOT include LB IP pool step."""
        mock_is_maas.return_value = False

        coordinator = LatestInChannelCoordinator(
            self.deployment, self.client, self.jhelper, self.manifest
        )
        plan = coordinator.get_plan()

        from sunbeam.steps.openstack import (
            OpenStackPatchLoadBalancerServicesIPPoolStep,
        )

        step_types = [type(step) for step in plan]
        assert OpenStackPatchLoadBalancerServicesIPPoolStep not in step_types

    @patch(f"{_INTRA_CHANNEL}.is_maas_deployment")
    def test_get_plan_maas_includes_lb_ip_pool_step(self, mock_is_maas):
        """MAAS deployment plan should include LB IP pool and pool management steps."""
        mock_is_maas.return_value = True
        self.deployment.public_api_label = "test-public-api"

        mock_maas_client = Mock()
        mock_maas_client_module = Mock()
        mock_maas_client_module.MaasClient.from_deployment.return_value = (
            mock_maas_client
        )

        mock_maas_deploy_k8s_cls = Mock()
        mock_maas_steps_module = Mock()
        mock_maas_steps_module.MaasDeployK8SApplicationStep = mock_maas_deploy_k8s_cls

        import sys

        with patch.dict(
            sys.modules,
            {
                "sunbeam.provider.maas.client": mock_maas_client_module,
                "sunbeam.provider.maas.steps": mock_maas_steps_module,
            },
        ):
            coordinator = LatestInChannelCoordinator(
                self.deployment, self.client, self.jhelper, self.manifest
            )
            plan = coordinator.get_plan()

        from sunbeam.steps.k8s import EnsureDefaultL2AdvertisementMutedStep
        from sunbeam.steps.openstack import (
            OpenStackPatchLoadBalancerServicesIPPoolStep,
        )

        step_types = [type(step) for step in plan]
        assert OpenStackPatchLoadBalancerServicesIPPoolStep in step_types
        assert EnsureDefaultL2AdvertisementMutedStep in step_types
        # MaasDeployK8SApplicationStep was called; its return value is in the plan
        assert mock_maas_deploy_k8s_cls.return_value in plan

    @patch(f"{_INTRA_CHANNEL}.is_maas_deployment")
    def test_get_plan_always_includes_core_steps(self, mock_is_maas):
        """Both local and MAAS plans should include all core refresh steps."""
        mock_is_maas.return_value = False

        coordinator = LatestInChannelCoordinator(
            self.deployment, self.client, self.jhelper, self.manifest
        )
        plan = coordinator.get_plan()

        from sunbeam.steps.upgrades.base import UpgradeFeatures

        step_types = [type(step) for step in plan]
        assert LatestInChannel in step_types
        assert ReapplyInfraModelConfigStep in step_types
        assert UpgradeFeatures in step_types


class TestReapplyInfraModelConfigStep:
    """Tests for ReapplyInfraModelConfigStep."""

    def setup_method(self):
        """Set up test fixtures."""
        self.deployment = Mock()
        self.deployment.infra_model = "openstack-infra"
        self.jhelper = Mock()
        self.manifest = Mock()

    @patch(f"{_INTRA_CHANNEL}.is_maas_deployment")
    def test_is_skip_local_deployment(self, mock_is_maas):
        """Local deployment should skip this step."""
        mock_is_maas.return_value = False
        step = ReapplyInfraModelConfigStep(self.deployment, self.jhelper, self.manifest)
        result = step.is_skip()
        assert result.result_type == ResultType.SKIPPED

    @patch(f"{_INTRA_CHANNEL}.is_maas_deployment")
    def test_is_skip_maas_deployment(self, mock_is_maas):
        """MAAS deployment should not skip this step."""
        mock_is_maas.return_value = True
        step = ReapplyInfraModelConfigStep(self.deployment, self.jhelper, self.manifest)
        result = step.is_skip()
        assert result.result_type == ResultType.COMPLETED

    def test_run_applies_manifest_config(self):
        """Config from manifest is applied to infra model apps."""
        clusterd_manifest = Mock()
        clusterd_manifest.config = {"snap-channel": "2026.1/stable", "debug": "true"}

        certs_manifest = Mock()
        certs_manifest.config = {"ca-common-name": "sunbeam"}

        def get_charm(name):
            if name == "sunbeam-clusterd":
                return clusterd_manifest
            if name == "self-signed-certificates":
                return certs_manifest
            return None

        self.manifest.core.software.charms.get = Mock(side_effect=get_charm)

        step = ReapplyInfraModelConfigStep(self.deployment, self.jhelper, self.manifest)
        result = step.run()

        assert result.result_type == ResultType.COMPLETED
        assert self.jhelper.set_app_config.call_count == 2
        self.jhelper.set_app_config.assert_any_call(
            "sunbeam-clusterd",
            "openstack-infra",
            {"snap-channel": "2026.1/stable", "debug": "true"},
        )
        self.jhelper.set_app_config.assert_any_call(
            "tls-operator",
            "openstack-infra",
            {"ca-common-name": "sunbeam"},
        )

    def test_run_skips_app_with_no_manifest_config(self):
        """Apps without manifest config are skipped."""
        clusterd_manifest = Mock()
        clusterd_manifest.config = None  # No config

        certs_manifest = Mock()
        certs_manifest.config = {"ca-common-name": "sunbeam"}

        def get_charm(name):
            if name == "sunbeam-clusterd":
                return clusterd_manifest
            if name == "self-signed-certificates":
                return certs_manifest
            return None

        self.manifest.core.software.charms.get = Mock(side_effect=get_charm)

        step = ReapplyInfraModelConfigStep(self.deployment, self.jhelper, self.manifest)
        result = step.run()

        assert result.result_type == ResultType.COMPLETED
        # Only tls-operator config applied
        self.jhelper.set_app_config.assert_called_once_with(
            "tls-operator",
            "openstack-infra",
            {"ca-common-name": "sunbeam"},
        )

    def test_run_skips_app_not_in_manifest(self):
        """Apps not in manifest are skipped."""
        self.manifest.core.software.charms.get = Mock(return_value=None)

        step = ReapplyInfraModelConfigStep(self.deployment, self.jhelper, self.manifest)
        result = step.run()

        assert result.result_type == ResultType.COMPLETED
        self.jhelper.set_app_config.assert_not_called()

    def test_run_empty_config_dict_skipped(self):
        """Apps with empty config dict are skipped."""
        clusterd_manifest = Mock()
        clusterd_manifest.config = {}  # Empty dict is falsy

        self.manifest.core.software.charms.get = Mock(return_value=clusterd_manifest)

        step = ReapplyInfraModelConfigStep(self.deployment, self.jhelper, self.manifest)
        result = step.run()

        assert result.result_type == ResultType.COMPLETED
        self.jhelper.set_app_config.assert_not_called()


class TestRefreshSnapStep:
    """Tests for RefreshSnapStep."""

    def setup_method(self):
        self.deployment = Mock()
        self.deployment.openstack_machines_model = "openstack-machines"
        self.jhelper = Mock()

    def _make_application(self, unit_names: list[str]) -> Mock:
        """Return a Mock application whose .units dict maps names to Mock units."""
        app = Mock()
        app.units = {name: Mock() for name in unit_names}
        return app

    # ------------------------------------------------------------------
    # _refresh_snap_for_apps
    # ------------------------------------------------------------------

    def test_skips_app_not_deployed(self):
        """Application not found in model is silently skipped."""
        self.jhelper.get_application.side_effect = ApplicationNotFoundException(
            "not found"
        )
        step = RefreshSnapStep(self.deployment, self.jhelper)

        result = step._refresh_snap_for_apps(
            ["openstack-hypervisor"], "openstack-machines"
        )

        assert result.result_type == ResultType.COMPLETED
        self.jhelper.run_action.assert_not_called()

    def test_runs_action_on_all_units(self):
        """refresh-snap is called once per unit for each app."""
        self.jhelper.get_application.return_value = self._make_application(
            ["openstack-hypervisor/0", "openstack-hypervisor/1"]
        )
        step = RefreshSnapStep(self.deployment, self.jhelper)

        result = step._refresh_snap_for_apps(
            ["openstack-hypervisor"], "openstack-machines"
        )

        assert result.result_type == ResultType.COMPLETED
        assert self.jhelper.run_action.call_count == 2
        for unit in ("openstack-hypervisor/0", "openstack-hypervisor/1"):
            self.jhelper.run_action.assert_any_call(
                unit, "openstack-machines", "refresh-snap", timeout=600
            )

    def test_returns_failed_when_action_fails(self):
        """A failed action on any unit returns FAILED immediately."""
        self.jhelper.get_application.return_value = self._make_application(
            ["openstack-hypervisor/0", "openstack-hypervisor/1"]
        )
        self.jhelper.run_action.side_effect = ActionFailedException("snap error")
        step = RefreshSnapStep(self.deployment, self.jhelper)

        result = step._refresh_snap_for_apps(
            ["openstack-hypervisor"], "openstack-machines"
        )

        assert result.result_type == ResultType.FAILED
        assert "snap error" in result.message
        # Stopped after first unit failure
        assert self.jhelper.run_action.call_count == 1

    def test_multiple_apps_all_refreshed(self):
        """All apps in the list have refresh-snap run on their units."""

        def get_app(name, model):
            return self._make_application([f"{name}/0"])

        self.jhelper.get_application.side_effect = get_app
        step = RefreshSnapStep(self.deployment, self.jhelper)

        result = step._refresh_snap_for_apps(
            ["openstack-hypervisor", "cinder-volume"], "openstack-machines"
        )

        assert result.result_type == ResultType.COMPLETED
        assert self.jhelper.run_action.call_count == 2

    def test_partial_deployment_skips_missing_apps(self):
        """Apps not deployed are skipped; deployed apps are still refreshed."""

        def get_app(name, model):
            if name == "cinder-volume":
                raise ApplicationNotFoundException("not deployed")
            return self._make_application([f"{name}/0"])

        self.jhelper.get_application.side_effect = get_app
        step = RefreshSnapStep(self.deployment, self.jhelper)

        result = step._refresh_snap_for_apps(
            ["openstack-hypervisor", "cinder-volume"], "openstack-machines"
        )

        assert result.result_type == ResultType.COMPLETED
        # Only openstack-hypervisor/0 should be refreshed
        self.jhelper.run_action.assert_called_once_with(
            "openstack-hypervisor/0", "openstack-machines", "refresh-snap", timeout=600
        )

    def test_empty_app_list_returns_completed(self):
        """Empty app list does nothing and returns COMPLETED."""
        step = RefreshSnapStep(self.deployment, self.jhelper)

        result = step._refresh_snap_for_apps([], "openstack-machines")

        assert result.result_type == ResultType.COMPLETED
        self.jhelper.get_application.assert_not_called()

    # ------------------------------------------------------------------
    # run()
    # ------------------------------------------------------------------

    @patch(f"{_INTRA_CHANNEL}.is_maas_deployment")
    def test_run_local_refreshes_machine_model_only(self, mock_is_maas):
        """Local deployment refreshes only the machines model."""
        mock_is_maas.return_value = False
        self.jhelper.get_application.side_effect = ApplicationNotFoundException("x")
        step = RefreshSnapStep(self.deployment, self.jhelper)

        result = step.run()

        assert result.result_type == ResultType.COMPLETED
        # All calls are for the machines model
        for c in self.jhelper.get_application.call_args_list:
            assert c.args[1] == "openstack-machines"

    @patch(f"{_INTRA_CHANNEL}.is_maas_deployment")
    def test_run_maas_also_refreshes_infra_model(self, mock_is_maas):
        """MAAS deployment also refreshes the infra model apps."""
        mock_is_maas.return_value = True
        self.deployment.infra_model = "openstack-infra"

        called_models: list[str] = []

        def get_app(name, model):
            called_models.append(model)
            raise ApplicationNotFoundException("not deployed")

        self.jhelper.get_application.side_effect = get_app
        step = RefreshSnapStep(self.deployment, self.jhelper)

        result = step.run()

        assert result.result_type == ResultType.COMPLETED
        assert "openstack-machines" in called_models
        assert "openstack-infra" in called_models

    @patch(f"{_INTRA_CHANNEL}.is_maas_deployment")
    def test_run_machine_failure_halts_before_infra(self, mock_is_maas):
        """Failure in machines model prevents infra model refresh."""
        mock_is_maas.return_value = True
        self.deployment.infra_model = "openstack-infra"

        self.jhelper.get_application.return_value = self._make_application(
            ["openstack-hypervisor/0"]
        )
        self.jhelper.run_action.side_effect = ActionFailedException("disk full")
        step = RefreshSnapStep(self.deployment, self.jhelper)

        result = step.run()

        assert result.result_type == ResultType.FAILED
        assert "disk full" in result.message
        # No infra-model get_application calls should have happened
        infra_calls = [
            c
            for c in self.jhelper.get_application.call_args_list
            if c.args[1] == "openstack-infra"
        ]
        assert infra_calls == []

    @patch(f"{_INTRA_CHANNEL}.is_maas_deployment")
    def test_run_uses_correct_snap_app_lists(self, mock_is_maas):
        """run() passes SNAP_APPS_MACHINE_MODEL and SNAP_APPS_INFRA_MODEL."""
        mock_is_maas.return_value = True
        self.deployment.infra_model = "openstack-infra"

        queried: dict[str, list[str]] = {
            "openstack-machines": [],
            "openstack-infra": [],
        }

        def get_app(name, model):
            queried[model].append(name)
            raise ApplicationNotFoundException("not deployed")

        self.jhelper.get_application.side_effect = get_app
        step = RefreshSnapStep(self.deployment, self.jhelper)
        step.run()

        assert set(queried["openstack-machines"]) == set(SNAP_APPS_MACHINE_MODEL)
        assert set(queried["openstack-infra"]) == set(SNAP_APPS_INFRA_MODEL)

    @patch(f"{_INTRA_CHANNEL}.is_maas_deployment")
    def test_get_plan_includes_refresh_snap_step(self, mock_is_maas):
        """LatestInChannelCoordinator plan includes RefreshSnapStep."""
        mock_is_maas.return_value = False

        deployment = Mock()
        deployment.openstack_machines_model = "openstack-machines"
        deployment.get_tfhelper = Mock(return_value=Mock())
        ovn_manager = Mock()
        ovn_manager.get_roles_for_microovn.return_value = []
        deployment.get_ovn_manager = Mock(return_value=ovn_manager)
        client = Mock()
        client.cluster.list_nodes_by_role.return_value = []
        jhelper = Mock()
        manifest = Mock()

        coordinator = LatestInChannelCoordinator(deployment, client, jhelper, manifest)
        plan = coordinator.get_plan()

        step_types = [type(s) for s in plan]
        assert RefreshSnapStep in step_types

    @patch(f"{_INTRA_CHANNEL}.is_maas_deployment")
    def test_refresh_snap_step_placed_after_charm_refresh(self, mock_is_maas):
        """RefreshSnapStep must appear after LatestInChannel in the plan."""
        mock_is_maas.return_value = False

        deployment = Mock()
        deployment.openstack_machines_model = "openstack-machines"
        deployment.get_tfhelper = Mock(return_value=Mock())
        ovn_manager = Mock()
        ovn_manager.get_roles_for_microovn.return_value = []
        deployment.get_ovn_manager = Mock(return_value=ovn_manager)
        client = Mock()
        client.cluster.list_nodes_by_role.return_value = []
        jhelper = Mock()
        manifest = Mock()

        coordinator = LatestInChannelCoordinator(deployment, client, jhelper, manifest)
        plan = coordinator.get_plan()

        step_types = [type(s) for s in plan]
        assert step_types.index(RefreshSnapStep) > step_types.index(LatestInChannel)
