# SPDX-FileCopyrightText: 2023 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import logging

import click
import pydantic
from packaging.version import Version
from rich.console import Console

from sunbeam.core.common import BaseStep, run_plan
from sunbeam.core.deployment import Deployment
from sunbeam.core.juju import JujuHelper
from sunbeam.core.manifest import (
    AddManifestStep,
    CharmManifest,
    FeatureConfig,
    SoftwareConfig,
)
from sunbeam.core.openstack import OPENSTACK_MODEL
from sunbeam.core.steps import (
    PatchLoadBalancerServicesIPPoolStep,
    PatchLoadBalancerServicesIPStep,
)
from sunbeam.core.terraform import TerraformInitStep
from sunbeam.features.interface.v1.base import is_maas_deployment
from sunbeam.features.interface.v1.openstack import (
    EnableOpenStackApplicationStep,
    OpenStackControlPlaneFeature,
    TerraformPlanLocation,
)
from sunbeam.utils import (
    click_option_show_hints,
    pass_method_obj,
)
from sunbeam.versions import BIND_CHANNEL, OPENSTACK_CHANNEL

LOG = logging.getLogger(__name__)
console = Console()


class DnsFeatureConfig(FeatureConfig):
    nameservers: str = pydantic.Field(examples=["ns1.example.com.,ns2.example.com."])

    @pydantic.field_validator("nameservers")
    @classmethod
    def validate_nameservers(cls, v: str):
        """Validate nameservers."""
        if not v:
            raise ValueError("Nameservers must be provided")
        for ns in v.split(","):
            if not ns.endswith("."):
                raise ValueError(f"Nameserver {ns} must end with a period")
        return v


class PatchBindLoadBalancerIPStep(PatchLoadBalancerServicesIPStep):
    def services(self) -> list[str]:
        """List of services to patch."""
        return ["bind"]

    def model(self) -> str:
        """Name of the model to use."""
        return OPENSTACK_MODEL


class PatchBindLoadBalancerIPPoolStep(PatchLoadBalancerServicesIPPoolStep):
    def services(self) -> list[str]:
        """List of services to patch."""
        return ["bind"]

    def model(self) -> str:
        """Name of the model to use."""
        return OPENSTACK_MODEL


class DnsFeature(OpenStackControlPlaneFeature):
    version = Version("0.0.1")

    name = "dns"
    tf_plan_location = TerraformPlanLocation.SUNBEAM_TERRAFORM_REPO

    def __init__(self) -> None:
        super().__init__()

    def default_software_overrides(self) -> SoftwareConfig:
        """Feature software configuration."""
        return SoftwareConfig(
            charms={
                "designate-k8s": CharmManifest(channel=OPENSTACK_CHANNEL),
                "designate-bind-k8s": CharmManifest(channel=BIND_CHANNEL),
            }
        )

    def manifest_attributes_tfvar_map(self) -> dict:
        """Manifest attributes terraformvars map."""
        return {
            self.tfplan: {
                "charms": {
                    "designate-k8s": {
                        "channel": "designate-channel",
                        "revision": "designate-revision",
                        "config": "designate-config",
                    },
                    "designate-bind-k8s": {
                        "channel": "bind-channel",
                        "revision": "bind-revision",
                        "config": "bind-config",
                    },
                }
            }
        }

    def run_enable_plans(
        self, deployment: Deployment, config: DnsFeatureConfig, show_hints: bool
    ):
        """Run plans to enable feature."""
        jhelper = JujuHelper(deployment.juju_controller)

        plan: list[BaseStep] = []
        if self.user_manifest:
            plan.append(AddManifestStep(deployment.get_client(), self.user_manifest))
        tfhelper = deployment.get_tfhelper(self.tfplan)
        plan.extend(
            [
                TerraformInitStep(tfhelper),
                EnableOpenStackApplicationStep(
                    deployment, config, tfhelper, jhelper, self
                ),
            ]
        )
        if is_maas_deployment(deployment):
            plan.append(
                PatchBindLoadBalancerIPPoolStep(
                    deployment.get_client(),
                    deployment.public_api_label,  # type: ignore [attr-defined]
                )
            )
        plan.append(PatchBindLoadBalancerIPStep(deployment.get_client()))

        run_plan(plan, console, show_hints)
        click.echo(f"OpenStack {self.display_name} application enabled.")

    def set_application_names(self, deployment: Deployment) -> list:
        """Application names handled by the terraform plan."""
        database_topology = self.get_database_topology(deployment)

        apps = ["bind", "designate", "designate-mysql-router"]
        if database_topology == "multi":
            apps.append("designate-mysql")

        return apps

    def set_tfvars_on_enable(
        self, deployment: Deployment, config: DnsFeatureConfig
    ) -> dict:
        """Set terraform variables to enable the application."""
        return {
            "enable-designate": True,
            "nameservers": config.nameservers,
        }

    def set_tfvars_on_disable(self, deployment: Deployment) -> dict:
        """Set terraform variables to disable the application."""
        return {"enable-designate": False}

    def set_tfvars_on_resize(
        self, deployment: Deployment, config: DnsFeatureConfig
    ) -> dict:
        """Set terraform variables to resize the application."""
        return {}

    def get_database_charm_processes(self) -> dict[str, dict[str, int]]:
        """Returns the database processes accessing this service."""
        return {
            "designate": {"designate-k8s": 8},
        }

    @click.command()
    @click.argument("nameservers", type=str)
    @click_option_show_hints
    @pass_method_obj
    def enable_cmd(
        self, deployment: Deployment, nameservers: str, show_hints: bool
    ) -> None:
        """Enable dns service.

        NAMESERVERS: Space delimited list of nameservers. These are the nameservers that
        have been provided to the domain registrar in order to delegate
        the domain to DNS service. e.g. "ns1.example.com. ns2.example.com."
        """
        self.enable_feature(
            deployment,
            DnsFeatureConfig(nameservers=nameservers),
            show_hints,
        )

    @click.command()
    @click_option_show_hints
    @pass_method_obj
    def disable_cmd(self, deployment: Deployment, show_hints: bool) -> None:
        """Disable dns service."""
        self.disable_feature(deployment, show_hints)

    @click.group()
    def dns_groups(self):
        """Manage dns."""

    def bind_address(self, deployment: Deployment) -> str | None:
        """Fetch bind address from juju."""
        model = OPENSTACK_MODEL
        application = "bind"
        jhelper = JujuHelper(deployment.juju_controller)
        status = jhelper.get_model_status(model)
        if app_status := status.apps.get(application):
            # TODO(gboutry): the address does not return the public address
            # since we don't modify the load balancer service anymore.
            return app_status.address
        return None

    @click.command()
    @pass_method_obj
    def dns_address(self, deployment: Deployment) -> None:
        """Retrieve DNS service address."""
        with console.status("Retrieving IP address from DNS service ... "):
            bind_address = self.bind_address(deployment)

            if bind_address:
                console.print(bind_address)
            else:
                _message = "No address found for DNS service."
                raise click.ClickException(_message)

    def enabled_commands(self) -> dict[str, list[dict]]:
        """Dict of clickgroup along with commands.

        Return the commands available once the feature is enabled.
        """
        return {
            "init": [{"name": "dns", "command": self.dns_groups}],
            "init.dns": [{"name": "address", "command": self.dns_address}],
        }
