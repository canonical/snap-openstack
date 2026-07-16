# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import logging
from typing import Any

import tenacity
from snaphelpers import Snap, UnknownConfigKey

from sunbeam import versions
from sunbeam.clusterd.client import Client
from sunbeam.clusterd.service import (
    NodeNotExistInClusterException,
)
from sunbeam.core import ovn
from sunbeam.core.common import (
    BaseStep,
    Result,
    ResultType,
    StepContext,
    convert_retry_failure_as_result,
)
from sunbeam.core.deployment import Deployment, Networks
from sunbeam.core.juju import (
    ApplicationNotFoundException,
    JujuHelper,
    JujuStepHelper,
)
from sunbeam.core.manifest import CharmManifest, Manifest
from sunbeam.core.steps import DeployMachineApplicationStep, RemoveMachineUnitsStep
from sunbeam.core.terraform import (
    TerraformException,
    TerraformHelper,
    TerraformStateLockedException,
)
from sunbeam.steps.configure import get_external_network_configs

LOG = logging.getLogger(__name__)
CONFIG_KEY = "TerraformVarsMicroovnPlan"
CONFIG_DISKS_KEY = "TerraformVarsMicroovn"
APPLICATION = "microovn"
ARM64_APPLICATION = "microovn-arm64"
MICROOVN_APP_TIMEOUT = 1200
MICROOVN_UNIT_TIMEOUT = 1200
AGENT_APP = "openstack-network-agents"
ROLE_DISTRIBUTOR_APP = "role-distributor"


def _microovn_application_name(architecture: str) -> str:
    """Return the MicroOVN application name for an architecture."""
    if architecture == ovn.DEFAULT_ARCHITECTURE:
        return APPLICATION
    return f"{APPLICATION}-{architecture}"


def _microovn_architecture_sort_key(architecture: str) -> tuple[bool, str]:
    """Prefer the default architecture, then keep remaining apps deterministic."""
    return architecture != ovn.DEFAULT_ARCHITECTURE, architecture


def _microovn_applications_to_wait(
    machines_by_architecture: dict[str, list[str]],
) -> list[str]:
    """Return MicroOVN application names with machines assigned."""
    return [
        _microovn_application_name(architecture)
        for architecture in sorted(
            machines_by_architecture,
            key=_microovn_architecture_sort_key,
        )
        if machines_by_architecture[architecture]
    ]


def _role_distributor_application_name(jhelper: JujuHelper, model: str) -> str | None:
    """Return role-distributor application name when deployed."""
    try:
        jhelper.get_application(ROLE_DISTRIBUTOR_APP, model)
    except ApplicationNotFoundException:
        return None
    return ROLE_DISTRIBUTOR_APP


def _microovn_accepted_statuses(ovn_manager: ovn.OvnManager) -> list[str]:
    """Return statuses accepted while waiting for MicroOVN."""
    statuses = ["active", "unknown"]
    if ovn_manager.get_provider() == ovn.OvnProvider.OVN_K8S:
        statuses.append("blocked")
    return statuses


def _openstack_network_agents_tfvars(
    deployment: Deployment,
    manifest: Manifest,
) -> dict[str, Any]:
    """Return common openstack-network-agents Terraform variables."""
    tfvars: dict[str, Any] = {
        "charm_openstack_network_agents_config": {
            "snap-channel": versions.OPENSTACK_CHANNEL
        },
        "openstack_network_agents_endpoint_bindings": [
            {"space": deployment.get_space(Networks.MANAGEMENT)},
            {
                "endpoint": "data",
                "space": deployment.get_space(Networks.DATA),
            },
        ],
    }

    agent_charm_manifest: CharmManifest | None = manifest.core.software.charms.get(
        AGENT_APP
    )
    if agent_charm_manifest and agent_charm_manifest.config:
        tfvars["charm_openstack_network_agents_config"].update(
            agent_charm_manifest.config
        )

    return tfvars


class DeployMicroOVNApplicationStep(DeployMachineApplicationStep):
    """Deploy MicroOVN application using Terraform."""

    def __init__(
        self,
        deployment: Deployment,
        client: Client,
        tfhelper: TerraformHelper,
        jhelper: JujuHelper,
        manifest: Manifest,
        model: str,
        ovn_manager: ovn.OvnManager,
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
            list(ovn_manager.get_roles_for_microovn()),
            "Deploy MicroOVN",
            "Deploying MicroOVN",
        )
        self.ovn_manager = ovn_manager

    def get_application_timeout(self) -> int:
        """Return application timeout in seconds."""
        return MICROOVN_APP_TIMEOUT

    def get_accepted_application_status(self) -> list[str]:
        """Accepted status to pass wait_application_ready function."""
        return _microovn_accepted_statuses(self.ovn_manager)

    def extra_tfvars(self) -> dict:
        """Extra terraform vars to pass to terraform apply."""
        openstack_tfhelper = self.deployment.get_tfhelper("openstack-plan")
        openstack_tf_output = openstack_tfhelper.output()

        juju_offers = {
            "ca-offer-url",
            "ovn-relay-offer-url",
        }
        extra_tfvars: dict[str, Any] = {
            offer: openstack_tf_output.get(offer) for offer in juju_offers
        }

        machines_by_arch = self.ovn_manager.get_machines_by_architecture()
        extra_tfvars["microovn_machine_ids_by_architecture"] = machines_by_arch
        distributor_ids = self.ovn_manager.get_token_distributor_machines(
            ovn.OvnProvider.MICROOVN
        )
        extra_tfvars["token_distributor_machine_ids"] = distributor_ids[:1]

        # Juju does not resolve per-arch revisions for subordinates, so pin the
        # arm64 revision explicitly from the manifest-configured channel.
        if machines_by_arch.get(ovn.ARM64_ARCHITECTURE):
            agent_charm_manifest = self.manifest.core.software.charms.get(AGENT_APP)
            channel = (
                agent_charm_manifest.channel
                if agent_charm_manifest and agent_charm_manifest.channel
                else versions.OPENSTACK_CHANNEL
            )
            revisions = self.jhelper.get_available_charm_revisions(AGENT_APP, channel)
            arm64_revision = revisions.get(ovn.ARM64_ARCHITECTURE)
            if arm64_revision is not None:
                extra_tfvars["charm_openstack_network_agents_arm64_revision"] = (
                    arm64_revision
                )

        extra_tfvars.update(
            {
                "endpoint_bindings": [
                    {"space": self.deployment.get_space(Networks.MANAGEMENT)},
                    {
                        "endpoint": "cluster",
                        "space": self.deployment.get_space(Networks.MANAGEMENT),
                    },
                    {
                        "endpoint": "certificates",
                        "space": self.deployment.get_space(Networks.INTERNAL),
                    },
                    {
                        "endpoint": "ovsdb-external",
                        "space": self.deployment.get_space(Networks.INTERNAL),
                    },
                    {
                        "endpoint": "ovsdb",
                        "space": self.deployment.get_space(Networks.INTERNAL),
                    },
                ],
                "role_distributor_application_name": (
                    _role_distributor_application_name(self.jhelper, self.model)
                ),
            }
        )
        extra_tfvars.update(
            _openstack_network_agents_tfvars(self.deployment, self.manifest)
        )

        return extra_tfvars

    def _applications_to_wait(self) -> list[str]:
        """Return Juju application names that should be ready after deploy."""
        machines_by_arch = self.ovn_manager.get_machines_by_architecture()
        return _microovn_applications_to_wait(machines_by_arch)

    @tenacity.retry(
        wait=tenacity.wait_fixed(60),
        stop=tenacity.stop_after_delay(300),
        retry=tenacity.retry_if_exception_type(TerraformStateLockedException),
        retry_error_callback=convert_retry_failure_as_result,
    )
    def run(self, context: StepContext) -> Result:
        """Apply terraform and wait for MicroOVN applications."""
        try:
            extra_tfvars = self.extra_tfvars()
            extra_tfvars["machine_model_uuid"] = self.jhelper.get_model_uuid(self.model)

            self.tfhelper.update_tfvars_and_apply_tf(
                self.client,
                self.manifest,
                tfvar_config=self.config,
                override_tfvars=extra_tfvars,
                tf_apply_extra_args=self.tf_apply_extra_args(),
                reporter=context.reporter,
            )
        except TerraformException as e:
            return Result(ResultType.FAILED, str(e))

        if not self.wait_for_readiness:
            return Result(ResultType.COMPLETED)

        accepted_status = self.get_accepted_application_status()
        timeout = self.get_application_timeout()
        for application in self._applications_to_wait():
            try:
                self.jhelper.wait_application_ready(
                    application,
                    self.model,
                    accepted_status=accepted_status,
                    timeout=timeout,
                )
            except TimeoutError as e:
                LOG.warning("Application %r is not ready: %r", application, e)
                return Result(ResultType.FAILED, str(e))

        return Result(ResultType.COMPLETED)


class ReapplyMicroOVNOptionalIntegrationsStep(DeployMicroOVNApplicationStep):
    """Reapply MicroOVN optional integrations using Terraform."""

    def tf_apply_extra_args(self) -> list[str]:
        """Extra args for terraform apply to reapply only optional CMR integrations."""
        extra_args = [
            "-target=juju_integration.microovn-microcluster-token-distributor",
            "-target=juju_integration.microovn-certs",
            "-target=juju_integration.microovn-ovsdb-cms",
            "-target=juju_integration.microovn-openstack-network-agents",
            "-target=juju_integration.microovn_arm64_microcluster_token_distributor",
            "-target=juju_integration.microovn_arm64_certs",
            "-target=juju_integration.microovn_arm64_ovsdb_cms",
        ]
        if _role_distributor_application_name(self.jhelper, self.model):
            extra_args.append("-target=juju_integration.role-distributor-microovn")
            extra_args.append(
                "-target=juju_integration.role-distributor-microovn-arm64"
            )
        return extra_args


class ReapplyMicroOVNTerraformPlanStep(BaseStep):
    """Reapply MicroOVN terraform plan."""

    _CONFIG = CONFIG_KEY

    def __init__(
        self,
        client: Client,
        tfhelper: TerraformHelper,
        jhelper: JujuHelper,
        manifest: Manifest,
        model: str,
        ovn_manager: ovn.OvnManager,
        extra_tfvars: dict | None = None,
    ):
        super().__init__(
            "Reapply MicroOVN Terraform plan",
            "Reapply MicroOVN Terraform plan",
        )
        self.client = client
        self.tfhelper = tfhelper
        self.jhelper = jhelper
        self.manifest = manifest
        self.model = model
        self.ovn_manager = ovn_manager
        self.extra_tfvars = extra_tfvars or {}

    def is_skip(self, context: StepContext) -> Result:
        """Determines if the step should be skipped or not.

        :return: ResultType.SKIPPED if the Step should be skipped,
                ResultType.COMPLETED or ResultType.FAILED otherwise
        """
        for role in self.ovn_manager.get_roles_for_microovn():
            if self.client.cluster.list_nodes_by_role(role.name.lower()):
                return Result(ResultType.COMPLETED)

        return Result(ResultType.SKIPPED)

    @tenacity.retry(
        wait=tenacity.wait_fixed(60),
        stop=tenacity.stop_after_delay(300),
        retry=tenacity.retry_if_exception_type(TerraformStateLockedException),
        retry_error_callback=convert_retry_failure_as_result,
    )
    def run(self, context: StepContext) -> Result:
        """Apply terraform configuration to deploy MicroOVN."""
        # Apply Network configs everytime reapply is called
        network_configs = get_external_network_configs(self.client)
        if "charm_openstack_network_agents_config" not in self.extra_tfvars:
            self.extra_tfvars["charm_openstack_network_agents_config"] = {}

        if network_configs:
            LOG.debug(
                "Add external network configs from DemoSetup to extra tfvars: %s",
                network_configs,
            )
            self.extra_tfvars["charm_openstack_network_agents_config"].update(
                network_configs
            )

        statuses = _microovn_accepted_statuses(self.ovn_manager)
        try:
            self.tfhelper.update_tfvars_and_apply_tf(
                self.client,
                self.manifest,
                tfvar_config=self._CONFIG,
                override_tfvars=self.extra_tfvars,
                reporter=context.reporter,
            )
        except TerraformException as e:
            return Result(ResultType.FAILED, str(e))

        machines_by_arch = self.ovn_manager.get_machines_by_architecture()
        apps_to_wait = _microovn_applications_to_wait(machines_by_arch)
        try:
            for application in apps_to_wait:
                self.jhelper.wait_application_ready(
                    application,
                    self.model,
                    accepted_status=statuses,
                    timeout=MICROOVN_UNIT_TIMEOUT,
                )
        except TimeoutError as e:
            LOG.warning("Timed out waiting for reapplying MicroOVN: %r", e)
            return Result(ResultType.FAILED, str(e))

        return Result(ResultType.COMPLETED)


class RemoveMicroOVNUnitsStep(RemoveMachineUnitsStep):
    """Remove MicroOVN Unit."""

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
            "Remove MicroOVN unit",
            "Removing MicroOVN unit from machine",
        )
        self.units_to_remove_by_app: dict[str, set[str]] = {}

    def get_unit_timeout(self) -> int:
        """Return unit timeout in seconds."""
        return MICROOVN_UNIT_TIMEOUT

    def is_skip(self, context: StepContext) -> Result:
        """Find MicroOVN units to remove across architecture-specific applications."""
        if len(self.names) == 0:
            return Result(ResultType.SKIPPED)

        nodes: list[dict] = self.client.cluster.list_nodes()
        filtered_nodes = list(filter(lambda node: node["name"] in self.names, nodes))
        if len(filtered_nodes) != len(self.names):
            filtered_node_names = [node["name"] for node in filtered_nodes]
            missing_nodes = set(self.names) - set(filtered_node_names)
            LOG.debug(
                "Nodes do not exist in cluster database: %s", ",".join(missing_nodes)
            )

        to_remove_node_ids = {str(node["machineid"]) for node in filtered_nodes}
        self.units_to_remove_by_app = {}

        applications = {
            _microovn_application_name(node.get("arch") or ovn.DEFAULT_ARCHITECTURE)
            for node in filtered_nodes
        }
        applications.update((APPLICATION, ARM64_APPLICATION))

        for application in sorted(applications):
            try:
                app = self.jhelper.get_application(application, self.model)
            except ApplicationNotFoundException:
                LOG.debug("Application %r has not been deployed yet", application)
                continue

            for unit_name, unit in app.units.items():
                if unit.machine in to_remove_node_ids:
                    self.units_to_remove_by_app.setdefault(application, set()).add(
                        unit_name
                    )

        if not self.units_to_remove_by_app:
            return Result(ResultType.SKIPPED)

        return Result(ResultType.COMPLETED)

    def run(self, context: StepContext) -> Result:
        """Remove MicroOVN units from architecture-specific applications."""
        try:
            self.update_status(context, "Removing units")
            for application, units in self.units_to_remove_by_app.items():
                for unit in units:
                    LOG.debug("Removing unit %s from application %s", unit, application)
                    self.jhelper.remove_unit(application, unit, self.model)
                self.update_status(context, "Waiting for units to be removed")
                self.jhelper.wait_units_gone(
                    list(units), self.model, self.get_unit_timeout()
                )
                self.jhelper.wait_application_ready(
                    application,
                    self.model,
                    accepted_status=["active", "unknown"],
                    timeout=self.get_unit_timeout(),
                )
        except (ApplicationNotFoundException, TimeoutError) as e:
            LOG.warning("Failed to remove MicroOVN units: %r", e)
            return Result(ResultType.FAILED, str(e))

        return Result(ResultType.COMPLETED)


class EnableMicroOVNStep(BaseStep, JujuStepHelper):
    """Enable MicroOVN service."""

    def __init__(
        self,
        client: Client,
        node: str,
        jhelper: JujuHelper,
        model: str,
    ):
        super().__init__(
            "Enable MicroOVN service",
            "Enabling MicroOVN service for unit",
        )
        self.client = client
        self.node = node
        self.jhelper = jhelper
        self.model = model
        self.unit: str | None = None
        self.machine_id = ""

    def is_skip(self, context: StepContext) -> Result:
        """Determines if the step should be skipped or not.

        :return: ResultType.SKIPPED if the Step should be skipped,
                ResultType.COMPLETED or ResultType.FAILED otherwise
        """
        try:
            node = self.client.cluster.get_node_info(self.node)
            self.machine_id = str(node.get("machineid"))
            arch = node.get("arch") or ovn.DEFAULT_ARCHITECTURE
            app_name = _microovn_application_name(arch)
        except NodeNotExistInClusterException:
            LOG.debug("Machine %s does not exist, skipping", self.node)
            return Result(ResultType.SKIPPED)

        try:
            application = self.jhelper.get_application(app_name, self.model)
        except ApplicationNotFoundException as e:
            LOG.debug("MicroOVN application is not found: %r", e)
            return Result(
                ResultType.SKIPPED,
                f"{app_name} application has not been deployed yet",
            )

        for unit_name, unit in application.units.items():
            if unit.machine == self.machine_id:
                LOG.debug(
                    "Unit %s is deployed on machine: %s", unit_name, self.machine_id
                )
                self.unit = unit_name
                break
        if not self.unit:
            LOG.debug("Unit is not deployed on machine: %s, skipping", self.machine_id)
            return Result(ResultType.SKIPPED)
        return Result(ResultType.COMPLETED)

    def run(self, context: StepContext) -> Result:
        """Enable MicroOVN service on node."""
        if not self.unit:
            return Result(ResultType.FAILED, "Unit not found on machine")

        return Result(ResultType.COMPLETED)


class SetOvnProviderStep(BaseStep):
    """Set OVN provider in the deployment configuration."""

    def __init__(self, client: Client, snap: Snap):
        super().__init__(
            "Set OVN provider",
            "Setting OVN provider in deployment configuration",
        )
        self.client = client
        self.snap = snap
        self.wanted_provider: ovn.OvnProvider | None = None

    def get_config_from_snap(self, snap: Snap) -> ovn.OvnProvider:
        """Get OVN provider from snap configuration.

        Returns MICROOVN only when the provider config 'ovn.provider' is set
        to 'microovn'.

        :param snap: the snap instance
        :return: the OVN provider
        """
        try:
            provider_value = snap.config.get(ovn.SNAP_PROVIDER_CONFIG_KEY)
            if provider_value:
                # Check if it's a valid OvnProvider value
                try:
                    parsed_provider = ovn.OvnProvider(provider_value)
                    if parsed_provider == ovn.OvnProvider.MICROOVN:
                        return ovn.OvnProvider.MICROOVN
                except ValueError:
                    # Invalid provider value - raise error to fail fast
                    valid_values = ", ".join([p.value for p in ovn.OvnProvider])
                    raise ValueError(
                        f"Invalid value '{provider_value}' for "
                        f"{ovn.SNAP_PROVIDER_CONFIG_KEY}. "
                        f"Valid values are: {valid_values}"
                    )
        except UnknownConfigKey:
            # fallback to default
            pass
        return ovn.DEFAULT_PROVIDER

    def is_skip(self, context: StepContext) -> Result:
        """Determines if the step should be skipped or not.

        :return: ResultType.SKIPPED if the Step should be skipped,
                ResultType.COMPLETED or ResultType.FAILED otherwise
        """
        try:
            snap_value = self.get_config_from_snap(self.snap)
        except ValueError as e:
            return Result(
                ResultType.FAILED,
                str(e),
            )

        config = ovn.load_provider_config(self.client)
        configured_provider = config.provider
        if configured_provider == snap_value:
            LOG.debug(
                "OVN provider is already set to %s in deployment configuration",
                snap_value,
            )
            return Result(ResultType.SKIPPED)

        already_bootstrapped = self.client.cluster.check_sunbeam_bootstrapped()
        if already_bootstrapped and configured_provider != snap_value:
            LOG.debug(
                "OVN provider change detected after bootstrap, which is not supported"
            )
            return Result(ResultType.FAILED, "Changing OVN provider is not supported.")
        self.wanted_provider = snap_value
        return Result(ResultType.COMPLETED)

    def run(self, context: StepContext) -> Result:
        """Set OVN provider in deployment configuration to the desired provider."""
        if self.wanted_provider is None:
            return Result(ResultType.FAILED, "Invalid state, wanted_provider is None")
        config = ovn.load_provider_config(self.client)
        config.provider = self.wanted_provider
        ovn.write_provider_config(self.client, config)
        return Result(ResultType.COMPLETED)
