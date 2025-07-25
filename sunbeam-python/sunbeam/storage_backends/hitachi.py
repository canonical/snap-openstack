# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import ipaddress
import logging
import re
from typing import Any, Dict

import click
import pydantic
from rich.console import Console

from sunbeam.core.common import BaseStep
from sunbeam.core.deployment import Deployment
from sunbeam.storage_backends.base import (
    StorageBackendBase,
    StorageBackendConfig,
)
from sunbeam.storage_backends.steps import (
    CheckBackendExistsStep,
    DeployCharmStep,
    IntegrateWithCinderVolumeStep,
    RemoveBackendStep,
    ValidateBackendExistsStep,
    ValidateConfigStep,
    WaitForReadyStep,
)

LOG = logging.getLogger(__name__)
console = Console()


class HitachiConfig(StorageBackendConfig):
    """Configuration model for Hitachi storage backend."""

    serial: str = pydantic.Field(..., description="Array serial number")
    pools: str = pydantic.Field(..., description="Storage pools (comma separated)")
    protocol: str = pydantic.Field(default="FC", description="Protocol (FC or iSCSI)")
    san_ip: str = pydantic.Field(..., description="Management IP/FQDN")
    san_username: str = pydantic.Field(
        default="maintenance", description="SAN username"
    )
    san_password: str = pydantic.Field(..., description="SAN password")

    @pydantic.validator("protocol")
    def validate_protocol(cls, v):  # noqa: N805
        """Validate protocol field."""
        if v.upper() not in ["FC", "ISCSI"]:
            raise ValueError("Protocol must be FC or iSCSI")
        return v.upper()

    @pydantic.validator("san_ip")
    def validate_san_ip(cls, v):  # noqa: N805
        """Validate SAN IP field."""
        return HitachiBackend._validate_ip_or_fqdn(v)


class HitachiBackend(StorageBackendBase):
    """Hitachi VSP storage backend implementation."""

    name = "hitachi"
    display_name = "Hitachi VSP Storage Backend"

    @property
    def config_class(self) -> type[HitachiConfig]:
        """Return the configuration class for Hitachi backend."""
        return HitachiConfig

    @staticmethod
    def _validate_ip_or_fqdn(value: str) -> bool:
        """Validate IP address or FQDN."""
        try:
            ipaddress.ip_address(value)
            return True
        except ValueError:
            # Check if it's a valid FQDN
            fqdn_pattern = (
                r"^[a-zA-Z0-9]([a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?"
                r"(\.[a-zA-Z0-9]([a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?)*$"
            )
            return bool(re.match(fqdn_pattern, value))

    def _create_add_plan(
        self, deployment: Deployment, config: HitachiConfig, local_charm: str = ""
    ) -> list[BaseStep]:
        """Create a plan for adding a Hitachi storage backend."""
        return [
            ValidateHitachiConfigStep(config),
            CheckBackendExistsStep(deployment, config.name),
            DeployHitachiCharmStep(deployment, config, local_charm),
            IntegrateWithCinderVolumeStep(deployment, config),
            WaitForHitachiReadyStep(deployment, config),
        ]

    def _create_remove_plan(
        self, deployment: Deployment, backend_name: str
    ) -> list[BaseStep]:
        """Create a plan for removing a Hitachi storage backend."""
        return [
            ValidateBackendExistsStep(deployment, backend_name),
            RemoveHitachiBackendStep(deployment, backend_name),
        ]

    def _prompt_for_config(self) -> Dict[str, Any]:
        """Prompt user for Hitachi backend configuration."""
        console.print("[bold]Hitachi VSP Storage Backend Configuration[/bold]")

        name = click.prompt("Backend name", type=str).lower()
        serial = click.prompt("Array serial number", type=str)
        pools = click.prompt("Storage pools (comma separated)", type=str)
        protocol = click.prompt(
            "Protocol", type=click.Choice(["FC", "iSCSI"]), default="FC"
        )
        san_ip = click.prompt("Management IP/FQDN", type=str, value_proc=self._validate_ip_or_fqdn)
        san_username = click.prompt("SAN username", type=str, default="maintenance")
        san_password = click.prompt("SAN password", type=str, hide_input=True)

        return {
            "name": name,
            "serial": serial,
            "pools": pools,
            "protocol": protocol,
            "san_ip": san_ip,
            "san_username": san_username,
            "san_password": san_password,
        }


# Hitachi-specific step implementations
class ValidateHitachiConfigStep(ValidateConfigStep):
    """Step to validate Hitachi-specific configuration."""

    def __init__(self, config: HitachiConfig):
        super().__init__(config)
        self.name = "Validate Hitachi Configuration"
        self.description = f"Validating Hitachi VSP configuration for {config.name}"
        # TODO: Validate not allready instsalled
        #       Validate cinder-volume is installed and ready
        


class DeployHitachiCharmStep(DeployCharmStep):
    """Step to deploy Hitachi VSP charm."""

    def __init__(
        self, deployment: Deployment, config: HitachiConfig, local_charm: str = ""
    ):
        charm_config = {
            "san-ip": config.san_ip,
            "san-login": config.san_username,
            "san-password": config.san_password,
            "protocol": config.protocol.lower(),
        }
        super().__init__(
            deployment, config, "cinder-volume-hitachi", charm_config, local_charm
        )


class WaitForHitachiReadyStep(WaitForReadyStep):
    """Step to wait for Hitachi VSP to be ready."""

    def __init__(self, deployment: Deployment, config: HitachiConfig):
        super().__init__(deployment, config, timeout=600)
        self.description = f"Waiting for Hitachi VSP {config.name} to be ready"


class RemoveHitachiBackendStep(RemoveBackendStep):
    """Step to remove Hitachi backend."""

    def __init__(self, deployment: Deployment, backend_name: str):
        super().__init__(deployment, backend_name)
        self.description = f"Removing Hitachi VSP backend '{backend_name}'"
