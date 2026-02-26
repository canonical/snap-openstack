# SPDX-FileCopyrightText: 2023 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

from unittest.mock import Mock, patch

import click
from click.testing import CliRunner

from sunbeam.clusterd.service import ClusterServiceUnavailableException
from sunbeam.core.common import SunbeamException
from sunbeam.feature_manager import FeatureManager, list_features


@click.group()
@click.pass_context
def cli_group(ctx):
    """Top-level CLI for testing."""
    pass


cli_group.add_command(list_features)


class TestListFeatures:
    """Tests for the sunbeam list-features command."""

    def test_list_features_cluster_unavailable(self):
        """When cluster is unavailable, ClickException is raised with clear message."""
        deployment = Mock()
        deployment.get_client.side_effect = ClusterServiceUnavailableException(
            "not available"
        )

        runner = CliRunner()
        result = runner.invoke(cli_group, ["list-features"], obj=deployment)

        assert result.exit_code != 0
        assert "cluster service is not available" in result.output
        assert "bootstrapped cluster" in result.output

    def test_list_features_success(self):
        """Command runs successfully when cluster is available."""
        deployment = Mock()
        client = Mock()
        deployment.get_client.return_value = client
        feature_manager = Mock()
        feature_manager.features.return_value = {}
        deployment.get_feature_manager.return_value = feature_manager

        runner = CliRunner()
        result = runner.invoke(cli_group, ["list-features"], obj=deployment)

        assert result.exit_code == 0
        assert "Feature" in result.output
        assert "Enabled" in result.output
        assert "Experimental" in result.output


class TestFeatureRegistration:
    """Tests for feature registration with insufficient permissions."""

    @patch("sunbeam.feature_manager.Snap")
    @patch("sunbeam.feature_manager.infer_risk")
    def test_register_without_permissions(self, mock_infer_risk, mock_snap):
        """Feature registration should handle SunbeamException from get_client()."""
        # Setup mocks
        mock_infer_risk.return_value = 0
        mock_snap.return_value = Mock()

        # Create a mock feature with check_gated method
        mock_feature = Mock()
        mock_feature.name = "test-feature"
        mock_feature.risk_availability = 0
        mock_feature.check_gated.return_value = False  # Not gated
        mock_feature.is_enabled.side_effect = SunbeamException(
            "Insufficient permissions"
        )

        # Create feature manager with the mock feature
        feature_manager = FeatureManager()
        feature_manager._features = {"test-feature": mock_feature}
        feature_manager._groups = {}

        # Setup deployment that raises SunbeamException on get_client()
        deployment = Mock()
        deployment.get_client.side_effect = SunbeamException("Insufficient permissions")

        # Create a mock CLI group
        cli = click.Group()

        # This should not raise an exception
        feature_manager.register(cli, deployment)

        # Verify check_gated was called with None client
        mock_feature.check_gated.assert_called_once()
        call_args = mock_feature.check_gated.call_args
        assert call_args[1]["client"] is None

        # Verify feature.register was called with enabled=False
        mock_feature.register.assert_called_once_with(cli, {"enabled": False})
