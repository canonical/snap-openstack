# SPDX-FileCopyrightText: 2023 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import json
import logging
import queue
from pathlib import Path

import click
import yaml
from packaging.version import Version
from rich.console import Console
from rich.status import Status

from sunbeam.clusterd.service import (
    ConfigItemNotFoundException,
)
from sunbeam.core.common import (
    BaseStep,
    Result,
    ResultType,
    read_config,
    run_plan,
    update_config,
    update_status_background,
)
from sunbeam.core.deployment import Deployment
from sunbeam.core.juju import (
    JujuHelper,
    JujuStepHelper,
    JujuWaitException,
)
from sunbeam.core.manifest import CharmManifest, FeatureConfig, SoftwareConfig
from sunbeam.core.openstack import OPENSTACK_MODEL
from sunbeam.core.terraform import TerraformException, TerraformInitStep
from sunbeam.features.interface.v1.openstack import (
    OpenStackControlPlaneFeature,
    TerraformPlanLocation,
)
from sunbeam.utils import click_option_show_hints, pass_method_obj
from sunbeam.versions import OPENSTACK_CHANNEL

LOG = logging.getLogger(__name__)
console = Console()

APPLICATION_DEPLOY_TIMEOUT = 900  # 15 minutes
APPLICATION_REMOVE_TIMEOUT = 300  # 5 minutes


class DisableLDAPDomainStep(BaseStep, JujuStepHelper):
    """Generic step to enable OpenStack application using Terraform."""

    def __init__(
        self,
        deployment: Deployment,
        config: FeatureConfig,
        jhelper: JujuHelper,
        feature: OpenStackControlPlaneFeature,
        domain_name: str,
    ) -> None:
        """Constructor for the generic plan.

        :param jhelper: Juju helper with loaded juju credentials
        :param feature: Feature that uses this plan to perform callbacks to
                       feature.
        """
        super().__init__(
            f"Enable OpenStack {feature.name}",
            f"Enabling OpenStack {feature.name} application",
        )
        self.deployment = deployment
        self.config = config
        self.jhelper = jhelper
        self.feature = feature
        self.model = OPENSTACK_MODEL
        self.domain_name = domain_name
        self.client = deployment.get_client()
        self.tfhelper = deployment.get_tfhelper(self.feature.tfplan)

    def run(self, status: Status | None = None) -> Result:
        """Apply terraform configuration to deploy openstack application."""
        config_key = self.feature.get_tfvar_config_key()

        try:
            tfvars = read_config(self.client, config_key)
        except ConfigItemNotFoundException:
            tfvars = {}
        tfvars.update(self.feature.set_tfvars_on_enable(self.deployment, self.config))
        if tfvars.get("ldap-apps") and self.domain_name in tfvars["ldap-apps"]:
            del tfvars["ldap-apps"][self.domain_name]
        else:
            return Result(ResultType.FAILED, "Domain not found")
        self.tfhelper.write_tfvars(tfvars)
        update_config(self.client, config_key, tfvars)

        try:
            self.tfhelper.apply()
        except TerraformException as e:
            return Result(ResultType.FAILED, str(e))

        try:
            self.jhelper.wait_application_gone(
                [f"keystone-ldap-{self.domain_name}"],
                self.model,
                timeout=APPLICATION_REMOVE_TIMEOUT,
            )
            self.jhelper.wait_until_desired_status(
                self.model,
                ["keystone"],
                timeout=APPLICATION_REMOVE_TIMEOUT,
            )
        except (JujuWaitException, TimeoutError) as e:
            LOG.warning(str(e))
            return Result(ResultType.FAILED, str(e))

        return Result(ResultType.COMPLETED)


class UpdateLDAPDomainStep(BaseStep, JujuStepHelper):
    def __init__(
        self,
        deployment: Deployment,
        jhelper: JujuHelper,
        feature: OpenStackControlPlaneFeature,
        charm_config: dict,
    ) -> None:
        """Constructor for the generic plan.

        :param jhelper: Juju helper with loaded juju credentials
        :param feature: Feature that uses this plan to perform callbacks to
                       feature.
        """
        super().__init__(
            f"Enable OpenStack {feature.name}",
            f"Enabling OpenStack {feature.name} application",
        )
        self.deployment = deployment
        self.jhelper = jhelper
        self.feature = feature
        self.model = OPENSTACK_MODEL
        self.charm_config = charm_config
        self.client = deployment.get_client()
        self.tfhelper = deployment.get_tfhelper(self.feature.tfplan)

    def run(self, status: Status | None = None) -> Result:
        """Apply terraform configuration to deploy openstack application."""
        config_key = self.feature.get_tfvar_config_key()

        try:
            tfvars = read_config(self.client, config_key)
        except ConfigItemNotFoundException:
            tfvars = {}
        config: dict = tfvars["ldap-apps"].get(self.charm_config["domain-name"])
        if config:
            for k in config.keys():
                if self.charm_config.get(k):
                    config[k] = self.charm_config[k]
        else:
            return Result(ResultType.FAILED, "Domain not found")

        self.tfhelper.write_tfvars(tfvars)
        update_config(self.client, config_key, tfvars)

        try:
            self.tfhelper.apply()
        except TerraformException as e:
            return Result(ResultType.FAILED, str(e))
        charm_name = "keystone-ldap-{}".format(self.charm_config["domain-name"])
        apps = ["keystone", charm_name]
        LOG.debug(f"Application monitored for readiness: {apps}")
        status_queue: queue.Queue[str] = queue.Queue(maxsize=len(apps))
        task = update_status_background(self, apps, status_queue, status)
        try:
            self.jhelper.wait_until_active(
                self.model,
                apps,
                timeout=APPLICATION_DEPLOY_TIMEOUT,
                queue=status_queue,
            )
        except (JujuWaitException, TimeoutError) as e:
            LOG.warning(str(e))
            return Result(ResultType.FAILED, str(e))
        finally:
            task.stop()
        return Result(ResultType.COMPLETED)


class AddLDAPDomainStep(BaseStep, JujuStepHelper):
    """Generic step to enable OpenStack application using Terraform."""

    def __init__(
        self,
        deployment: Deployment,
        config: FeatureConfig,
        jhelper: JujuHelper,
        feature: OpenStackControlPlaneFeature,
        charm_config: dict,
    ) -> None:
        """Constructor for the generic plan.

        :param jhelper: Juju helper with loaded juju credentials
        :param feature: Feature that uses this plan to perform callbacks to
                       feature.
        """
        super().__init__(
            f"Enable OpenStack {feature.name}",
            f"Enabling OpenStack {feature.name} application",
        )
        self.deployment = deployment
        self.config = config
        self.jhelper = jhelper
        self.feature = feature
        self.model = OPENSTACK_MODEL
        self.charm_config = charm_config
        self.client = deployment.get_client()
        self.tfhelper = deployment.get_tfhelper(self.feature.tfplan)

    def run(self, status: Status | None = None) -> Result:
        """Apply terraform configuration to deploy openstack application."""
        config_key = self.feature.get_tfvar_config_key()

        try:
            tfvars = read_config(self.client, config_key)
        except ConfigItemNotFoundException:
            tfvars = {}
        tfvars.update(self.feature.set_tfvars_on_enable(self.deployment, self.config))
        if tfvars.get("ldap-apps"):
            tfvars["ldap-apps"][self.charm_config["domain-name"]] = self.charm_config
        else:
            tfvars["ldap-apps"] = {self.charm_config["domain-name"]: self.charm_config}
        self.tfhelper.write_tfvars(tfvars)
        update_config(self.client, config_key, tfvars)

        try:
            self.tfhelper.apply()
        except TerraformException as e:
            return Result(ResultType.FAILED, str(e))
        charm_name = "keystone-ldap-{}".format(self.charm_config["domain-name"])
        apps = ["keystone", charm_name]
        LOG.debug(f"Application monitored for readiness: {apps}")
        status_queue: queue.Queue[str] = queue.Queue(maxsize=len(apps))
        task = update_status_background(self, apps, status_queue, status)
        try:
            self.jhelper.wait_until_active(
                self.model,
                apps,
                timeout=APPLICATION_DEPLOY_TIMEOUT,
                queue=status_queue,
            )
        except (JujuWaitException, TimeoutError) as e:
            LOG.warning(str(e))
            return Result(ResultType.FAILED, str(e))
        finally:
            task.stop()

        return Result(ResultType.COMPLETED)


class LDAPFeature(OpenStackControlPlaneFeature):
    version = Version("0.0.1")

    name = "ldap"
    tf_plan_location = TerraformPlanLocation.SUNBEAM_TERRAFORM_REPO

    def __init__(self) -> None:
        super().__init__()
        self.config_flags = None

    def default_software_overrides(self) -> SoftwareConfig:
        """Feature software configuration."""
        return SoftwareConfig(
            charms={
                "keystone-ldap-k8s": CharmManifest(channel=OPENSTACK_CHANNEL),
            }
        )

    def manifest_attributes_tfvar_map(self) -> dict:
        """Manifest attributes terraformvars map."""
        return {
            self.tfplan: {
                "charms": {
                    "keystone-ldap-k8s": {
                        "channel": "ldap-channel",
                        "revision": "ldap-revision",
                    }
                }
            }
        }

    def set_tfvars_on_enable(
        self, deployment: Deployment, config: FeatureConfig
    ) -> dict:
        """Set terraform variables to enable the application."""
        return {}

    def set_tfvars_on_disable(self, deployment: Deployment) -> dict:
        """Set terraform variables to disable the application."""
        return {"ldap-apps": {}}

    def set_tfvars_on_resize(
        self, deployment: Deployment, config: FeatureConfig
    ) -> dict:
        """Set terraform variables to resize the application."""
        return {}

    def set_application_names(self, deployment: Deployment) -> list:
        """Application names handled by the terraform plan."""
        return []

    @click.command()
    @click_option_show_hints
    @pass_method_obj
    def enable_cmd(self, deployment: Deployment, show_hints: bool) -> None:
        """Enable ldap service."""
        self.enable_feature(deployment, FeatureConfig(), show_hints)

    @click.command()
    @click_option_show_hints
    @pass_method_obj
    def disable_cmd(self, deployment: Deployment, show_hints: bool) -> None:
        """Disable OpenStack LDAP application."""
        self.disable_feature(deployment, show_hints)

    @click.command()
    @pass_method_obj
    def list_domains(self, deployment: Deployment) -> None:
        """List LDAP backed domains."""
        try:
            tfvars = read_config(deployment.get_client(), self.get_tfvar_config_key())
        except ConfigItemNotFoundException:
            tfvars = {}
        click.echo(" ".join(tfvars.get("ldap-apps", {}).keys()))

    @click.command()
    @click.argument("domain-name")
    @click.option(
        "--domain-config-file",
        required=True,
        help="""
        Config file with entries
        """,
    )
    @click.option(
        "--ca-cert-file",
        required=False,
        help="""
        CA for contacting ldap
        """,
    )
    @click_option_show_hints
    @pass_method_obj
    def add_domain(
        self,
        deployment: Deployment,
        ca_cert_file: str,
        domain_config_file: str,
        domain_name: str,
        show_hints: bool,
    ) -> None:
        """Add LDAP backed domain."""
        with Path(domain_config_file).open(mode="r") as f:
            content = yaml.safe_load(f)
        if ca_cert_file:
            with Path(ca_cert_file).open(mode="r") as f:
                ca = f.read()
        else:
            ca = ""
        charm_config = {
            "ldap-config-flags": json.dumps(content),
            "domain-name": domain_name,
            "tls-ca-ldap": ca,
        }
        jhelper = JujuHelper(deployment.juju_controller)
        plan = [
            TerraformInitStep(deployment.get_tfhelper(self.tfplan)),
            AddLDAPDomainStep(deployment, FeatureConfig(), jhelper, self, charm_config),
        ]

        run_plan(plan, console, show_hints)
        click.echo(f"{domain_name} added.")

    @click.command()
    @click.argument("domain-name")
    @click.option(
        "--domain-config-file",
        required=False,
        help="""
        Config file with entries
        """,
    )
    @click.option(
        "--ca-cert-file",
        required=False,
        help="""
        CA for contacting ldap
        """,
    )
    @click_option_show_hints
    @pass_method_obj
    def update_domain(
        self,
        deployment: Deployment,
        ca_cert_file: str,
        domain_config_file: str,
        domain_name: str,
        show_hints: bool,
    ) -> None:
        """Add LDAP backed domain."""
        charm_config = {"domain-name": domain_name}
        if domain_config_file:
            with Path(domain_config_file).open(mode="r") as f:
                content = yaml.safe_load(f)
            charm_config["ldap-config-flags"] = json.dumps(content)
        if ca_cert_file:
            with Path(ca_cert_file).open(mode="r") as f:
                ca = f.read()
            charm_config["tls-ca-ldap"] = ca
        jhelper = JujuHelper(deployment.juju_controller)
        plan = [
            TerraformInitStep(deployment.get_tfhelper(self.tfplan)),
            UpdateLDAPDomainStep(deployment, jhelper, self, charm_config),
        ]

        run_plan(plan, console, show_hints)

    @click.command()
    @click.argument("domain-name")
    @click_option_show_hints
    @pass_method_obj
    def remove_domain(
        self, deployment: Deployment, domain_name: str, show_hints: bool
    ) -> None:
        """Remove LDAP backed domain."""
        jhelper = JujuHelper(deployment.juju_controller)
        plan = [
            TerraformInitStep(deployment.get_tfhelper(self.tfplan)),
            DisableLDAPDomainStep(
                deployment, FeatureConfig(), jhelper, self, domain_name
            ),
        ]
        run_plan(plan, console, show_hints)
        click.echo(f"{domain_name} removed.")

    @click.group()
    def ldap_groups(self):
        """Manage ldap."""

    def enabled_commands(self) -> dict[str, list[dict]]:
        """Dict of clickgroup along with commands.

        Return the commands available once the feature is enabled.
        """
        return {
            "init": [{"name": "ldap", "command": self.ldap_groups}],
            "init.ldap": [
                {"name": "list-domains", "command": self.list_domains},
                {"name": "add-domain", "command": self.add_domain},
                {"name": "update-domain", "command": self.update_domain},
                {"name": "remove-domain", "command": self.remove_domain},
            ],
        }
