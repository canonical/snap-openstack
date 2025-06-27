# SPDX-FileCopyrightText: 2023 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import logging
from pathlib import Path

import click
from packaging.version import Version
from pydantic import Field
from rich.console import Console
from rich.status import Status

from sunbeam.clusterd.client import Client
from sunbeam.commands.configure import retrieve_admin_credentials
from sunbeam.core.common import BaseStep, Result, ResultType, RiskLevel, run_plan
from sunbeam.core.deployment import Deployment
from sunbeam.core.juju import JujuHelper
from sunbeam.core.manifest import (
    CharmManifest,
    FeatureConfig,
    Manifest,
    SoftwareConfig,
    TerraformManifest,
)
from sunbeam.core.openstack import OPENSTACK_MODEL
from sunbeam.core.terraform import (
    TerraformException,
    TerraformHelper,
    TerraformInitStep,
    TerraformStateLockedException,
)
from sunbeam.features.interface.v1.base import FeatureRequirement
from sunbeam.features.interface.v1.openstack import (
    OpenStackControlPlaneFeature,
    TerraformPlanLocation,
)
from sunbeam.utils import click_option_show_hints, pass_method_obj
from sunbeam.versions import OPENSTACK_CHANNEL

LOG = logging.getLogger(__name__)
console = Console()


class CaasConfig(FeatureConfig):
    image_name: str | None = Field(default=None, description="CAAS Image name")
    image_url: str | None = Field(
        default=None, description="CAAS Image URL to upload to glance"
    )
    container_format: str | None = Field(
        default=None, description="Image container format"
    )
    disk_format: str | None = Field(default=None, description="Image disk format")
    properties: dict = Field(
        default={}, description="Properties to set for image in glance"
    )


class CaasConfigureStep(BaseStep):
    """Configure CaaS service."""

    def __init__(
        self,
        client: Client,
        tfhelper: TerraformHelper,
        manifest: Manifest,
        tfvar_map: dict,
    ):
        super().__init__(
            "Configure Container as a Service",
            "Configure Cloud for Container as a Service use",
        )
        self.client = client
        self.tfhelper = tfhelper
        self.manifest = manifest
        self.tfvar_map = tfvar_map

    def run(self, status: Status | None = None) -> Result:
        """Execute configuration using terraform."""
        try:
            override_tfvars = {}
            try:
                feature_manifest = self.manifest.get_feature("caas")
                if not feature_manifest:
                    raise ValueError("No caas feature found in manifest")
                manifest_caas_config = feature_manifest.config
                if not manifest_caas_config:
                    raise ValueError("No caas configuration found in manifest")
                manifest_caas_config_dict = manifest_caas_config.model_dump(
                    by_alias=True
                )
                for caas_config_attribute, tfvar_name in self.tfhelper.tfvar_map.get(
                    "caas_config", {}
                ).items():
                    if caas_config_attribute_ := manifest_caas_config_dict.get(
                        caas_config_attribute
                    ):
                        override_tfvars[tfvar_name] = caas_config_attribute_
            except AttributeError:
                # caas_config not defined in manifest, ignore
                pass

            self.tfhelper.update_tfvars_and_apply_tf(
                self.client, self.manifest, override_tfvars=override_tfvars
            )
        except (TerraformException, TerraformStateLockedException) as e:
            LOG.exception("Error configuring Container as a Service feature.")
            return Result(ResultType.FAILED, str(e))

        return Result(ResultType.COMPLETED)


class CaasFeature(OpenStackControlPlaneFeature):
    version = Version("0.0.1")
    requires = {
        FeatureRequirement("secrets"),
        FeatureRequirement("orchestration"),
        FeatureRequirement("loadbalancer", optional=True),
    }

    name = "caas"
    risk_availability = RiskLevel.BETA

    tf_plan_location = TerraformPlanLocation.SUNBEAM_TERRAFORM_REPO
    configure_plan = "caas-setup"

    def config_type(self) -> type | None:
        """Feature configuration model."""
        return CaasConfig

    def default_software_overrides(self) -> SoftwareConfig:
        """Feature software configuration."""
        return SoftwareConfig(
            charms={"magnum-k8s": CharmManifest(channel=OPENSTACK_CHANNEL)},
            terraform={
                self.configure_plan: TerraformManifest(
                    source=Path(__file__).parent / "etc" / self.configure_plan
                ),
            },
        )

    def manifest_attributes_tfvar_map(self) -> dict:
        """Manifest attributes terraformvars map."""
        return {
            self.tfplan: {
                "charms": {
                    "magnum-k8s": {
                        "channel": "magnum-channel",
                        "revision": "magnum-revision",
                        "config": "magnum-config",
                    }
                }
            },
            self.configure_plan: {
                "caas_config": {
                    "image_name": "image-name",
                    "image_url": "image-source-url",
                    "container_format": "image-container-format",
                    "disk_format": "image-disk-format",
                    "properties": "image-properties",
                }
            },
        }

    def set_application_names(self, deployment: Deployment) -> list:
        """Application names handled by the terraform plan."""
        apps = ["magnum", "magnum-mysql-router"]
        if self.get_database_topology(deployment) == "multi":
            apps.extend(["magnum-mysql"])

        return apps

    def set_tfvars_on_enable(
        self, deployment: Deployment, config: FeatureConfig
    ) -> dict:
        """Set terraform variables to enable the application."""
        return {
            "enable-magnum": True,
            **self.add_horizon_plugin_to_tfvars(deployment, "magnum"),
        }

    def set_tfvars_on_disable(self, deployment: Deployment) -> dict:
        """Set terraform variables to disable the application."""
        return {
            "enable-magnum": False,
            **self.remove_horizon_plugin_from_tfvars(deployment, "magnum"),
        }

    def set_tfvars_on_resize(
        self, deployment: Deployment, config: FeatureConfig
    ) -> dict:
        """Set terraform variables to resize the application."""
        return {}

    def get_database_charm_processes(self) -> dict[str, dict[str, int]]:
        """Returns the database processes accessing this service."""
        return {
            "magnum": {"magnum-k8s": 10},
        }

    @click.command()
    @click_option_show_hints
    @pass_method_obj
    def enable_cmd(self, deployment: Deployment, show_hints: bool) -> None:
        """Enable Container as a Service feature."""
        self.enable_feature(deployment, CaasConfig(), show_hints)

    @click.command()
    @click_option_show_hints
    @pass_method_obj
    def disable_cmd(self, deployment: Deployment, show_hints: bool) -> None:
        """Disable Container as a Service feature."""
        self.disable_feature(deployment, show_hints)

    @click.command()
    @click_option_show_hints
    @pass_method_obj
    def configure(self, deployment: Deployment, show_hints: bool) -> None:
        """Configure Cloud for Container as a Service use."""
        jhelper = JujuHelper(deployment.juju_controller)
        admin_credentials = retrieve_admin_credentials(jhelper, OPENSTACK_MODEL)

        tfhelper = deployment.get_tfhelper(self.configure_plan)
        tfhelper.env = (tfhelper.env or {}) | admin_credentials
        plan = [
            TerraformInitStep(tfhelper),
            CaasConfigureStep(
                deployment.get_client(),
                tfhelper,
                self.manifest,
                self.manifest_attributes_tfvar_map(),
            ),
        ]

        run_plan(plan, console, show_hints)

    def enabled_commands(self) -> dict[str, list[dict]]:
        """Dict of clickgroup along with commands.

        Return the commands available once the feature is enabled.
        """
        return {
            "configure": [{"name": "caas", "command": self.configure}],
        }
