# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""CLI functionality for Pure Storage storage backend.

This module contains all CLI-related code following the Hitachi pattern,
including command registration and helper functions.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional

import click
import pydantic
from rich.console import Console

try:
    import yaml as _yaml  # type: ignore

    yaml: Any = _yaml
except Exception:  # yaml optional; handle gracefully at runtime
    yaml = None

from sunbeam.core.deployment import Deployment
from sunbeam.storage.backends.purestorage.backend import PureStorageBackend
from sunbeam.storage.service import StorageBackendService

LOG = logging.getLogger(__name__)
console = Console()


class PurestorageCLI:
    """CLI functionality for Pure Storage storage backend."""

    def __init__(self, backend: PureStorageBackend):
        self.backend = backend

    def _load_config_file(self, path: Optional[Path]) -> Dict[str, Any]:
        """Load YAML or JSON config file into a dictionary.

        YAML is preferred if PyYAML is available, otherwise JSON is used.
        """
        if not path:
            return {}
        text = path.read_text()
        if yaml is not None:
            return dict(yaml.safe_load(text) or {})

        return dict(json.loads(text))

    def register_add_cli(self, add: click.Group) -> None:  # noqa: C901
        """Register 'sunbeam storage add purestorage'.

        Includes typed options and a --config-file flag.
        """

        def _click_type_for(field_info) -> click.types.ParamType:
            # Map pydantic field to Click type
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

        def _build_params(required_all: bool) -> list:
            params: list = []
            # name option (not required; prompt in interactive mode)
            params.append(
                click.Option(["--name"], type=str, required=False, help="Backend name")
            )
            # config file (optional)
            params.append(
                click.Option(
                    ["--config-file"],
                    type=click.Path(exists=True, dir_okay=False, path_type=Path),
                    required=False,
                    help="YAML/JSON config file",
                )
            )
            # Model-derived options
            fields = getattr(self.backend.config_class, "model_fields", {})
            for fname, finfo in fields.items():
                if fname == "name":
                    continue
                opt_name = "--" + fname.replace("_", "-")
                click_type = _click_type_for(finfo)
                # Determine requiredness for add (respect model)
                # For interactive UX, keep CLI options optional; the model
                # enforces requiredness.
                is_required = False
                # Help text
                descr = None
                if hasattr(finfo, "field_info") and hasattr(
                    finfo.field_info, "description"
                ):
                    descr = finfo.field_info.description
                elif hasattr(finfo, "description"):
                    descr = finfo.description
                params.append(
                    click.Option(
                        [opt_name], type=click_type, required=is_required, help=descr
                    )
                )
            return params

        def _build_config_from_kwargs(kwargs: Dict[str, Any]) -> Dict[str, Any]:
            # Extract name and field values from kwargs
            # (Click converts dashes to underscores)
            cfg: Dict[str, Any] = {}
            for k, v in kwargs.items():
                if v is None:
                    continue
                cfg[k] = v
            return cfg

        def add_callback(**kwargs):
            deployment: Deployment = click.get_current_context().obj
            cfg_file = kwargs.pop("config_file", None)
            file_cfg = self._load_config_file(cfg_file)
            cli_cfg = _build_config_from_kwargs(kwargs)
            # Determine if interactive: no config-file and no CLI options supplied
            provided_cli_values = {k: v for k, v in cli_cfg.items() if v is not None}
            interactive = not file_cfg and not provided_cli_values

            if interactive:
                # Prompt for name and full config via helper
                console.print(
                    f"[blue]Setting up {self.backend.display_name} backend[/blue]"
                )
                backend_name = click.prompt("Backend name", type=str)
                config_instance = self.backend.prompt_for_config(backend_name)
                config_instance.name = backend_name
                self.backend.add_backend(
                    deployment, backend_name, config_instance, console
                )
                return

            # Non-interactive path: merge file and CLI, validate
            merged = {**file_cfg, **provided_cli_values}
            try:
                config_instance = self.backend.config_class(**merged)
            except pydantic.ValidationError as e:
                console.print("[red]Configuration validation error:[/red]")
                for error in e.errors():
                    field_name = error.get("loc", ["unknown"])[0]
                    # Convert field name to CLI parameter format
                    cli_param = f"--{field_name.replace('_', '-')}"
                    console.print(f"  {cli_param}: {error['msg']}")
                raise click.Abort()
            backend_name = merged.get("name")
            if not backend_name:
                raise click.BadParameter(
                    "--name is required when not running interactively"
                )
            self.backend.add_backend(deployment, backend_name, config_instance, console)

        # Build command dynamically with parameters
        params = _build_params(required_all=False)
        help_text = (
            f"Add {self.backend.display_name} backend.\n\n"
            "Behavior:\n"
            "- If no options are provided, runs in interactive mode and prompts "
            "for all required fields.\n"
            "- If options and/or --config-file are provided, runs non-"
            "interactively and validates against the model.\n"
            "- In non-interactive mode, --name is required (or supplied via "
            "--config-file).\n\n"
            "Examples:\n"
            "  sunbeam storage add purestorage\n"
            "  sunbeam storage add purestorage --config-file purestorage.yaml\n"
            "  sunbeam storage add purestorage --name mypure --san-ip 10.0.0.10 "
            "--pure-api-token mytoken\n"
        )
        cmd = click.Command(
            name=self.backend.name,
            params=params,
            callback=add_callback,
            help=help_text,
        )
        add.add_command(cmd)

    def register_cli(  # noqa: C901
        self,
        remove: click.Group,
        config_show: click.Group,
        config_set: click.Group,
        config_options: click.Group,
        deployment: Deployment,
    ) -> None:
        """Register management commands for Pure Storage backend."""

        @click.command(name=self.backend.name)
        @click.argument("backend_name", type=str)
        @click.option("--yes", "-y", is_flag=True, help="Skip confirmation prompt")
        @click.pass_context
        def remove_purestorage(ctx, backend_name: str, yes: bool):
            service = self.backend._get_service(deployment)
            if not service.backend_exists(backend_name, self.backend.name):
                console.print(f"[red]Error: Backend '{backend_name}' not found[/red]")
                raise click.Abort()
            if not yes:
                click.confirm(
                    f"Remove {self.backend.display_name} backend '{backend_name}'?",
                    abort=True,
                )
            try:
                self.backend.remove_backend(deployment, backend_name, console)
            except Exception as e:
                console.print(f"[red]Error removing backend: {e}[/red]")
                raise click.Abort()

        remove.add_command(remove_purestorage)

        @click.command(name=self.backend.name)
        @click.argument("backend_name", type=str)
        @click.pass_context
        def config_show_purestorage(ctx, backend_name: str):
            service = self.backend._get_service(deployment)
            config = service.get_backend_config(backend_name, self.backend.name)
            self.backend.display_config_table(backend_name, config)

        config_show.add_command(config_show_purestorage)

        # Build typed options for config set (only provided options are updated)
        def _build_set_params() -> list:
            params = []
            # Add config-file option first
            params.append(
                click.Option(
                    ["--config-file"],
                    type=click.Path(
                        exists=True, dir_okay=False, readable=True, path_type=Path
                    ),
                    required=False,
                    help="YAML/JSON config file with updates",
                )
            )
            fields = getattr(self.backend.config_class, "model_fields", {})
            for fname, finfo in fields.items():
                if fname == "name":
                    continue
                opt = "--" + fname.replace("_", "-")
                # For updates, make everything optional with default None
                # so we can detect presence
                click_type: click.ParamType = click.STRING
                ann = getattr(finfo, "annotation", None)
                if ann is not None and ("bool" in str(ann)):
                    click_type = click.BOOL
                elif ann is not None and ("int" in str(ann)):
                    click_type = click.INT
                elif ann is not None and ("float" in str(ann)):
                    click_type = click.FLOAT
                descr = None
                if hasattr(finfo, "field_info") and hasattr(
                    finfo.field_info, "description"
                ):
                    descr = finfo.field_info.description
                elif hasattr(finfo, "description"):
                    descr = finfo.description
                params.append(
                    click.Option(
                        [opt], type=click_type, required=False, default=None, help=descr
                    )
                )
            return params

        def set_callback(backend_name: str, **kwargs):
            cfg_file = kwargs.pop("config_file", None)
            file_cfg = {}
            if cfg_file:
                file_cfg = self._load_config_file(cfg_file)
            # Only include keys that were explicitly provided (value not None)
            updates = {k: v for k, v in kwargs.items() if v is not None}
            updates = {**file_cfg, **updates}
            try:
                # Get current configuration and merge with updates for validation
                service = StorageBackendService(deployment)
                current_config = service.get_backend_config(
                    backend_name, self.backend.name
                )

                # Create a merged config for validation
                # Current config comes from charm (kebab-case keys),
                # convert to snake_case
                merged_config = {}
                for key, value in current_config.items():
                    snake_key = key.replace("-", "_")
                    merged_config[snake_key] = value

                # Updates from CLI are already in snake_case
                # (Click converts --protocol to protocol)
                merged_config.update(updates)
                merged_config["name"] = backend_name  # Ensure name is set
                _ = self.backend.config_class(**merged_config)
            except pydantic.ValidationError as e:
                console.print("[red]Configuration validation error:[/red]")
                for error in e.errors():
                    field_name = error["loc"][0] if error.get("loc") else "unknown"
                    # Convert field name to CLI parameter format
                    cli_param = f"--{str(field_name).replace('_', '-')}"
                    console.print(f"  {cli_param}: {error['msg']}")
                raise click.ClickException(
                    "Configuration update failed due to validation error"
                )
            try:
                self.backend.update_backend_config(deployment, backend_name, updates)
                console.print(
                    (
                        f"[green]Configuration updated for {self.backend.display_name} "
                        f"backend '{backend_name}'[/green]"
                    )
                )
            except Exception as e:
                console.print(f"[red]Failed to update configuration: {e}[/red]")
                raise click.ClickException(f"Configuration update failed: {e}")

        set_params = _build_set_params()
        # Add backend_name as a required argument
        set_params.insert(0, click.Argument(["backend_name"], type=str, required=True))
        set_cmd = click.Command(
            name=self.backend.name,
            params=set_params,
            callback=set_callback,
            help="Set configuration options",
        )
        config_set.add_command(set_cmd)

        @click.command(name=self.backend.name)
        @click.pass_context
        def config_options_purestorage(ctx):
             self.backend.display_config_options()
            

        config_options.add_command(config_options_purestorage)
