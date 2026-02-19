# SPDX-FileCopyrightText: 2023 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import logging

import click
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
from sunbeam.core.terraform import TerraformInitStep
from sunbeam.features.interface.v1.openstack import (
    DisableOpenStackApplicationStep,
    EnableOpenStackApplicationStep,
    OpenStackControlPlaneFeature,
    TerraformPlanLocation,
)
from sunbeam.steps.cinder_volume import DeployCinderVolumeApplicationStep
from sunbeam.steps.hypervisor import ReapplyHypervisorTerraformPlanStep
from sunbeam.steps.juju import RemoveSaasApplicationsStep
from sunbeam.storage.manager import StorageBackendManager
from sunbeam.storage.steps import DeploySpecificCinderVolumeStep
from sunbeam.utils import click_option_show_hints, pass_method_obj
from sunbeam.versions import OPENSTACK_CHANNEL

LOG = logging.getLogger(__name__)
console = Console()


class TelemetryFeature(OpenStackControlPlaneFeature):
    version = Version("0.0.1")

    name = "telemetry"
    tf_plan_location = TerraformPlanLocation.SUNBEAM_TERRAFORM_REPO

    def default_software_overrides(self) -> SoftwareConfig:
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

    def run_enable_plans(
        self, deployment: Deployment, config: FeatureConfig, show_hints: bool
    ) -> None:
        """Run plans to enable feature."""
        tfhelper = deployment.get_tfhelper(self.tfplan)
        tfhelper_openstack = deployment.get_tfhelper("openstack-plan")
        tfhelper_hypervisor = deployment.get_tfhelper("hypervisor-plan")
        tfhelper_cinder_volume = deployment.get_tfhelper("cinder-volume-plan")
        jhelper = JujuHelper(deployment.juju_controller)
        plan1: list[BaseStep] = []
        if self.user_manifest:
            plan1.append(AddManifestStep(deployment.get_client(), self.user_manifest))
        plan1.extend(
            [
                TerraformInitStep(tfhelper),
                EnableOpenStackApplicationStep(
                    deployment, config, tfhelper, jhelper, self
                ),
            ]
        )
        run_plan(plan1, console, show_hints)

        openstack_tf_output = tfhelper_openstack.output()
        extra_tfvars = {
            "ceilometer-offer-url": openstack_tf_output.get("ceilometer-offer-url")
        }
        extra_tfvars_cinder_volume = {"enable-telemetry-notifications": True}
        plan2: list[BaseStep] = []
        plan2.extend(
            [
                TerraformInitStep(tfhelper_hypervisor),
                # No need to pass any extra terraform vars for this feature
                ReapplyHypervisorTerraformPlanStep(
                    deployment.get_client(),
                    tfhelper_hypervisor,
                    jhelper,
                    self.manifest,
                    deployment.openstack_machines_model,
                    extra_tfvars=extra_tfvars,
                ),
                TerraformInitStep(tfhelper_cinder_volume),
                DeployCinderVolumeApplicationStep(
                    deployment,
                    deployment.get_client(),
                    tfhelper_cinder_volume,
                    jhelper,
                    self.manifest,
                    deployment.openstack_machines_model,
                    extra_tfvars=extra_tfvars_cinder_volume,
                ),
            ]
        )

        run_plan(plan2, console, show_hints)

        # Deploy specific cinder-volume applications for each storage backend
        client = deployment.get_client()
        storage_backends = client.cluster.get_storage_backends()

        if storage_backends.root:
            storage_manager = StorageBackendManager()
            tfhelper_storage = deployment.get_tfhelper("storage-plan")

            plan3: list[BaseStep] = []
            plan3.append(TerraformInitStep(tfhelper_storage))

            # Track principal applications to avoid duplicates
            processed_principals = set()

            for backend_metadata in storage_backends.root:
                # Get the backend instance from the manager
                backend_type = backend_metadata.type
                backend_name = backend_metadata.name

                try:
                    backend_instance = storage_manager.backends().get(backend_type)
                    if backend_instance:
                        # Skip if we've already processed this principal application
                        principal_app = backend_instance.principal_application
                        if principal_app in processed_principals:
                            LOG.debug(
                                f"Skipping {backend_name}: principal application "
                                f"{principal_app} already processed"
                            )
                            continue

                        processed_principals.add(principal_app)

                        # Add step to deploy specific cinder-volume for this backend
                        plan3.append(
                            DeploySpecificCinderVolumeStep(
                                deployment,
                                client,
                                tfhelper_storage,
                                jhelper,
                                self.manifest,
                                backend_name,
                                backend_instance,
                                deployment.openstack_machines_model,
                                extra_tfvars=extra_tfvars_cinder_volume,
                            )
                        )
                except Exception as e:
                    LOG.warning(
                        f"Failed to add specific cinder-volume step for backend "
                        f"{backend_name}: {e}"
                    )

            if len(plan3) > 1:  # More than just TerraformInitStep
                run_plan(plan3, console, show_hints)

        click.echo(f"OpenStack {self.display_name} application enabled.")

    def run_disable_plans(self, deployment: Deployment, show_hints: bool) -> None:
        """Run plans to disable the feature."""
        tfhelper = deployment.get_tfhelper(self.tfplan)
        tfhelper_hypervisor = deployment.get_tfhelper("hypervisor-plan")
        tfhelper_cinder_volume = deployment.get_tfhelper("cinder-volume-plan")
        jhelper = JujuHelper(deployment.juju_controller)
        extra_tfvars = {"ceilometer-offer-url": None}
        extra_tfvars_cinder_volume = {"enable-telemetry-notifications": False}
        plan = [
            TerraformInitStep(tfhelper_hypervisor),
            ReapplyHypervisorTerraformPlanStep(
                deployment.get_client(),
                tfhelper_hypervisor,
                jhelper,
                self.manifest,
                deployment.openstack_machines_model,
                extra_tfvars=extra_tfvars,
            ),
            TerraformInitStep(tfhelper_cinder_volume),
            DeployCinderVolumeApplicationStep(
                deployment,
                deployment.get_client(),
                tfhelper_cinder_volume,
                jhelper,
                self.manifest,
                deployment.openstack_machines_model,
                extra_tfvars=extra_tfvars_cinder_volume,
            ),
            RemoveSaasApplicationsStep(
                jhelper,
                deployment.openstack_machines_model,
                OPENSTACK_MODEL,
                saas_apps_to_delete=["ceilometer"],
            ),
            TerraformInitStep(tfhelper),
            DisableOpenStackApplicationStep(deployment, tfhelper, jhelper, self),
        ]

        run_plan(plan, console, show_hints)

        # Update specific cinder-volume applications for each storage backend
        client = deployment.get_client()
        storage_backends = client.cluster.get_storage_backends()

        if storage_backends.root:
            storage_manager = StorageBackendManager()
            tfhelper_storage = deployment.get_tfhelper("storage-plan")

            plan2: list[BaseStep] = []
            plan2.append(TerraformInitStep(tfhelper_storage))

            # Track principal applications to avoid duplicates
            processed_principals = set()

            for backend_metadata in storage_backends.root:
                # Get the backend instance from the manager
                backend_type = backend_metadata.type
                backend_name = backend_metadata.name

                try:
                    backend_instance = storage_manager.backends().get(backend_type)
                    if backend_instance:
                        # Skip if we've already processed this principal application
                        principal_app = backend_instance.principal_application
                        if principal_app in processed_principals:
                            LOG.debug(
                                f"Skipping {backend_name}: principal application "
                                f"{principal_app} already processed"
                            )
                            continue

                        processed_principals.add(principal_app)

                        # Add step to update specific cinder-volume for this backend
                        # (this will reapply with enable-telemetry-notifications=False)
                        plan2.append(
                            DeploySpecificCinderVolumeStep(
                                deployment,
                                client,
                                tfhelper_storage,
                                jhelper,
                                self.manifest,
                                backend_name,
                                backend_instance,
                                deployment.openstack_machines_model,
                                extra_tfvars=extra_tfvars_cinder_volume,
                            )
                        )
                except Exception as e:
                    LOG.warning(
                        f"Failed to add specific cinder-volume step for backend "
                        f"{backend_name}: {e}"
                    )

            if len(plan2) > 1:  # More than just TerraformInitStep
                run_plan(plan2, console, show_hints)

        click.echo(f"OpenStack {self.display_name} application disabled.")

    def set_application_names(self, deployment: Deployment) -> list:
        """Application names handled by the terraform plan."""
        database_topology = self.get_database_topology(deployment)

        apps = ["aodh", "aodh-mysql-router", "openstack-exporter"]
        if database_topology == "multi":
            apps.append("aodh-mysql")

        if deployment.get_client().cluster.list_nodes_by_role("storage"):
            apps.extend(["ceilometer", "gnocchi", "gnocchi-mysql-router"])
            if database_topology == "multi":
                apps.append("gnocchi-mysql")

        return apps

    def get_database_default_charm_storage(self) -> dict[str, str]:
        """Returns the database storage defaults for this service."""
        return {"gnocchi": "10G"}

    def set_tfvars_on_enable(
        self, deployment: Deployment, config: FeatureConfig
    ) -> dict:
        """Set terraform variables to enable the application."""
        return {
            "enable-telemetry": True,
        }

    def set_tfvars_on_disable(self, deployment: Deployment) -> dict:
        """Set terraform variables to disable the application."""
        return {"enable-telemetry": False}

    def set_tfvars_on_resize(
        self, deployment: Deployment, config: FeatureConfig
    ) -> dict:
        """Set terraform variables to resize the application."""
        return {}

    def get_database_charm_processes(self) -> dict[str, dict[str, int]]:
        """Returns the database processes accessing this service."""
        return {
            "aodh": {"aodh-k8s": 8},
            "gnocchi": {"gnocchi-k8s": 12},
        }

    @click.command()
    @click_option_show_hints
    @pass_method_obj
    def enable_cmd(self, deployment: Deployment, show_hints: bool) -> None:
        """Enable OpenStack Telemetry applications."""
        self.enable_feature(deployment, FeatureConfig(), show_hints)

    @click.command()
    @click_option_show_hints
    @pass_method_obj
    def disable_cmd(self, deployment: Deployment, show_hints: bool) -> None:
        """Disable OpenStack Telemetry applications."""
        self.disable_feature(deployment, show_hints)
