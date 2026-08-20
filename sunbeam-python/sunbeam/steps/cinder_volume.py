# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import logging
import typing
from typing import Any

import sunbeam.steps.microceph as microceph
from sunbeam import versions
from sunbeam.clusterd.client import Client
from sunbeam.clusterd.service import (
    NodeNotExistInClusterException,
)
from sunbeam.core.common import (
    BaseStep,
    Result,
    ResultType,
    Role,
    StepContext,
    SunbeamException,
)
from sunbeam.core.deployment import Deployment, Networks
from sunbeam.core.juju import (
    ApplicationNotFoundException,
    JujuException,
    JujuHelper,
)
from sunbeam.core.manifest import CharmManifest, Manifest
from sunbeam.core.openstack import OPENSTACK_MODEL
from sunbeam.core.openstack_api import get_admin_connection
from sunbeam.core.steps import (
    DeployMachineApplicationStep,
    DestroyMachineApplicationStep,
    RemoveMachineUnitsStep,
)
from sunbeam.core.terraform import TerraformException, TerraformHelper
from sunbeam.lazy import LazyImport

if typing.TYPE_CHECKING:
    import openstack
    from keystoneauth1 import exceptions as keystoneauth_exceptions
else:
    keystoneauth_exceptions = LazyImport("keystoneauth1.exceptions")
    openstack = LazyImport("openstack")

LOG = logging.getLogger(__name__)
CONFIG_KEY = "TerraformVarsCinderVolumePlan"
APPLICATION = "cinder-volume"
CINDER_VOLUME_APP_TIMEOUT = 1200
CINDER_VOLUME_UNIT_TIMEOUT = (
    1800  # 30 minutes, adding / removing units can take a long time
)
CINDER_APPLICATION = "cinder"
CINDER_API_CONTAINER = "cinder-api"
CINDER_VOLUME_BINARY = "cinder-volume"
CINDER_SERVICE_REMOVE_REASON = "Removing node from cluster"
CINDER_SERVICE_REMOVE_TIMEOUT = 1800


def get_cinder_volume_services(
    conn: "openstack.connection.Connection",
    hostname: str,
    fqdn: str,
) -> list[Any]:
    """Return cinder-volume services for the exact node hostnames."""
    expected_hosts = {hostname, fqdn}

    services = []
    for service in conn.block_storage.services(binary=CINDER_VOLUME_BINARY):
        if service.host.split("@", 1)[0] in expected_hosts:
            services.append(service)

    return sorted(services, key=lambda service: service.host)


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


def get_optional_control_plane_offers(
    tfhelper: TerraformHelper,
) -> dict[str, str | None]:
    """Get optional control plane offers."""
    openstack_tf_output = tfhelper.output()

    tfvars = {
        "cert-distributor-offer-url": openstack_tf_output.get(
            "cert-distributor-offer-url"
        ),
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
        extra_tfvars: dict | None = None,
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
        self._optional_offers: dict[str, str | None] = {}
        self.override_tfvars: dict[str, Any] = extra_tfvars or {}

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

    def _get_optional_offers(self):
        if not self._optional_offers:
            self._optional_offers = get_optional_control_plane_offers(
                self.deployment.get_tfhelper("openstack-plan")
            )
        return self._optional_offers

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
                    "endpoint": "receive-ca-cert",
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
            "charm_cinder_volume_config": {"snap-channel": versions.OPENSTACK_CHANNEL},
            "charm_cinder_volume_ceph_config": {
                "ceph-osd-replication-count": microceph.ceph_replica_scale(
                    len(storage_nodes)
                ),
            },
        }

        charm_manifest: CharmManifest | None = self.manifest.core.software.charms.get(
            APPLICATION
        )
        if charm_manifest and charm_manifest.config:
            tfvars["charm_cinder_volume_config"].update(charm_manifest.config)

        # This may not be required ideally as Cinder volume is deployed always
        # before user can enable or disable telemetry.
        feature_manager = self.deployment.get_feature_manager()
        if feature_manager.is_feature_enabled(self.deployment, "telemetry"):
            tfvars["enable-telemetry-notifications"] = True
        else:
            tfvars["enable-telemetry-notifications"] = False

        if len(storage_nodes):
            microceph_tfhelper = self.deployment.get_tfhelper("microceph-plan")
            microceph_tf_output = microceph_tfhelper.output()

            ceph_application_name = microceph_tf_output.get("ceph-application-name")

            if ceph_application_name:
                tfvars["ceph-application-name"] = ceph_application_name
            tfvars.update(self._get_offers())
            tfvars.update(self._get_optional_offers())

        # Any tfvars that needs override will take precedence from self.override_tfvars
        # Example usage: When telemetry is enabled/disabled, telemetry feature can set
        # enable-telemetry-notifications using override_tfvars
        tfvars.update(self.override_tfvars)

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


class _CinderVolumeServiceStep(BaseStep):
    """Shared service discovery for Cinder cleanup steps."""

    def __init__(
        self,
        name: str,
        description: str,
        jhelper: JujuHelper,
        deployment: Deployment,
        hostname: str,
        fqdn: str,
    ):
        super().__init__(name, description)
        self.jhelper = jhelper
        self.deployment = deployment
        self.hostname = hostname
        self.fqdn = fqdn
        self.connection: Any | None = None
        self.services: list[Any] = []

    def _discover_services(self) -> Result:
        """Discover Cinder service records for the target node."""
        try:
            self.connection = get_admin_connection(self.jhelper, self.deployment)
            self.services = get_cinder_volume_services(
                self.connection, self.hostname, self.fqdn
            )
        except (
            openstack.exceptions.SDKException,
            keystoneauth_exceptions.ClientException,
        ) as e:
            LOG.warning("Failed to discover Cinder volume services: %r", e)
            return Result(ResultType.FAILED, str(e))

        if not self.services:
            return Result(ResultType.SKIPPED)
        return Result(ResultType.COMPLETED)

    def _current_services(self) -> list[Any]:
        """Return matching Cinder service records."""
        if self.connection is None:
            raise SunbeamException("Cinder admin connection not found")
        return get_cinder_volume_services(self.connection, self.hostname, self.fqdn)


class DisableCinderVolumeServicesStep(_CinderVolumeServiceStep):
    """Disable matching Cinder volume services before unit removal."""

    def __init__(
        self,
        jhelper: JujuHelper,
        deployment: Deployment,
        hostname: str,
        fqdn: str,
    ):
        super().__init__(
            "Disable Cinder Volume services",
            "Disabling Cinder Volume services",
            jhelper,
            deployment,
            hostname,
            fqdn,
        )

    def is_skip(self, context: StepContext) -> Result:
        """Determine whether matching Cinder services exist."""
        return self._discover_services()

    def run(self, context: StepContext) -> Result:
        """Disable each enabled matching Cinder volume service."""
        if self.connection is None:
            return Result(ResultType.FAILED, "Cinder admin connection not found")
        try:
            for service in self.services:
                if service.status != "enabled":
                    continue
                LOG.info("Disabling %s on %s", service.binary, service.host)
                self.connection.block_storage.disable_service(
                    service, reason=CINDER_SERVICE_REMOVE_REASON
                )
        except (
            openstack.exceptions.SDKException,
            keystoneauth_exceptions.ClientException,
        ) as e:
            LOG.warning("Failed to disable Cinder volume service: %r", e)
            return Result(ResultType.FAILED, str(e))

        return Result(ResultType.COMPLETED)


class RemoveCinderVolumeServicesStep(_CinderVolumeServiceStep):
    """Remove matching Cinder volume service records after unit removal."""

    def __init__(
        self,
        jhelper: JujuHelper,
        deployment: Deployment,
        hostname: str,
        fqdn: str,
    ):
        super().__init__(
            "Remove Cinder Volume services",
            "Removing Cinder Volume services",
            jhelper,
            deployment,
            hostname,
            fqdn,
        )

    def is_skip(self, context: StepContext) -> Result:
        """Determine whether matching Cinder services exist."""
        return self._discover_services()

    def _healthy_units(self) -> list[str]:
        """Return healthy Cinder API units with a leader first."""
        try:
            application = self.jhelper.get_application(
                CINDER_APPLICATION, OPENSTACK_MODEL
            )
        except JujuException as e:
            LOG.warning("Failed to find Cinder control-plane units: %r", e)
            return []

        units = [
            (name, unit)
            for name, unit in application.units.items()
            if unit.workload_status.current == "active"
            and unit.juju_status.current == "idle"
        ]
        units.sort(key=lambda item: (not item[1].leader, item[0]))
        return [name for name, _ in units]

    def _remove_service(self, unit: str, service: Any) -> None:
        """Remove one Cinder service record through a healthy unit."""
        command = f"cinder-manage service remove {CINDER_VOLUME_BINARY} {service.host}"
        result = self.jhelper.run_cmd_on_unit_payload(
            unit,
            OPENSTACK_MODEL,
            command,
            CINDER_API_CONTAINER,
            timeout=CINDER_SERVICE_REMOVE_TIMEOUT,
        )
        if result.get("return-code") != 0:
            raise JujuException(f"Failed to remove Cinder service {service.host}")

    def run(self, context: StepContext) -> Result:
        """Remove matching records and verify that none remain."""
        if self.connection is None:
            return Result(ResultType.FAILED, "Cinder admin connection not found")

        try:
            remaining = self.services
            healthy_units = self._healthy_units()
            if not healthy_units:
                raise SunbeamException(
                    "No healthy Cinder control-plane units available"
                )

            for unit in healthy_units:
                for service in remaining:
                    try:
                        self._remove_service(unit, service)
                    except JujuException as e:
                        LOG.warning(
                            "Failed to remove Cinder service on %s: %r", unit, e
                        )
                        break
                remaining = self._current_services()
                if not remaining:
                    return Result(ResultType.COMPLETED)

            raise SunbeamException("Cinder service records remain after removal")
        except (
            SunbeamException,
            openstack.exceptions.SDKException,
            keystoneauth_exceptions.ClientException,
        ) as e:
            LOG.warning("Failed to remove Cinder volume services: %r", e)
            return Result(ResultType.FAILED, str(e))


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

    def is_skip(self, context: StepContext) -> Result:
        """Determines if the step should be skipped or not.

        :return: ResultType.SKIPPED if the Step should be skipped,
                ResultType.COMPLETED or ResultType.FAILED otherwise
        """
        try:
            node_info = self.client.cluster.get_node_info(self.node)
        except NodeNotExistInClusterException:
            return Result(
                ResultType.SKIPPED, f"Node {self.node} is not found in the cluster"
            )

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

    def run(self, context: StepContext) -> Result:
        """Destroy Cinder Volume application."""
        # note(gboutry):this is a workaround for
        # https://github.com/juju/terraform-provider-juju/issues/473
        try:
            resources = self.tfhelper.state_list()
        except TerraformException as e:
            LOG.debug("Failed to list Terraform state: %r", e)
            return Result(ResultType.FAILED, "Failed to list terraform state")

        for resource in resources:
            if "integration" in resource:
                try:
                    self.tfhelper.state_rm(resource)
                except TerraformException as e:
                    LOG.debug("Failed to remove resource %s: %r", resource, e)
                    return Result(
                        ResultType.FAILED,
                        f"Failed to remove resource {resource} from state",
                    )

        return super().run(context)
