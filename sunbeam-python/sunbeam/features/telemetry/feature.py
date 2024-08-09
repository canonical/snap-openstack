# Copyright (c) 2023 Canonical Ltd.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or
# implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import logging

import click
from packaging.version import Version
from rich.console import Console

from sunbeam.commands.hypervisor import ReapplyHypervisorTerraformPlanStep
from sunbeam.commands.terraform import TerraformInitStep
from sunbeam.features.interface.v1.openstack import (
    DisableOpenStackApplicationStep,
    EnableOpenStackApplicationStep,
    OpenStackControlPlaneFeature,
    TerraformPlanLocation,
)
from sunbeam.jobs.common import BaseStep, run_plan
from sunbeam.jobs.deployment import Deployment
from sunbeam.jobs.juju import JujuHelper
from sunbeam.jobs.manifest import AddManifestStep, CharmManifest, SoftwareConfig
from sunbeam.versions import OPENSTACK_CHANNEL

LOG = logging.getLogger(__name__)
console = Console()


class TelemetryFeature(OpenStackControlPlaneFeature):
    version = Version("0.0.1")

    def __init__(self, deployment: Deployment) -> None:
        super().__init__(
            "telemetry",
            deployment,
            tf_plan_location=TerraformPlanLocation.SUNBEAM_TERRAFORM_REPO,
        )

    def manifest_defaults(self) -> SoftwareConfig:
        """Feature software configuration."""
        return SoftwareConfig(
            charms={
                "aodh-k8s": CharmManifest(channel=OPENSTACK_CHANNEL),
                "gnocchi-k8s": CharmManifest(channel=OPENSTACK_CHANNEL),
                "ceilometer-k8s": CharmManifest(channel=OPENSTACK_CHANNEL),
                "openstack-exporter-k8s": CharmManifest(channel=OPENSTACK_CHANNEL),
            }
        )

    def manifest_attributes_tfvar_map(self) -> dict:
        """Manifest attributes terraformvars map."""
        return {
            self.tfplan: {
                "charms": {
                    "aodh-k8s": {
                        "channel": "aodh-channel",
                        "revision": "aodh-revision",
                        "config": "aodh-config",
                    },
                    "gnocchi-k8s": {
                        "channel": "gnocchi-channel",
                        "revision": "gnocchi-revision",
                        "config": "gnocchi-config",
                    },
                    "ceilometer-k8s": {
                        "channel": "ceilometer-channel",
                        "revision": "ceilometer-revision",
                        "config": "ceilometer-config",
                    },
                    "openstack-exporter-k8s": {
                        "channel": "openstack-exporter-channel",
                        "revision": "openstack-exporter-revision",
                        "config": "openstack-exporter-config",
                    },
                }
            }
        }

    def run_enable_plans(self) -> None:
        """Run plans to enable feature."""
        tfhelper = self.deployment.get_tfhelper(self.tfplan)
        tfhelper_hypervisor = self.deployment.get_tfhelper("hypervisor-plan")
        jhelper = JujuHelper(self.deployment.get_connected_controller())
        plan: list[BaseStep] = []
        if self.user_manifest:
            plan.append(
                AddManifestStep(self.deployment.get_client(), self.user_manifest)
            )
        plan.extend(
            [
                TerraformInitStep(tfhelper),
                EnableOpenStackApplicationStep(tfhelper, jhelper, self),
                TerraformInitStep(tfhelper_hypervisor),
                # No need to pass any extra terraform vars for this feature
                ReapplyHypervisorTerraformPlanStep(
                    self.deployment.get_client(),
                    tfhelper_hypervisor,
                    jhelper,
                    self.manifest,
                    self.deployment.openstack_machines_model,
                ),
            ]
        )

        run_plan(plan, console)
        click.echo(f"OpenStack {self.name} application enabled.")

    def run_disable_plans(self) -> None:
        """Run plans to disable the feature."""
        tfhelper = self.deployment.get_tfhelper(self.tfplan)
        tfhelper_hypervisor = self.deployment.get_tfhelper("hypervisor-plan")
        jhelper = JujuHelper(self.deployment.get_connected_controller())
        plan = [
            TerraformInitStep(tfhelper),
            DisableOpenStackApplicationStep(tfhelper, jhelper, self),
            TerraformInitStep(tfhelper_hypervisor),
            ReapplyHypervisorTerraformPlanStep(
                self.deployment.get_client(),
                tfhelper_hypervisor,
                jhelper,
                self.manifest,
                self.deployment.openstack_machines_model,
            ),
        ]

        run_plan(plan, console)
        click.echo(f"OpenStack {self.name} application disabled.")

    def set_application_names(self) -> list:
        """Application names handled by the terraform plan."""
        database_topology = self.get_database_topology()

        apps = ["aodh", "aodh-mysql-router", "openstack-exporter"]
        if database_topology == "multi":
            apps.append("aodh-mysql")

        if self.deployment.get_client().cluster.list_nodes_by_role("storage"):
            apps.extend(["ceilometer", "gnocchi", "gnocchi-mysql-router"])
            if database_topology == "multi":
                apps.append("gnocchi-mysql")

        return apps

    def set_tfvars_on_enable(self) -> dict:
        """Set terraform variables to enable the application."""
        return {
            "enable-telemetry": True,
        }

    def set_tfvars_on_disable(self) -> dict:
        """Set terraform variables to disable the application."""
        return {"enable-telemetry": False}

    def set_tfvars_on_resize(self) -> dict:
        """Set terraform variables to resize the application."""
        return {}

    def get_database_charm_processes(self) -> dict[str, dict[str, int]]:
        """Returns the database processes accessing this service."""
        return {
            "aodh": {"aodh-k8s": 8},
            "gnocchi": {"gnocchi-k8s": 8},
        }

    @click.command()
    def enable_feature(self) -> None:
        """Enable OpenStack Telemetry applications."""
        super().enable_feature()

    @click.command()
    def disable_feature(self) -> None:
        """Disable OpenStack Telemetry applications."""
        super().disable_feature()
