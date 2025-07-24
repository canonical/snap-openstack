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
    BackendValidationException,
    StorageBackendBase,
    StorageBackendConfig,
)
from sunbeam.storage_backends.steps import (
    CheckBackendExistsStep,
    DeployCharmStep,
    IntegrateWithCinderStep,
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
    name = "hitachi"
    display_name = "Hitachi VSP Storage Backend"

    @staticmethod
    def _validate_ip_or_fqdn(value: str) -> str:
        """Validate IP address or FQDN."""
        # Try to validate as an IP address
        try:
            ipaddress.ip_address(value)
            return value
        except ValueError:
            pass  # Not an IP, check for FQDN next

        # Regex to validate FQDN
        fqdn_regex = re.compile(
            r"^(?=.{1,253}$)(?!-)([A-Za-z0-9-]{1,63}\.)+[A-Za-z]{2,63}\.?$"
        )
        if fqdn_regex.match(value):
            return value

        raise BackendValidationException(f"{value} is not a valid IP address or FQDN.")

    def get_config_model(self) -> type[StorageBackendConfig]:
        """Return the configuration model for Hitachi backend."""
        return HitachiConfig

    def validate_config(self, config: StorageBackendConfig) -> None:
        """Validate Hitachi-specific configuration."""
        if not isinstance(config, HitachiConfig):
            raise BackendValidationException(
                "Invalid configuration type for Hitachi backend"
            )

        # Additional validation can be added here
        if not config.serial:
            raise BackendValidationException("Array serial number is required")

        if not config.pools:
            raise BackendValidationException("Storage pools are required")

    def _create_add_plan(
        self, deployment: Deployment, config: HitachiConfig
    ) -> list[BaseStep]:
        """Create a plan for adding a Hitachi storage backend."""
        return [
            ValidateHitachiConfigStep(config),
            CheckBackendExistsStep(deployment, config.name),
            DeployHitachiCharmStep(deployment, config),
            WaitForHitachiReadyStep(deployment, config),
            IntegrateWithCinderStep(deployment, config),
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
        name = click.prompt("Backend name", default="hitachi-vsp")
        serial = click.prompt("Array serial")
        pools = click.prompt("Pools (comma separated)")
        protocol = click.prompt(
            "Protocol", type=click.Choice(["FC", "iSCSI"]), default="FC"
        )
        san_ip = click.prompt("Management IP/FQDN")
        san_username = click.prompt("SAN Username", default="maintenance")
        san_password = click.prompt("SAN Password", hide_input=True)

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


class DeployHitachiCharmStep(DeployCharmStep):
    """Step to deploy Hitachi VSP charm."""

    def __init__(self, deployment: Deployment, config: HitachiConfig):
        charm_config = {
            "san-ip": config.san_ip,
            "san-login": config.san_username,
            "san-password": config.san_password,
            "protocol": config.protocol.lower(),
        }
        super().__init__(deployment, config, "cinder-volume-hitachi", charm_config)


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
