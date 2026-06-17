# SPDX-FileCopyrightText: 2023 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import enum
import logging

import click
import pydantic
from packaging.version import Version
from rich.console import Console

from sunbeam.core.common import BaseStep, SunbeamException, run_plan
from sunbeam.core.deployment import Deployment
from sunbeam.core.juju import (
    JujuHelper,
    JujuStepHelper,
    split_controller_from_offer_url,
)
from sunbeam.core.manifest import (
    AddManifestStep,
    CharmManifest,
    FeatureConfig,
    SoftwareConfig,
)
from sunbeam.core.openstack import OPENSTACK_MODEL
from sunbeam.core.questions import load_answers, write_answers
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

TELEMETRY_METRICS_BACKEND_KEY = "TelemetryMetricsBackend"


class MetricsBackendType(enum.Enum):
    """Telemetry metrics storage backend types."""

    LOCAL = "local"
    S3 = "s3"


class TelemetryFeatureConfig(FeatureConfig):
    """Telemetry feature configuration."""

    model_config = pydantic.ConfigDict(populate_by_name=True)

    s3_integrator_offer_url: str | None = pydantic.Field(
        default=None,
        description=(
            "Juju offer URL of an externally deployed s3-integrator application "
            "to use for Gnocchi metrics storage."
        ),
        validation_alias=pydantic.AliasChoices(
            "s3-integrator-offer-url",
            "s3_integrator_offer_url",
        ),
        serialization_alias="s3-integrator-offer-url",
    )


class TelemetryMetricsBackendConfig(pydantic.BaseModel):
    """Persisted metrics storage backend configuration."""

    backend: MetricsBackendType | None = None
    s3_integrator_offer_url: str | None = None


class TelemetryFeature(OpenStackControlPlaneFeature):
    version = Version("0.0.1")

    name = "telemetry"
    tf_plan_location = TerraformPlanLocation.SUNBEAM_TERRAFORM_REPO

    def __init__(self) -> None:
        super().__init__()
        self.s3_integrator_offer_url: str | None = None

    def config_type(self) -> type[TelemetryFeatureConfig]:
        """Feature config type."""
        return TelemetryFeatureConfig

    def _load_metrics_config(
        self, deployment: Deployment
    ) -> TelemetryMetricsBackendConfig:
        """Load the metrics storage backend config from cluster DB."""
        client = deployment.get_client()
        answers = load_answers(client, TELEMETRY_METRICS_BACKEND_KEY)
        return TelemetryMetricsBackendConfig.model_validate(answers)

    def _save_metrics_config(
        self, deployment: Deployment, config: TelemetryMetricsBackendConfig
    ) -> None:
        """Save the metrics storage backend config to cluster DB."""
        client = deployment.get_client()
        write_answers(
            client,
            TELEMETRY_METRICS_BACKEND_KEY,
            config.model_dump(mode="json", exclude_none=True),
        )

    def _config_uses_external_storage(
        self, config: TelemetryMetricsBackendConfig
    ) -> bool:
        """Whether a persisted metrics backend config uses external storage."""
        return config.backend == MetricsBackendType.S3

    def _set_s3_integrator_application_from_config(self, config: FeatureConfig) -> None:
        """Set the requested external s3-integrator offer URL from config."""
        if isinstance(config, TelemetryFeatureConfig):
            self.s3_integrator_offer_url = config.s3_integrator_offer_url

    def _has_local_metrics_storage(self, deployment: Deployment) -> bool:
        """Check if local metrics storage is available for Gnocchi."""
        return bool(deployment.get_client().cluster.list_nodes_by_role("storage"))

    def _has_metrics_storage(self, deployment: Deployment) -> bool:
        """Check if metrics storage is available for Gnocchi."""
        if self.s3_integrator_offer_url:
            return True
        if self._config_uses_external_storage(self._load_metrics_config(deployment)):
            return True
        return self._has_local_metrics_storage(deployment)

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
        self._set_s3_integrator_application_from_config(config)

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
                    deployment,
                    config,
                    tfhelper,
                    jhelper,
                    self,
                    app_desired_status=(
                        ["active", "blocked"]
                        if self.s3_integrator_offer_url
                        else ["active"]
                    ),
                ),
            ]
        )
        run_plan(plan1, console, show_hints)

        if self.s3_integrator_offer_url:
            self._save_metrics_config(
                deployment,
                TelemetryMetricsBackendConfig(
                    backend=MetricsBackendType.S3,
                    s3_integrator_offer_url=self.s3_integrator_offer_url,
                ),
            )
        else:
            self._save_metrics_config(
                deployment,
                TelemetryMetricsBackendConfig(backend=MetricsBackendType.LOCAL),
            )

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

            plan3: list[BaseStep] = []
            tfhelper_storage = None

            # Track principal applications to avoid duplicates
            processed_principals = set()

            for backend_metadata in storage_backends.root:
                # Get the backend instance from the manager
                backend_type = backend_metadata.type
                backend_name = backend_metadata.name

                try:
                    backend_instance = storage_manager.backends().get(backend_type)
                    if backend_instance:
                        # Register the storage-backend plan before fetching it
                        # (mirrors storage/base.py add_backend_instance/remove_backend).
                        if tfhelper_storage is None:
                            backend_instance.register_terraform_plan(deployment)
                            tfhelper_storage = deployment.get_tfhelper(
                                "storage-backend-plan"
                            )
                            plan3.append(TerraformInitStep(tfhelper_storage))

                        # Skip if we've already processed this principal application
                        principal_app = backend_instance.principal_application
                        if principal_app in processed_principals:
                            LOG.debug(
                                "Skipping %s: principal application %s "
                                "already processed",
                                backend_name,
                                principal_app,
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
                        "Failed to add specific cinder-volume step for backend %s: %s",
                        backend_name,
                        e,
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

            plan2: list[BaseStep] = []
            # PATCH: same as run_enable_plans - register the dynamically-registered
            # "storage-backend-plan" before fetching it; do so once inside the loop
            # where a backend_instance is available.
            tfhelper_storage = None

            # Track principal applications to avoid duplicates
            processed_principals = set()

            for backend_metadata in storage_backends.root:
                # Get the backend instance from the manager
                backend_type = backend_metadata.type
                backend_name = backend_metadata.name

                try:
                    backend_instance = storage_manager.backends().get(backend_type)
                    if backend_instance:
                        # Register the storage-backend plan before fetching it.
                        if tfhelper_storage is None:
                            backend_instance.register_terraform_plan(deployment)
                            tfhelper_storage = deployment.get_tfhelper(
                                "storage-backend-plan"
                            )
                            plan2.append(TerraformInitStep(tfhelper_storage))

                        # Skip if we've already processed this principal application
                        principal_app = backend_instance.principal_application
                        if principal_app in processed_principals:
                            LOG.debug(
                                "Skipping %s: principal application %s "
                                "already processed",
                                backend_name,
                                principal_app,
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
                        "Failed to add specific cinder-volume step for backend %s: %s",
                        backend_name,
                        e,
                    )

            if len(plan2) > 1:  # More than just TerraformInitStep
                run_plan(plan2, console, show_hints)

        click.echo(f"OpenStack {self.display_name} application disabled.")
        self._save_metrics_config(deployment, TelemetryMetricsBackendConfig())

    def set_application_names(self, deployment: Deployment) -> list:
        """Application names handled by the terraform plan."""
        database_topology = self.get_database_topology(deployment)

        apps = ["aodh", "aodh-mysql-router", "openstack-exporter"]
        if database_topology == "multi":
            apps.append("aodh-mysql")

        if self._has_metrics_storage(deployment):
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
        self._set_s3_integrator_application_from_config(config)

        tfvars: dict = {
            "enable-telemetry": True,
            "enable-telemetry-s3-storage": False,
            "telemetry-s3-integrator-offer-url": None,
            "telemetry-s3-integrator-offering-controller": None,
        }
        if self.s3_integrator_offer_url:
            # Split the controller from the offer URL if it is present
            controller, s3_url = split_controller_from_offer_url(
                self.s3_integrator_offer_url
            )
            tfvars.update(
                {
                    "enable-telemetry-s3-storage": True,
                    "telemetry-s3-integrator-offer-url": s3_url,
                    "telemetry-s3-integrator-offering-controller": controller,
                }
            )
            # Validate and register the offering controller
            if controller:
                ctrl_config = JujuStepHelper().get_controller_config(controller)
                if not ctrl_config:
                    raise SunbeamException(
                        f"Telemetry S3 offering controller {controller} is not "
                        "registered in Juju provider"
                    )
                tfvars.update({"remote-controllers": {controller: ctrl_config}})

        return tfvars

    def set_tfvars_on_disable(self, deployment: Deployment) -> dict:
        """Set terraform variables to disable the application."""
        return {
            "enable-telemetry": False,
            "enable-telemetry-s3-storage": False,
            "telemetry-s3-integrator-offer-url": None,
            "telemetry-s3-integrator-offering-controller": None,
        }

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
    @click.option(
        "--s3-integrator-offer-url",
        "s3_integrator_offer_url",
        default=None,
        help=(
            "Juju offer URL of an externally deployed s3-integrator application "
            "to use for Gnocchi metrics storage.  Format: "
            "``[<controller>:][<owner>/]<model>.<application>``"
        ),
    )
    @click_option_show_hints
    @pass_method_obj
    def enable_cmd(
        self,
        deployment: Deployment,
        s3_integrator_offer_url: str | None,
        show_hints: bool,
    ) -> None:
        """Enable OpenStack Telemetry applications."""
        config = TelemetryFeatureConfig(
            s3_integrator_offer_url=s3_integrator_offer_url,
        )
        self.enable_feature(deployment, config, show_hints)

    @click.command()
    @click_option_show_hints
    @pass_method_obj
    def disable_cmd(self, deployment: Deployment, show_hints: bool) -> None:
        """Disable OpenStack Telemetry applications."""
        self.disable_feature(deployment, show_hints)
