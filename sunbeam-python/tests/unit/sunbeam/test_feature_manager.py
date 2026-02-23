# SPDX-FileCopyrightText: 2023 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

from unittest.mock import Mock

import click
from click.testing import CliRunner

from sunbeam.clusterd.service import ClusterServiceUnavailableException
from sunbeam.feature_manager import list_features


@click.group("enable")
@click.pass_context
def enable_group(ctx):
    """Enable features."""
    pass


enable_group.add_command(list_features)


class TestListFeatures:
    """Tests for the sunbeam enable list command."""

    def test_list_features_cluster_unavailable(self):
        """When cluster is unavailable, ClickException is raised with clear message."""
        deployment = Mock()
        deployment.get_client.side_effect = ClusterServiceUnavailableException(
            "not available"
        )

        runner = CliRunner()
        result = runner.invoke(enable_group, ["list"], obj=deployment)

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
        result = runner.invoke(enable_group, ["list"], obj=deployment)

        assert result.exit_code == 0
        assert "Feature" in result.output and "Enabled" in result.output
