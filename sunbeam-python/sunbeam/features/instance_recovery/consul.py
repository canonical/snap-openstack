# SPDX-FileCopyrightText: 2024 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Consul feature.

Consul offers service discovery, service mesh, traffic management,
node failure detection and automated updates to network infrastructure
devices.

Sunbeam enables multiple instances of consul server one for each
network - management, tenant and storage. If the networks are bind
to the same juju space then only one consul server need to be started.
"""

import enum
import logging
import queue
from typing import Any

from rich.console import Console
from rich.status import Status

from sunbeam.core.common import (
    BaseStep,
    Result,
    ResultType,
    update_config,
    update_status_background,
)
from sunbeam.core.deployment import Deployment, Networks
from sunbeam.core.juju import JujuHelper, JujuWaitException
from sunbeam.core.manifest import (
    FeatureConfig,
    Manifest,
)
from sunbeam.core.openstack import OPENSTACK_MODEL
from sunbeam.core.steps import (
    PatchLoadBalancerServicesIPPoolStep,
    PatchLoadBalancerServicesIPStep,
)
from sunbeam.core.terraform import (
    TerraformException,
    TerraformHelper,
    TerraformStateLockedException,
)
from sunbeam.features.interface.v1.openstack import (
    APPLICATION_DEPLOY_TIMEOUT,
)

LOG = logging.getLogger(__name__)
console = Console()

CONSUL_MANAGEMENT_SERF_LAN_PORT = 30301
CONSUL_TENANT_SERF_LAN_PORT = 30311
CONSUL_STORAGE_SERF_LAN_PORT = 30321
CONSUL_CLIENT_MANAGEMENT_SERF_LAN_PORT = 8301
CONSUL_CLIENT_TENANT_SERF_LAN_PORT = 8311
CONSUL_CLIENT_STORAGE_SERF_LAN_PORT = 8321

CONSUL_CLIENT_TFPLAN = "consul-client-plan"
CONSUL_CLIENT_CONFIG_KEY = "TerraformVarsFeatureConsulPlanConsulClient"
PRINCIPAL_APP = "openstack-hypervisor"
CONSUL_STORAGE_LB_SERVICE = "consul-storage-lb"


class ConsulServerNetworks(enum.Enum):
    MANAGEMENT = "management"
    TENANT = "tenant"
    STORAGE = "storage"


class DeployConsulClientStep(BaseStep):
    """Deploy Consul Client using Terraform."""

    _CONFIG = CONSUL_CLIENT_CONFIG_KEY

    def __init__(
        self,
        deployment: Deployment,
        tfhelper: TerraformHelper,
        openstack_tfhelper: TerraformHelper,
        jhelper: JujuHelper,
        manifest: Manifest,
        app_desired_status: list[str] = ["active"],
    ):
        super().__init__("Deploy Consul Client", "Deploy Consul Client")
        self.deployment = deployment
        self.tfhelper = tfhelper
        self.openstack_tfhelper = openstack_tfhelper
        self.jhelper = jhelper
        self.manifest = manifest
        self.app_desired_status = app_desired_status
        self.client = self.deployment.get_client()
        self.model = self.deployment.openstack_machines_model

    def _get_tfvars(self) -> dict:
        """Construct tfvars for consul client."""
        openstack_backend_config = self.openstack_tfhelper.backend_config()

        tfvars: dict[str, Any] = {
            "principal-application-model": self.model,
            "principal-application": PRINCIPAL_APP,
            "openstack-state-backend": self.openstack_tfhelper.backend,
            "openstack-state-config": openstack_backend_config,
        }

        clients_to_enable = ConsulFeature.consul_servers_to_enable(self.deployment)
        health_check_options = ConsulFeature.health_checks_to_enable(clients_to_enable)

        consul_config_map = {}
        consul_endpoint_bindings_map = {}
        if clients_to_enable.get(ConsulServerNetworks.MANAGEMENT):
            tfvars["enable-consul-management"] = True
            _management_config = {
                "serf-lan-port": CONSUL_CLIENT_MANAGEMENT_SERF_LAN_PORT,
            }
            _management_config.update(
                ConsulFeature.get_config_from_manifest(
                    self.manifest, "consul-client", ConsulServerNetworks.MANAGEMENT
                )
            )
            if "enable-health-check" not in _management_config:
                _management_config["enable-health-check"] = health_check_options[
                    ConsulServerNetworks.MANAGEMENT
                ]
            consul_config_map["consul-management"] = _management_config
            consul_endpoint_bindings_map["consul-management"] = [
                {"space": self.deployment.get_space(Networks.MANAGEMENT)},
                {
                    "endpoint": "consul",
                    "space": self.deployment.get_space(Networks.MANAGEMENT),
                },
                {
                    "endpoint": "consul-notify",
                    "space": self.deployment.get_space(Networks.MANAGEMENT),
                },
            ]
        else:
            tfvars["enable-consul-management"] = False

        if clients_to_enable.get(ConsulServerNetworks.TENANT):
            tfvars["enable-consul-tenant"] = True
            _tenant_config = {
                "serf-lan-port": CONSUL_CLIENT_TENANT_SERF_LAN_PORT,
            }
            _tenant_config.update(
                ConsulFeature.get_config_from_manifest(
                    self.manifest, "consul-client", ConsulServerNetworks.TENANT
                )
            )
            if "enable-health-check" not in _tenant_config:
                _tenant_config["enable-health-check"] = health_check_options[
                    ConsulServerNetworks.TENANT
                ]
            consul_config_map["consul-tenant"] = _tenant_config
            consul_endpoint_bindings_map["consul-tenant"] = [
                {"space": self.deployment.get_space(Networks.MANAGEMENT)},
                {
                    "endpoint": "consul",
                    "space": self.deployment.get_space(Networks.DATA),
                },
                {
                    "endpoint": "consul-notify",
                    "space": self.deployment.get_space(Networks.DATA),
                },
            ]
        else:
            tfvars["enable-consul-tenant"] = False

        if clients_to_enable.get(ConsulServerNetworks.STORAGE):
            tfvars["enable-consul-storage"] = True
            _storage_config = {
                "serf-lan-port": CONSUL_CLIENT_STORAGE_SERF_LAN_PORT,
            }
            _storage_config.update(
                ConsulFeature.get_config_from_manifest(
                    self.manifest, "consul-client", ConsulServerNetworks.STORAGE
                )
            )
            if "enable-health-check" not in _storage_config:
                _storage_config["enable-health-check"] = health_check_options[
                    ConsulServerNetworks.STORAGE
                ]
            consul_config_map["consul-storage"] = _storage_config
            consul_endpoint_bindings_map["consul-storage"] = [
                {"space": self.deployment.get_space(Networks.MANAGEMENT)},
                {
                    "endpoint": "consul",
                    "space": self.deployment.get_space(Networks.STORAGE),
                },
                {
                    "endpoint": "consul-notify",
                    "space": self.deployment.get_space(Networks.STORAGE),
                },
            ]
        else:
            tfvars["enable-consul-storage"] = False

        tfvars["consul-config-map"] = consul_config_map
        tfvars["consul-endpoint-bindings-map"] = consul_endpoint_bindings_map
        return tfvars

    def run(self, status: Status | None = None) -> Result:
        """Execute configuration using terraform."""
        extra_tfvars = self._get_tfvars()
        try:
            self.update_status(status, "deploying services")
            self.tfhelper.update_tfvars_and_apply_tf(
                self.client,
                self.manifest,
                tfvar_config=self._CONFIG,
                override_tfvars=extra_tfvars,
            )
        except (TerraformException, TerraformStateLockedException) as e:
            LOG.exception("Error deploying consul client")
            return Result(ResultType.FAILED, str(e))

        apps = ConsulFeature.set_consul_client_application_names(self.deployment)
        LOG.debug(f"Application monitored for readiness: {apps}")
        status_queue: queue.Queue[str] = queue.Queue()
        task = update_status_background(self, apps, status_queue, status)
        try:
            self.jhelper.wait_until_desired_status(
                self.model,
                apps,
                status=self.app_desired_status,
                timeout=APPLICATION_DEPLOY_TIMEOUT,
                queue=status_queue,
            )
        except (JujuWaitException, TimeoutError) as e:
            LOG.debug("Failed to deploy consul client", exc_info=True)
            return Result(ResultType.FAILED, str(e))
        finally:
            task.stop()

        return Result(ResultType.COMPLETED)


class RemoveConsulClientStep(BaseStep):
    """Remove Consul Client using Terraform."""

    _CONFIG = CONSUL_CLIENT_CONFIG_KEY

    def __init__(
        self,
        deployment: Deployment,
        tfhelper: TerraformHelper,
        jhelper: JujuHelper,
    ):
        super().__init__("Remove Consul Client", "Removing Consul Client")
        self.deployment = deployment
        self.tfhelper = tfhelper
        self.jhelper = jhelper
        self.client = deployment.get_client()
        self.model = deployment.openstack_machines_model

    def run(self, status: Status | None = None) -> Result:
        """Execute configuration using terraform."""
        try:
            self.tfhelper.destroy()
        except TerraformException as e:
            LOG.exception("Error destroying consul client")
            return Result(ResultType.FAILED, str(e))

        apps = ConsulFeature.set_consul_client_application_names(self.deployment)
        LOG.debug(f"Application monitored for removal: {apps}")
        try:
            self.jhelper.wait_application_gone(
                apps,
                self.model,
                timeout=APPLICATION_DEPLOY_TIMEOUT,
            )
        except TimeoutError as e:
            LOG.debug(f"Failed to destroy {apps}", exc_info=True)
            return Result(ResultType.FAILED, str(e))

        extra_tfvars = {
            "enable-consul-management": False,
            "enable-consul-tenant": False,
            "enable-consul-storage": False,
        }
        update_config(self.client, self._CONFIG, extra_tfvars)

        return Result(ResultType.COMPLETED)


class ConsulFeature:
    @staticmethod
    def consul_servers_to_enable(
        deployment: Deployment,
    ) -> dict[ConsulServerNetworks, bool]:
        """Return consul servers to enable.

        Return dict to enable/disable consul server per network.
        """
        # Default to false
        enable = dict.fromkeys(ConsulServerNetworks, False)

        try:
            management_space = deployment.get_space(Networks.MANAGEMENT)
            enable[ConsulServerNetworks.MANAGEMENT] = True
        except ValueError:
            management_space = None

        # If storage space is same as management space, dont enable consul
        # server for storage
        try:
            storage_space = deployment.get_space(Networks.STORAGE)
            if storage_space != management_space:
                enable[ConsulServerNetworks.STORAGE] = True
        except ValueError:
            storage_space = None

        # If data space is same as either of management or storage space,
        # dont enable consul server for tenant
        try:
            tenant_space = deployment.get_space(Networks.DATA)
            if tenant_space not in (management_space, storage_space):
                enable[ConsulServerNetworks.TENANT] = True
        except ValueError:
            tenant_space = None

        return enable

    @staticmethod
    def health_checks_to_enable(
        clients_to_enable: dict[ConsulServerNetworks, bool],
    ) -> dict[ConsulServerNetworks, bool]:
        """Determine if health check should be enabled.

        Health check is required for Consul storage network.
        https://opendev.org/openstack/sunbeam-charms/src/commit/fc884608f9524d133347b9f18046c49cfb5c8340/charms/masakari-k8s/src/charm.py#L141

        In case Consul storage network does not exist, Consul management is used
        for storage access.
        """
        # If storage network exists, enable TCP check for storage network
        if clients_to_enable.get(ConsulServerNetworks.STORAGE):
            return {
                ConsulServerNetworks.MANAGEMENT: False,
                ConsulServerNetworks.TENANT: False,
                ConsulServerNetworks.STORAGE: True,
            }

        # This is a case where storage network does not exist but storage is handled via
        # management network. So enable health check on management network.
        return {
            ConsulServerNetworks.MANAGEMENT: True,
            ConsulServerNetworks.TENANT: False,
            ConsulServerNetworks.STORAGE: False,
        }

    @staticmethod
    def get_config_from_manifest(
        manifest: Manifest, charm: str, network: ConsulServerNetworks
    ) -> dict:
        """Compute config from manifest.

        Compute config from manifest based on sections config and config-map.
        config-map holds consul configs for each ConsulServerNetworks.
        config-map takes precedence over config section.
        """
        feature_manifest = manifest.get_feature("instance-recovery")
        if not feature_manifest:
            return {}

        charm_manifest = feature_manifest.software.charms.get(charm)
        if not charm_manifest:
            return {}

        config = {}
        # Read feature.instance-recovery.software.charms.consul-k8s.config
        if charm_manifest.config:
            config.update(charm_manifest.config)

        # Read feature-instance-recovery.software.charms.consul-k8s.config-map
        # config-map is an extra field for CharmManifest, so use model_extra
        if charm_manifest.model_extra:
            config.update(
                charm_manifest.model_extra.get("config-map", {}).get(
                    f"consul-{network.value}", {}
                )
            )

        return config

    @staticmethod
    def set_application_names(deployment: Deployment) -> list:
        """Application names handled by the terraform plan."""
        enable = [
            f"consul-{k.value}"
            for k, v in ConsulFeature.consul_servers_to_enable(deployment).items()
            if v
        ]
        return enable

    @staticmethod
    def set_consul_client_application_names(deployment: Deployment) -> list:
        """Application names handled by the consul client terraform plan."""
        enable = [
            f"consul-client-{k.value}"
            for k, v in ConsulFeature.consul_servers_to_enable(deployment).items()
            if v
        ]
        return enable

    @staticmethod
    def set_tfvars_on_enable(
        deployment: Deployment, config: FeatureConfig, manifest: Manifest
    ) -> dict:
        """Set terraform variables to enable the consul-k8s application."""
        tfvars: dict[str, Any] = {}
        servers_to_enable = ConsulFeature.consul_servers_to_enable(deployment)
        health_check_options = ConsulFeature.health_checks_to_enable(servers_to_enable)

        consul_config_map = {}
        if servers_to_enable.get(ConsulServerNetworks.MANAGEMENT):
            tfvars["enable-consul-management"] = True
            _management_config = {}
            # Manifest takes precedence
            _management_config.update(
                ConsulFeature.get_config_from_manifest(
                    manifest, "consul-k8s", ConsulServerNetworks.MANAGEMENT
                )
            )
            if "expose-gossip-and-rpc-ports" not in _management_config:
                if health_check_options[ConsulServerNetworks.MANAGEMENT]:
                    _management_config["expose-gossip-and-rpc-ports"] = "loadbalancer"
                else:
                    _management_config["expose-gossip-and-rpc-ports"] = "nodeport"
            if "serflan-node-port" not in _management_config:
                _management_config["serflan-node-port"] = (
                    CONSUL_MANAGEMENT_SERF_LAN_PORT
                )
            consul_config_map["consul-management"] = _management_config
        else:
            tfvars["enable-consul-management"] = False

        if servers_to_enable.get(ConsulServerNetworks.TENANT):
            tfvars["enable-consul-tenant"] = True
            _tenant_config = {}
            # Manifest takes precedence
            _tenant_config.update(
                ConsulFeature.get_config_from_manifest(
                    manifest, "consul-k8s", ConsulServerNetworks.TENANT
                )
            )
            if "expose-gossip-and-rpc-ports" not in _tenant_config:
                if health_check_options[ConsulServerNetworks.TENANT]:
                    _tenant_config["expose-gossip-and-rpc-ports"] = "loadbalancer"
                else:
                    _tenant_config["expose-gossip-and-rpc-ports"] = "nodeport"
            if "serflan-node-port" not in _tenant_config:
                _tenant_config["serflan-node-port"] = CONSUL_TENANT_SERF_LAN_PORT
            consul_config_map["consul-tenant"] = _tenant_config
        else:
            tfvars["enable-consul-tenant"] = False

        if servers_to_enable.get(ConsulServerNetworks.STORAGE):
            tfvars["enable-consul-storage"] = True
            _storage_config = {}
            # Manifest takes precedence
            _storage_config.update(
                ConsulFeature.get_config_from_manifest(
                    manifest, "consul-k8s", ConsulServerNetworks.STORAGE
                )
            )
            # Given the logic for masakarimonitors matrix to determine when to
            # apply action recovery, recovery is needed only when storage network
            # is used. In some cases, the storage network is same as management
            # network. No health check is required for management network as
            # consul-client already have the necessary endpoints exposed on
            # management network.
            # For storage network, the consul-server should expose the gossip port
            # as loadbalancer to enable health check from consul-client.
            # So only update option expose-gossip-and-rpc-ports for Storage
            # network and ignore for other networks.
            # This also reduces the number of metallb ip allocations for
            # instance-recovery feature to 1, that too only if storage network exists.
            if "expose-gossip-and-rpc-ports" not in _storage_config:
                if health_check_options[ConsulServerNetworks.STORAGE]:
                    _storage_config["expose-gossip-and-rpc-ports"] = "loadbalancer"
                else:
                    _storage_config["expose-gossip-and-rpc-ports"] = "nodeport"
            if "serflan-node-port" not in _storage_config:
                _storage_config["serflan-node-port"] = CONSUL_STORAGE_SERF_LAN_PORT
            consul_config_map["consul-storage"] = _storage_config
        else:
            tfvars["enable-consul-storage"] = False

        tfvars["consul-config-map"] = consul_config_map
        return tfvars


class PatchConsulStorageLoadBalancerIPStep(PatchLoadBalancerServicesIPStep):
    def services(self) -> list[str]:
        """List of services to patch."""
        return [CONSUL_STORAGE_LB_SERVICE]

    def model(self) -> str:
        """Name of the model to use."""
        return OPENSTACK_MODEL


class PatchConsulStorageLoadBalancerIPPoolStep(PatchLoadBalancerServicesIPPoolStep):
    def services(self) -> list[str]:
        """List of services to patch."""
        return [CONSUL_STORAGE_LB_SERVICE]

    def model(self) -> str:
        """Name of the model to use."""
        return OPENSTACK_MODEL
