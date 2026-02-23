# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for feature gates framework."""

import json
from unittest.mock import MagicMock, patch

import pytest
from snaphelpers import UnknownConfigKey

from sunbeam.clusterd.service import ConfigItemNotFoundException
from sunbeam.feature_gates import (
    FeatureGatedChoice,
    FeatureGateError,
    FeatureGateMixin,
    check_feature_gate,
    check_option_value,
    feature_gate_option_on_value,
    is_feature_gate_enabled,
)


class TestFeatureGateMixin:
    """Test FeatureGateMixin class."""

    def test_gate_key_property_with_name(self):
        """Test gate_key property with feature name."""

        class TestFeature(FeatureGateMixin):
            name = "test-feature"

        feature = TestFeature()
        assert feature.gate_key == "feature.test-feature"

    def test_gate_key_property_with_backend_type(self):
        """Test gate_key property with backend_type."""

        class TestBackend(FeatureGateMixin):
            backend_type = "test-backend"

        backend = TestBackend()
        assert backend.gate_key == "feature.storage.test-backend"

    def test_gate_key_property_no_attributes_raises(self):
        """Test gate_key property raises when no name or backend_type."""

        class InvalidFeature(FeatureGateMixin):
            pass

        feature = InvalidFeature()
        with pytest.raises(
            ValueError, match="requires either 'backend_type' or 'name'"
        ):
            _ = feature.gate_key

    def test_is_gated_generally_available(self):
        """Test is_gated when generally_available is True."""

        class AvailableFeature(FeatureGateMixin):
            name = "available-feature"
            generally_available = True

        feature = AvailableFeature()
        # Should not be gated when generally available
        assert not feature.is_gated

    def test_is_gated_via_snap_config(self):
        """Test is_gated when enabled via snap config."""

        class GatedFeature(FeatureGateMixin):
            name = "gated-feature"
            generally_available = False

        feature = GatedFeature()
        mock_snap = MagicMock()
        mock_snap.config.get.return_value = True

        # Should not be gated when enabled via snap
        assert not feature.check_gated(snap=mock_snap)
        mock_snap.config.get.assert_called_once_with("feature.gated-feature")

    def test_is_gated_snap_config_false(self):
        """Test is_gated when snap config key doesn't exist."""

        class GatedFeature(FeatureGateMixin):
            name = "gated-feature"
            generally_available = False

        feature = GatedFeature()
        mock_snap = MagicMock()
        mock_snap.config.get.side_effect = UnknownConfigKey("")

        # Should be gated when snap config doesn't exist
        assert feature.check_gated(snap=mock_snap)

    def test_is_gated_via_cluster_config_backend(self):
        """Test is_gated for storage backend via cluster config."""

        class GatedBackend(FeatureGateMixin):
            backend_type = "test-storage"
            generally_available = False

        backend = GatedBackend()
        mock_client = MagicMock()
        mock_client.cluster.get_config.return_value = json.dumps(
            ["test-storage", "other-storage"]
        )
        mock_snap = MagicMock()
        mock_snap.config.get.side_effect = UnknownConfigKey("")

        # Should not be gated when enabled in cluster config
        assert not backend.check_gated(
            client=mock_client,
            snap=mock_snap,
            enabled_config_key="StorageBackendsEnabled",
        )

    def test_is_gated_via_cluster_config_feature(self):
        """Test is_gated for feature via cluster config."""

        class GatedFeature(FeatureGateMixin):
            name = "test-feature"
            generally_available = False

        feature = GatedFeature()
        mock_client = MagicMock()
        mock_client.cluster.get_config.return_value = json.dumps(
            ["test-feature", "other-feature"]
        )
        mock_snap = MagicMock()
        mock_snap.config.get.side_effect = UnknownConfigKey("")

        # Should not be gated when enabled in cluster config
        assert not feature.check_gated(
            client=mock_client,
            snap=mock_snap,
            enabled_config_key="EnabledFeatures",
        )

    def test_is_gated_not_in_cluster_config(self):
        """Test is_gated when feature not in cluster config."""

        class GatedFeature(FeatureGateMixin):
            name = "test-feature"
            generally_available = False

        feature = GatedFeature()
        mock_client = MagicMock()
        # Feature gate not found in cluster DB
        mock_client.cluster.get_feature_gate.side_effect = Exception("Not found")
        mock_client.cluster.get_config.return_value = json.dumps(["other-feature"])
        mock_snap = MagicMock()
        mock_snap.config.get.side_effect = UnknownConfigKey("")

        # Should be gated when not in cluster config
        assert feature.check_gated(
            client=mock_client,
            snap=mock_snap,
            enabled_config_key="EnabledFeatures",
        )

    def test_is_gated_cluster_config_not_found(self):
        """Test is_gated when cluster config doesn't exist."""

        class GatedFeature(FeatureGateMixin):
            name = "test-feature"
            generally_available = False

        feature = GatedFeature()
        mock_client = MagicMock()
        # Feature gate not found in cluster DB
        mock_client.cluster.get_feature_gate.side_effect = Exception("Not found")
        mock_client.cluster.get_config.side_effect = ConfigItemNotFoundException("")
        mock_snap = MagicMock()
        mock_snap.config.get.side_effect = UnknownConfigKey("")

        # Should be gated when cluster config doesn't exist
        assert feature.check_gated(
            client=mock_client,
            snap=mock_snap,
            enabled_config_key="EnabledFeatures",
        )

    def test_is_gated_with_different_states(self):
        """Test is_gated with different configuration states."""

        class GatedFeature(FeatureGateMixin):
            name = "test-feature"
            generally_available = False

        feature = GatedFeature()
        mock_snap = MagicMock()

        # When gated (snap config disabled)
        mock_snap.config.get.side_effect = UnknownConfigKey("")
        assert feature.check_gated(snap=mock_snap)

        # When not gated (snap config enabled)
        mock_snap.config.get.side_effect = None
        mock_snap.config.get.return_value = True
        assert not feature.check_gated(snap=mock_snap)


class TestIsFeatureGateEnabled:
    """Test is_feature_gate_enabled utility function."""

    def test_gate_enabled(self):
        """Test when gate is enabled."""
        mock_snap = MagicMock()
        mock_snap.config.get.return_value = True

        assert is_feature_gate_enabled("feature.test", snap=mock_snap)
        mock_snap.config.get.assert_called_once_with("feature.test")

    def test_gate_disabled(self):
        """Test when gate is disabled."""
        mock_snap = MagicMock()
        mock_snap.config.get.return_value = False

        assert not is_feature_gate_enabled("feature.test", snap=mock_snap)

    def test_gate_key_not_found(self):
        """Test when gate key doesn't exist."""
        mock_snap = MagicMock()
        mock_snap.config.get.side_effect = UnknownConfigKey("")

        assert not is_feature_gate_enabled("feature.test", snap=mock_snap)

    @patch("sunbeam.feature_gates.Snap")
    def test_gate_creates_snap_if_not_provided(self, mock_snap_class):
        """Test that Snap is created if not provided."""
        mock_snap = MagicMock()
        mock_snap.config.get.return_value = True
        mock_snap_class.return_value = mock_snap

        is_feature_gate_enabled("feature.test")
        mock_snap_class.assert_called_once()


class TestCheckFeatureGate:
    """Test check_feature_gate utility function."""

    def test_check_gate_enabled_passes(self):
        """Test check passes when gate is enabled."""
        with patch("sunbeam.feature_gates.is_feature_gate_enabled", return_value=True):
            # Should not raise
            check_feature_gate("feature.test")

    def test_check_gate_disabled_raises(self):
        """Test check raises when gate is disabled."""
        with patch("sunbeam.feature_gates.is_feature_gate_enabled", return_value=False):
            with pytest.raises(FeatureGateError, match="not enabled"):
                check_feature_gate("feature.test")

    def test_check_gate_custom_message(self):
        """Test check with custom error message."""
        custom_message = "Custom error message"
        with patch("sunbeam.feature_gates.is_feature_gate_enabled", return_value=False):
            with pytest.raises(FeatureGateError, match=custom_message):
                check_feature_gate("feature.test", error_message=custom_message)


class TestFeatureGateDecorators:
    """Test feature gate decorators."""

    def test_feature_gate_option_enabled(self):
        """Test feature_gate_option when gate is enabled."""
        import click

        from sunbeam.feature_gates import feature_gate_option

        with patch("sunbeam.feature_gates.is_feature_gate_enabled", return_value=True):

            @click.command()
            @feature_gate_option(
                "--test-option",
                gate_key="feature.test",
                is_flag=True,
                help="Test option",
            )
            def test_cmd(test_option):
                if test_option:
                    return "enabled"
                return "disabled"

            # Option should be present
            assert any(param.name == "test_option" for param in test_cmd.params), (
                "Option should be added when gate is enabled"
            )

    def test_feature_gate_option_disabled(self):
        """Test feature_gate_option when gate is disabled."""
        import click

        from sunbeam.feature_gates import feature_gate_option

        with patch("sunbeam.feature_gates.is_feature_gate_enabled", return_value=False):

            @click.command()
            @feature_gate_option(
                "--test-option",
                gate_key="feature.test",
                is_flag=True,
                help="Test option",
            )
            def test_cmd(test_option=False):
                if test_option:
                    return "enabled"
                return "disabled"

            # Option should not be present
            assert not any(param.name == "test_option" for param in test_cmd.params), (
                "Option should not be added when gate is disabled"
            )

    def test_feature_gate_command_enabled(self):
        """Test feature_gate_command when gate is enabled."""
        import click

        from sunbeam.feature_gates import feature_gate_command

        with patch("sunbeam.feature_gates.is_feature_gate_enabled", return_value=True):

            @click.command()
            @feature_gate_command(
                gate_key="feature.test",
                hidden_message="Feature not enabled",
            )
            def test_cmd():
                return "success"

            # Command should work normally
            result = test_cmd.callback()
            assert result == "success"

    def test_feature_gate_command_disabled_with_message(self):
        """Test feature_gate_command when gate is disabled with message."""
        import click

        from sunbeam.feature_gates import feature_gate_command

        with patch("sunbeam.feature_gates.is_feature_gate_enabled", return_value=False):
            hidden_message = "Feature not enabled"

            @click.command()
            @feature_gate_command(
                gate_key="feature.test",
                hidden_message=hidden_message,
            )
            def test_cmd():
                return "success"

            # Command should raise ClickException
            with pytest.raises(click.ClickException, match=hidden_message):
                test_cmd.callback()


class TestCheckOptionValue:
    """Test check_option_value utility function."""

    def test_check_option_value_matches_single_value(self):
        """Test check_option_value when option matches single expected value."""
        import click

        # Create a mock context with params
        ctx = MagicMock(spec=click.Context)
        ctx.params = {"role": "region_controller"}

        result = check_option_value(ctx, "role", ["region_controller"])
        assert result is True

    def test_check_option_value_matches_multiple_values(self):
        """Test check_option_value when option matches one of multiple values."""
        import click

        ctx = MagicMock(spec=click.Context)
        ctx.params = {"deployment_type": "multi-region"}

        result = check_option_value(
            ctx, "deployment_type", ["multi-region", "distributed"]
        )
        assert result is True

    def test_check_option_value_no_match(self):
        """Test check_option_value when option doesn't match expected values."""
        import click

        ctx = MagicMock(spec=click.Context)
        ctx.params = {"role": "control"}

        result = check_option_value(ctx, "role", ["region_controller"])
        assert result is False

    def test_check_option_value_option_not_set(self):
        """Test check_option_value when option is not set (None)."""
        import click

        ctx = MagicMock(spec=click.Context)
        ctx.params = {"role": None}

        result = check_option_value(ctx, "role", ["region_controller"])
        assert result is False

    def test_check_option_value_option_missing_from_params(self):
        """Test check_option_value when option is not in params dict."""
        import click

        ctx = MagicMock(spec=click.Context)
        ctx.params = {}

        result = check_option_value(ctx, "role", ["region_controller"])
        assert result is False


class TestFeatureGateOptionOnValue:
    """Test feature_gate_option_on_value decorator."""

    def test_option_shown_when_trigger_matches(self):
        """Test option is added when trigger option has expected value."""
        import click

        @click.command()
        @click.option("--role", type=click.Choice(["control", "region_controller"]))
        @feature_gate_option_on_value(
            "--region-token",
            trigger_option="role",
            trigger_values=["region_controller"],
            help="Region controller token",
        )
        def test_cmd(role, region_token=None):
            return region_token

        # Verify the option was added
        param_names = [param.name for param in test_cmd.params]
        assert "region_token" in param_names
        assert "role" in param_names

    def test_option_callback_when_trigger_matches(self):
        """Test option callback behavior when trigger matches."""
        import click

        @click.command()
        @click.option("--role")
        @feature_gate_option_on_value(
            "--region-token",
            trigger_option="role",
            trigger_values=["region_controller"],
        )
        def test_cmd(role, region_token=None):
            return region_token

        # Find the region-token parameter
        region_param = next(p for p in test_cmd.params if p.name == "region_token")

        # Create mock context with role=region_controller
        ctx = MagicMock(spec=click.Context)
        ctx.params = {"role": "region_controller"}

        # Callback should return the value unchanged
        result = region_param.callback(ctx, region_param, "test-token-123")
        assert result == "test-token-123"

    def test_option_callback_when_trigger_no_match(self):
        """Test option callback returns None when trigger doesn't match."""
        import click

        @click.command()
        @click.option("--role")
        @feature_gate_option_on_value(
            "--region-token",
            trigger_option="role",
            trigger_values=["region_controller"],
        )
        def test_cmd(role, region_token=None):
            return region_token

        region_param = next(p for p in test_cmd.params if p.name == "region_token")

        # Create mock context with role=control (not region_controller)
        ctx = MagicMock(spec=click.Context)
        ctx.params = {"role": "control"}

        # Callback should return None
        result = region_param.callback(ctx, region_param, "test-token-123")
        assert result is None

    def test_option_callback_with_multiple_trigger_values(self):
        """Test option works with multiple trigger values."""
        import click

        @click.command()
        @click.option("--deployment-type")
        @feature_gate_option_on_value(
            "--region-config",
            trigger_option="deployment_type",
            trigger_values=["multi-region", "distributed"],
        )
        def test_cmd(deployment_type, region_config=None):
            return region_config

        region_param = next(p for p in test_cmd.params if p.name == "region_config")

        # Test with first trigger value
        ctx1 = MagicMock(spec=click.Context)
        ctx1.params = {"deployment_type": "multi-region"}
        result1 = region_param.callback(ctx1, region_param, "config1")
        assert result1 == "config1"

        # Test with second trigger value
        ctx2 = MagicMock(spec=click.Context)
        ctx2.params = {"deployment_type": "distributed"}
        result2 = region_param.callback(ctx2, region_param, "config2")
        assert result2 == "config2"

        # Test with non-matching value
        ctx3 = MagicMock(spec=click.Context)
        ctx3.params = {"deployment_type": "single"}
        result3 = region_param.callback(ctx3, region_param, "config3")
        assert result3 is None

    def test_option_preserves_other_attrs(self):
        """Test that decorator preserves other option attributes."""
        import click

        @click.command()
        @click.option("--role")
        @feature_gate_option_on_value(
            "--region-token",
            trigger_option="role",
            trigger_values=["region_controller"],
            help="Token for region controller",
            required=False,
            default="default-token",
        )
        def test_cmd(role, region_token=None):
            return region_token

        region_param = next(p for p in test_cmd.params if p.name == "region_token")

        assert region_param.help == "Token for region controller"
        assert region_param.required is False
        assert region_param.default == "default-token"

    def test_option_with_existing_callback(self):
        """Test that decorator can chain with existing callback."""
        import click

        def custom_callback(ctx, param, value):
            """Custom validation callback."""
            if value and len(value) < 5:
                raise click.BadParameter("Token too short")
            return value

        @click.command()
        @click.option("--role")
        @feature_gate_option_on_value(
            "--region-token",
            trigger_option="role",
            trigger_values=["region_controller"],
            callback=custom_callback,
        )
        def test_cmd(role, region_token=None):
            return region_token

        region_param = next(p for p in test_cmd.params if p.name == "region_token")

        ctx = MagicMock(spec=click.Context)
        ctx.params = {"role": "region_controller"}

        # Should apply both callbacks
        result = region_param.callback(ctx, region_param, "long-token-123")
        assert result == "long-token-123"

        # Short token should fail validation
        with pytest.raises(click.BadParameter, match="Token too short"):
            region_param.callback(ctx, region_param, "abc")

    def test_option_hidden_when_trigger_not_set(self):
        """Test option returns None when trigger option is not set."""
        import click

        @click.command()
        @click.option("--role")
        @feature_gate_option_on_value(
            "--region-token",
            trigger_option="role",
            trigger_values=["region_controller"],
        )
        def test_cmd(role, region_token=None):
            return region_token

        region_param = next(p for p in test_cmd.params if p.name == "region_token")

        # Context with no role set
        ctx = MagicMock(spec=click.Context)
        ctx.params = {}

        result = region_param.callback(ctx, region_param, "test-token")
        assert result is None


class TestFeatureGatedChoice:
    """Test FeatureGatedChoice class."""

    def test_all_choices_available_when_no_gates(self):
        """Test all choices are available when none are gated."""
        choice_type = FeatureGatedChoice(
            choices=["control", "compute", "storage"],
            gated_choices={},
        )

        assert set(choice_type.choices) == {"control", "compute", "storage"}

    def test_gated_choice_available_when_enabled(self):
        """Test gated choice is available when feature gate is enabled."""
        with patch("sunbeam.feature_gates.is_feature_gate_enabled", return_value=True):
            choice_type = FeatureGatedChoice(
                choices=["control", "compute", "region_controller"],
                gated_choices={"feature.multi-region": ["region_controller"]},
            )

        assert "region_controller" in choice_type.choices
        assert "control" in choice_type.choices
        assert "compute" in choice_type.choices

    def test_gated_choice_hidden_when_disabled(self):
        """Test gated choice is hidden when feature gate is disabled."""
        with patch("sunbeam.feature_gates.is_feature_gate_enabled", return_value=False):
            choice_type = FeatureGatedChoice(
                choices=["control", "compute", "region_controller"],
                gated_choices={"feature.multi-region": ["region_controller"]},
            )

        assert "region_controller" not in choice_type.choices
        assert "control" in choice_type.choices
        assert "compute" in choice_type.choices

    def test_multiple_gated_choices(self):
        """Test multiple choices can be gated with different gates."""

        def mock_gate_enabled(gate_key):
            return gate_key == "feature.multi-region"

        with patch(
            "sunbeam.feature_gates.is_feature_gate_enabled",
            side_effect=mock_gate_enabled,
        ):
            choice_type = FeatureGatedChoice(
                choices=["control", "compute", "region_controller", "experimental"],
                gated_choices={
                    "feature.multi-region": ["region_controller"],
                    "feature.experimental": ["experimental"],
                },
            )

        assert "region_controller" in choice_type.choices
        assert "experimental" not in choice_type.choices
        assert "control" in choice_type.choices

    def test_get_metavar_shows_all_choices(self):
        """Test get_metavar shows all choices including gated ones."""
        import click

        with patch("sunbeam.feature_gates.is_feature_gate_enabled", return_value=False):
            choice_type = FeatureGatedChoice(
                choices=["control", "compute", "region_controller"],
                gated_choices={"feature.multi-region": ["region_controller"]},
            )

        param = MagicMock(spec=click.Parameter)
        metavar = choice_type.get_metavar(param)

        # Should show all choices
        assert "control" in metavar
        assert "compute" in metavar
        assert "region_controller" in metavar
        # Should indicate which are gated
        assert "feature.multi-region" in metavar

    def test_choice_validation_accepts_enabled_choice(self):
        """Test that validation accepts a choice that is enabled."""
        with patch("sunbeam.feature_gates.is_feature_gate_enabled", return_value=True):
            choice_type = FeatureGatedChoice(
                choices=["control", "compute", "region_controller"],
                gated_choices={"feature.multi-region": ["region_controller"]},
            )

        # Should not raise an error
        result = choice_type.convert("region_controller", None, None)
        assert result == "region_controller"

    def test_choice_validation_rejects_disabled_choice(self):
        """Test that validation rejects a gated choice when gate is disabled."""
        import click

        with patch("sunbeam.feature_gates.is_feature_gate_enabled", return_value=False):
            choice_type = FeatureGatedChoice(
                choices=["control", "compute", "region_controller"],
                gated_choices={"feature.multi-region": ["region_controller"]},
            )

        # Should raise click.BadParameter
        with pytest.raises(click.BadParameter):
            choice_type.convert("region_controller", None, None)

    def test_case_sensitive_default(self):
        """Test that choices are case-sensitive by default."""
        choice_type = FeatureGatedChoice(
            choices=["Control", "compute"],
            gated_choices={},
        )

        # Should match exactly
        with pytest.raises(Exception):  # click.BadParameter
            choice_type.convert("control", None, None)

    def test_case_insensitive_when_configured(self):
        """Test that case-insensitive can be configured."""
        choice_type = FeatureGatedChoice(
            choices=["Control", "Compute"],
            gated_choices={},
            case_sensitive=False,
        )

        # Should accept any case
        result = choice_type.convert("control", None, None)
        assert result.lower() == "control"
