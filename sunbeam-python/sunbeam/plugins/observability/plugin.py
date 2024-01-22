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

"""Observability plugin.

Plugin to deploy and manage observability, powered by COS Lite.
"""

import logging
import shutil
from pathlib import Path
from typing import Optional

import click
from packaging.version import Version
from rich.console import Console
from rich.status import Status
from snaphelpers import Snap

from sunbeam.clusterd.client import Client
from sunbeam.clusterd.service import (
    ClusterServiceUnavailableException,
    ConfigItemNotFoundException,
)
from sunbeam.commands.juju import JujuStepHelper
from sunbeam.commands.microk8s import (
    CREDENTIAL_SUFFIX,
    MICROK8S_CLOUD,
    MICROK8S_DEFAULT_STORAGECLASS,
)
from sunbeam.commands.openstack import OPENSTACK_MODEL, PatchLoadBalancerServicesStep
from sunbeam.commands.terraform import (
    TerraformException,
    TerraformHelper,
    TerraformInitStep,
)
from sunbeam.jobs.common import (
    BaseStep,
    Result,
    ResultType,
    read_config,
    run_plan,
    update_config,
    update_status_background,
)
from sunbeam.jobs.juju import (
    CONTROLLER_MODEL,
    JujuHelper,
    JujuWaitException,
    TimeoutException,
    run_sync,
)
from sunbeam.plugins.interface.v1.base import EnableDisablePlugin, PluginRequirement
from sunbeam.plugins.interface.v1.openstack import (
    OPENSTACK_TERRAFORM_PLAN,
    OPENSTACK_TERRAFORM_VARS,
)

LOG = logging.getLogger(__name__)
console = Console()

OBSERVABILITY_MODEL = "observability"
OBSERVABILITY_DEPLOY_TIMEOUT = 1200  # 20 minutes
CONTROLLER_MODEL = CONTROLLER_MODEL.split("/")[-1]


class FillObservabilityOffersStep(BaseStep):
    """Update terraform plan to fill observability offers."""

    def __init__(
        self,
        client: Client,
        tfhelper: TerraformHelper,
        tfhelper_cos: TerraformHelper,
        jhelper: JujuHelper,
    ) -> None:
        super().__init__(
            "Fill Observability Offers",
            "Fill Observability Offers in Openstack",
        )
        self.client = client
        self.tfhelper = tfhelper
        self.tfhelper_cos = tfhelper_cos
        self.jhelper = jhelper
        self.model = OPENSTACK_MODEL

    def run(self, status: Optional[Status] = None) -> Result:
        """Apply terraform configuration"""
        config_key = OPENSTACK_TERRAFORM_VARS

        try:
            tfvars = read_config(self.client, config_key)
        except ConfigItemNotFoundException:
            tfvars = {}
        output_vars = self.tfhelper_cos.output()

        for key, value in output_vars.items():
            if key in (
                "prometheus-metrics-offer-url",
                "grafana-dashboard-offer-url",
            ):
                tfvars[key] = value
        update_config(self.client, config_key, tfvars)
        self.tfhelper.write_tfvars(tfvars)

        try:
            self.tfhelper.apply()
        except TerraformException as e:
            return Result(ResultType.FAILED, str(e))

        return Result(ResultType.COMPLETED)


class RemoveObservabilityIntegrationStep(BaseStep):
    def __init__(
        self,
        client: Client,
        tfhelper: TerraformHelper,
        jhelper: JujuHelper,
    ) -> None:
        super().__init__(
            "Remove Observability Integration",
            "Remove Observability Integration in Openstack",
        )
        self.client = client
        self.tfhelper = tfhelper
        self.jhelper = jhelper
        self.model = OPENSTACK_MODEL

    def run(self, status: Optional[Status] = None) -> Result:
        """Apply terraform configuration"""
        config_key = OPENSTACK_TERRAFORM_VARS

        try:
            tfvars = read_config(self.client, config_key)
        except ConfigItemNotFoundException:
            tfvars = {}

        tfvars.pop("prometheus-metrics-offer-url", None)
        tfvars.pop("grafana-dashboard-offer-url", None)
        update_config(self.client, config_key, tfvars)
        self.tfhelper.write_tfvars(tfvars)

        try:
            self.tfhelper.apply()
        except TerraformException as e:
            return Result(ResultType.FAILED, str(e))

        return Result(ResultType.COMPLETED)


class DeployObservabilityStackStep(BaseStep, JujuStepHelper):
    """Deploy Observability Stack using Terraform"""

    def __init__(
        self,
        plugin: "ObservabilityPlugin",
        tfhelper: TerraformHelper,
        jhelper: JujuHelper,
    ):
        super().__init__("Deploy Observability Stack", "Deploying Observability Stack")
        self.tfhelper = tfhelper
        self.jhelper = jhelper
        self.model = OBSERVABILITY_MODEL
        self.cloud = MICROK8S_CLOUD
        self.read_config = lambda: plugin.get_plugin_info().get(
            "observability-stack-config", {}
        )
        self.update_config = lambda c: plugin.update_plugin_info(
            {"observability-stack-config": c}
        )

    def run(self, status: Optional[Status] = None) -> Result:
        """Execute configuration using terraform."""

        try:
            config = self.read_config()
        except ConfigItemNotFoundException as e:
            LOG.exception("Failed deploying Observability Stack: unable to read config")
            return Result(ResultType.FAILED, str(e))

        tfvars = {
            "model": self.model,
            "cos-channel": "1.0/candidate",
            "cloud": self.cloud,
            "credential": f"{self.cloud}{CREDENTIAL_SUFFIX}",
            "config": {"workload-storage": MICROK8S_DEFAULT_STORAGECLASS},
        }
        config.update(tfvars)
        self.update_config(config)
        self.tfhelper.write_tfvars(tfvars)

        self.update_status(status, "deploying services")
        try:
            self.tfhelper.apply()
        except TerraformException as e:
            LOG.exception("Error deploying Observability Stack")
            return Result(ResultType.FAILED, str(e))

        apps = run_sync(self.jhelper.get_application_names(self.model))
        LOG.debug(f"Application monitored for readiness: {apps}")
        task = run_sync(update_status_background(self, apps, status))
        try:
            run_sync(
                self.jhelper.wait_until_active(
                    self.model,
                    apps,
                    timeout=OBSERVABILITY_DEPLOY_TIMEOUT,
                )
            )
        except (JujuWaitException, TimeoutException) as e:
            LOG.debug("Failed to deploy Observability Stack", exc_info=True)
            return Result(ResultType.FAILED, str(e))
        finally:
            if not task.done():
                task.cancel()

        return Result(ResultType.COMPLETED)


class DeployGrafanaAgentStep(BaseStep, JujuStepHelper):
    """Deploy Grafana Agent using Terraform"""

    def __init__(
        self,
        plugin: "ObservabilityPlugin",
        tfhelper: TerraformHelper,
        tfhelper_cos: TerraformHelper,
        jhelper: JujuHelper,
    ):
        super().__init__("Deploy Grafana Agent", "Deploy Grafana Agent")
        self.tfhelper = tfhelper
        self.tfhelper_cos = tfhelper_cos
        self.jhelper = jhelper
        self.model = CONTROLLER_MODEL
        self.read_config = lambda: plugin.get_plugin_info().get(
            "grafana-agent-config", {}
        )
        self.update_config = lambda c: plugin.update_plugin_info(
            {"grafana-agent-config": c}
        )

    def run(self, status: Optional[Status] = None) -> Result:
        """Execute configuration using terraform."""

        cos_backend = self.tfhelper_cos.backend
        cos_backend_config = self.tfhelper_cos.backend_config()
        try:
            config = self.read_config()
        except ConfigItemNotFoundException as e:
            LOG.exception("Failed deploying grafana agent: unable to read config")
            return Result(ResultType.FAILED, str(e))

        tfvars = {
            "grafana-agent-channel": "latest/edge",
            "principal-application-model": self.model,
            "cos-state-backend": cos_backend,
            "cos-state-config": cos_backend_config,
            "principal-application": "openstack-hypervisor",
        }
        config.update(tfvars)
        self.update_config(config)
        self.tfhelper.write_tfvars(tfvars)

        self.update_status(status, "deploying application")
        try:
            self.tfhelper.apply()
        except TerraformException as e:
            LOG.exception("Error deploying grafana agent")
            return Result(ResultType.FAILED, str(e))

        app = "grafana-agent"
        LOG.debug(f"Application monitored for readiness: {app}")
        try:
            run_sync(
                self.jhelper.wait_application_ready(
                    app,
                    self.model,
                    timeout=OBSERVABILITY_DEPLOY_TIMEOUT,
                )
            )
        except (JujuWaitException, TimeoutException) as e:
            LOG.debug("Failed to deploy grafana agent", exc_info=True)
            return Result(ResultType.FAILED, str(e))

        return Result(ResultType.COMPLETED)


class RemoveObservabilityStackStep(BaseStep, JujuStepHelper):
    """Remove Observability Stack using Terraform"""

    def __init__(
        self,
        plugin: "ObservabilityPlugin",
        tfhelper: TerraformHelper,
        jhelper: JujuHelper,
    ):
        super().__init__("Remove Observability Stack", "Removing Observability Stack")
        self.tfhelper = tfhelper
        self.jhelper = jhelper
        self.model = OBSERVABILITY_MODEL
        self.cloud = MICROK8S_CLOUD
        self.read_config = lambda: plugin.get_plugin_info().get(
            "observability-stack-config", {}
        )
        self.update_config = lambda c: plugin.update_plugin_info(
            {"observability-stack-config": c}
        )

    def run(self, status: Optional[Status] = None) -> Result:
        """Execute configuration using terraform."""

        try:
            config = self.read_config()
        except ConfigItemNotFoundException as e:
            LOG.exception("Failed removing Observability Stack: unable to read config")
            return Result(ResultType.FAILED, str(e))

        tfvars = {
            "model": self.model,
            "cos-channel": "1.0/candidate",
            "cloud": self.cloud,
            "credential": f"{self.cloud}{CREDENTIAL_SUFFIX}",
            "config": {"workload-storage": MICROK8S_DEFAULT_STORAGECLASS},
        }
        config.update(tfvars)
        self.update_config(config)
        self.tfhelper.write_tfvars(tfvars)
        try:
            self.tfhelper.destroy()
        except TerraformException as e:
            LOG.exception("Error destroying Observability Stack")
            return Result(ResultType.FAILED, str(e))

        try:
            run_sync(
                self.jhelper.wait_model_gone(
                    self.model,
                    timeout=OBSERVABILITY_DEPLOY_TIMEOUT,
                )
            )
        except TimeoutException as e:
            LOG.debug("Failed to destroy Observability Stack", exc_info=True)
            return Result(ResultType.FAILED, str(e))

        return Result(ResultType.COMPLETED)


class RemoveGrafanaAgentStep(BaseStep, JujuStepHelper):
    """Remove Grafana Agent using Terraform"""

    def __init__(
        self,
        plugin: "ObservabilityPlugin",
        tfhelper: TerraformHelper,
        tfhelper_cos: TerraformHelper,
        jhelper: JujuHelper,
    ):
        super().__init__("Remove Grafana Agent", "Removing Grafana Agent")
        self.tfhelper = tfhelper
        self.tfhelper_cos = tfhelper_cos
        self.jhelper = jhelper
        self.model = CONTROLLER_MODEL
        self.read_config = lambda: plugin.get_plugin_info().get(
            "grafana-agent-config", {}
        )
        self.update_config = lambda c: plugin.update_plugin_info(
            {"grafana-agent-config": c}
        )

    def run(self, status: Optional[Status] = None) -> Result:
        """Execute configuration using terraform."""

        cos_backend = self.tfhelper_cos.backend
        cos_backend_config = self.tfhelper_cos.backend_config()
        try:
            config = self.read_config()
        except ConfigItemNotFoundException as e:
            LOG.exception("Failed removing grafana agent: unable to read config")
            return Result(ResultType.FAILED, str(e))

        tfvars = {
            "grafana-agent-channel": "latest/edge",
            "principal-application-model": self.model,
            "cos-state-backend": cos_backend,
            "cos-state-config": cos_backend_config,
            "principal-application": "openstack-hypervisor",
        }
        config.update(tfvars)
        self.update_config(config)
        self.tfhelper.write_tfvars(tfvars)
        try:
            self.tfhelper.destroy()
        except TerraformException as e:
            LOG.exception("Error destroying grafana agent")
            return Result(ResultType.FAILED, str(e))

        apps = ["grafana-agent"]
        try:
            run_sync(
                self.jhelper.wait_application_gone(
                    apps,
                    self.model,
                    timeout=OBSERVABILITY_DEPLOY_TIMEOUT,
                )
            )
        except TimeoutException as e:
            LOG.debug("Failed to destroy grafana agent", exc_info=True)
            return Result(ResultType.FAILED, str(e))

        return Result(ResultType.COMPLETED)


class PatchCosLoadBalancerStep(PatchLoadBalancerServicesStep):
    SERVICES = ["traefik"]
    MODEL = OBSERVABILITY_MODEL


class ObservabilityPlugin(EnableDisablePlugin):
    version = Version("0.0.1")
    requires = {PluginRequirement("telemetry", optional=True)}

    def __init__(self, client: Client) -> None:
        super().__init__("observability", client)
        self.snap = Snap()
        self.tfplan_cos = "deploy-cos"
        self.tfplan_grafana_agent = "deploy-grafana-agent"

    def pre_enable(self):
        src = Path(__file__).parent / "etc" / self.tfplan_cos
        dst = self.snap.paths.user_common / "etc" / self.tfplan_cos
        LOG.debug(f"Updating {dst} from {src}...")
        shutil.copytree(src, dst, dirs_exist_ok=True)

        src = Path(__file__).parent / "etc" / self.tfplan_grafana_agent
        dst = self.snap.paths.user_common / "etc" / self.tfplan_grafana_agent
        LOG.debug(f"Updating {dst} from {src}...")
        shutil.copytree(src, dst, dirs_exist_ok=True)

    def run_enable_plans(self):
        data_location = self.snap.paths.user_data
        tfhelper_cos = TerraformHelper(
            path=self.snap.paths.user_common / "etc" / self.tfplan_cos,
            plan="cos-plan",
            backend="http",
            data_location=data_location,
        )
        tfhelper_grafana_agent = TerraformHelper(
            path=self.snap.paths.user_common / "etc" / self.tfplan_grafana_agent,
            plan="grafana-agent-plan",
            backend="http",
            data_location=data_location,
        )
        openstack_plan = "deploy-" + OPENSTACK_TERRAFORM_PLAN
        tfhelper_openstack = TerraformHelper(
            path=self.snap.paths.user_common / "etc" / openstack_plan,
            plan=OPENSTACK_TERRAFORM_PLAN + "-plan",
            backend="http",
            data_location=data_location,
        )

        jhelper = JujuHelper(self.client, data_location)

        cos_plan = [
            TerraformInitStep(tfhelper_cos),
            DeployObservabilityStackStep(self, tfhelper_cos, jhelper),
            PatchCosLoadBalancerStep(self.client),
            FillObservabilityOffersStep(
                self.client, tfhelper_openstack, tfhelper_cos, jhelper
            ),
        ]

        grafana_agent_plan = [
            TerraformInitStep(tfhelper_grafana_agent),
            DeployGrafanaAgentStep(self, tfhelper_grafana_agent, tfhelper_cos, jhelper),
        ]

        run_plan(cos_plan, console)
        run_plan(grafana_agent_plan, console)

        click.echo("Observability enabled.")

    def pre_disable(self):
        self.pre_enable()

    def run_disable_plans(self):
        data_location = self.snap.paths.user_data
        tfhelper_cos = TerraformHelper(
            path=self.snap.paths.user_common / "etc" / self.tfplan_cos,
            plan="cos-plan",
            backend="http",
            data_location=data_location,
        )
        tfhelper_grafana_agent = TerraformHelper(
            path=self.snap.paths.user_common / "etc" / self.tfplan_grafana_agent,
            plan="grafana-agent-plan",
            backend="http",
            data_location=data_location,
        )
        openstack_plan = "deploy-" + OPENSTACK_TERRAFORM_PLAN
        tfhelper_openstack = TerraformHelper(
            path=self.snap.paths.user_common / "etc" / openstack_plan,
            plan=OPENSTACK_TERRAFORM_PLAN + "-plan",
            backend="http",
            data_location=data_location,
        )

        jhelper = JujuHelper(self.client, data_location)

        cos_plan = [
            TerraformInitStep(tfhelper_cos),
            RemoveObservabilityIntegrationStep(
                self.client, tfhelper_openstack, jhelper
            ),
            RemoveObservabilityStackStep(self, tfhelper_cos, jhelper),
        ]

        grafana_agent_plan = [
            TerraformInitStep(tfhelper_grafana_agent),
            RemoveGrafanaAgentStep(self, tfhelper_grafana_agent, tfhelper_cos, jhelper),
        ]

        run_plan(grafana_agent_plan, console)
        run_plan(cos_plan, console)
        click.echo("Observability disabled.")

    @click.command()
    def enable_plugin(self) -> None:
        """Enable Observability."""
        super().enable_plugin()

    @click.command()
    def disable_plugin(self) -> None:
        """Disable  Observability."""
        super().disable_plugin()

    @click.group()
    def observability_group(self):
        """Manage Observability."""

    @click.command()
    def dashboard_url(self) -> None:
        """Retrieve COS Dashboard URL."""
        data_location = self.snap.paths.user_data
        jhelper = JujuHelper(self.client, data_location)

        with console.status("Retrieving dashboard URL from Grafana service ... "):
            # Retrieve config from juju actions
            model = OBSERVABILITY_MODEL
            app = "grafana"
            action_cmd = "get-admin-password"
            unit = run_sync(jhelper.get_leader_unit(app, model))
            if not unit:
                _message = f"Unable to get {app} leader"
                raise click.ClickException(_message)

            action_result = run_sync(jhelper.run_action(unit, model, action_cmd))

            if action_result.get("return-code", 0) > 1:
                _message = "Unable to retrieve URL from Grafana service"
                raise click.ClickException(_message)

            url = action_result.get("url")
            if url:
                console.print(url)
            else:
                _message = "No URL provided by Grafana service"
                raise click.ClickException(_message)

    def commands(self) -> dict:
        """Dict of clickgroup along with commands."""
        commands = super().commands()
        try:
            enabled = self.enabled
        except ClusterServiceUnavailableException:
            LOG.debug(
                "Failed to query for plugin status, is cloud bootstrapped ?",
                exc_info=True,
            )
            enabled = False

        if enabled:
            commands.update(
                {
                    "init": [
                        {"name": "observability", "command": self.observability_group}
                    ],
                    "observability": [
                        {"name": "dashboard-url", "command": self.dashboard_url}
                    ],
                }
            )
        return commands
