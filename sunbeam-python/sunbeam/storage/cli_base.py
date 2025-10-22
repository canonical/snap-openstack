# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Base CLI functionality for storage backends.

This module contains the base CLI class that provides common functionality
for all storage backend CLI implementations.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

import click
import yaml
from rich.console import Console

from sunbeam.core.deployment import Deployment

if TYPE_CHECKING:
    from sunbeam.storage.base import StorageBackendBase


console = Console()


class StorageBackendCLIBase:
    """Base CLI functionality for storage backends.

    This class provides common CLI operations for storage backends including:
    - Loading configuration files (YAML)
    - Building Click parameters from Pydantic models
    - Registering add/remove/config commands
    - Handling interactive and non-interactive modes
    """

    backend: StorageBackendBase

    def __init__(self, backend: StorageBackendBase):
        """Initialize CLI with a backend instance.

        Args:
            backend: The storage backend instance (must have config_class attribute)
        """
        self.backend = backend

    def _load_config_file(self, path: Path | None = None) -> dict[str, Any]:
        """Load YAML config file into a dictionary.

        Args:
            path: Path to the configuration file

        Returns:
            Dictionary containing the configuration
        """
        if not path:
            return {}
        text = path.read_text()
        return dict(yaml.safe_load(text) or {})

    def _click_type_for(self, field_info) -> click.types.ParamType:
        """Map pydantic field to Click type.

        Args:
            field_info: Pydantic field info object

        Returns:
            Appropriate Click parameter type
        """
        ann = getattr(field_info, "annotation", None)
        typ = None
        if ann is not None:
            typ = str(ann)
        elif hasattr(field_info, "type_"):
            typ = str(field_info.type_)
        if typ and ("int" in typ):
            return click.INT
        if typ and ("float" in typ):
            return click.FLOAT
        if typ and ("bool" in typ):
            return click.BOOL
        return click.STRING

    def _build_add_params(self) -> list:
        """Build Click parameters for the add command from config model."""
        params: list = []
        # name option (not required; prompt in interactive mode)
        params.append(click.Argument(["name"], type=str, required=True))
        # config file (optional)
        params.append(
            click.Option(
                ["--config-file"],
                type=click.Path(exists=True, dir_okay=False, path_type=Path),
                required=False,
                help="YAML config file",
            )
        )
        params.append(
            click.Option(
                ["--accept-defaults", "-a"],
                is_flag=True,
                required=False,
                help="In interactive mode, accept default values where available",
            )
        )
        # Model-derived options
        fields = self.backend.config_type().model_fields
        for fname, finfo in fields.items():
            if fname == "name":
                continue
            opt_name = "--" + fname.replace("_", "-")
            click_type = self._click_type_for(finfo)
            # For interactive UX, keep CLI options optional; the model
            # enforces requiredness.
            is_required = False
            # Help text
            descr = finfo.description
            params.append(
                click.Option(
                    [opt_name], type=click_type, required=is_required, help=descr
                )
            )
        return params

    def _build_config_from_kwargs(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        """Extract config values from Click kwargs.

        Click converts dashes to underscores in parameter names.

        Args:
            kwargs: Keyword arguments from Click command

        Returns:
            Dictionary with non-None configuration values
        """
        cfg: dict[str, Any] = {}
        for k, v in kwargs.items():
            if v is None:
                continue
            cfg[k] = v
        return cfg

    def _build_set_params(self) -> list:
        """Build Click parameters for config set command.

        All parameters are optional since we only update provided values.

        Returns:
            List of Click Option objects
        """
        params = []
        # Add config-file option first
        params.append(
            click.Option(
                ["--config-file"],
                type=click.Path(
                    exists=True, dir_okay=False, readable=True, path_type=Path
                ),
                required=False,
                help="YAML config file with updates",
            )
        )
        fields = self.backend.config_type().model_fields
        for fname, finfo in fields.items():
            if fname == "name":
                continue
            opt = "--" + fname.replace("_", "-")
            # For updates, make everything optional with default None
            # so we can detect presence
            click_type: click.ParamType = click.STRING
            ann = finfo.annotation
            if ann is not None and ("bool" in str(ann)):
                click_type = click.BOOL
            elif ann is not None and ("int" in str(ann)):
                click_type = click.INT
            elif ann is not None and ("float" in str(ann)):
                click_type = click.FLOAT
            params.append(
                click.Option(
                    [opt],
                    type=click_type,
                    required=False,
                    default=None,
                    help=finfo.description,
                )
            )
        return params

    def register_add_cli(self, add: click.Group) -> None:  # noqa: C901
        """Register 'sunbeam storage add <backend>' command.

        Includes typed options and a --config-file flag.
        Supports both interactive and non-interactive modes.

        Args:
            add: Click group to add the command to
        """

        def add_callback(**kwargs):
            deployment: Deployment = click.get_current_context().obj
            cfg_file = kwargs.pop("config_file", None)
            accept_defaults = kwargs.pop("accept_defaults", False)
            file_cfg = self._load_config_file(cfg_file)
            cli_cfg = self._build_config_from_kwargs(kwargs)
            # Determine if interactive: no config-file and no CLI options supplied
            provided_cli_values = {k: v for k, v in cli_cfg.items() if v is not None}
            # Name is guaranted to be given through the CLI.
            backend_name = provided_cli_values.pop("name")

            merged = {**file_cfg, **provided_cli_values}
            self.backend.add_backend_instance(
                deployment, backend_name, merged, console, accept_defaults
            )

        # Build command dynamically with parameters
        params = self._build_add_params()
        help_text = (
            f"Add {self.backend.display_name} backend.\n\n"
            "Behavior:\n\n"
            "- If no options are provided, runs in interactive mode and prompts "
            "for all required fields.\n\n"
            "- If options and/or --config-file are provided, runs non-"
            "interactively and validates against the model.\n\n"
            "- In non-interactive mode.\n\n"
            "Examples:\n\n"
            f"  sunbeam storage add {self.backend.backend_type} my-backend\n\n"
            f"  sunbeam storage add {self.backend.backend_type} my-backend "
            f"--config-file {self.backend.backend_type}.yaml\n"
        )
        cmd = click.Command(
            name=self.backend.backend_type,
            params=params,
            callback=add_callback,
            help=help_text,
        )
        add.add_command(cmd)

    def register_options_cli(self, options: click.Group) -> None:
        """Register 'sunbeam storage options <backend>' command.

        Args:
            options: Click group to add the command to
        """

        def options_callback(**kwargs):
            self.backend.display_config_options()

        help_text = (
            f"Show configuration options for all {self.backend.display_name} backends."
            "\n\n"
            "Displays the current configuration values for each backend instance.\n"
        )
        cmd = click.Command(
            name=self.backend.backend_type,
            callback=options_callback,
            help=help_text,
        )
        options.add_command(cmd)
