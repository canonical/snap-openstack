# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Feature gates framework for Sunbeam.

This module provides a comprehensive framework for gating features in Sunbeam.
Feature gates allow hiding features, commands, and options from users unless
they explicitly enable them via snap configuration.

Usage Examples:

1. For features (enable/disable):
    class MyFeature(OpenStackControlPlaneFeature):
        name = "my-feature"
        # Make feature only visible when enabled - new features should be gated
        generally_available = False

        # Users enable with: snap set openstack feature.my-feature=true

2. For command options (snap config-based):
    @click.command()
    @feature_gate_option(
        "--multi-region",
        gate_key="feature.multi-region",
        help="Enable multi-region support"
    )
    def bootstrap(multi_region):
        pass

3. For gating specific choice values:
    from sunbeam.feature_gates import FeatureGatedChoice

    @click.command()
    @click.option(
        "--role",
        type=FeatureGatedChoice(
            choices=["control", "compute", "region_controller"],
            gated_choices={"feature.multi-region": ["region_controller"]}
        ),
        help="Role for this node"
    )
    def bootstrap(role):
        pass
    # region_controller only available when feature.multi-region is enabled

4. For entire commands:
    @click.command()
    @feature_gate_command(gate_key="feature.experimental-cmd")
    def experimental_command():
        pass

Feature gates can be enabled via:
    snap set openstack feature.<feature-name>=true

Or for storage:
    snap set openstack storage.<backend-type>=true
"""

import functools
import json
import logging
from typing import Any, Callable, Optional

import click
from snaphelpers import Snap, UnknownConfigKey

from sunbeam.clusterd.client import Client
from sunbeam.clusterd.service import (
    ClusterServiceUnavailableException,
    ConfigItemNotFoundException,
)

LOG = logging.getLogger(__name__)


class FeatureGateMixin:
    """Mixin class for adding feature gate functionality.

    This mixin provides methods to check if a feature is gated and should be
    hidden from users. It follows the pattern established in storage backends.

    Attributes:
        generally_available: If True, feature is always available
        backend_type: For storage backends, the backend type name
        name: For features, the feature name
    """

    # Flag to indicate if feature is generally available
    # For new features, this should default to False
    generally_available: bool = False

    @property
    def gate_key(self) -> str:
        """Get the snap configuration key for this feature's gate.

        Returns:
            The snap config key (e.g., "feature.my-feature" or "storage.ceph")
        """
        # For storage backends
        if hasattr(self, "backend_type"):
            return f"storage.{self.backend_type}"
        # For features
        elif hasattr(self, "name"):
            return f"feature.{self.name}"
        else:
            raise ValueError(
                "FeatureGateMixin requires either 'backend_type' or 'name' attribute"
            )

    def check_gated(
        self,
        client: Optional[Client] = None,
        snap: Optional[Snap] = None,
        enabled_config_key: Optional[str] = None,
    ) -> bool:
        """Check if the feature is gated (hidden unless explicitly enabled).

        This method checks in order:
        1. If generally_available is True, feature is not gated (always visible)
        2. Check cluster DB for feature gate state (multi-node deployments)
        3. If enabled in clusterd config (for storage backends or enabled features)
        4. If enabled via snap configuration

        Args:
            client: Client instance for checking cluster config
            snap: Snap instance for checking snap config
            enabled_config_key: Optional custom cluster config key to check
                               (e.g., "StorageBackendsEnabled"). For features,
                               this is typically None as they track enabled state
                               via their own is_enabled() method. For storage
                               backends, pass the config key for checking which
                               backends are enabled in the cluster.

        Returns:
            True if feature IS gated (hidden from users)
            False if feature is NOT gated (visible to users)
        """
        # If generally available, not gated
        if self.generally_available:
            return False

        # Create snap instance if not provided
        if snap is None:
            snap = Snap()

        # For feature gates, check cluster database first (authoritative in multi-node)
        # This ensures all nodes see the same state
        if client is not None:
            try:
                gate = client.cluster.get_feature_gate(self.gate_key)
                if gate and gate.enabled:
                    return False  # Not gated, enabled in cluster
            except Exception:  # noqa: BLE001, S110
                # Feature gate not in cluster DB or cluster unavailable
                pass

        # Check cluster config if client provided (for storage backends)
        if client is not None and enabled_config_key is not None:
            try:
                enabled_items = client.cluster.get_config(enabled_config_key)
                # For storage, check if backend_type is in the list
                if hasattr(self, "backend_type"):
                    if self.backend_type in json.loads(enabled_items):
                        return False  # Not gated, it's enabled
                # For features, check if feature name is in the list
                elif hasattr(self, "name"):
                    if self.name in json.loads(enabled_items):
                        return False  # Not gated, it's enabled
            except (ConfigItemNotFoundException, ClusterServiceUnavailableException):
                pass

        # Check snap config (fallback for single-node or unavailable)
        try:
            if snap.config.get(self.gate_key):
                return False  # Not gated, explicitly enabled via snap config
        except UnknownConfigKey:
            pass

        # Feature is gated (hidden)
        return True

    @property
    def is_gated(self) -> bool:
        """Check if the feature is gated (property version).

        Returns:
            True if feature IS gated (hidden from users)
            False if feature is NOT gated (visible to users)
        """
        return self.check_gated()

    @property
    def is_visible(self) -> bool:
        """Check if the feature is visible (not gated).

        This is the inverse of is_gated for more intuitive usage.

        Returns:
            True if feature is visible (not gated)
            False if feature is hidden (gated)
        """
        return not self.is_gated


# ============================================================================
# Feature Gate Configuration
# ============================================================================
# Central registry for feature gates that are embedded within existing commands
# (not standalone feature commands).
#
# These gates control:
# - Command options (via @feature_gate_option decorator)
# - Entire commands (via @feature_gate_command decorator)
# - Role/tag availability (via ROLE_GATES/ROLETAG_GATES)
#
# To make a feature generally available (GA):
# Set generally_available=True, and all decorators/filters automatically adapt.
# No need to remove decorator code or update command definitions.
#
# Example gates:
# - feature.multi-region: Gates multi-region deployment options and
#                         region_controller role
# - feature.experimental: Gates experimental features
#
FEATURE_GATES: dict[str, dict[str, bool]] = {}


def is_feature_gate_enabled(
    gate_key: str,
    snap: Optional[Snap] = None,
) -> bool:
    """Check if a feature gate is enabled via snap configuration or GA status.

    This checks if a feature is enabled either because:
    1. It's marked as generally_available=True in FEATURE_GATES, OR
    2. It's explicitly enabled via snap configuration

    Args:
        gate_key: The snap configuration key (e.g., "feature.multi-region")
        snap: Optional Snap instance (will create one if not provided)

    Returns:
        True if the gate is enabled or feature is GA, False otherwise

    Example:
        if is_feature_gate_enabled("feature.multi-region"):
            # Show multi-region option
            pass
    """
    # Check if feature is generally available
    gate_config = FEATURE_GATES.get(gate_key)
    if gate_config and gate_config.get("generally_available"):
        return True

    # Otherwise check snap configuration
    if snap is None:
        snap = Snap()

    try:
        return bool(snap.config.get(gate_key))
    except UnknownConfigKey:
        return False


class FeatureGatedChoice(click.Choice):
    """A Click Choice type that gates specific values based on feature gates.

    This allows creating options where some choices are only available when
    certain feature gates are enabled. For example, making "region_controller"
    role only available when multi-region feature is enabled.

    Args:
        choices: All possible choice values
        gated_choices: Dict mapping gate keys to lists of choice values they control
        case_sensitive: Whether choices are case-sensitive (default: True)

    Example:
        @click.option(
            "--role",
            type=FeatureGatedChoice(
                choices=["control", "compute", "region_controller"],
                gated_choices={"feature.multi-region": ["region_controller"]}
            )
        )
        def bootstrap(role):
            pass
    """

    def __init__(
        self,
        choices: list[str],
        gated_choices: dict[str, list[str]],
        case_sensitive: bool = True,
    ):
        """Initialize FeatureGatedChoice.

        Args:
            choices: All possible choice values
            gated_choices: Dict mapping gate_key -> list of choice values
            case_sensitive: Whether choices are case-sensitive
        """
        self.all_choices = choices
        self.gated_choices = gated_choices

        # Build reverse mapping for easy lookup: choice -> gate_key
        self._choice_to_gate: dict[str, str] = {}
        for gate_key, gated_choice_list in gated_choices.items():
            for choice in gated_choice_list:
                self._choice_to_gate[choice] = gate_key

        # Filter choices based on enabled gates
        enabled_choices = []
        for choice in choices:
            if choice in self._choice_to_gate:
                gate_key = self._choice_to_gate[choice]
                if is_feature_gate_enabled(gate_key):
                    enabled_choices.append(choice)
            else:
                # Not gated, always available
                enabled_choices.append(choice)

        # Initialize parent with filtered choices
        super().__init__(enabled_choices, case_sensitive=case_sensitive)

    def get_metavar(self, param: click.Parameter) -> str:
        """Get metavar showing all possible choices including gated ones.

        This ensures help text shows all choices, even if some are currently
        gated, so users know what's available with feature gates.
        """
        choices_str = "|".join(self.all_choices)

        # Indicate which choices are gated
        gated_info = []
        for choice in self.all_choices:
            if choice in self._choice_to_gate and choice not in self.choices:
                gate_key = self._choice_to_gate[choice]
                gated_info.append(f"{choice} (requires {gate_key})")

        if gated_info:
            return f"[{choices_str}]  Note: {', '.join(gated_info)}"
        return f"[{choices_str}]"

    def get_missing_message(self, param: click.Parameter) -> str:
        """Get error message when a gated choice is used but gate is disabled."""
        return (
            f"Invalid value. Available choices: {', '.join(self.choices)}. "
            f"Some choices require feature gates to be enabled."
        )


def feature_gate_option(
    *param_decls: str,
    gate_key: str,
    **option_attrs: Any,
) -> Callable:
    """Decorator to add a click option that is only shown when gate is enabled.

    This decorator wraps click.option() to conditionally add an option based on
    whether a feature gate is enabled.

    Args:
        *param_decls: Option names (e.g., "--multi-region", "-m")
        gate_key: The snap config key for the gate (e.g., "feature.multi-region")
        **option_attrs: Additional click.option() attributes

    Returns:
        Decorator function

    Example:
        @click.command()
        @feature_gate_option(
            "--multi-region",
            gate_key="feature.multi-region",
            is_flag=True,
            help="Enable multi-region deployment"
        )
        def bootstrap(multi_region):
            if multi_region:
                # Handle multi-region setup
                pass
    """

    def decorator(func: Callable) -> Callable:
        # Check if gate is enabled
        if is_feature_gate_enabled(gate_key):
            # Gate is enabled, add the option
            return click.option(*param_decls, **option_attrs)(func)
        else:
            # Gate is disabled, don't add the option
            # But we need to ensure the function still works if called
            # Add a default parameter value

            # Get the parameter name from param_decls
            param_name = None
            for decl in param_decls:
                if decl.startswith("--"):
                    param_name = decl[2:].replace("-", "_")
                    break
                elif decl.startswith("-") and len(decl) == 2:
                    continue  # Skip short options

            if param_name:
                # Use functools.wraps to preserve function metadata
                @functools.wraps(func)
                def wrapper(*args, **kwargs):
                    # Provide default value if parameter not in kwargs
                    if param_name not in kwargs:
                        # Determine default value
                        default = option_attrs.get("default")
                        if option_attrs.get("is_flag"):
                            default = False
                        kwargs[param_name] = default
                    return func(*args, **kwargs)

                return wrapper

            return func

    return decorator


def feature_gate_option_on_value(
    *param_decls: str,
    trigger_option: str,
    trigger_values: list[Any],
    **option_attrs: Any,
) -> Callable:
    """Add a click option shown when another option has specific values.

    This is useful for conditionally showing options based on the value
    of another option, such as showing multi-region options only when
    --role includes region_controller.

    Args:
        *param_decls: Option names (e.g., "--region-name", "-r")
        trigger_option: The parameter name to check (e.g., "roles")
        trigger_values: List of values that trigger this option to be shown
        **option_attrs: Additional click.option() attributes

    Returns:
        Decorator function

    Example:
        @click.command()
        @click.option("--role", multiple=True)
        @feature_gate_option_on_value(
            "--region-name",
            trigger_option="roles",
            trigger_values=["region_controller"],
            type=str,
            help="Name for this region (required when role is region_controller)"
        )
        def bootstrap(roles, region_name=None):
            if "region_controller" in roles:
                if not region_name:
                    raise click.ClickException("Region name required")
                # Handle region controller setup
                pass
    """

    def decorator(func: Callable) -> Callable:
        # Get the parameter name from param_decls
        param_name = None
        for decl in param_decls:
            if decl.startswith("--"):
                param_name = decl[2:].replace("-", "_")
                break

        if not param_name:
            # No parameter name found, just add the option normally
            return click.option(*param_decls, **option_attrs)(func)

        # Always add the option, but make it conditional in a callback
        original_callback = option_attrs.get("callback")

        def conditional_callback(
            ctx: click.Context, param: click.Parameter, value: Any
        ) -> Any:
            # Check if trigger option has one of the trigger values
            trigger_value = ctx.params.get(trigger_option)

            # Default value to return when condition is not met
            default = option_attrs.get("default")

            # If trigger option is not set or is None, return default
            if trigger_value is None:
                if original_callback and default is not None:
                    return original_callback(ctx, param, default)
                return default

            # Handle both single values and multiple values (lists/tuples)
            has_trigger = False
            if isinstance(trigger_value, (list, tuple)):
                # Check if any trigger value is in the list
                has_trigger = any(
                    str(tv) in [str(v) for v in trigger_value] for tv in trigger_values
                )
            else:
                # Single value check
                has_trigger = str(trigger_value) in [str(tv) for tv in trigger_values]

            if not has_trigger:
                # Trigger condition not met, return default
                if original_callback and default is not None:
                    return original_callback(ctx, param, default)
                return default

            # Trigger condition met, process normally
            if original_callback:
                return original_callback(ctx, param, value)
            return value

        # Update callback in option_attrs
        option_attrs_with_callback = option_attrs.copy()
        option_attrs_with_callback["callback"] = conditional_callback
        option_attrs_with_callback["is_eager"] = (
            False  # Ensure trigger option is processed first
        )

        return click.option(*param_decls, **option_attrs_with_callback)(func)

    return decorator


def feature_gate_command(
    gate_key: str,
    hidden_message: Optional[str] = None,
) -> Callable:
    """Decorator to gate an entire click command.

    When the gate is disabled, the command is either hidden or shows a message
    indicating it's not available.

    Args:
        gate_key: The snap config key for the gate
        hidden_message: Optional message to show when command is not available

    Returns:
        Decorator function

    Example:
        @click.command()
        @feature_gate_command(
            gate_key="feature.experimental",
            hidden_message=(
                "This feature is experimental. Enable with: "
                "snap set openstack feature.experimental=true"
            ),
        )
        def experimental():
            click.echo("Experimental feature!")
    """

    def decorator(func: Callable) -> Callable:
        if is_feature_gate_enabled(gate_key):
            # Gate enabled, return command as-is
            return func
        else:
            # Gate disabled
            if hidden_message:
                # Replace command with one that shows message
                @functools.wraps(func)
                def wrapper(*args, **kwargs):
                    raise click.ClickException(hidden_message)

                return wrapper
            else:
                # Hide the command by marking it as hidden
                # This assumes the function is a click.Command
                if isinstance(func, click.Command):
                    func.hidden = True
                return func

    return decorator


class FeatureGateError(Exception):
    """Exception raised when a feature gate prevents an operation."""

    pass


def check_feature_gate(
    gate_key: str,
    error_message: Optional[str] = None,
) -> None:
    """Check if a feature gate is enabled, raise exception if not.

    This is useful for programmatic checks within functions.

    Args:
        gate_key: The snap config key for the gate
        error_message: Custom error message to show

    Raises:
        FeatureGateError: If the gate is not enabled

    Example:
        def setup_multi_region():
            check_feature_gate(
                "feature.multi-region",
                error_message=(
                    "Multi-region is not enabled. Enable with: "
                    "snap set openstack feature.multi-region=true"
                ),
            )
            # Proceed with multi-region setup
    """
    if not is_feature_gate_enabled(gate_key):
        if error_message is None:
            error_message = (
                f"Feature gate '{gate_key}' is not enabled. "
                f"Enable with: snap set openstack {gate_key}=true"
            )
        raise FeatureGateError(error_message)


def check_option_value(
    ctx: Optional[click.Context] = None,
    option_name: str = "",
    expected_values: Optional[list[Any]] = None,
) -> bool:
    """Check if a click option has one of the expected values.

    This is useful for programmatically checking option values to
    enable/disable features.

    Args:
        ctx: Click context (if None, tries to get current context)
        option_name: Name of the option parameter to check
        expected_values: List of values to check against

    Returns:
        True if option has one of the expected values, False otherwise

    Example:
        @click.command()
        @click.option("--role", multiple=True)
        @click.pass_context
        def bootstrap(ctx, roles):
            if check_option_value(ctx, "roles", ["region_controller"]):
                # Enable multi-region features
                setup_region_controller()
    """
    if ctx is None:
        try:
            ctx = click.get_current_context()
        except RuntimeError:
            return False

    if expected_values is None:
        expected_values = []

    option_value = ctx.params.get(option_name)

    if option_value is None:
        return False

    # Handle both single values and multiple values (lists/tuples)
    if isinstance(option_value, (list, tuple)):
        return any(str(ev) in [str(v) for v in option_value] for ev in expected_values)
    else:
        return str(option_value) in [str(ev) for ev in expected_values]


# Convenience function for logging when features are gated
def log_gated_feature(feature_name: str, gate_key: str) -> None:
    """Log that a feature is gated and not visible to users.

    Args:
        feature_name: Human-readable feature name
        gate_key: The snap config key for the gate
    """
    LOG.debug(
        f"Feature '{feature_name}' is gated via '{gate_key}'. "
        f"Enable with: snap set openstack {gate_key}=true"
    )


def get_feature_gate_from_cluster(
    gate_key: str,
    client: Optional[Client] = None,
) -> Optional[bool]:
    """Retrieve feature gate state from the cluster database.

    This function queries the cluster database for a feature gate's state,
    providing a centralized source of truth for multi-node deployments.

    Args:
        gate_key: The feature gate key (e.g., "feature.multi-region")
        client: Clusterd client (uses socket client if None)

    Returns:
        Boolean indicating if the gate is enabled, or None if not found

    Example:
        # Check if multi-region is enabled in cluster
        enabled = get_feature_gate_from_cluster("feature.multi-region")
        if enabled:
            # Multi-region is enabled across the cluster
            setup_region_controller()
    """
    if client is None:
        client = Client.from_socket()

    try:
        gate = client.cluster.get_feature_gate(gate_key)
        return gate.enabled
    except Exception as e:
        LOG.debug(f"Feature gate '{gate_key}' not found in cluster DB: {e}")
        return None
