# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import logging
from typing import Any

from rich.status import Status

import sunbeam.steps.microceph as microceph
from sunbeam.clusterd.client import Client
from sunbeam.clusterd.service import (
    NodeNotExistInClusterException,
)
from sunbeam.core.common import BaseStep, Result, ResultType, Role
from sunbeam.core.deployment import Deployment, Networks
from sunbeam.core.juju import (
    ApplicationNotFoundException,
    JujuHelper,
)
from sunbeam.core.manifest import Manifest
from sunbeam.core.steps import (
    DeployMachineApplicationStep,
    DestroyMachineApplicationStep,
    RemoveMachineUnitsStep,
)
from sunbeam.core.terraform import TerraformException, TerraformHelper

LOG = logging.getLogger(__name__)
CONFIG_KEY = "TerraformVarsCinderVolumePlan"
APPLICATION = "cinder-volume"
CINDER_VOLUME_APP_TIMEOUT = 1200
CINDER_VOLUME_UNIT_TIMEOUT = (
    1800  # 30 minutes, adding / removing units can take a long time
)


def get_mandatory_control_plane_offers(
    tfhelper: TerraformHelper,
) -> dict[str, str | None]:
    """Get mandatory control plane offers."""
    openstack_tf_output = tfhelper.output()

    tfvars = {
        "keystone-offer-url": openstack_tf_output.get("keystone-offer-url"),
        "database-offer-url": openstack_tf_output.get(
            "cinder-volume-database-offer-url"
        ),
        "amqp-offer-url": openstack_tf_output.get("rabbitmq-offer-url"),
    }
    return tfvars


class DeployCinderVolumeApplicationStep(DeployMachineApplicationStep):
    """Deploy Cinder Volume application using Terraform."""

    def __init__(
        self,
        deployment: Deployment,
        client: Client,
        tfhelper: TerraformHelper,
        jhelper: JujuHelper,
        manifest: Manifest,
        model: str,
    ):
        super().__init__(
            deployment,
            client,
            tfhelper,
            jhelper,
            manifest,
            CONFIG_KEY,
            APPLICATION,
            model,
            [Role.STORAGE],
            "Deploy Cinder Volume",
            "Deploying Cinder Volume",
        )
        self._offers: dict[str, str | None] = {}

    def get_application_timeout(self) -> int:
        """Return application timeout in seconds."""
        return CINDER_VOLUME_APP_TIMEOUT

    def get_accepted_application_status(self) -> list[str]:
        """Return accepted application status."""
        accepted_status = super().get_accepted_application_status()
        offers = self._get_offers()
        if not offers or not all(offers.values()):
            accepted_status.append("blocked")
        return accepted_status

    def _get_offers(self):
        if not self._offers:
            self._offers = get_mandatory_control_plane_offers(
                self.deployment.get_tfhelper("openstack-plan")
            )
        return self._offers

    def extra_tfvars(self) -> dict:
        """Extra terraform vars to pass to terraform apply."""
        storage_nodes = self.client.cluster.list_nodes_by_role("storage")
        tfvars: dict[str, Any] = {
            "endpoint_bindings": [
                {
                    "space": self.deployment.get_space(Networks.MANAGEMENT),
                },
                {
                    "endpoint": "amqp",
                    "space": self.deployment.get_space(Networks.INTERNAL),
                },
                {
                    "endpoint": "database",
                    "space": self.deployment.get_space(Networks.INTERNAL),
                },
                {
                    "endpoint": "cinder-volume",
                    "space": self.deployment.get_space(Networks.MANAGEMENT),
                },
                {
                    "endpoint": "identity-credentials",
                    "space": self.deployment.get_space(Networks.INTERNAL),
                },
                {
                    # relation to cinder-api
                    "endpoint": "storage-backend",
                    "space": self.deployment.get_space(Networks.INTERNAL),
                },
            ],
            "cinder_volume_ceph_endpoint_bindings": [
                {
                    "space": self.deployment.get_space(Networks.MANAGEMENT),
                },
                {
                    # relation between hypervisor and cinder-volume-ceph
                    # providing credentials to access Ceph
                    "space": self.deployment.get_space(Networks.MANAGEMENT),
                    "endpoint": "ceph-access",
                },
                {
                    "space": self.deployment.get_space(Networks.STORAGE),
                    "endpoint": "ceph",
                },
            ],
            "charm_cinder_volume_config": {},
            "charm_cinder_volume_ceph_config": {
                "ceph-osd-replication-count": microceph.ceph_replica_scale(
                    len(storage_nodes)
                ),
            },
        }

        if len(storage_nodes):
            microceph_tfhelper = self.deployment.get_tfhelper("microceph-plan")
            microceph_tf_output = microceph_tfhelper.output()

            ceph_application_name = microceph_tf_output.get("ceph-application-name")

            if ceph_application_name:
                tfvars["ceph-application-name"] = ceph_application_name
            tfvars.update(self._get_offers())

        return tfvars


class RemoveCinderVolumeUnitsStep(RemoveMachineUnitsStep):
    """Remove Cinder Volume Unit."""

    def __init__(
        self, client: Client, names: list[str] | str, jhelper: JujuHelper, model: str
    ):
        super().__init__(
            client,
            names,
            jhelper,
            CONFIG_KEY,
            APPLICATION,
            model,
            "Remove Cinder Volume unit(s)",
            "Removing Cinder Volume unit(s) from machine",
        )

    def get_unit_timeout(self) -> int:
        """Return unit timeout in seconds."""
        return CINDER_VOLUME_UNIT_TIMEOUT


class CheckCinderVolumeDistributionStep(BaseStep):
    _APPLICATION = APPLICATION

    def __init__(
        self,
        client: Client,
        name: str,
        jhelper: JujuHelper,
        model: str,
        force: bool = False,
    ):
        super().__init__(
            "Check Cinder Volume distribution",
            "Check if node is hosting units of Cinder Volume",
        )
        self.client = client
        self.node = name
        self.jhelper = jhelper
        self.model = model
        self.force = force

    def is_skip(self, status: Status | None = None) -> Result:
        """Determines if the step should be skipped or not.

        :return: ResultType.SKIPPED if the Step should be skipped,
                ResultType.COMPLETED or ResultType.FAILED otherwise
        """
        try:
            node_info = self.client.cluster.get_node_info(self.node)
        except NodeNotExistInClusterException:
            return Result(ResultType.FAILED, f"Node {self.node} not found in cluster")
        if Role.STORAGE.name.lower() not in node_info.get("role", ""):
            LOG.debug("Node %s is not a storage node", self.node)
            return Result(ResultType.SKIPPED)
        try:
            app = self.jhelper.get_application(self._APPLICATION, self.model)
        except ApplicationNotFoundException:
            LOG.debug("Failed to get application", exc_info=True)
            return Result(
                ResultType.SKIPPED,
                f"Application {self._APPLICATION} has not been deployed yet",
            )

        for unit_name, unit in app.units.items():
            if unit.machine == str(node_info.get("machineid")):
                LOG.debug("Unit %s is running on node %s", unit_name, self.node)
                break
        else:
            LOG.debug("No %s units found on %s", self._APPLICATION, self.node)
            return Result(ResultType.SKIPPED)
        nb_storage_nodes = len(self.client.cluster.list_nodes_by_role("storage"))
        if nb_storage_nodes == 1 and not self.force:
            return Result(
                ResultType.FAILED,
                "Cannot remove the last cinder-volume,"
                "--force to override, volume capabilities"
                " will be lost.",
            )

        return Result(ResultType.COMPLETED)


class DestroyCinderVolumeApplicationStep(DestroyMachineApplicationStep):
    """Destroy Cinder Volume application using Terraform."""

    def __init__(
        self,
        client: Client,
        tfhelper: TerraformHelper,
        jhelper: JujuHelper,
        manifest: Manifest,
        model: str,
    ):
        super().__init__(
            client,
            tfhelper,
            jhelper,
            manifest,
            CONFIG_KEY,
            [APPLICATION],
            model,
            "Destroy Cinder Volume",
            "Destroying Cinder Volume",
        )

    def get_application_timeout(self) -> int:
        """Return application timeout in seconds."""
        return CINDER_VOLUME_APP_TIMEOUT

    def run(self, status: Status | None = None) -> Result:
        """Destroy Cinder Volume application."""
        # note(gboutry):this is a workaround for
        # https://github.com/juju/terraform-provider-juju/issues/473
        try:
            resources = self.tfhelper.state_list()
        except TerraformException as e:
            LOG.debug(f"Failed to list terraform state: {str(e)}")
            return Result(ResultType.FAILED, "Failed to list terraform state")

        for resource in resources:
            if "integration" in resource:
                try:
                    self.tfhelper.state_rm(resource)
                except TerraformException as e:
                    LOG.debug(f"Failed to remove resource {resource}: {str(e)}")
                    return Result(
                        ResultType.FAILED,
                        f"Failed to remove resource {resource} from state",
                    )

        return super().run(status)
