# SPDX-FileCopyrightText: 2023 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

from unittest.mock import Mock, patch

import click
import pytest
from click.testing import CliRunner

from sunbeam.clusterd.service import ClusterServiceUnavailableException
from sunbeam.core.common import SunbeamException
from sunbeam.core.juju import ApplicationReadiness
from sunbeam.feature_manager import FeatureManager, list_feature_gates, list_features
from sunbeam.features.interface.v1.base import EnableDisableFeature
from sunbeam.features.vault.feature import VaultFeature


@click.group()
@click.pass_context
def cli_group(ctx):
    """Top-level CLI for testing."""
    pass


cli_group.add_command(list_features)
cli_group.add_command(list_feature_gates)


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
        assert "Feature" in result.output and "Enabled" in result.output


class TestFeatureRegistration:
    """Tests for feature registration with insufficient permissions."""

    @patch("sunbeam.feature_manager.Snap")
    def test_register_without_permissions(self, mock_snap):
        """Feature registration should handle SunbeamException from get_client()."""
        # Setup mocks
        mock_snap.return_value = Mock()

        # Create a mock feature with check_gated method
        mock_feature = Mock()
        mock_feature.name = "test-feature"
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


class TestListFeatureGates:
    """Tests for the sunbeam list-feature-gates command."""

    @patch("sunbeam.feature_manager.Snap")
    @patch(
        "sunbeam.feature_manager.FEATURE_GATES",
        {
            "feature.test-gate": {"generally_available": False},
        },
    )
    def test_list_feature_gates_cluster_unavailable(self, mock_snap_class):
        """When cluster is unavailable, command still works using snap config only."""
        deployment = Mock()
        deployment.get_client.side_effect = ClusterServiceUnavailableException(
            "not available"
        )

        # Mock snap instance
        mock_snap = Mock()
        mock_snap.config.get.return_value = False
        mock_snap_class.return_value = mock_snap

        # Mock storage and feature managers
        storage_manager = Mock()
        storage_manager.backends.return_value = {}
        deployment.get_storage_manager.return_value = storage_manager

        feature_manager = Mock()
        feature_manager.features.return_value = {}
        deployment.get_feature_manager.return_value = feature_manager

        runner = CliRunner()
        result = runner.invoke(cli_group, ["list-feature-gates"], obj=deployment)

        assert result.exit_code == 0
        assert "Gate Key" in result.output
        assert "Unlocked" in result.output
        assert "feature.test-gate" in result.output

    @patch("sunbeam.feature_manager.Snap")
    @patch(
        "sunbeam.feature_manager.FEATURE_GATES",
        {
            "feature.test-gate": {"generally_available": False},
            "feature.ga-gate": {"generally_available": True},  # Should not appear
        },
    )
    def test_list_feature_gates_filters_ga_gates(self, mock_snap_class):
        """GA feature gates should not appear in the list."""
        deployment = Mock()
        client = Mock()
        deployment.get_client.return_value = client

        # Mock cluster gate retrieval - no gates in DB
        client.cluster.get_feature_gate.side_effect = Exception("Not found")

        # Mock snap instance
        mock_snap = Mock()
        mock_snap.config.get.return_value = False
        mock_snap_class.return_value = mock_snap

        # Mock storage and feature managers
        storage_manager = Mock()
        storage_manager.backends.return_value = {}
        deployment.get_storage_manager.return_value = storage_manager

        feature_manager = Mock()
        feature_manager.features.return_value = {}
        deployment.get_feature_manager.return_value = feature_manager

        runner = CliRunner()
        result = runner.invoke(cli_group, ["list-feature-gates"], obj=deployment)

        assert result.exit_code == 0
        assert "feature.test-gate" in result.output
        assert "feature.ga-gate" not in result.output

    @patch("sunbeam.feature_manager.Snap")
    @patch(
        "sunbeam.feature_manager.FEATURE_GATES",
        {
            "feature.test-gate": {"generally_available": False},
        },
    )
    def test_list_feature_gates_checks_cluster_db_first(self, mock_snap_class):
        """When cluster DB is available, it should be checked before snap config."""
        deployment = Mock()
        client = Mock()
        deployment.get_client.return_value = client

        # Mock cluster gate retrieval - gate enabled in DB
        mock_gate = Mock()
        mock_gate.enabled = True
        client.cluster.get_feature_gate.return_value = mock_gate

        # Mock snap instance (should not be checked if DB works)
        mock_snap = Mock()
        mock_snap_class.return_value = mock_snap

        # Mock storage and feature managers
        storage_manager = Mock()
        storage_manager.backends.return_value = {}
        deployment.get_storage_manager.return_value = storage_manager

        feature_manager = Mock()
        feature_manager.features.return_value = {}
        deployment.get_feature_manager.return_value = feature_manager

        runner = CliRunner()
        result = runner.invoke(cli_group, ["list-feature-gates"], obj=deployment)

        assert result.exit_code == 0
        # Should show as unlocked (enabled in DB)
        lines = result.output.split("\n")
        # Find the line with feature.test-gate and check for X
        gate_line = [line for line in lines if "feature.test-gate" in line]
        assert len(gate_line) > 0
        assert "X" in gate_line[0]  # Should have X in Unlocked column

    @patch("sunbeam.feature_manager.Snap")
    @patch("sunbeam.feature_manager.FEATURE_GATES", {})
    def test_list_feature_gates_includes_storage_backends(self, mock_snap_class):
        """Storage backends with generally_available=False should be included."""
        deployment = Mock()
        client = Mock()
        deployment.get_client.return_value = client

        # Mock snap instance
        mock_snap = Mock()
        mock_snap.config.get.return_value = True  # Backend gate unlocked
        mock_snap_class.return_value = mock_snap

        # Mock storage backend
        from sunbeam.feature_gates import FeatureGateMixin

        mock_backend = Mock(spec=FeatureGateMixin)
        mock_backend.generally_available = False
        mock_backend.gate_key = "feature.storage.test-backend"
        mock_backend.backend_type = "test-backend"

        storage_manager = Mock()
        storage_manager.backends.return_value = {"test-backend": mock_backend}
        deployment.get_storage_manager.return_value = storage_manager

        feature_manager = Mock()
        feature_manager.features.return_value = {}
        deployment.get_feature_manager.return_value = feature_manager

        runner = CliRunner()
        result = runner.invoke(cli_group, ["list-feature-gates"], obj=deployment)

        assert result.exit_code == 0
        assert "feature.storage.test-backend" in result.output
        assert "storage-backend" in result.output

    @patch("sunbeam.feature_manager.Snap")
    @patch("sunbeam.feature_manager.FEATURE_GATES", {})
    def test_list_feature_gates_includes_features(self, mock_snap_class):
        """Features with generally_available=False should be included."""
        deployment = Mock()
        client = Mock()
        deployment.get_client.return_value = client

        # Mock snap instance
        mock_snap = Mock()
        mock_snap.config.get.return_value = False
        mock_snap_class.return_value = mock_snap

        # Mock feature
        from sunbeam.feature_gates import FeatureGateMixin

        mock_feature = Mock(spec=FeatureGateMixin)
        mock_feature.generally_available = False
        mock_feature.gate_key = "feature.test-feature"
        mock_feature.name = "test-feature"

        storage_manager = Mock()
        storage_manager.backends.return_value = {}
        deployment.get_storage_manager.return_value = storage_manager

        feature_manager = Mock()
        feature_manager.features.return_value = {"test-feature": mock_feature}
        deployment.get_feature_manager.return_value = feature_manager

        runner = CliRunner()
        result = runner.invoke(cli_group, ["list-feature-gates"], obj=deployment)

        assert result.exit_code == 0
        assert "feature.test-feature" in result.output

    @patch("sunbeam.feature_manager.Snap")
    @patch(
        "sunbeam.feature_manager.FEATURE_GATES",
        {
            "feature.test-gate": {"generally_available": False},
        },
    )
    def test_list_feature_gates_yaml_format(self, mock_snap_class):
        """Command should support YAML output format."""
        deployment = Mock()
        client = Mock()
        deployment.get_client.return_value = client

        # Mock cluster gate retrieval
        client.cluster.get_feature_gate.side_effect = Exception("Not found")

        # Mock snap instance
        mock_snap = Mock()
        mock_snap.config.get.return_value = True
        mock_snap_class.return_value = mock_snap

        # Mock storage and feature managers
        storage_manager = Mock()
        storage_manager.backends.return_value = {}
        deployment.get_storage_manager.return_value = storage_manager

        feature_manager = Mock()
        feature_manager.features.return_value = {}
        deployment.get_feature_manager.return_value = feature_manager

        runner = CliRunner()
        result = runner.invoke(
            cli_group, ["list-feature-gates", "--format", "yaml"], obj=deployment
        )

        assert result.exit_code == 0
        assert "feature.test-gate" in result.output
        assert "unlocked: true" in result.output or "unlocked: True" in result.output

    @patch("sunbeam.feature_manager.Snap")
    @patch(
        "sunbeam.feature_manager.FEATURE_GATES",
        {
            "feature.multi-region": {"generally_available": False},
        },
    )
    def test_list_feature_gates_removes_feature_prefix_from_name(self, mock_snap_class):
        """Feature gate names should have 'feature.' prefix removed."""
        deployment = Mock()
        client = Mock()
        deployment.get_client.return_value = client

        # Mock cluster gate retrieval
        client.cluster.get_feature_gate.side_effect = Exception("Not found")

        # Mock snap instance
        mock_snap = Mock()
        mock_snap.config.get.return_value = False
        mock_snap_class.return_value = mock_snap

        # Mock storage and feature managers
        storage_manager = Mock()
        storage_manager.backends.return_value = {}
        deployment.get_storage_manager.return_value = storage_manager

        feature_manager = Mock()
        feature_manager.features.return_value = {}
        deployment.get_feature_manager.return_value = feature_manager

        runner = CliRunner()
        result = runner.invoke(cli_group, ["list-feature-gates"], obj=deployment)

        assert result.exit_code == 0
        assert "feature.multi-region" in result.output
        # Check that the name column shows "multi-region" not "feature.multi-region"
        lines = result.output.split("\n")
        # Find the data row (skip header rows with │)
        data_lines = [line for line in lines if "feature.multi-region" in line]
        assert len(data_lines) > 0
        # The name "multi-region" should appear separately from the full gate key
        assert "multi-region" in data_lines[0]


class TestFeatureReadiness:
    @pytest.fixture
    def manager(self):
        # This test controls the registry without loading unrelated plugins.
        manager = object.__new__(FeatureManager)
        manager._features = {}
        return manager

    def feature(self, name, requirements, enabled=True):
        feature = Mock(spec=EnableDisableFeature)
        feature.name = name
        feature.is_enabled.return_value = enabled
        feature.readiness_requirements.return_value = requirements
        return feature

    def test_enabled_definitions_are_limited_to_affected_workloads(self, manager):
        deployment = Mock()
        owner = self.feature(
            "owner",
            {
                "openstack": {
                    "vault": ApplicationReadiness(
                        status=["active", "blocked"],
                        units=3,
                        workload_status_message=["Please unseal Vault"],
                        relations={"certificates": {"consumer"}},
                    ),
                    "unrelated": ApplicationReadiness(units=99),
                }
            },
        )
        disabled = self.feature("disabled", {}, enabled=False)
        manager._features = {"owner": owner, "disabled": disabled}
        scope = {
            "openstack": {
                "vault": ApplicationReadiness(
                    units=3,
                    relations={"logging": {"collector"}},
                )
            }
        }
        result = manager.readiness_requirements(deployment, scope)
        assert set(result["openstack"]) == {"vault"}
        requirement = result["openstack"]["vault"]
        assert requirement.status == ["active", "blocked"]
        assert requirement.workload_status_message == ["Please unseal Vault"]
        assert requirement.agent_status == ("idle",)
        assert requirement.units == 3
        assert requirement.relations == {
            "certificates": {"consumer"},
            "logging": {"collector"},
        }
        assert scope["openstack"]["vault"].status == ("active",)
        assert scope["openstack"]["vault"].relations == {"logging": {"collector"}}
        owner.readiness_requirements.assert_called_once_with(
            deployment,
            {"openstack": ("vault",)},
        )
        disabled.readiness_requirements.assert_not_called()

    @pytest.mark.parametrize("registered_enabled", [False, True])
    def test_enabling_instance_participates_without_persisting_state(
        self, manager, registered_enabled
    ):
        deployment = Mock()
        registered = self.feature("current", {}, enabled=registered_enabled)
        enabling = self.feature(
            "current",
            {"model": {"agent": ApplicationReadiness(units=3)}},
            enabled=False,
        )
        manager._features = {"current": registered}
        result = manager.readiness_requirements(
            deployment,
            {"model": {"agent": ApplicationReadiness()}},
            enabling=enabling,
        )
        assert result["model"]["agent"].units == 3
        registered.readiness_requirements.assert_not_called()
        enabling.readiness_requirements.assert_called_once()
        enabling.update_feature_info.assert_not_called()

    def test_plugin_constraints_compose_without_losing_intent(self, manager):
        first = self.feature(
            "first",
            {
                "model": {
                    "collector": ApplicationReadiness(
                        machines=["0", "1"],
                        units=2,
                        principals={"k8s": ["0", "1"]},
                        relations={"cos-agent": {"k8s"}},
                    )
                }
            },
        )
        second = self.feature(
            "second",
            {
                "model": {
                    "collector": ApplicationReadiness(
                        machines=["1", "0"],
                        units=2,
                        principals={"microceph": ["1"]},
                        relations={"cos-agent": {"microceph"}},
                    )
                }
            },
        )
        manager._features = {"first": first, "second": second}
        scope = {
            "model": {
                "collector": ApplicationReadiness(
                    relations={"send-loki-logs": {"loki"}},
                )
            }
        }
        requirement = manager.readiness_requirements(Mock(), scope)["model"][
            "collector"
        ]
        assert requirement.units == 2
        assert set(requirement.machines) == {"0", "1"}
        assert requirement.principals == {"k8s": ("0", "1"), "microceph": ("1",)}
        assert requirement.relations == {
            "cos-agent": {"k8s", "microceph"},
            "send-loki-logs": {"loki"},
        }

    @pytest.mark.parametrize(
        "conflict",
        [
            {"status": ["active", "blocked"]},
            {"workload_status_message": ["Only this message"]},
            {"agent_status": ["idle", "executing"]},
            {"units": 2},
            {"machines": ["1"]},
            {"principals": {"k8s": ["1"]}},
        ],
    )
    def test_conflicting_plugin_definitions_fail(self, manager, conflict):
        defaults = {"units": 1, "machines": ["0"], "principals": {"k8s": ["0"]}}
        first = self.feature(
            "first",
            {
                "model": {
                    "app": ApplicationReadiness(**defaults),
                }
            },
        )
        second = self.feature(
            "second",
            {
                "model": {
                    "app": ApplicationReadiness(**(defaults | conflict)),
                }
            },
        )
        manager._features = {"first": first, "second": second}
        with pytest.raises(ValueError, match="Conflicting application readiness"):
            manager.readiness_requirements(
                Mock(), {"model": {"app": ApplicationReadiness()}}
            )

    def test_default_openstack_hook_does_not_pull_unaffected_plan(self):
        deployment = Mock()
        feature = VaultFeature()
        assert (
            feature.readiness_requirements(deployment, {"openstack": ["ceilometer"]})
            == {}
        )
        deployment.get_tfhelper.assert_not_called()

    def test_disabled_owner_cannot_relax_affected_workload(self, manager):
        feature = self.feature(
            "disabled",
            {
                "model": {
                    "app": ApplicationReadiness(status=["active", "blocked"]),
                }
            },
            enabled=False,
        )
        manager._features = {"disabled": feature}
        requirement = manager.readiness_requirements(
            Mock(),
            {
                "model": {"app": ApplicationReadiness(units=3)},
            },
        )["model"]["app"]
        assert requirement.status == ("active",)
        assert requirement.agent_status == ("idle",)
        assert requirement.units == 3
        feature.readiness_requirements.assert_not_called()

    def test_plugin_cannot_replace_conflicting_scope_intent(self, manager):
        owner = self.feature(
            "owner",
            {
                "model": {
                    "app": ApplicationReadiness(units=1),
                }
            },
        )
        manager._features = {"owner": owner}
        with pytest.raises(
            ValueError, match="Conflicting application readiness unit counts"
        ):
            manager.readiness_requirements(
                Mock(),
                {
                    "model": {"app": ApplicationReadiness(units=3)},
                },
            )
