# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

from unittest.mock import MagicMock

from sunbeam.hooks import (
    _check_feature_gate_dependencies,
    sync_feature_gates_from_snap_to_cluster,
)


class TestCheckFeatureGateDependencies:
    """Tests for _check_feature_gate_dependencies."""

    def test_loadbalancer_amphora_no_dependencies(self):
        """loadbalancer-amphora has no retired dependency."""
        flattened = {}
        result = _check_feature_gate_dependencies(
            "feature.loadbalancer-amphora",
            flattened,
        )
        assert result == []

    def test_multi_region_no_dependencies(self):
        """multi-region has no requires, returns empty list."""
        flattened = {}
        result = _check_feature_gate_dependencies("feature.multi-region", flattened)
        assert result == []

    def test_unknown_gate_key(self):
        """Unknown gate key returns empty list."""
        flattened = {"unknown": True}
        result = _check_feature_gate_dependencies("feature.nonexistent", flattened)
        assert result == []


class TestSyncFeatureGatesWithDependencies:
    """Tests for sync_feature_gates_from_snap_to_cluster with dependency validation."""

    def _make_client(self, existing_gates=None):
        """Create a mock client.

        :param existing_gates: dict mapping gate_key to enabled bool.
            If a gate_key is not in the dict, get_feature_gate raises Exception.
        """
        if existing_gates is None:
            existing_gates = {}

        client = MagicMock()

        def get_feature_gate(key):
            if key in existing_gates:
                gate = MagicMock()
                gate.enabled = existing_gates[key]
                return gate
            raise Exception("not found")

        client.cluster.get_feature_gate = MagicMock(side_effect=get_feature_gate)
        client.cluster.update_feature_gate = MagicMock()
        client.cluster.add_feature_gate = MagicMock()
        return client

    def _make_snap(self, feature_dict):
        """Create a mock snap with given feature config dict."""
        mock_snap = MagicMock()
        options = MagicMock()
        options.as_dict.return_value = feature_dict
        mock_snap.config.get_options.return_value = options
        return mock_snap

    def test_enable_loadbalancer_amphora_without_microovn_syncs(self):
        """Amphora gate syncs without a retired dependency."""
        client = self._make_client()
        snap = self._make_snap({"loadbalancer-amphora": True})

        sync_feature_gates_from_snap_to_cluster(client, snap)

        add_calls = client.cluster.add_feature_gate.call_args_list
        assert len(add_calls) == 1
        assert add_calls[0][0] == ("feature.loadbalancer-amphora", True)


class TestConfigureHookEnforcement:
    """Tests that configure() rejects invalid feature gate config."""

    def _make_snap(self, gate_values: dict[str, bool]) -> MagicMock:
        """Create a mock snap with specific gate values and required attrs."""
        mock_snap = MagicMock()

        def config_get(key):
            from snaphelpers import UnknownConfigKey

            if key in gate_values:
                return gate_values[key]
            raise UnknownConfigKey(key)

        mock_snap.config.get.side_effect = config_get

        # configure() also calls get_options and paths
        mock_snap.config.get_options.return_value = MagicMock(
            as_dict=MagicMock(return_value={})
        )
        mock_snap.paths.common = MagicMock()
        mock_snap.paths.data = MagicMock()
        return mock_snap

    def test_configure_accepts_valid_config(self):
        """configure() succeeds when dependencies are met."""
        from unittest.mock import patch

        from sunbeam.hooks import configure

        snap = self._make_snap({"feature.loadbalancer-amphora": True})

        # Patch _sync and file operations to avoid side effects
        with (
            patch("sunbeam.hooks._sync_feature_gates_to_cluster"),
            patch("sunbeam.hooks._read_config", return_value={}),
            patch("sunbeam.hooks._write_config"),
            patch("sunbeam.hooks.setup_logging"),
        ):
            configure(snap)
