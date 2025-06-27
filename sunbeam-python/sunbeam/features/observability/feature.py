# SPDX-FileCopyrightText: 2023 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Observability feature.

Feature to deploy and manage observability, powered by COS Lite.
This feature have options to deploy a COS Lite stack internally
or point to an external COS Lite.
"""

import enum
import logging
import queue
from pathlib import Path

import click
from packaging.version import Version
from rich.console import Console
from rich.status import Status

from sunbeam.clusterd.service import (
    ClusterServiceUnavailableException,
    ConfigItemNotFoundException,
)
from sunbeam.core.checks import (
    Check,
    JujuControllerRegistrationCheck,
    run_preflight_checks,
)
from sunbeam.core.common import (
    BaseStep,
    Result,
    ResultType,
    convert_proxy_to_model_configs,
    read_config,
    run_plan,
    update_config,
    update_status_background,
)
from sunbeam.core.deployment import Deployment
from sunbeam.core.juju import (
    ActionFailedException,
    JujuHelper,
    JujuStepHelper,
    JujuWaitException,
)
from sunbeam.core.k8s import K8SHelper
from sunbeam.core.manifest import (
    AddManifestStep,
    CharmManifest,
    FeatureConfig,
    Manifest,
    SoftwareConfig,
    TerraformManifest,
)
from sunbeam.core.openstack import OPENSTACK_MODEL
from sunbeam.core.steps import (
    PatchLoadBalancerServicesIPPoolStep,
    PatchLoadBalancerServicesIPStep,
)
from sunbeam.core.terraform import (
    TerraformException,
    TerraformHelper,
    TerraformInitStep,
    TerraformStateLockedException,
)
from sunbeam.features.interface.v1.base import (
    BaseFeatureGroup,
    FeatureRequirement,
    is_maas_deployment,
)
from sunbeam.features.interface.v1.openstack import (
    DisableOpenStackApplicationStep,
    EnableOpenStackApplicationStep,
    OpenStackControlPlaneFeature,
    TerraformPlanLocation,
)
from sunbeam.steps.juju import RemoveSaasApplicationsStep
from sunbeam.steps.k8s import CREDENTIAL_SUFFIX
from sunbeam.utils import click_option_show_hints, pass_method_obj
from sunbeam.versions import TRAEFIK_CHANNEL

LOG = logging.getLogger(__name__)
console = Console()

OBSERVABILITY_FEATURE_KEY = "ObservabilityProviderType"
OBSERVABILITY_MODEL = "observability"
OBSERVABILITY_DEPLOY_TIMEOUT = 1200  # 20 minutes
COS_TFPLAN = "cos-plan"
GRAFANA_AGENT_TFPLAN = "grafana-agent-plan"
COS_CONFIG_KEY = "TerraformVarsFeatureObservabilityPlanCos"
GRAFANA_AGENT_CONFIG_KEY = "TerraformVarsFeatureObservabilityPlanGrafanaAgent"

COS_CHANNEL = "1/stable"
GRAFANA_AGENT_CHANNEL = "1/stable"
GRAFANA_AGENT_K8S_CHANNEL = "1/stable"
OBSERVABILITY_OFFER_INTERFACES = [
    "grafana_dashboard",
    "prometheus_remote_write",
    "loki_push_api",
]


class ProviderType(enum.Enum):
    EXTERNAL = 1
    EMBEDDED = 2


class DeployObservabilityStackStep(BaseStep, JujuStepHelper):
    """Deploy Observability Stack using Terraform."""

    _CONFIG = COS_CONFIG_KEY

    def __init__(
        self,
        deployment: Deployment,
        feature: "ObservabilityFeature",
        tfhelper: TerraformHelper,
        jhelper: JujuHelper,
    ):
        super().__init__("Deploy Observability Stack", "Deploying Observability Stack")
        self.deployment = deployment
        self.feature = feature
        self.tfhelper = tfhelper
        self.jhelper = jhelper
        self.manifest = self.feature.manifest
        self.model = OBSERVABILITY_MODEL
        self.cloud = K8SHelper.get_cloud(deployment.name)

    def run(self, status: Status | None = None) -> Result:
        """Execute configuration using terraform."""
        f_manifest = self.manifest.get_feature(self.feature.name.split(".")[-1])
        if f_manifest is not None:
            model_config = f_manifest.software.juju.bootstrap_model_configs.get(
                OBSERVABILITY_MODEL, {}
            )
        else:
            model_config = {}
        proxy_settings = self.deployment.get_proxy_settings()
        model_config.update(convert_proxy_to_model_configs(proxy_settings))
        model_config.update({"workload-storage": K8SHelper.get_default_storageclass()})

        extra_tfvars = {
            "model": self.model,
            "cloud": self.cloud,
            "credential": f"{self.cloud}{CREDENTIAL_SUFFIX}",
            "config": model_config,
        }

        try:
            self.update_status(status, "deploying services")
            self.tfhelper.update_tfvars_and_apply_tf(
                self.deployment.get_client(),
                self.manifest,
                tfvar_config=self._CONFIG,
                override_tfvars=extra_tfvars,
            )
        except (TerraformException, TerraformStateLockedException) as e:
            LOG.exception("Error deploying Observability Stack")
            return Result(ResultType.FAILED, str(e))

        apps = self.jhelper.get_application_names(self.model)
        LOG.debug(f"Application monitored for readiness: {apps}")
        status_queue: queue.Queue[str] = queue.Queue(maxsize=len(apps))
        task = update_status_background(self, apps, status_queue, status)
        try:
            self.jhelper.wait_until_active(
                self.model,
                apps,
                timeout=OBSERVABILITY_DEPLOY_TIMEOUT,
                queue=status_queue,
            )
        except (JujuWaitException, TimeoutError) as e:
            LOG.debug("Failed to deploy Observability Stack", exc_info=True)
            return Result(ResultType.FAILED, str(e))
        finally:
            task.stop()

        return Result(ResultType.COMPLETED)


class UpdateObservabilityModelConfigStep(BaseStep, JujuStepHelper):
    """Update Observability Model config  using Terraform."""

    _CONFIG = COS_CONFIG_KEY

    def __init__(
        self,
        deployment: Deployment,
        feature: "ObservabilityFeature",
        tfhelper: TerraformHelper,
    ):
        super().__init__(
            "Update Observability Model Config",
            "Updating Observability proxy related model config",
        )
        self.deployment = deployment
        self.feature = feature
        self.tfhelper = tfhelper
        self.manifest = self.feature.manifest
        self.client = deployment.get_client()
        self.model = OBSERVABILITY_MODEL
        self.cloud = K8SHelper.get_cloud(deployment.name)

    def run(self, status: Status | None = None) -> Result:
        """Execute configuration using terraform."""
        proxy_settings = self.deployment.get_proxy_settings()
        model_config = convert_proxy_to_model_configs(proxy_settings)
        model_config.update({"workload-storage": K8SHelper.get_default_storageclass()})
        extra_tfvars = {
            "model": self.model,
            "cloud": self.cloud,
            "credential": f"{self.cloud}{CREDENTIAL_SUFFIX}",
            "config": model_config,
        }

        try:
            self.tfhelper.update_tfvars_and_apply_tf(
                self.client,
                self.manifest,
                tfvar_config=self._CONFIG,
                override_tfvars=extra_tfvars,
                tf_apply_extra_args=["-target=juju_model.cos"],
            )
        except (TerraformException, TerraformStateLockedException) as e:
            LOG.exception("Error updating Observability Model config")
            return Result(ResultType.FAILED, str(e))

        return Result(ResultType.COMPLETED)


class DeployGrafanaAgentStep(BaseStep, JujuStepHelper):
    """Deploy Grafana Agent using Terraform."""

    _CONFIG = GRAFANA_AGENT_CONFIG_KEY

    def __init__(
        self,
        deployment: Deployment,
        config: FeatureConfig,
        feature: "ObservabilityFeature",
        tfhelper: TerraformHelper,
        jhelper: JujuHelper,
        accepted_app_status: list[str] = ["active"],
    ):
        super().__init__("Deploy Grafana Agent", "Deploy Grafana Agent")
        self.deployment = deployment
        self.config = config
        self.feature = feature
        self.tfhelper = tfhelper
        self.jhelper = jhelper
        self.manifest = self.feature.manifest
        self.accepted_app_status = accepted_app_status
        self.client = self.deployment.get_client()
        self.model = self.deployment.openstack_machines_model

    def run(self, status: Status | None = None) -> Result:
        """Execute configuration using terraform."""
        integration_apps = ["openstack-hypervisor"]
        extra_tfvars = {
            "principal-application-model": self.model,
            "grafana-agent-integration-apps": integration_apps,
        }
        # Offer URLs from COS are added from feature
        extra_tfvars.update(
            self.feature.set_tfvars_on_enable(self.deployment, self.config)
        )

        try:
            self.update_status(status, "deploying services")
            self.tfhelper.update_tfvars_and_apply_tf(
                self.client,
                self.manifest,
                tfvar_config=self._CONFIG,
                override_tfvars=extra_tfvars,
            )
        except (TerraformException, TerraformStateLockedException) as e:
            LOG.exception("Error deploying grafana agent")
            return Result(ResultType.FAILED, str(e))

        app = "grafana-agent"
        LOG.debug(f"Application monitored for readiness: {app}")
        try:
            self.jhelper.wait_application_ready(
                app,
                self.model,
                accepted_status=self.accepted_app_status,
                timeout=OBSERVABILITY_DEPLOY_TIMEOUT,
            )
        except (JujuWaitException, TimeoutError) as e:
            LOG.debug("Failed to deploy grafana agent", exc_info=True)
            return Result(ResultType.FAILED, str(e))

        return Result(ResultType.COMPLETED)


class RemoveObservabilityStackStep(BaseStep, JujuStepHelper):
    """Remove Observability Stack using Terraform."""

    def __init__(
        self,
        deployment: Deployment,
        feature: "ObservabilityFeature",
        tfhelper: TerraformHelper,
        jhelper: JujuHelper,
    ):
        super().__init__("Remove Observability Stack", "Removing Observability Stack")
        self.deployment = deployment
        self.feature = feature
        self.tfhelper = tfhelper
        self.manifest = self.feature.manifest
        self.jhelper = jhelper
        self.model = OBSERVABILITY_MODEL
        self.cloud = K8SHelper.get_cloud(deployment.name)

    def run(self, status: Status | None = None) -> Result:
        """Execute configuration using terraform."""
        try:
            self.tfhelper.destroy()
        except TerraformException as e:
            LOG.exception("Error destroying Observability Stack")
            return Result(ResultType.FAILED, str(e))

        try:
            self.jhelper.wait_model_gone(
                self.model,
                timeout=OBSERVABILITY_DEPLOY_TIMEOUT,
            )
        except TimeoutError as e:
            LOG.debug("Failed to destroy Observability Stack", exc_info=True)
            return Result(ResultType.FAILED, str(e))

        return Result(ResultType.COMPLETED)


class RemoveGrafanaAgentStep(BaseStep, JujuStepHelper):
    """Remove Grafana Agent using Terraform."""

    _CONFIG = GRAFANA_AGENT_CONFIG_KEY

    def __init__(
        self,
        deployment: Deployment,
        feature: "ObservabilityFeature",
        tfhelper: TerraformHelper,
        jhelper: JujuHelper,
    ):
        super().__init__("Remove Grafana Agent", "Removing Grafana Agent")
        self.deployment = deployment
        self.feature = feature
        self.tfhelper = tfhelper
        self.jhelper = jhelper
        self.manifest = self.feature.manifest
        self.client = deployment.get_client()
        self.model = deployment.openstack_machines_model

    def run(self, status: Status | None = None) -> Result:
        """Execute configuration using terraform."""
        try:
            self.tfhelper.destroy()
        except TerraformException as e:
            LOG.exception("Error destroying grafana agent")
            return Result(ResultType.FAILED, str(e))

        apps = ["grafana-agent"]
        try:
            self.jhelper.wait_application_gone(
                apps,
                self.model,
                timeout=OBSERVABILITY_DEPLOY_TIMEOUT,
            )
        except TimeoutError as e:
            LOG.debug("Failed to destroy grafana agent", exc_info=True)
            return Result(ResultType.FAILED, str(e))

        extra_tfvars = {
            "principal-application-model": self.model,
            "principal-application": "openstack-hypervisor",
        }
        # Offer URLs from COS are added from feature
        extra_tfvars.update(self.feature.set_tfvars_on_disable(self.deployment))
        update_config(self.client, self._CONFIG, extra_tfvars)

        return Result(ResultType.COMPLETED)


class PatchCosLoadBalancerIPStep(PatchLoadBalancerServicesIPStep):
    def services(self) -> list[str]:
        """List of services to patch."""
        return ["traefik"]

    def model(self) -> str:
        """Name of the model to use."""
        return OBSERVABILITY_MODEL


class PatchCosLoadBalancerIPPoolStep(PatchLoadBalancerServicesIPPoolStep):
    def services(self) -> list[str]:
        """List of services to patch."""
        return ["traefik"]

    def model(self) -> str:
        """Name of the model to use."""
        return OBSERVABILITY_MODEL


class IntegrateRemoteCosOffersStep(BaseStep, JujuStepHelper):
    """Integrate COS Offers across Juju controllers.

    This is a workaround for https://github.com/juju/terraform-provider-juju/issues/119
    """

    def __init__(
        self,
        deployment: Deployment,
        feature: "ObservabilityFeature",
        jhelper: JujuHelper,
    ):
        super().__init__(
            "Integrate external Observability offers",
            "Integrating external Observability offers",
        )
        self.deployment = deployment
        self.feature = feature
        self.jhelper = jhelper
        self.model = OPENSTACK_MODEL
        self.relations = [
            (
                "grafana-agent:grafana-dashboards-provider",
                self.feature.grafana_offer_url,
            ),
            ("grafana-agent:send-remote-write", self.feature.prometheus_offer_url),
            ("grafana-agent:logging-consumer", self.feature.loki_offer_url),
        ]

    def run(self, status: Status | None = None) -> Result:
        """Execute integrations using external offers."""
        for model in [
            OPENSTACK_MODEL,
            self.deployment.openstack_machines_model,
        ]:
            for relation_pair in self.relations:
                if relation_pair[0] and relation_pair[1]:
                    self.integrate(
                        model,
                        relation_pair[0],
                        relation_pair[1],
                    )

        for model in [
            OPENSTACK_MODEL,
            self.deployment.openstack_machines_model,
        ]:
            app = "grafana-agent"
            LOG.debug(f"Application monitored for readiness: {app}")
            try:
                self.jhelper.wait_application_ready(
                    app,
                    model,
                    timeout=OBSERVABILITY_DEPLOY_TIMEOUT,
                )
            except (JujuWaitException, TimeoutError) as e:
                LOG.debug("Failed to deploy grafana agent", exc_info=True)
                return Result(ResultType.FAILED, str(e))

        return Result(ResultType.COMPLETED)


class RemoveRemoteCosOffersStep(BaseStep, JujuStepHelper):
    """Remove COS Offers across Juju controllers.

    This is a workaround for https://github.com/juju/terraform-provider-juju/issues/119
    """

    def __init__(
        self,
        deployment: Deployment,
        feature: "ObservabilityFeature",
        jhelper: JujuHelper,
    ):
        super().__init__(
            "Remove external Observability offers",
            "Removing external Observability offers",
        )
        self.deployment = deployment
        self.feature = feature
        self.jhelper = jhelper
        self.endpoints = [
            "grafana-agent:grafana-dashboards-provider",
            "grafana-agent:send-remote-write",
            "grafana-agent:logging-consumer",
        ]

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
                continue

        return relations

    def run(self, status: Status | None = None) -> Result:
        """Execute integrations using external offers."""
        for model in [
            OPENSTACK_MODEL,
            self.deployment.openstack_machines_model,
        ]:
            relations = self._get_relations(model, self.endpoints)
            LOG.debug(f"List of relations to remove in model {model}: {relations}")
            for relation_pair in relations:
                self.remove_relation(
                    model,
                    relation_pair[0],
                    relation_pair[1],
                )

        for model in [
            OPENSTACK_MODEL,
            self.deployment.openstack_machines_model,
        ]:
            app = "grafana-agent"
            LOG.debug(f"Application monitored for readiness: {app}")
            try:
                self.jhelper.wait_application_ready(
                    app,
                    model,
                    accepted_status=["blocked"],
                    timeout=OBSERVABILITY_DEPLOY_TIMEOUT,
                )
            except (JujuWaitException, TimeoutError) as e:
                LOG.debug("Failed to deploy grafana agent", exc_info=True)
                return Result(ResultType.FAILED, str(e))

        return Result(ResultType.COMPLETED)


class ObservabilityFeatureGroup(BaseFeatureGroup):
    name = "observability"

    @click.group()
    @pass_method_obj
    def enable_group(self, deployment: Deployment) -> None:
        """Enable Observability service."""

    @click.group()
    @pass_method_obj
    def disable_group(self, deployment: Deployment) -> None:
        """Disable Observability service."""


class ObservabilityFeature(OpenStackControlPlaneFeature):
    version = Version("0.0.1")
    requires = {FeatureRequirement("telemetry")}

    # name = "observability"
    group = ObservabilityFeatureGroup
    tf_plan_location = TerraformPlanLocation.SUNBEAM_TERRAFORM_REPO

    def __init__(self) -> None:
        super().__init__()
        self.tfplan_cos = COS_TFPLAN
        self.tfplan_cos_dir = "deploy-cos"
        self.tfplan_grafana_agent = GRAFANA_AGENT_TFPLAN
        self.tfplan_grafana_agent_dir = "deploy-grafana-agent"
        self.tfplan_grafana_agent_k8s_dir = "deploy-grafana-agent-k8s"

        self.prometheus_offer_url = ""
        self.grafana_offer_url = ""
        self.loki_offer_url = ""

    @property
    def manifest(self) -> Manifest:
        """Return the manifest."""
        if self._manifest:
            return self._manifest

        manifest = click.get_current_context().obj.get_manifest(self.user_manifest)
        self._manifest = manifest

        return manifest

    def default_software_overrides(self) -> SoftwareConfig:
        """Feature software configuration."""
        return SoftwareConfig(
            charms={
                "cos-traefik-k8s": CharmManifest(channel=TRAEFIK_CHANNEL),
                "alertmanager-k8s": CharmManifest(channel=COS_CHANNEL),
                "grafana-k8s": CharmManifest(channel=COS_CHANNEL),
                "catalogue-k8s": CharmManifest(channel=COS_CHANNEL),
                "prometheus-k8s": CharmManifest(channel=COS_CHANNEL),
                "loki-k8s": CharmManifest(channel=COS_CHANNEL),
                "grafana-agent": CharmManifest(channel=GRAFANA_AGENT_CHANNEL),
                "grafana-agent-k8s": CharmManifest(channel=GRAFANA_AGENT_K8S_CHANNEL),
            },
            terraform={
                self.tfplan_cos: TerraformManifest(
                    source=Path(__file__).parent / "etc" / self.tfplan_cos_dir
                ),
                self.tfplan_grafana_agent: TerraformManifest(
                    source=Path(__file__).parent / "etc" / self.tfplan_grafana_agent_dir
                ),
            },
        )

    def manifest_attributes_tfvar_map(self) -> dict:
        """Manifest attributes terraformvars map."""
        return {
            self.tfplan_cos: {
                "charms": {
                    "cos-traefik-k8s": {
                        "channel": "traefik-channel",
                        "revision": "traefik-revision",
                        "config": "traefik-config",
                    },
                    "alertmanager-k8s": {
                        "channel": "alertmanager-channel",
                        "revision": "alertmanager-revision",
                        "config": "alertmanager-config",
                    },
                    "grafana-k8s": {
                        "channel": "grafana-channel",
                        "revision": "grafana-revision",
                        "config": "grafana-config",
                    },
                    "catalogue-k8s": {
                        "channel": "catalogue-channel",
                        "revision": "catalogue-revision",
                        "config": "catalogue-config",
                    },
                    "prometheus-k8s": {
                        "channel": "prometheus-channel",
                        "revision": "prometheus-revision",
                        "config": "prometheus-config",
                    },
                    "loki-k8s": {
                        "channel": "loki-channel",
                        "revision": "loki-revision",
                        "config": "loki-config",
                    },
                }
            },
            self.tfplan_grafana_agent: {
                "charms": {
                    "grafana-agent": {
                        "channel": "grafana-agent-channel",
                        "revision": "grafana-agent-revision",
                        "config": "grafana-agent-config",
                    }
                }
            },
            self.tfplan: {
                "charms": {
                    "grafana-agent-k8s": {
                        "channel": "grafana-agent-channel",
                        "revision": "grafana-agent-revision",
                        "config": "grafana-agent-config",
                    }
                }
            },
        }

    def set_application_names(self, deployment: Deployment) -> list:
        """Application names handled by the main terraform plan."""
        # main plan only handles grafana-agent-k8s, named grafana-agent
        return ["grafana-agent"]

    def get_cos_offer_urls(self, deployment: Deployment) -> dict:
        """Get COS offer URLs."""
        raise NotImplementedError

    def set_tfvars_on_enable(
        self, deployment: Deployment, config: FeatureConfig
    ) -> dict:
        """Set terraform variables to enable the application."""
        tfvars = {
            "enable-observability": True,
        }
        tfvars.update(self.get_cos_offer_urls(deployment))
        return tfvars

    def set_tfvars_on_disable(self, deployment: Deployment) -> dict:
        """Set terraform variables to disable the application."""
        return {
            "enable-observability": False,
            "grafana-dashboard-offer-url": None,
            "logging-offer-url": None,
            "receive-remote-write-offer-url": None,
        }

    def set_tfvars_on_resize(
        self, deployment: Deployment, config: FeatureConfig
    ) -> dict:
        """Set terraform variables to resize the application."""
        return {}

    def get_provider_type(self) -> ProviderType:
        """Return provide type external or embedded."""
        raise NotImplementedError

    def get_provider_type_from_cluster(self, deployment: Deployment) -> str | None:
        """Return provider type from database.

        Return None if provider type is not set in database.
        """
        try:
            config = read_config(deployment.get_client(), OBSERVABILITY_FEATURE_KEY)
        except ConfigItemNotFoundException:
            config = {}

        return config.get("provider")

    def pre_checks(self, deployment: Deployment) -> None:
        """Perform preflight checks before enabling the feature.

        Also copies terraform plans to required locations.
        """
        super().pre_checks(deployment)
        provider = self.get_provider_type_from_cluster(deployment)
        if provider and provider != self.get_provider_type().name:
            raise Exception(f"Observability provider already set to {provider!r}")

    def post_enable(
        self, deployment: Deployment, config: FeatureConfig, show_hints: bool
    ) -> None:
        """Handler to perform tasks after the feature is enabled."""
        super().post_enable(deployment, config, show_hints)
        provider = {
            "provider": self.get_provider_type().name,
        }
        update_config(deployment.get_client(), OBSERVABILITY_FEATURE_KEY, provider)

    def pre_disable(self, deployment: Deployment, show_hints: bool) -> None:
        """Handler to perform tasks before disabling the feature."""
        super().pre_disable(deployment, show_hints)
        try:
            config = read_config(deployment.get_client(), OBSERVABILITY_FEATURE_KEY)
        except ConfigItemNotFoundException:
            config = {}

        provider = config.get("provider")
        if provider and provider != self.get_provider_type().name:
            raise Exception(f"Observability provider set to {provider!r}")

    def post_disable(self, deployment: Deployment, show_hints: bool) -> None:
        """Handler to perform tasks after the feature is disabled."""
        super().post_disable(deployment, show_hints)

        config: dict = {}
        update_config(deployment.get_client(), OBSERVABILITY_FEATURE_KEY, config)

    # @click.group(invoke_without_command=True)
    # @pass_method_obj
    # def enable_cmd(self, deployment: Deployment) -> None:
    #     """Enable Observability service."""
    #     ctx = click.get_current_context()
    #     if ctx.invoked_subcommand is None:
    #         click.echo(
    #             "WARNING: This command is deprecated. "
    #             "Use `sunbeam enable observability embedded` instead."
    #         )
    #         self.enable_feature(deployment, FeatureConfig())

    # @click.group(invoke_without_command=True)
    # @pass_method_obj
    # def disable_cmd(self, deployment: Deployment) -> None:
    #     """Disable Observability service."""
    #     ctx = click.get_current_context()
    #     if ctx.invoked_subcommand is None:
    #         click.echo(
    #             "WARNING: This command is deprecated. "
    #             "Use `sunbeam disable observability embedded` instead."
    #         )
    #         self.disable_feature(deployment, FeatureConfig())

    @click.group()
    def observability_group(self):
        """Manage Observability."""


class EmbeddedObservabilityFeature(ObservabilityFeature):
    name = "observability.embedded"

    def update_proxy_model_configs(
        self, deployment: Deployment, show_hints: bool
    ) -> None:
        """Update proxy model configs."""
        try:
            if not self.is_enabled(deployment.get_client()):
                LOG.debug("Observability feature is not enabled, nothing to do")
                return
        except ClusterServiceUnavailableException:
            LOG.debug(
                "Failed to query for feature status, is cloud bootstrapped ?",
                exc_info=True,
            )
            return

        plan = [
            TerraformInitStep(deployment.get_tfhelper(self.tfplan_cos)),
            UpdateObservabilityModelConfigStep(
                deployment, self, deployment.get_tfhelper(self.tfplan_cos)
            ),
        ]
        run_plan(plan, console, show_hints)

    def run_enable_plans(
        self, deployment: Deployment, config: FeatureConfig, show_hints: bool
    ):
        """Run the enablement plans for embedded."""
        jhelper = JujuHelper(deployment.juju_controller)

        tfhelper = deployment.get_tfhelper(self.tfplan)
        tfhelper_cos = deployment.get_tfhelper(self.tfplan_cos)
        tfhelper_grafana_agent = deployment.get_tfhelper(self.tfplan_grafana_agent)

        client = deployment.get_client()
        plan = []
        if self.user_manifest:
            plan.append(AddManifestStep(client, self.user_manifest))

        cos_plan = [
            TerraformInitStep(tfhelper_cos),
            DeployObservabilityStackStep(deployment, self, tfhelper_cos, jhelper),
        ]
        if is_maas_deployment(deployment):
            cos_plan.append(
                PatchCosLoadBalancerIPPoolStep(client, deployment.public_api_label)  # type: ignore [attr-defined]
            )
        cos_plan.append(PatchCosLoadBalancerIPStep(client))

        grafana_agent_k8s_plan = [
            TerraformInitStep(tfhelper),
            EnableOpenStackApplicationStep(deployment, config, tfhelper, jhelper, self),
        ]

        grafana_agent_plan = [
            TerraformInitStep(tfhelper_grafana_agent),
            DeployGrafanaAgentStep(
                deployment, config, self, tfhelper_grafana_agent, jhelper
            ),
        ]

        run_plan(plan, console, show_hints)
        run_plan(cos_plan, console, show_hints)
        run_plan(grafana_agent_k8s_plan, console, show_hints)
        run_plan(grafana_agent_plan, console, show_hints)

        click.echo("Observability enabled.")

    def run_disable_plans(self, deployment: Deployment, show_hints: bool):
        """Run the disablement plans for embedded."""
        jhelper = JujuHelper(deployment.juju_controller)
        tfhelper = deployment.get_tfhelper(self.tfplan)
        tfhelper_cos = deployment.get_tfhelper(self.tfplan_cos)
        tfhelper_grafana_agent = deployment.get_tfhelper(self.tfplan_grafana_agent)

        agent_grafana_k8s_plan = [
            TerraformInitStep(tfhelper),
            DisableOpenStackApplicationStep(deployment, tfhelper, jhelper, self),
            RemoveSaasApplicationsStep(
                jhelper, OPENSTACK_MODEL, offering_model=OBSERVABILITY_MODEL
            ),
        ]

        grafana_agent_plan = [
            TerraformInitStep(tfhelper_grafana_agent),
            RemoveGrafanaAgentStep(deployment, self, tfhelper_grafana_agent, jhelper),
            RemoveSaasApplicationsStep(
                jhelper,
                deployment.openstack_machines_model,
                offering_model=OBSERVABILITY_MODEL,
            ),
        ]

        cos_plan = [
            TerraformInitStep(tfhelper_cos),
            RemoveObservabilityStackStep(deployment, self, tfhelper_cos, jhelper),
        ]

        run_plan(agent_grafana_k8s_plan, console, show_hints)
        run_plan(grafana_agent_plan, console, show_hints)
        run_plan(cos_plan, console, show_hints)

        click.echo("Observability disabled.")

    @click.command()
    @pass_method_obj
    def dashboard_url(self, deployment: Deployment) -> None:
        """Retrieve COS Dashboard URL."""
        jhelper = JujuHelper(deployment.juju_controller)

        with console.status("Retrieving dashboard URL from Grafana service ... "):
            # Retrieve config from juju actions
            model = OBSERVABILITY_MODEL
            app = "grafana"
            action_cmd = "get-admin-password"
            unit = jhelper.get_leader_unit(app, model)
            if not unit:
                _message = f"Unable to get {app} leader"
                raise click.ClickException(_message)

            try:
                action_result = jhelper.run_action(unit, model, action_cmd)
            except ActionFailedException:
                _message = "Unable to retrieve URL from Grafana service"
                raise click.ClickException(_message)

            url = action_result.get("url")
            if url:
                console.print(url)
            else:
                _message = "No URL provided by Grafana service"
                raise click.ClickException(_message)

    @click.command()
    @click_option_show_hints
    @pass_method_obj
    def enable_cmd(self, deployment: Deployment, show_hints: bool) -> None:
        """Deploy Observability stack."""
        self.enable_feature(deployment, FeatureConfig(), show_hints)

    @click.command()
    @click_option_show_hints
    @pass_method_obj
    def disable_cmd(self, deployment: Deployment, show_hints: bool) -> None:
        """Disable Observability stack."""
        self.disable_feature(deployment, show_hints)

    def enabled_commands(self) -> dict[str, list[dict]]:
        """Dict of clickgroup along with commands.

        Return the commands available once the feature is enabled.
        """
        return {
            "init": [{"name": "observability", "command": self.observability_group}],
            "init.observability": [
                {"name": "dashboard-url", "command": self.dashboard_url}
            ],
        }

    def get_provider_type(self) -> ProviderType:
        """Return provide type external or embedded."""
        return ProviderType.EMBEDDED

    def get_cos_offer_urls(self, deployment: Deployment) -> dict:
        """Return COS offer URLs."""
        tfhelper_cos = deployment.get_tfhelper(self.tfplan_cos)
        output = tfhelper_cos.output()
        return {
            "grafana-dashboard-offer-url": output["grafana-dashboard-offer-url"],
            "logging-offer-url": output["loki-logging-offer-url"],
            "receive-remote-write-offer-url": output[
                "prometheus-receive-remote-write-offer-url"
            ],
        }


class ExternalObservabilityFeature(ObservabilityFeature):
    name = "observability.external"

    def run_enable_plans(
        self, deployment: Deployment, config: FeatureConfig, show_hints: bool
    ):
        """Run the enablement plans for external."""
        jhelper = JujuHelper(deployment.juju_controller)

        tfhelper = deployment.get_tfhelper(self.tfplan)
        tfhelper_grafana_agent = deployment.get_tfhelper(self.tfplan_grafana_agent)

        client = deployment.get_client()
        plan = []
        if self.user_manifest:
            plan.append(AddManifestStep(client, self.user_manifest))

        grafana_agent_k8s_plan = [
            TerraformInitStep(tfhelper),
            EnableOpenStackApplicationStep(
                deployment,
                config,
                tfhelper,
                jhelper,
                self,
                app_desired_status=["active", "blocked"],
            ),
        ]

        grafana_agent_plan = [
            TerraformInitStep(tfhelper_grafana_agent),
            DeployGrafanaAgentStep(
                deployment,
                config,
                self,
                tfhelper_grafana_agent,
                jhelper,
                accepted_app_status=["active", "blocked"],
            ),
        ]

        # Workaround as integrations are not handled in terraform plan
        # https://github.com/juju/terraform-provider-juju/issues/119
        grafana_integrations_plan = [
            IntegrateRemoteCosOffersStep(deployment, self, jhelper)
        ]

        run_plan(plan, console, show_hints)
        run_plan(grafana_agent_k8s_plan, console, show_hints)
        run_plan(grafana_agent_plan, console, show_hints)
        run_plan(grafana_integrations_plan, console, show_hints)

        click.echo("Observability enabled.")

    def run_disable_plans(self, deployment: Deployment, show_hints: bool):
        """Run the disablement plans for external."""
        jhelper = JujuHelper(deployment.juju_controller)
        tfhelper = deployment.get_tfhelper(self.tfplan)
        tfhelper_grafana_agent = deployment.get_tfhelper(self.tfplan_grafana_agent)

        # Workaround as integrations are not handled in terraform plan
        # https://github.com/juju/terraform-provider-juju/issues/119
        grafana_remove_offers_plan = [
            RemoveRemoteCosOffersStep(deployment, self, jhelper)
        ]

        agent_grafana_k8s_plan = [
            TerraformInitStep(tfhelper),
            DisableOpenStackApplicationStep(deployment, tfhelper, jhelper, self),
            RemoveSaasApplicationsStep(
                jhelper,
                OPENSTACK_MODEL,
                offering_interfaces=OBSERVABILITY_OFFER_INTERFACES,
            ),
        ]

        grafana_agent_plan = [
            TerraformInitStep(tfhelper_grafana_agent),
            RemoveGrafanaAgentStep(deployment, self, tfhelper_grafana_agent, jhelper),
            RemoveSaasApplicationsStep(
                jhelper,
                deployment.openstack_machines_model,
                offering_interfaces=OBSERVABILITY_OFFER_INTERFACES,
            ),
        ]

        run_plan(grafana_remove_offers_plan, console, show_hints)
        run_plan(agent_grafana_k8s_plan, console, show_hints)
        run_plan(grafana_agent_plan, console, show_hints)

        click.echo("Observability disabled.")

    @click.command()
    @click.argument(
        "controller",
        type=str,
    )
    @click.argument(
        "grafana-dashboard-offer-url",
        type=str,
    )
    @click.argument(
        "prometheus-receive-remote-write-offer-url",
        type=str,
    )
    @click.argument("loki-logging-offer-url", type=str)
    @click_option_show_hints
    @pass_method_obj
    def enable_cmd(
        self,
        deployment: Deployment,
        controller: str,
        grafana_dashboard_offer_url: str,
        prometheus_receive_remote_write_offer_url: str,
        loki_logging_offer_url: str,
        show_hints: bool,
    ) -> None:
        """Connect to external Observability stack."""
        self.prometheus_offer_url = (
            f"{controller}:{prometheus_receive_remote_write_offer_url}"
        )
        self.grafana_offer_url = f"{controller}:{grafana_dashboard_offer_url}"
        self.loki_offer_url = f"{controller}:{loki_logging_offer_url}"

        data_location = self.snap.paths.user_data
        preflight_checks: list[Check] = []
        preflight_checks.append(
            JujuControllerRegistrationCheck(controller, data_location)
        )
        run_preflight_checks(preflight_checks, console)

        self.enable_feature(deployment, FeatureConfig(), show_hints)

    @click.command()
    @click_option_show_hints
    @pass_method_obj
    def disable_cmd(self, deployment: Deployment, show_hints: bool) -> None:
        """Disable Observability stack."""
        self.disable_feature(deployment, show_hints)

    def get_provider_type(self) -> ProviderType:
        """Return provide type external or embedded."""
        return ProviderType.EXTERNAL

    def get_cos_offer_urls(self, deployment: Deployment) -> dict:
        """Return COS offer URLs."""
        # Returning empty dict as integrations are not handled in terraform plan
        # https://github.com/juju/terraform-provider-juju/issues/119
        # Should return URLs from user input when above bug is fixed
        return {}
