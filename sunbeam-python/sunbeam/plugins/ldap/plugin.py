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

import json
import logging
from pathlib import Path
from typing import Optional

import click
import yaml
from packaging.version import Version
from rich.console import Console
from rich.status import Status

from sunbeam.clusterd.client import Client
from sunbeam.clusterd.service import (
    ClusterServiceUnavailableException,
    ConfigItemNotFoundException,
)
from sunbeam.commands.juju import JujuStepHelper
from sunbeam.commands.openstack import OPENSTACK_MODEL
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
)
from sunbeam.jobs.juju import JujuHelper, JujuWaitException, TimeoutException, run_sync
from sunbeam.plugins.interface.v1.openstack import (
    OpenStackControlPlanePlugin,
    TerraformPlanLocation,
)

LOG = logging.getLogger(__name__)
console = Console()

APPLICATION_DEPLOY_TIMEOUT = 900  # 15 minutes
APPLICATION_REMOVE_TIMEOUT = 300  # 5 minutes


class DisableLDAPDomainStep(BaseStep, JujuStepHelper):
    """Generic step to enable OpenStack application using Terraform"""

    def __init__(
        self,
        client: Client,
        tfhelper: TerraformHelper,
        jhelper: JujuHelper,
        plugin: OpenStackControlPlanePlugin,
        domain_name: str,
    ) -> None:
        """Constructor for the generic plan.

        :param tfhelper: Terraform helper pointing to terraform plan
        :param jhelper: Juju helper with loaded juju credentials
        :param plugin: Plugin that uses this plan to perform callbacks to
                       plugin.
        """
        super().__init__(
            f"Enable OpenStack {plugin.name}",
            f"Enabling OpenStack {plugin.name} application",
        )
        self.client = client
        self.tfhelper = tfhelper
        self.jhelper = jhelper
        self.plugin = plugin
        self.model = OPENSTACK_MODEL
        self.domain_name = domain_name

    def run(self, status: Optional[Status] = None) -> Result:
        """Apply terraform configuration to deploy openstack application"""
        config_key = self.plugin.get_tfvar_config_key()

        try:
            tfvars = read_config(self.client, config_key)
        except ConfigItemNotFoundException:
            tfvars = {}
        tfvars.update(self.plugin.set_tfvars_on_enable())
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
            run_sync(
                self.jhelper.wait_application_gone(
                    [f"keystone-ldap-{self.domain_name}"],
                    self.model,
                    timeout=APPLICATION_REMOVE_TIMEOUT,
                )
            )
            run_sync(
                self.jhelper.wait_all_units_ready(
                    "keystone",
                    self.model,
                    timeout=APPLICATION_REMOVE_TIMEOUT,
                )
            )
        except (JujuWaitException, TimeoutException) as e:
            LOG.warning(str(e))
            return Result(ResultType.FAILED, str(e))

        return Result(ResultType.COMPLETED)


class UpdateLDAPDomainStep(BaseStep, JujuStepHelper):
    def __init__(
        self,
        client: Client,
        tfhelper: TerraformHelper,
        jhelper: JujuHelper,
        plugin: OpenStackControlPlanePlugin,
        charm_config: str,
    ) -> None:
        """Constructor for the generic plan.

        :param tfhelper: Terraform helper pointing to terraform plan
        :param jhelper: Juju helper with loaded juju credentials
        :param plugin: Plugin that uses this plan to perform callbacks to
                       plugin.
        """
        super().__init__(
            f"Enable OpenStack {plugin.name}",
            f"Enabling OpenStack {plugin.name} application",
        )
        self.client = client
        self.tfhelper = tfhelper
        self.jhelper = jhelper
        self.plugin = plugin
        self.model = OPENSTACK_MODEL
        self.charm_config = charm_config

    def run(self, status: Optional[Status] = None) -> Result:
        """Apply terraform configuration to deploy openstack application"""
        config_key = self.plugin.get_tfvar_config_key()

        try:
            tfvars = read_config(self.client, config_key)
        except ConfigItemNotFoundException:
            tfvars = {}
        config = tfvars["ldap-apps"].get(self.charm_config["domain-name"])
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
        try:
            run_sync(
                self.jhelper.wait_until_active(
                    self.model,
                    apps,
                    timeout=APPLICATION_DEPLOY_TIMEOUT,
                )
            )
        except (JujuWaitException, TimeoutException) as e:
            LOG.warning(str(e))
            return Result(ResultType.FAILED, str(e))
        return Result(ResultType.COMPLETED)


class AddLDAPDomainStep(BaseStep, JujuStepHelper):
    """Generic step to enable OpenStack application using Terraform"""

    def __init__(
        self,
        client: Client,
        tfhelper: TerraformHelper,
        jhelper: JujuHelper,
        plugin: OpenStackControlPlanePlugin,
        charm_config: str,
    ) -> None:
        """Constructor for the generic plan.

        :param tfhelper: Terraform helper pointing to terraform plan
        :param jhelper: Juju helper with loaded juju credentials
        :param plugin: Plugin that uses this plan to perform callbacks to
                       plugin.
        """
        super().__init__(
            f"Enable OpenStack {plugin.name}",
            f"Enabling OpenStack {plugin.name} application",
        )
        self.client = client
        self.tfhelper = tfhelper
        self.jhelper = jhelper
        self.plugin = plugin
        self.model = OPENSTACK_MODEL
        self.charm_config = charm_config

    def run(self, status: Optional[Status] = None) -> Result:
        """Apply terraform configuration to deploy openstack application"""
        config_key = self.plugin.get_tfvar_config_key()

        try:
            tfvars = read_config(self.client, config_key)
        except ConfigItemNotFoundException:
            tfvars = {}
        tfvars.update(self.plugin.set_tfvars_on_enable())
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
        try:
            run_sync(
                self.jhelper.wait_until_active(
                    self.model,
                    apps,
                    timeout=APPLICATION_DEPLOY_TIMEOUT,
                )
            )
        except (JujuWaitException, TimeoutException) as e:
            LOG.warning(str(e))
            return Result(ResultType.FAILED, str(e))

        return Result(ResultType.COMPLETED)


class LDAPPlugin(OpenStackControlPlanePlugin):
    version = Version("0.0.1")

    def __init__(self, client: Client) -> None:
        super().__init__(
            "ldap",
            client,
            tf_plan_location=TerraformPlanLocation.SUNBEAM_TERRAFORM_REPO,
        )
        self.config_flags = None

    def set_tfvars_on_enable(self) -> dict:
        """Set terraform variables to enable the application."""
        return {
            "ldap-channel": "2023.2/edge",
        }

    def set_tfvars_on_disable(self) -> dict:
        """Set terraform variables to disable the application."""
        return {"ldap-apps": {}}

    def set_tfvars_on_resize(self) -> dict:
        """Set terraform variables to resize the application."""
        return {}

    def set_application_names(self) -> list:
        """Application names handled by the terraform plan."""
        return []

    @click.command()
    def enable_plugin(self):
        """Enable ldap service."""
        super().enable_plugin()

    @click.command()
    def disable_plugin(self) -> None:
        """Disable OpenStack LDAP application."""
        super().disable_plugin()

    @click.command()
    def list_domains(self) -> None:
        """List LDAP backed domains."""
        try:
            tfvars = read_config(self.client, self.get_tfvar_config_key())
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
    def add_domain(
        self, ca_cert_file: str, domain_config_file: str, domain_name: str
    ) -> None:
        """Add LDAP backed domain."""
        with Path(domain_config_file).open(mode="r") as f:
            content = yaml.safe_load(f)
        if ca_cert_file:
            with Path(ca_cert_file).open(mode="r") as f:
                ca = f.read()
        else:
            ca = ""
        data_location = self.snap.paths.user_data
        charm_config = {
            "ldap-config-flags": json.dumps(content),
            "domain-name": domain_name,
            "tls-ca-ldap": ca,
        }
        data_location = self.snap.paths.user_data
        tfhelper = TerraformHelper(
            path=self.snap.paths.user_common / "etc" / f"deploy-{self.tfplan}",
            plan=self._get_plan_name(),
            backend="http",
            data_location=data_location,
        )
        jhelper = JujuHelper(self.client, data_location)
        plan = [
            TerraformInitStep(tfhelper),
            AddLDAPDomainStep(self.client, tfhelper, jhelper, self, charm_config),
        ]

        run_plan(plan, console)
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
    def update_domain(
        self, ca_cert_file: str, domain_config_file: str, domain_name: str
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
        data_location = self.snap.paths.user_data
        tfhelper = TerraformHelper(
            path=self.snap.paths.user_common / "etc" / f"deploy-{self.tfplan}",
            plan=self._get_plan_name(),
            backend="http",
            data_location=data_location,
        )
        jhelper = JujuHelper(self.client, data_location)
        plan = [
            TerraformInitStep(tfhelper),
            UpdateLDAPDomainStep(self.client, tfhelper, jhelper, self, charm_config),
        ]

        run_plan(plan, console)

    @click.command()
    @click.argument("domain-name")
    def remove_domain(self, domain_name: str) -> None:
        """Remove LDAP backed domain."""
        data_location = self.snap.paths.user_data
        tfhelper = TerraformHelper(
            path=self.snap.paths.user_common / "etc" / f"deploy-{self.tfplan}",
            plan=self._get_plan_name(),
            backend="http",
            data_location=data_location,
        )
        jhelper = JujuHelper(self.client, data_location)
        plan = [
            TerraformInitStep(tfhelper),
            DisableLDAPDomainStep(self.client, tfhelper, jhelper, self, domain_name),
        ]
        run_plan(plan, console)
        click.echo(f"{domain_name} removed.")

    @click.group()
    def ldap_groups(self):
        """Manage ldap."""

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
                    "init": [{"name": "ldap", "command": self.ldap_groups}],
                    "ldap": [
                        {"name": "list-domains", "command": self.list_domains},
                        {"name": "add-domain", "command": self.add_domain},
                        {"name": "update-domain", "command": self.update_domain},
                        {"name": "remove-domain", "command": self.remove_domain},
                    ],
                }
            )
        return commands
