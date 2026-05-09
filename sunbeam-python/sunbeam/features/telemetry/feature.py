# SPDX-FileCopyrightText: 2023 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import logging

import click
import pydantic
from packaging.version import Version
from rich.console import Console

from sunbeam.clusterd.client import Client
from sunbeam.clusterd.service import ConfigItemNotFoundException
from sunbeam.core.ceph import is_internal_ceph_enabled_feature_aware
from sunbeam.core.checks import (
    Check,
    JujuControllerRegistrationCheck,
    run_preflight_checks,
)
from sunbeam.core.common import (
    BaseStep,
    Result,
    ResultType,
    StepContext,
    read_config,
    run_plan,
    update_config,
)
from sunbeam.core.deployment import Deployment
from sunbeam.core.juju import JujuHelper, JujuStepHelper, JujuWaitException
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
from sunbeam.steps.hypervisor import ReapplyHypervisorTerraformPlanStep
from sunbeam.steps.juju import RemoveSaasApplicationsStep
from sunbeam.storage.base import STORAGE_TFPLAN, register_storage_terraform_plan
from sunbeam.storage.steps import (
    STORAGE_BACKEND_TFVAR_CONFIG_KEY,
    ReapplyStorageBackendTerraformPlanStep,
)
from sunbeam.utils import click_option_show_hints, pass_method_obj
from sunbeam.versions import OPENSTACK_CHANNEL

LOG = logging.getLogger(__name__)
console = Console()

TELEMETRY_METRICS_BACKEND_KEY = "TelemetryMetricsBackend"


class UpdateCinderVolumeTelemetryTfvarsStep(BaseStep):
    """Update enable-telemetry-notifications in all cinder-volume entries.

    Reads the storage backend tfvars from clusterd, flips the
    ``enable-telemetry-notifications`` flag on every ``cinder-volumes``
    entry, and writes them back.  A subsequent
    ``ReapplyStorageBackendTerraformPlanStep`` is needed to actually
    apply the change.
    """

    def __init__(
        self,
        client: Client,
        enable: bool,
    ):
        action = "Enable" if enable else "Disable"
        super().__init__(
            f"{action} telemetry notifications on cinder-volume",
            f"{action.lower().rstrip('e')}ing telemetry notifications"
            " on cinder-volume entries",
        )
        self.client = client
        self.enable = enable

    def is_skip(self, context: StepContext) -> Result:
        """Skip when there are no cinder-volume entries to update."""
        try:
            tfvars = read_config(self.client, STORAGE_BACKEND_TFVAR_CONFIG_KEY)
        except ConfigItemNotFoundException:
            return Result(
                ResultType.SKIPPED,
                "No storage backend config found; nothing to update.",
            )

        if not tfvars.get("cinder-volumes"):
            return Result(
                ResultType.SKIPPED,
                "No cinder-volume entries found; nothing to update.",
            )

        return Result(ResultType.COMPLETED)

    def run(self, context: StepContext) -> Result:
        """Update cinder-volume tfvars with telemetry notification flag."""
        try:
            tfvars = read_config(self.client, STORAGE_BACKEND_TFVAR_CONFIG_KEY)
        except ConfigItemNotFoundException:
            return Result(ResultType.COMPLETED)

        cinder_volumes = tfvars.get("cinder-volumes", {})
        if not cinder_volumes:
            return Result(ResultType.COMPLETED)

        for entry in cinder_volumes.values():
            entry["enable-telemetry-notifications"] = self.enable

        update_config(self.client, STORAGE_BACKEND_TFVAR_CONFIG_KEY, tfvars)
        return Result(ResultType.COMPLETED)


GNOCCHI_S3_ENDPOINT = "gnocchi:s3-credentials"
TELEMETRY_DEPLOY_TIMEOUT = 1200  # 20 minutes


class TelemetryMetricsBackendConfig(pydantic.BaseModel):
    """Persisted metrics storage backend configuration."""

    offer_url: str | None = None


class IntegrateMetricsStorageOfferStep(BaseStep, JujuStepHelper):
    """Integrate Gnocchi with an external S3 offer.

    Workaround for https://github.com/juju/terraform-provider-juju/issues/119
    """

    def __init__(
        self,
        deployment: Deployment,
        feature: "TelemetryFeature",
        jhelper: JujuHelper,
    ):
        super().__init__(
            "Integrate metrics storage offer",
            "Integrating S3 metrics storage offer with Gnocchi",
        )
        self.deployment = deployment
        self.feature = feature
        self.jhelper = jhelper

    def run(self, context: StepContext) -> Result:
        """Integrate gnocchi with the S3 offer."""
        if not self.feature.metrics_storage_offer_url:
            return Result(ResultType.SKIPPED)

        self.integrate(
            OPENSTACK_MODEL,
            GNOCCHI_S3_ENDPOINT,
            self.feature.metrics_storage_offer_url,
        )

        try:
            self.jhelper.wait_application_ready(
                "gnocchi",
                OPENSTACK_MODEL,
                timeout=TELEMETRY_DEPLOY_TIMEOUT,
            )
        except (JujuWaitException, TimeoutError) as e:
            LOG.debug("Gnocchi not ready after S3 integration", exc_info=True)
            return Result(ResultType.FAILED, str(e))

        return Result(ResultType.COMPLETED)


class RemoveMetricsStorageOfferStep(BaseStep, JujuStepHelper):
    """Remove the S3 offer integration from Gnocchi.

    Workaround for https://github.com/juju/terraform-provider-juju/issues/119
    """

    def __init__(
        self,
        deployment: Deployment,
        feature: "TelemetryFeature",
        jhelper: JujuHelper,
    ):
        super().__init__(
            "Remove metrics storage offer",
            "Removing S3 metrics storage offer from Gnocchi",
        )
        self.deployment = deployment
        self.feature = feature
        self.jhelper = jhelper
        self.endpoints = [GNOCCHI_S3_ENDPOINT]

    def _get_relations(self, model: str, endpoints: list[str]) -> list[tuple]:
        """Return model relations for the provided endpoints."""
        relations = []
        model_status = self.jhelper.get_model_status(model)
        for endpoint in endpoints:
            app, relation = endpoint.split(":")
            if app not in model_status.apps:
                continue
            app_status = model_status.apps[app]
            if relation in app_status.relations:
                relations.append((endpoint, app_status.relations[relation]))
        return relations

    def run(self, context: StepContext) -> Result:
        """Remove the S3 relation from Gnocchi."""
        relations = self._get_relations(OPENSTACK_MODEL, self.endpoints)
        LOG.debug(f"S3 relations to remove: {relations}")
        for relation_pair in relations:
            self.remove_relation(
                OPENSTACK_MODEL,
                relation_pair[0],
                relation_pair[1],
            )

        return Result(ResultType.COMPLETED)


class TelemetryFeature(OpenStackControlPlaneFeature):
    version = Version("0.0.1")

    name = "telemetry"
    tf_plan_location = TerraformPlanLocation.SUNBEAM_TERRAFORM_REPO

    def __init__(self) -> None:
        super().__init__()
        self.metrics_storage_offer_url = ""

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
        write_answers(client, TELEMETRY_METRICS_BACKEND_KEY, config.model_dump())

    def _has_metrics_storage(self, deployment: Deployment) -> bool:
        """Check if metrics storage (Ceph or S3) is available for Gnocchi.

        S3 offer takes precedence over microceph: if an S3 offer was
        configured (instance state or cluster DB), it wins even when
        storage nodes exist.
        """
        # S3 offer — instance state (set during enable_cmd or disable flow)
        if self.metrics_storage_offer_url:
            return True
        # S3 offer — cluster DB (persisted from a previous enable)
        try:
            config = self._load_metrics_config(deployment)
            if config.offer_url:
                return True
        except ConfigItemNotFoundException:
            LOG.debug("Failed to load metrics config from cluster DB", exc_info=True)
        # Storage nodes with internal Ceph (microceph)
        try:
            client = deployment.get_client()
            return bool(
                client.cluster.list_nodes_by_role("storage")
                and is_internal_ceph_enabled_feature_aware(deployment, client)
            )
        except ConfigItemNotFoundException:
            return False

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

        # Integrate S3 metrics storage offer with Gnocchi after deployment
        if self.metrics_storage_offer_url:
            self._save_metrics_config(
                deployment,
                TelemetryMetricsBackendConfig(offer_url=self.metrics_storage_offer_url),
            )
            s3_plan: list[BaseStep] = [
                IntegrateMetricsStorageOfferStep(deployment, self, jhelper),
            ]
            run_plan(s3_plan, console, show_hints)

        openstack_tf_output = tfhelper_openstack.output()
        extra_tfvars = {
            "ceilometer-offer-url": openstack_tf_output.get("ceilometer-offer-url")
        }
        plan2: list[BaseStep] = [
            TerraformInitStep(tfhelper_hypervisor),
            ReapplyHypervisorTerraformPlanStep(
                deployment.get_client(),
                tfhelper_hypervisor,
                jhelper,
                self.manifest,
                deployment.openstack_machines_model,
                extra_tfvars=extra_tfvars,
                deployment=deployment,
            ),
        ]

        run_plan(plan2, console, show_hints)

        # Update telemetry notification flag on all existing cinder-volume entries
        client = deployment.get_client()
        register_storage_terraform_plan(deployment)
        tfhelper_storage = deployment.get_tfhelper(STORAGE_TFPLAN)

        plan3: list[BaseStep] = [
            TerraformInitStep(tfhelper_storage),
            UpdateCinderVolumeTelemetryTfvarsStep(client, enable=True),
            ReapplyStorageBackendTerraformPlanStep(
                deployment,
                client,
                tfhelper_storage,
                jhelper,
                self.manifest,
                deployment.openstack_machines_model,
            ),
        ]
        run_plan(plan3, console, show_hints)

        click.echo(f"OpenStack {self.display_name} application enabled.")

    def run_disable_plans(self, deployment: Deployment, show_hints: bool) -> None:
        """Run plans to disable the feature."""
        tfhelper = deployment.get_tfhelper(self.tfplan)
        tfhelper_hypervisor = deployment.get_tfhelper("hypervisor-plan")
        jhelper = JujuHelper(deployment.juju_controller)

        # Load persisted S3 config for cleanup
        metrics_config = self._load_metrics_config(deployment)
        if metrics_config.offer_url:
            self.metrics_storage_offer_url = metrics_config.offer_url
            s3_removal_plan: list[BaseStep] = [
                RemoveMetricsStorageOfferStep(deployment, self, jhelper),
                RemoveSaasApplicationsStep(
                    jhelper,
                    OPENSTACK_MODEL,
                    offering_interfaces=["s3"],
                ),
            ]
            run_plan(s3_removal_plan, console, show_hints)

        extra_tfvars = {"ceilometer-offer-url": None}
        plan = [
            TerraformInitStep(tfhelper_hypervisor),
            ReapplyHypervisorTerraformPlanStep(
                deployment.get_client(),
                tfhelper_hypervisor,
                jhelper,
                self.manifest,
                deployment.openstack_machines_model,
                extra_tfvars=extra_tfvars,
                deployment=deployment,
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

        # Update telemetry notification flag on all existing cinder-volume entries
        client = deployment.get_client()
        register_storage_terraform_plan(deployment)
        tfhelper_storage = deployment.get_tfhelper(STORAGE_TFPLAN)

        plan2: list[BaseStep] = [
            TerraformInitStep(tfhelper_storage),
            UpdateCinderVolumeTelemetryTfvarsStep(client, enable=False),
            ReapplyStorageBackendTerraformPlanStep(
                deployment,
                client,
                tfhelper_storage,
                jhelper,
                self.manifest,
                deployment.openstack_machines_model,
            ),
        ]
        run_plan(plan2, console, show_hints)

        click.echo(f"OpenStack {self.display_name} application disabled.")

        # Clear persisted S3 config after successful disable
        if metrics_config.offer_url:
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
        tfvars: dict = {
            "enable-telemetry": True,
        }
        if self.metrics_storage_offer_url:
            tfvars["metrics-storage-offer-url"] = self.metrics_storage_offer_url
        return tfvars

    def set_tfvars_on_disable(self, deployment: Deployment) -> dict:
        """Set terraform variables to disable the application."""
        return {
            "enable-telemetry": False,
            "metrics-storage-offer-url": "",
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
        "--metrics-storage-controller",
        type=str,
        default=None,
        help=(
            "Juju controller name for the S3 metrics storage offer"
            " (required for cross-controller offers)"
        ),
    )
    @click.option(
        "--metrics-storage-offer",
        type=str,
        default=None,
        help=(
            "Juju offer URL for S3-compatible storage backend for Gnocchi"
            " (mandatory when microceph is not configured)"
        ),
    )
    @click_option_show_hints
    @pass_method_obj
    def enable_cmd(
        self,
        deployment: Deployment,
        metrics_storage_controller: str | None,
        metrics_storage_offer: str | None,
        show_hints: bool,
    ) -> None:
        """Enable OpenStack Telemetry applications.

        Metrics storage precedence: a configured S3 offer always takes
        priority over internal Ceph-backed object storage. To switch back to
        internal storage the user
        must first disable the telemetry feature, then re-enable it
        without --metrics-storage-offer.
        """
        client = deployment.get_client()
        existing_config = self._load_metrics_config(deployment)

        if metrics_storage_offer:
            # Explicit S3 offer provided — use it (overrides any prior config)
            if metrics_storage_controller:
                self.metrics_storage_offer_url = (
                    f"{metrics_storage_controller}:{metrics_storage_offer}"
                )
                data_location = self.snap.paths.user_data
                preflight_checks: list[Check] = [
                    JujuControllerRegistrationCheck(
                        metrics_storage_controller, data_location
                    )
                ]
                run_preflight_checks(preflight_checks, console)
            else:
                self.metrics_storage_offer_url = metrics_storage_offer
        elif existing_config.offer_url:
            # No new offer, but S3 was previously configured — keep it
            self.metrics_storage_offer_url = existing_config.offer_url
        elif not is_internal_ceph_enabled_feature_aware(deployment, client):
            # No S3 (new or existing) and no internal Ceph storage.
            raise click.ClickException(
                "No internal storage is configured. --metrics-storage-offer is "
                "required to provide S3-compatible storage for Gnocchi metrics."
            )

        self.enable_feature(deployment, FeatureConfig(), show_hints)

    @click.command()
    @click_option_show_hints
    @pass_method_obj
    def disable_cmd(self, deployment: Deployment, show_hints: bool) -> None:
        """Disable OpenStack Telemetry applications."""
        self.disable_feature(deployment, show_hints)
