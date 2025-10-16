# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import abc
import logging
import queue

import click
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
    update_status_background,
)
from sunbeam.core.deployment import Deployment
from sunbeam.core.juju import (
    ActionFailedException,
    JujuHelper,
    JujuStepHelper,
    JujuWaitException,
    LeaderNotFoundException,
)
from sunbeam.core.openstack import OPENSTACK_MODEL
from sunbeam.features.baremetal import constants
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
    ):
        super().__init__(
            "Run the set-temp-url-secret action on ironic-conductor",
            "Running the set-temp-url-secret action on ironic-conductor",
        )
        self.jhelper = jhelper
        self.deployment = deployment
        self.model = OPENSTACK_MODEL

    def run(self, status: Status | None = None) -> Result:
        """Run the set-temp-url-secret action on ironic-conductor."""
        try:
            unit = self.jhelper.get_leader_unit(
                constants.IRONIC_CONDUCTOR_APP,
                self.model,
            )
            self.jhelper.run_action(
                unit,
                self.model,
                "set-temp-url-secret",
            )
        except (ActionFailedException, LeaderNotFoundException) as e:
            LOG.error(
                "Error running the set-temp-url-secret action on ironic-conductor: %s",
                e,
            )
            return Result(ResultType.FAILED, str(e))

        apps = [constants.IRONIC_CONDUCTOR_APP]
        LOG.debug(f"Application monitored for readiness: {apps}")
        status_queue: queue.Queue[str] = queue.Queue()
        task = update_status_background(self, apps, status_queue, status)
        try:
            self.jhelper.wait_until_active(
                self.model,
                apps,
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
    ):
        super().__init__(name, description)
        self.deployment = deployment
        self.feature = feature
        self.manifest = feature.manifest
        self.tfvars_key = tfvars_key
        self.client = deployment.get_client()
        self.config_key = feature.get_tfvar_config_key()
        self.tfhelper = deployment.get_tfhelper(feature.tfplan)

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
        jhelper = JujuHelper(self.deployment.juju_controller)

        try:
            jhelper.wait_until_active(
                OPENSTACK_MODEL,
                apps,
                timeout=constants.IRONIC_APP_TIMEOUT,
                queue=status_queue,
            )
        except (JujuWaitException, TimeoutError):
            raise click.ClickException(
                f"Timed out waiting for {apps} to become active."
            )
        finally:
            task.stop()

        click.echo(f"Resource(s) {apps} added.")


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
    ):
        super().__init__(name, description, deployment, feature, tfvars_key)
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
        jhelper = JujuHelper(self.deployment.juju_controller)
        try:
            jhelper.wait_application_gone(
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
