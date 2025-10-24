# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import abc
import logging
import queue

import click
from rich import box
from rich.console import Console
from rich.status import Status
from rich.table import Column, Table

from sunbeam.clusterd.service import (
    ConfigItemNotFoundException,
)
from sunbeam.core.common import (
    BaseStep,
    Result,
    ResultType,
    read_config,
    update_status_background,
)
from sunbeam.core.deployment import Deployment
from sunbeam.core.juju import (
    ActionFailedException,
    JujuHelper,
    JujuSecretNotFound,
    JujuStepHelper,
    JujuWaitException,
    LeaderNotFoundException,
)
from sunbeam.core.openstack import OPENSTACK_MODEL
from sunbeam.features.baremetal import constants, feature_config
from sunbeam.features.interface.v1.openstack import (
    OpenStackControlPlaneFeature,
)

LOG = logging.getLogger(__name__)
console = Console()


class RunSetTempUrlSecretStep(BaseStep, JujuStepHelper):
    """Run the set-temp-url-secret action on the ironic-conductor."""

    def __init__(
        self,
        deployment: Deployment,
        jhelper: JujuHelper,
        apps: list[str] = [constants.IRONIC_CONDUCTOR_APP],
    ):
        super().__init__(
            "Run the set-temp-url-secret action on ironic-conductor",
            "Running the set-temp-url-secret action on ironic-conductor",
        )
        self.jhelper = jhelper
        self.deployment = deployment
        self.model = OPENSTACK_MODEL
        self.apps = apps

    def run(self, status: Status | None = None) -> Result:
        """Run the set-temp-url-secret action on ironic-conductor apps."""
        try:
            for app in self.apps:
                unit = self.jhelper.get_leader_unit(
                    app,
                    self.model,
                )
                self.jhelper.run_action(
                    unit,
                    self.model,
                    "set-temp-url-secret",
                )
        except (ActionFailedException, LeaderNotFoundException) as e:
            LOG.error(
                "Error running the set-temp-url-secret action on %s: %s",
                app,
                e,
            )
            return Result(ResultType.FAILED, str(e))

        LOG.debug(f"Application monitored for readiness: {self.apps}")
        status_queue: queue.Queue[str] = queue.Queue()
        task = update_status_background(self, self.apps, status_queue, status)
        try:
            self.jhelper.wait_until_active(
                self.model,
                self.apps,
                timeout=constants.IRONIC_APP_TIMEOUT,
                queue=status_queue,
            )
        except (JujuWaitException, TimeoutError) as e:
            LOG.warning(str(e))
            return Result(ResultType.FAILED, str(e))
        finally:
            task.stop()

        return Result(ResultType.COMPLETED)


class _BaseStep(abc.ABC, BaseStep, JujuStepHelper):
    def __init__(
        self,
        name: str,
        description: str,
        deployment: Deployment,
        feature: OpenStackControlPlaneFeature,
        tfvars_key: str,
        apps_desired_status: list[str] = ["active"],
    ):
        super().__init__(name, description)
        self.deployment = deployment
        self.feature = feature
        self.manifest = feature.manifest
        self.tfvars_key = tfvars_key
        self.client = deployment.get_client()
        self.config_key = feature.get_tfvar_config_key()
        self.tfhelper = deployment.get_tfhelper(feature.tfplan)
        self.jhelper = JujuHelper(self.deployment.juju_controller)
        self.apps_desired_status = apps_desired_status

    def run(self, status: Status | None = None) -> Result:
        """Execute step."""
        try:
            self._run()
        except Exception as ex:
            LOG.exception(str(ex))
            return Result(ResultType.FAILED, str(ex))

        return Result(ResultType.COMPLETED)

    @abc.abstractmethod
    def _run(self) -> None:
        pass

    def _get_tfvars(self) -> dict:
        try:
            return read_config(self.client, self.config_key)
        except ConfigItemNotFoundException:
            return {}

    def _apply_tfvars(
        self,
        tfvars: dict,
        apps: list[str],
    ) -> None:
        self.tfhelper.update_tfvars_and_apply_tf(
            self.client,
            self.manifest,
            tfvar_config=self.config_key,
            override_tfvars=tfvars,
        )
        LOG.debug(f"Applications monitored for readiness: {apps}")
        status_queue: queue.Queue[str] = queue.Queue()
        task = update_status_background(self.feature, apps, status_queue)

        try:
            self.jhelper.wait_until_desired_status(
                OPENSTACK_MODEL,
                apps,
                timeout=constants.IRONIC_APP_TIMEOUT,
                queue=status_queue,
                status=self.apps_desired_status,
            )
        except (JujuWaitException, TimeoutError):
            raise click.ClickException(
                f"Timed out waiting for {apps} to become active."
            )
        finally:
            task.stop()


class _DeployResourcesStep(_BaseStep):
    """Deploy resources using Terraform."""

    def __init__(
        self,
        name: str,
        description: str,
        deployment: Deployment,
        feature: OpenStackControlPlaneFeature,
        tfvars_key: str,
        items: dict[str, dict],
        charm_name_prefix: str,
        replace: bool = False,
        apps_desired_status: list[str] = ["active"],
    ):
        super().__init__(
            name, description, deployment, feature, tfvars_key, apps_desired_status
        )
        self.items = items
        self.charm_name_prefix = charm_name_prefix
        self.replace = replace

    def _run(self) -> None:
        tfvars = self._get_tfvars()
        current_items = tfvars.get(self.tfvars_key, {})

        if self.replace:
            current_items = self.items
        else:
            for item, charm_config in self.items.items():
                if item in current_items:
                    raise click.ClickException(f"Resource {item} already exists.")
                current_items[item] = charm_config

        tfvars[self.tfvars_key] = current_items

        apps = [f"{self.charm_name_prefix}-{suffix}" for suffix in self.items.keys()]
        self._apply_tfvars(tfvars, apps)

        click.echo(f"Resource(s) {apps} added.")


class _DeleteResourcesStep(_BaseStep):
    """Delete resources using Terraform."""

    def __init__(
        self,
        name: str,
        description: str,
        deployment: Deployment,
        feature: OpenStackControlPlaneFeature,
        tfvars_key: str,
        item: str,
        charm_name: str,
    ):
        super().__init__(name, description, deployment, feature, tfvars_key)
        self.item = item
        self.charm_name = charm_name

    def _run(self) -> None:
        tfvars = self._get_tfvars()
        items = tfvars.get(self.tfvars_key, {})
        if self.item not in items:
            raise click.ClickException(f"Resource {self.item} doesn't exist.")

        items.pop(self.item)

        self.tfhelper.update_tfvars_and_apply_tf(
            self.client,
            self.manifest,
            tfvar_config=self.config_key,
            override_tfvars=tfvars,
        )

        LOG.debug(f"Waiting for application to disappear: {self.charm_name}")
        try:
            self.jhelper.wait_application_gone(
                [self.charm_name],
                OPENSTACK_MODEL,
                timeout=constants.IRONIC_APP_TIMEOUT,
            )
        except (JujuWaitException, TimeoutError):
            raise click.ClickException(
                f"Timed out waiting for {self.charm_name} to disappear."
            )

        click.echo(f"Resource {self.item} deleted.")


class _ListResourcesStep(_BaseStep):
    """List resources."""

    def _run(self) -> None:
        """List resources."""
        tfvars = self._get_tfvars()
        items = tfvars.get(self.tfvars_key, {})
        for item in items.keys():
            console.print(item)


class DeployNovaIronicShardsStep(_DeployResourcesStep):
    """Deploy nova-ironic shards using Terraform."""

    def __init__(
        self,
        deployment: Deployment,
        feature: OpenStackControlPlaneFeature,
        shards: list[str],
        replace: bool = False,
    ):
        # item_name: charm_config
        items = {}
        for shard in shards:
            items[shard] = {"shard": shard}

        super().__init__(
            "Deploy nova-ironic shards",
            "Deploying nova-ironic shards",
            deployment,
            feature,
            constants.NOVA_IRONIC_SHARDS_TFVAR,
            items,
            "nova-ironic",
            replace,
        )


class DeleteNovaIronicShardStep(_DeleteResourcesStep):
    """Delete nova-ironic shards using Terraform."""

    def __init__(
        self,
        deployment: Deployment,
        feature: OpenStackControlPlaneFeature,
        shard: str,
    ):
        super().__init__(
            "Delete nova-ironic shard",
            "Deleting nova-ironic shard",
            deployment,
            feature,
            constants.NOVA_IRONIC_SHARDS_TFVAR,
            shard,
            f"nova-ironic-{shard}",
        )


class ListNovaIronicShardsStep(_ListResourcesStep):
    """List nova-ironic shards."""

    def __init__(
        self,
        deployment: Deployment,
        feature: OpenStackControlPlaneFeature,
    ):
        super().__init__(
            "List nova-ironic shards",
            "Listing nova-ironic shards",
            deployment,
            feature,
            constants.NOVA_IRONIC_SHARDS_TFVAR,
        )


class DeployIronicConductorGroupsStep(_DeployResourcesStep):
    """Deploy ironic-conductor groups using Terraform."""

    def __init__(
        self,
        deployment: Deployment,
        feature: OpenStackControlPlaneFeature,
        conductor_groups: list[str],
        replace: bool = False,
    ):
        # item_name: charm_config
        items = {}
        for conductor_group in conductor_groups:
            items[conductor_group] = {"conductor-group": conductor_group}

        super().__init__(
            "Deploy ironic-conductor groups",
            "Deploying ironic-conductor groups",
            deployment,
            feature,
            constants.IRONIC_CONDUCTOR_GROUPS_TFVAR,
            items,
            "ironic-conductor",
            replace,
            apps_desired_status=["active", "blocked"],
        )


class DeleteIronicConductorGroupStep(_DeleteResourcesStep):
    """Delete ironic-conductor group using Terraform."""

    def __init__(
        self,
        deployment: Deployment,
        feature: OpenStackControlPlaneFeature,
        group_name: str,
    ):
        super().__init__(
            "Delete ironic-conductor group",
            "Deleting ironic-conductor group",
            deployment,
            feature,
            constants.IRONIC_CONDUCTOR_GROUPS_TFVAR,
            group_name,
            f"ironic-conductor-{group_name}",
        )


class ListIronicConductorGroupsStep(_ListResourcesStep):
    """List ironic-conductor groups."""

    def __init__(
        self,
        deployment: Deployment,
        feature: OpenStackControlPlaneFeature,
    ):
        super().__init__(
            "List ironic-conductor groups",
            "Listing ironic-conductor groups",
            deployment,
            feature,
            constants.IRONIC_CONDUCTOR_GROUPS_TFVAR,
        )


class UpdateSwitchConfigSecretsStep(_BaseStep):
    """Update Neutron baremetal / generic switch config secrets."""

    def __init__(
        self,
        deployment: Deployment,
        feature: OpenStackControlPlaneFeature,
        switch_configs: feature_config._SwitchConfigs,
    ):
        super().__init__(
            "Update neutron baremetal / generic switch configs",
            "Updating neutron baremetal / generic switch configs",
            deployment,
            feature,
            constants.NEUTRON_SWITCH_CONF_SECRETS_TFVAR,
        )
        self.switch_configs = switch_configs

    def _run(self) -> None:
        """Update juju secrets containing switch configs."""
        tfvars = self._get_tfvars()
        conf_secrets = tfvars.get(self.tfvars_key, {})
        tfvars[self.tfvars_key] = conf_secrets

        # Remove existing secrets and add new ones.
        secrets = conf_secrets.get("netconf", [])
        self._remove_secrets(secrets)
        secret_ids = self._add_secrets("netconf", self.switch_configs.netconf)
        opt = ",".join(secret_ids)
        tfvars[constants.NEUTRON_BAREMETAL_SWITCH_CONF_SECRETS_TFVAR] = opt
        new_names = self.switch_configs.netconf.keys()
        conf_secrets["netconf"] = [f"switch-config-{name}" for name in new_names]

        secrets = conf_secrets.get("generic", [])
        self._remove_secrets(secrets)
        secret_ids = self._add_secrets("generic", self.switch_configs.generic)
        opt = ",".join(secret_ids)
        tfvars[constants.NEUTRON_GENERIC_SWITCH_CONF_SECRETS_TFVAR] = opt
        new_names = self.switch_configs.generic.keys()
        conf_secrets["generic"] = [f"switch-config-{name}" for name in new_names]

        # write and apply terraform changes.
        apps = ["neutron"]
        if self.switch_configs.netconf:
            apps.append("neutron-baremetal-switch-config")
        if self.switch_configs.generic:
            apps.append("neutron-generic-switch-config")

        self._apply_tfvars(tfvars, apps)

    def _remove_secrets(self, secrets_list: list[str]):
        for secret in secrets_list:
            self.jhelper.remove_secret(OPENSTACK_MODEL, secret)
            secrets_list.remove(secret)

    def _add_secrets(self, protocol: str, configs: dict[str, feature_config._Config]):
        config_charm = "neutron-baremetal-switch-config"
        if protocol == "generic":
            config_charm = "neutron-generic-switch-config"

        secret_ids = []
        for config_name, config in configs.items():
            secret_name = f"switch-config-{config_name}"
            secret_data = {
                "conf": config.configfile,
                **config.additional_files,
            }

            secret_id = self.jhelper.add_secret(
                OPENSTACK_MODEL,
                secret_name,
                secret_data,
                "Neutron switch config",
            )

            for app in ["neutron", config_charm]:
                self.jhelper.grant_secret(OPENSTACK_MODEL, secret_name, app)

            secret_ids.append(secret_id)

        return secret_ids


class AddSwitchConfigStep(_BaseStep):
    """Add Neutron switch configuration using Terraform."""

    def __init__(
        self,
        deployment: Deployment,
        feature: OpenStackControlPlaneFeature,
        protocol: str,
        name: str,
        config_obj: feature_config._Config,
    ):
        super().__init__(
            "Add Neutron switch configuration",
            "Adding Neutron switch configuration",
            deployment,
            feature,
            constants.NEUTRON_SWITCH_CONF_SECRETS_TFVAR,
        )
        self.protocol = protocol
        self.name = name
        self.config_obj = config_obj

        self.config_charm = "neutron-baremetal-switch-config"
        if protocol == "generic":
            self.config_charm = "neutron-generic-switch-config"

    @property
    def _switch_config_secret_name(self):
        return f"switch-config-{self.name}"

    def _run(self) -> None:
        secret_name = self._switch_config_secret_name
        if self.jhelper.secret_exists(OPENSTACK_MODEL, secret_name):
            raise click.ClickException(f"Secret {secret_name} already exists.")

        # Create secret and grant it to the config charm and neutron.
        secret_data = {
            "conf": self.config_obj.configfile,
            **self.config_obj.additional_files,
        }
        secret_id = self.jhelper.add_secret(
            OPENSTACK_MODEL,
            secret_name,
            secret_data,
            "Neutron switch config",
        )

        for app in ["neutron", self.config_charm]:
            self.jhelper.grant_secret(OPENSTACK_MODEL, secret_name, app)

        # Update charm's "conf-secrets" config.
        tfvars_key = constants.SWITCH_CONFIG_TFVAR[self.protocol]
        tfvars = self._get_tfvars()

        val = tfvars.get(tfvars_key)
        val = ",".join([val, secret_id]) if val else secret_id
        tfvars[tfvars_key] = val

        # update list of secrets.
        tfvars_key = constants.NEUTRON_SWITCH_CONF_SECRETS_TFVAR
        conf_secrets = tfvars.get(tfvars_key, {})
        secret_list = conf_secrets.get(self.protocol, [])

        secret_list.append(secret_name)
        conf_secrets[self.protocol] = secret_list
        tfvars[tfvars_key] = conf_secrets

        self._apply_tfvars(tfvars, [self.config_charm, "neutron"])


class UpdateSwitchConfigStep(_BaseStep):
    """Update Neutron switch configuration using Terraform."""

    def __init__(
        self,
        deployment: Deployment,
        feature: OpenStackControlPlaneFeature,
        protocol: str,
        name: str,
        config_obj: feature_config._Config,
    ):
        super().__init__(
            "Update Neutron switch configuration",
            "Updating Neutron switch configuration",
            deployment,
            feature,
            constants.NEUTRON_SWITCH_CONF_SECRETS_TFVAR,
        )
        self.protocol = protocol
        self.name = name
        self.config_obj = config_obj

        self.config_charm = "neutron-baremetal-switch-config"
        if protocol == "generic":
            self.config_charm = "neutron-generic-switch-config"

    @property
    def _switch_config_secret_name(self):
        return f"switch-config-{self.name}"

    def _run(self) -> None:
        secret_name = self._switch_config_secret_name
        if not self.jhelper.secret_exists(OPENSTACK_MODEL, secret_name):
            raise click.ClickException(f"Secret {secret_name} does not exist.")

        # Update secret.
        secret_data = {
            "conf": self.config_obj.configfile,
            **self.config_obj.additional_files,
        }
        self.jhelper.update_secret(
            OPENSTACK_MODEL,
            secret_name,
            secret_data,
        )

        click.echo(f"Switch config {self.name} updated.")


class DeleteSwitchConfigStep(_BaseStep):
    """Delete Neutron Switch Config using Terraform."""

    def __init__(
        self,
        deployment: Deployment,
        feature: OpenStackControlPlaneFeature,
        name: str,
    ):
        super().__init__(
            "Delete Neutron switch config",
            "Deleting Neutron switch config",
            deployment,
            feature,
            constants.NEUTRON_SWITCH_CONF_SECRETS_TFVAR,
            apps_desired_status=["active", "blocked"],
        )
        self.name = name

    @property
    def _switch_config_secret_name(self):
        return f"switch-config-{self.name}"

    def _run(self) -> None:
        secret_name = self._switch_config_secret_name

        try:
            secret = self.jhelper.show_secret(OPENSTACK_MODEL, secret_name)
        except JujuSecretNotFound:
            raise click.ClickException(f"Secret {secret_name} does not exist.")

        # Remove secret.
        self.jhelper.remove_secret(
            OPENSTACK_MODEL,
            secret_name,
        )

        # infer protocol.
        tfvars_key = constants.NEUTRON_SWITCH_CONF_SECRETS_TFVAR
        tfvars = self._get_tfvars()
        conf_secrets = tfvars.get(tfvars_key, {})
        if secret_name in conf_secrets.get("netconf", []):
            protocol = "netconf"
            config_charm = "neutron-baremetal-switch-config"
        else:
            protocol = "generic"
            config_charm = "neutron-generic-switch-config"

        conf_secrets[protocol].remove(secret_name)

        # Update and apply terraform vars.
        tfvars_key = constants.SWITCH_CONFIG_TFVAR[protocol]
        conf_secrets = tfvars.get(tfvars_key, "").split(",")
        secret_id = secret.uri.unique_identifier
        if secret_id in conf_secrets:
            conf_secrets.remove(secret_id)

        tfvars[tfvars_key] = ",".join(conf_secrets)

        self._apply_tfvars(
            tfvars,
            [config_charm, "neutron"],
        )


class ListSwitchConfigsStep(_BaseStep):
    """List Neutron Switch Configs."""

    def __init__(
        self,
        deployment: Deployment,
        feature: OpenStackControlPlaneFeature,
    ):
        super().__init__(
            "List Neutron switch configs",
            "Listing Neutron switch configs",
            deployment,
            feature,
            constants.NEUTRON_SWITCH_CONF_SECRETS_TFVAR,
        )

    def _run(self) -> None:
        """List resources."""
        tfvars = self._get_tfvars()
        items = tfvars.get(self.tfvars_key, {})

        table = Table(
            Column("Protocol"),
            Column("Name"),
            box=box.SIMPLE,
        )
        for protocol in ["netconf", "generic"]:
            for secret_name in items.get(protocol, []):
                if secret_name.startswith("switch-config-"):
                    name = secret_name.removeprefix("switch-config-")
                    table.add_row(protocol, name)

        console.print(table)
