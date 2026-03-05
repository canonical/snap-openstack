# SPDX-FileCopyrightText: 2023 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import logging

import click
import lightkube.core.exceptions as l_core_exceptions
import lightkube.resources.core_v1 as l_core_v1
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
from sunbeam.steps.k8s import KubeClientError, get_kube_client
from sunbeam.utils import (
    click_option_show_hints,
    pass_method_obj,
)
from sunbeam.versions import BIND_CHANNEL, OPENSTACK_CHANNEL

LOG = logging.getLogger(__name__)
console = Console()

BIND_LB_SERVICE_NAME = "bind-lb"


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
        """Fetch bind LoadBalancer address from Kubernetes.

        This returns the external IP of the ``bind-lb`` Service in the
        OpenStack model namespace, instead of the internal ClusterIP of
        the ``bind`` Service.
        """
        client = deployment.get_client()
        try:
            kube = get_kube_client(client, OPENSTACK_MODEL)
        except KubeClientError as exc:
            LOG.debug("Failed to create k8s client for bind-lb lookup", exc_info=True)
            raise click.ClickException(
                f"Failed to create Kubernetes client for DNS service: {exc}"
            ) from exc

        try:
            service = kube.get(
                l_core_v1.Service,
                name=BIND_LB_SERVICE_NAME,
                namespace=OPENSTACK_MODEL,
            )
        except l_core_exceptions.ApiError as exc:
            LOG.debug("Failed to fetch bind-lb service", exc_info=True)
            raise click.ClickException(
                f"Failed to retrieve DNS LoadBalancer service: {exc}"
            ) from exc

        status = getattr(service, "status", None)
        load_balancer = getattr(status, "loadBalancer", None) if status else None
        ingress = getattr(load_balancer, "ingress", None) if load_balancer else None
        if not ingress:
            raise click.ClickException("DNS LoadBalancer has no ingress address")

        address = getattr(ingress[0], "ip", None) or getattr(
            ingress[0], "hostname", None
        )
        if not address:
            raise click.ClickException("DNS LoadBalancer ingress has no IP/hostname")

        return address

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

    def upgrade_hook(
        self,
        deployment: Deployment,
        upgrade_release: bool = False,
        show_hints: bool = False,
    ):
        """Run upgrade.

        :param upgrade_release: Whether to upgrade release
        """
        if upgrade_release:
            LOG.debug(f"Release upgrade not supported for feature {self.name}")
            return

        super().upgrade_hook(deployment, upgrade_release, show_hints)
        plan: list[BaseStep] = []
        if is_maas_deployment(deployment):
            plan.append(
                PatchBindLoadBalancerIPPoolStep(
                    deployment.get_client(),
                    deployment.public_api_label,  # type: ignore [attr-defined]
                )
            )
        plan.append(PatchBindLoadBalancerIPStep(deployment.get_client()))

        run_plan(plan, console, show_hints)
        LOG.debug(f"OpenStack {self.display_name} application refreshed.")
