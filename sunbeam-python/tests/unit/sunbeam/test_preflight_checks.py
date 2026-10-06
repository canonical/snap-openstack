# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Tests for upgrade preflight checks."""

from unittest.mock import MagicMock, patch

import pytest

from sunbeam.clusterd.models import FeatureGates
from sunbeam.upgrades.metadata import HopMetadata
from sunbeam.upgrades.preflight.capacity import CapacityCheck
from sunbeam.upgrades.preflight.checks import (
    CheckContext,
    ClusterHealthCheck,
    HopMetadataCheck,
    MySQLQuorumCheck,
    SnapVersionCheck,
    build_preflight_checks,
    run_upgrade_preflight_checks,
)

FROM = "2025.1"
TO = "2026.1"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_app(current: str, message: str = "") -> MagicMock:
    app = MagicMock()
    app.app_status.current = current
    app.app_status.message = message
    return app


@pytest.fixture
def mock_client():
    client = MagicMock()
    client.cluster.get_feature_gates.return_value = FeatureGates([])
    return client


@pytest.fixture
def mock_jhelper():
    return MagicMock()


@pytest.fixture
def mock_deployment(mock_client, mock_jhelper):
    deployment = MagicMock()
    deployment.openstack_machines_model = "openstack-machines"
    deployment.get_client.return_value = mock_client
    deployment.get_juju_helper.return_value = mock_jhelper
    return deployment


@pytest.fixture
def ctx(mock_deployment):
    return CheckContext(
        deployment=mock_deployment,
        from_release=FROM,
        to_release=TO,
    )


def _make_metadata(from_release: str = FROM, to_release: str = TO) -> HopMetadata:
    return HopMetadata.model_validate(
        {
            "from": from_release,
            "to": to_release,
            "control_plane_groups": [
                {"name": "identity-core", "apps": ["keystone-k8s"]}
            ],
        }
    )


def _model_status(apps: dict[str, MagicMock]) -> MagicMock:
    status = MagicMock()
    status.apps = apps
    return status


# ---------------------------------------------------------------------------
# SnapVersionCheck
# ---------------------------------------------------------------------------


class TestSnapVersionCheck:
    def test_passes_when_snap_matches_target(self, ctx):
        with patch(
            "sunbeam.upgrades.preflight.checks.detect_snap_release",
            return_value=TO,
        ):
            check = SnapVersionCheck(ctx)
            assert check.run() is True
            assert check.exit_code == 2

    def test_fails_when_snap_is_stale(self, ctx):
        with patch(
            "sunbeam.upgrades.preflight.checks.detect_snap_release",
            return_value=FROM,
        ):
            check = SnapVersionCheck(ctx)
            assert check.run() is False
            assert check.exit_code == 2
            assert TO in check.message
            assert FROM in check.message


# ---------------------------------------------------------------------------
# HopMetadataCheck (combined: hop validity + metadata present + compat)
# ---------------------------------------------------------------------------


class TestHopMetadataCheck:
    def test_passes_on_valid_hop_with_matching_metadata(self, ctx):
        metadata = _make_metadata()
        with patch(
            "sunbeam.upgrades.preflight.checks.load_upgrade_metadata",
            return_value=metadata,
        ):
            check = HopMetadataCheck(ctx)
            assert check.run() is True
            assert ctx.metadata is metadata
            assert check.exit_code == 2

    def test_fails_on_invalid_hop(self, ctx):
        ctx.from_release = "2024.1"
        ctx.to_release = "2026.1"
        check = HopMetadataCheck(ctx)
        assert check.run() is False
        assert check.exit_code == 2
        assert "2024.1" in check.message
        assert "2026.1" in check.message

    def test_fails_on_missing_metadata_file(self, ctx):
        with patch(
            "sunbeam.upgrades.preflight.checks.load_upgrade_metadata",
            side_effect=FileNotFoundError("not found"),
        ):
            check = HopMetadataCheck(ctx)
            assert check.run() is False
            assert check.exit_code == 2
            assert "not found" in check.message

    def test_fails_on_metadata_load_error(self, ctx):
        with patch(
            "sunbeam.upgrades.preflight.checks.load_upgrade_metadata",
            side_effect=ValueError("bad schema"),
        ):
            check = HopMetadataCheck(ctx)
            assert check.run() is False
            assert "bad schema" in check.message

    def test_fails_when_from_mismatches(self, ctx):
        with patch(
            "sunbeam.upgrades.preflight.checks.load_upgrade_metadata",
            return_value=_make_metadata("2024.1", TO),
        ):
            check = HopMetadataCheck(ctx)
            assert check.run() is False
            assert "2024.1" in check.message

    def test_fails_when_to_mismatches(self, ctx):
        with patch(
            "sunbeam.upgrades.preflight.checks.load_upgrade_metadata",
            return_value=_make_metadata(FROM, "2027.1"),
        ):
            check = HopMetadataCheck(ctx)
            assert check.run() is False
            assert "2027.1" in check.message

    def test_checks_hop_validity_before_metadata(self, ctx):
        """Hop validity is checked first — no metadata load for invalid hop."""
        ctx.from_release = "2024.1"
        ctx.to_release = "2026.1"
        with patch(
            "sunbeam.upgrades.preflight.checks.load_upgrade_metadata"
        ) as mock_load:
            check = HopMetadataCheck(ctx)
            assert check.run() is False
            mock_load.assert_not_called()


# ---------------------------------------------------------------------------
# ClusterHealthCheck (both openstack + machines models)
# ---------------------------------------------------------------------------


class TestClusterHealthCheck:
    def test_passes_when_all_apps_active_in_both_models(self, ctx):
        ctx.jhelper.get_model_status.side_effect = [
            _model_status({"keystone-k8s": _make_app("active")}),
            _model_status({"nova-compute": _make_app("active")}),
        ]
        check = ClusterHealthCheck(ctx)
        assert check.run() is True

    def test_passes_with_tolerated_blocked_message(self, ctx):
        with patch(
            "sunbeam.upgrades.preflight.checks.TOLERATED_BLOCKED_MESSAGES",
            {"Manual security enable required"},
        ):
            ctx.jhelper.get_model_status.side_effect = [
                _model_status(
                    {
                        "sunbeam-machine": _make_app(
                            "blocked", "Manual security enable required"
                        )
                    }
                ),
                _model_status({}),
            ]
            check = ClusterHealthCheck(ctx)
            assert check.run() is True

    def test_fails_on_unknown_blocked_message(self, ctx):
        ctx.jhelper.get_model_status.side_effect = [
            _model_status({"keystone-k8s": _make_app("blocked", "unknown reason")}),
            _model_status({}),
        ]
        check = ClusterHealthCheck(ctx)
        assert check.run() is False
        assert check.exit_code == 1
        assert "unknown reason" in check.message

    def test_fails_on_error_status(self, ctx):
        ctx.jhelper.get_model_status.side_effect = [
            _model_status({"nova-k8s": _make_app("error")}),
            _model_status({}),
        ]
        check = ClusterHealthCheck(ctx)
        assert check.run() is False
        assert "nova-k8s" in check.message
        assert "error" in check.message

    def test_fails_when_model_unreachable(self, ctx):
        ctx.jhelper.get_model_status.side_effect = Exception("connection lost")
        check = ClusterHealthCheck(ctx)
        assert check.run() is False
        assert "unreachable" in check.message
        assert "connection lost" in check.message

    def test_fails_on_unhealthy_app_in_machines_model(self, ctx):
        ctx.jhelper.get_model_status.side_effect = [
            _model_status({"keystone-k8s": _make_app("active")}),
            _model_status({"nova-compute": _make_app("blocked", "missing config")}),
        ]
        check = ClusterHealthCheck(ctx)
        assert check.run() is False
        assert "nova-compute" in check.message
        assert "missing config" in check.message


# ---------------------------------------------------------------------------
# MySQLQuorumCheck
# ---------------------------------------------------------------------------


class TestMySQLQuorumCheck:
    def test_passes_when_leader_present_and_action_succeeds(self, ctx):
        ctx.jhelper.get_leader_unit.return_value = "mysql-k8s/0"
        ctx.jhelper.run_action.return_value = {"cluster-status": "ok"}
        check = MySQLQuorumCheck(ctx)
        assert check.run() is True
        ctx.jhelper.run_action.assert_called_once_with(
            "mysql-k8s/0", "openstack", "get-cluster-status"
        )

    def test_fails_when_no_leader(self, ctx):
        ctx.jhelper.get_leader_unit.return_value = ""
        check = MySQLQuorumCheck(ctx)
        assert check.run() is False
        assert check.exit_code == 1
        assert "quorum" in check.message.lower()

    def test_fails_when_get_leader_raises(self, ctx):
        ctx.jhelper.get_leader_unit.side_effect = Exception("timeout")
        check = MySQLQuorumCheck(ctx)
        assert check.run() is False
        assert "timeout" in check.message

    def test_fails_when_action_raises(self, ctx):
        ctx.jhelper.get_leader_unit.return_value = "mysql-k8s/0"
        ctx.jhelper.run_action.side_effect = Exception("action failed")
        check = MySQLQuorumCheck(ctx)
        assert check.run() is False
        assert "action failed" in check.message

    def test_fails_when_action_returns_empty(self, ctx):
        ctx.jhelper.get_leader_unit.return_value = "mysql-k8s/0"
        ctx.jhelper.run_action.return_value = {}
        check = MySQLQuorumCheck(ctx)
        assert check.run() is False
        assert "no result" in check.message.lower()


# ---------------------------------------------------------------------------
# build_preflight_checks
# ---------------------------------------------------------------------------


class TestBuildPreflightChecks:
    def test_returns_five_checks_in_order(self, ctx):
        checks = build_preflight_checks(ctx)
        assert len(checks) == 5
        names = [type(c).__name__ for c in checks]
        assert names == [
            "SnapVersionCheck",
            "HopMetadataCheck",
            "ClusterHealthCheck",
            "CapacityCheck",
            "MySQLQuorumCheck",
        ]

    def test_all_checks_carry_exit_code(self, ctx):
        for check in build_preflight_checks(ctx):
            assert check.exit_code in (1, 2), f"{type(check).__name__} has no exit_code"

    def test_capacity_override_flag(self, ctx):
        checks = build_preflight_checks(ctx, capacity_override=True)
        capacity_check = next(c for c in checks if isinstance(c, CapacityCheck))
        assert capacity_check.override is True


# ---------------------------------------------------------------------------
# run_upgrade_preflight_checks
# ---------------------------------------------------------------------------


class TestRunUpgradePreflightChecks:
    def test_runs_all_when_passing(self, ctx):
        from rich.console import Console

        console = Console(record=True, width=80)
        checks = [
            SnapVersionCheck(ctx),
            HopMetadataCheck(ctx),
        ]
        with patch(
            "sunbeam.upgrades.preflight.checks.detect_snap_release",
            return_value=TO,
        ):
            with patch(
                "sunbeam.upgrades.preflight.checks.load_upgrade_metadata",
                return_value=_make_metadata(),
            ):
                run_upgrade_preflight_checks(checks, console)

    def test_short_circuits_on_first_failure(self, ctx):
        import click
        from rich.console import Console

        console = Console(record=True, width=80)
        failing = SnapVersionCheck(ctx)
        with patch(
            "sunbeam.upgrades.preflight.checks.detect_snap_release",
            return_value=FROM,
        ):
            with pytest.raises(click.ClickException) as exc_info:
                run_upgrade_preflight_checks([failing, HopMetadataCheck(ctx)], console)
        assert "exit 2" in str(exc_info.value)
