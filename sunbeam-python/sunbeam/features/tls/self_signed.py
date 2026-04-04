# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import click
from packaging.version import Version
from rich.console import Console

from sunbeam.core.common import BaseStep, run_plan, update_config
from sunbeam.core.deployment import Deployment
from sunbeam.core.juju import (
    ActionFailedException,
    JujuException,
    JujuHelper,
    LeaderNotFoundException,
)
from sunbeam.core.openstack import OPENSTACK_MODEL
from sunbeam.features.interface.utils import (
    encode_base64_as_string,
    is_certificate_valid,
)
from sunbeam.features.interface.v1.openstack import (
    TerraformPlanLocation,
    WaitForApplicationsStep,
)
from sunbeam.features.tls.common import (
    CERTIFICATE_FEATURE_KEY,
    INGRESS_CHANGE_APPLICATION_TIMEOUT,
    AddCACertsToKeystoneStep,
    TlsFeature,
    TlsFeatureConfig,
)
from sunbeam.utils import click_option_show_hints, pass_method_obj

console = Console()
CA_APP_NAME = "certificate-authority"
CA_CERTIFICATE_ACTION = "get-ca-certificate"


class SelfSignedTlsFeature(TlsFeature):
    version = Version("0.0.1")

    name = "tls.self-signed"
    tf_plan_location = TerraformPlanLocation.SUNBEAM_TERRAFORM_REPO

    def config_type(self) -> type | None:
        """Return the config type for the feature."""
        return TlsFeatureConfig

    @click.command()
    @click.option(
        "--endpoint",
        "endpoints",
        multiple=True,
        default=["public"],
        type=click.Choice(["public", "internal", "rgw"], case_sensitive=False),
        help="Specify which endpoints to apply TLS for.",
    )
    @click_option_show_hints
    @pass_method_obj
    def enable_cmd(
        self,
        deployment: Deployment,
        endpoints: list[str],
        show_hints: bool,
    ):
        """Enable self-signed TLS feature."""
        self.enable_feature(
            deployment,
            TlsFeatureConfig(endpoints=endpoints),
            show_hints,
        )

    @click.command()
    @click_option_show_hints
    @pass_method_obj
    def disable_cmd(self, deployment: Deployment, show_hints: bool):
        """Disable self-signed TLS feature."""
        self.disable_feature(deployment, show_hints)

    def set_application_names(self, deployment: Deployment) -> list:
        """Application names handled by the terraform plan."""
        return []

    def set_tfvars_on_enable(
        self, deployment: Deployment, config: TlsFeatureConfig
    ) -> dict:
        """Set terraform variables to enable the application."""
        tfvars: dict[str, str | bool] = {
            "traefik-to-tls-provider": CA_APP_NAME,
        }
        if "public" in config.endpoints:
            tfvars["enable-tls-for-public-endpoint"] = True
        if "internal" in config.endpoints:
            tfvars["enable-tls-for-internal-endpoint"] = True
        if "rgw" in config.endpoints:
            tfvars["enable-tls-for-rgw-endpoint"] = True
        return tfvars

    def set_tfvars_on_disable(self, deployment: Deployment) -> dict:
        """Set terraform variables to disable the application."""
        tfvars: dict[str, None | bool] = {"traefik-to-tls-provider": None}
        provider_config = self.provider_config(deployment)
        endpoints = provider_config.get("endpoints", [])
        if "public" in endpoints:
            tfvars["enable-tls-for-public-endpoint"] = False
        if "internal" in endpoints:
            tfvars["enable-tls-for-internal-endpoint"] = False
        if "rgw" in endpoints:
            tfvars["enable-tls-for-rgw-endpoint"] = False
        return tfvars

    def set_tfvars_on_resize(
        self, deployment: Deployment, config: TlsFeatureConfig
    ) -> dict:
        """Set terraform variables to resize the application."""
        return {}

    def _fetch_provider_ca(self, deployment: Deployment) -> str:
        """Fetch and validate CA from the certificate-authority application."""
        jhelper = JujuHelper(deployment.juju_controller)
        try:
            unit = jhelper.get_leader_unit(CA_APP_NAME, OPENSTACK_MODEL)
            action_result = jhelper.run_action(
                unit, OPENSTACK_MODEL, CA_CERTIFICATE_ACTION
            )
        except (ActionFailedException, JujuException, LeaderNotFoundException) as e:
            raise click.ClickException(
                "Cannot enable TLS self-signed because the certificate-authority "
                f"application is not ready: {e}"
            ) from e

        ca_pem = action_result.get("ca-certificate")
        if not ca_pem:
            raise click.ClickException(
                "Cannot enable TLS self-signed because certificate-authority "
                "did not return a CA certificate."
            )

        ca = encode_base64_as_string(ca_pem)
        if not ca or not is_certificate_valid(ca.encode()):
            raise click.ClickException(
                "Cannot enable TLS self-signed because certificate-authority "
                "returned an invalid CA certificate."
            )
        return ca

    def _ensure_provider_ready(self, deployment: Deployment) -> None:
        """Ensure the local certificate-authority application is ready."""
        jhelper = JujuHelper(deployment.juju_controller)
        try:
            jhelper.get_leader_unit(CA_APP_NAME, OPENSTACK_MODEL)
        except (JujuException, LeaderNotFoundException) as e:
            raise click.ClickException(
                "Cannot enable TLS self-signed because the certificate-authority "
                f"application is not ready: {e}"
            ) from e

    def _local_apps_to_monitor(
        self, deployment: Deployment, endpoints: list[str]
    ) -> list[str]:
        """Local applications to monitor after enabling or disabling self-signed TLS."""
        apps: list[str] = []
        if "internal" in endpoints:
            apps.append("traefik")
        if "public" in endpoints:
            apps.append("traefik-public")
        if "rgw" in endpoints and deployment.get_client().cluster.list_nodes_by_role(
            "storage"
        ):
            apps.append("traefik-rgw")
        if not deployment.external_keystone_model:
            apps.append("keystone")
        return apps

    def pre_enable(
        self, deployment: Deployment, config: TlsFeatureConfig, show_hints: bool
    ) -> None:
        """Validate certificate-authority availability before enabling."""
        super().pre_enable(deployment, config, show_hints)
        if deployment.region_ctrl_juju_controller:
            config.ca = self._fetch_provider_ca(deployment)
            return

        self._ensure_provider_ready(deployment)

    def post_enable(
        self, deployment: Deployment, config: TlsFeatureConfig, show_hints: bool
    ) -> None:
        """Finalize the self-signed TLS feature enablement."""
        local_apps_to_monitor = self._local_apps_to_monitor(
            deployment, config.endpoints
        )
        plan: list[BaseStep] = []
        if deployment.region_ctrl_juju_controller:
            jhelper_keystone = JujuHelper(deployment.region_ctrl_juju_controller)
            plan.append(
                AddCACertsToKeystoneStep(
                    jhelper_keystone,
                    self.ca_cert_name(deployment.get_region_name()),
                    config.ca,  # type: ignore[arg-type]
                    config.ca_chain,
                )
            )

        if local_apps_to_monitor:
            plan.append(
                WaitForApplicationsStep(
                    deployment.get_juju_helper(),
                    local_apps_to_monitor,
                    OPENSTACK_MODEL,
                    INGRESS_CHANGE_APPLICATION_TIMEOUT,
                )
            )
        if deployment.external_keystone_model:
            plan.append(
                WaitForApplicationsStep(
                    deployment.get_juju_helper(keystone=True),
                    ["keystone"],
                    OPENSTACK_MODEL,
                    INGRESS_CHANGE_APPLICATION_TIMEOUT,
                )
            )
        run_plan(plan, console, show_hints)

        update_config(
            deployment.get_client(),
            CERTIFICATE_FEATURE_KEY,
            {
                "provider": self.name,
                "endpoints": config.endpoints,
            },
        )

    def post_disable(self, deployment: Deployment, show_hints: bool) -> None:
        """Finalize the self-signed TLS feature disablement."""
        if deployment.region_ctrl_juju_controller:
            super().post_disable(deployment, show_hints)
            return

        provider_config = self.provider_config(deployment)
        apps_to_monitor = self._local_apps_to_monitor(
            deployment, provider_config.get("endpoints", [])
        )
        run_plan(
            [
                WaitForApplicationsStep(
                    deployment.get_juju_helper(),
                    apps_to_monitor,
                    OPENSTACK_MODEL,
                    INGRESS_CHANGE_APPLICATION_TIMEOUT,
                )
            ],
            console,
            show_hints,
        )
        update_config(deployment.get_client(), CERTIFICATE_FEATURE_KEY, {})
