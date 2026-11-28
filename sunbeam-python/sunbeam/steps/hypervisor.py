# SPDX-FileCopyrightText: 2023 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import json
import logging
import math
import subprocess
import time
import typing

import click
import tenacity
from jubilant import TaskError
from rich.console import Console
from snaphelpers import Snap

import sunbeam.core.questions
from sunbeam.clusterd.client import Client
from sunbeam.clusterd.service import (
    ConfigItemNotFoundException,
    NodeNotExistInClusterException,
)
from sunbeam.commands.configure import (
    get_dpdk_config,
    get_pci_allowedlist_config,
)
from sunbeam.core.checks import JujuControllerRegistrationCheck
from sunbeam.core.common import (
    BaseStep,
    Result,
    ResultType,
    Role,
    StepContext,
    convert_retry_failure_as_result,
    read_config,
    update_config,
)
from sunbeam.core.compute_storage import (
    get_vault_kv_offer_url,
    get_vault_kv_offering_controller,
    set_vault_kv_offer_url,
)
from sunbeam.core.deployment import Deployment, Networks
from sunbeam.core.juju import (
    MODEL_DELAY,
    ActionFailedException,
    ApplicationNotFoundException,
    JujuHelper,
    JujuStepHelper,
    ModelNotFoundException,
)
from sunbeam.core.manifest import Manifest
from sunbeam.core.openstack_api import (
    get_admin_connection,
    guests_on_hypervisor,
    remove_compute_service,
    remove_hypervisor,
    remove_network_service,
    resolve_hypervisor_name,
)
from sunbeam.core.steps import (
    DeployMachineApplicationStep,
    DestroyMachineApplicationStep,
)
from sunbeam.core.terraform import (
    TerraformException,
    TerraformHelper,
    TerraformStateLockedException,
)
from sunbeam.lazy import LazyImport
from sunbeam.steps.configure import get_external_network_configs

if typing.TYPE_CHECKING:
    import jubilant
    import openstack
    from keystoneauth1 import exceptions as keystoneauth_exceptions
else:
    keystoneauth_exceptions = LazyImport("keystoneauth1.exceptions")
    openstack = LazyImport("openstack")

LOG = logging.getLogger(__name__)
CONFIG_KEY = "TerraformVarsHypervisor"
APPLICATION = "openstack-hypervisor"
VAULTLOCKER_APPLICATION = "vaultlocker-hypervisor"
HYPERVISOR_APP_TIMEOUT = 1800  # 30 minutes, includes adding / removing units
HYPERVISOR_DESTROY_TIMEOUT = 600
HYPERVISOR_UNIT_TIMEOUT = (
    1800  # 30 minutes, adding / removing units can take a long time
)
# Deleted Nova services and Neutron agents are normally gone on the first
# check. Poll every 10 seconds for up to 5 minutes before failing removal.
HYPERVISOR_REFERENCES_TIMEOUT = 300
HYPERVISOR_REFERENCES_POLL_INTERVAL = 10
ENCRYPTED_STORAGE_ACTION = "configure-encrypted-storage"
ENCRYPTED_STORAGE_TIMEOUT = 600  # 10 minutes per host, after vaultlocker deployment


def check_no_guests_on_hypervisor(
    conn: "openstack.connection.Connection", node_name: str
) -> None:
    """Check that no guests are assigned to the hypervisor."""
    hypervisor_name = resolve_hypervisor_name(node_name, conn)
    guests = guests_on_hypervisor(hypervisor_name, conn)
    if guests:
        raise ValueError(
            f"{len(guests)} instance(s) still assigned; migrate or delete them first"
        )


def enroll_encrypted_storage(
    client: Client,
    jhelper: JujuHelper,
    model: str,
    node_name: str,
    target: str,
    secret_id: str,
    grant_ref: str,
) -> None:
    """Grant a credential and wait for one host's storage to be mounted."""
    jhelper.grant_secret(model, grant_ref, VAULTLOCKER_APPLICATION)
    machine_id = str(client.cluster.get_node_info(node_name)["machineid"])
    unit = jhelper.get_unit_from_machine(APPLICATION, machine_id, model)
    wait_for_encrypted_storage(jhelper, unit, model, target, secret_id)


def wait_for_encrypted_storage(
    jhelper: JujuHelper, unit: str, model: str, target: str, secret_id: str
) -> None:
    """Wait until the hypervisor has mounted the enrolled storage target."""
    deadline = time.monotonic() + ENCRYPTED_STORAGE_TIMEOUT
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError(
                f"Encrypted storage configuration did not complete for {unit}. "
                "Check the Vaultlocker subordinate unit."
            )

        try:
            result = jhelper.run_action(
                unit,
                model,
                ENCRYPTED_STORAGE_ACTION,
                {"target": target, "existing-key-secret-id": secret_id},
                timeout=max(1, math.ceil(remaining)),
            )
        except TaskError as e:
            task = e.task
            reason = task.message or task.stderr or str(e)
            raise ActionFailedException(
                f"Action {ENCRYPTED_STORAGE_ACTION!r} on unit {unit!r} failed: {reason}"
            ) from e
        if result.get("target") != target:
            raise ValueError(f"Unexpected encrypted storage result from {unit}")
        if result.get("status") == "completed":
            return
        if result.get("status") != "requested":
            raise ValueError(f"Unexpected encrypted storage status from {unit}")

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError(
                f"Encrypted storage configuration did not complete for {unit}. "
                "Check the Vaultlocker subordinate unit."
            )
        time.sleep(min(10, remaining))


class DeployHypervisorApplicationStep(DeployMachineApplicationStep):
    """Deploy openstack-hyervisor application using Terraform cloud."""

    _CONFIG = CONFIG_KEY

    def __init__(
        self,
        deployment: Deployment,
        client: Client,
        tfhelper: TerraformHelper,
        openstack_tfhelper: TerraformHelper,
        cinder_volume_tfhelper: TerraformHelper,
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
            [Role.COMPUTE],
            "Deploy OpenStack Hypervisor",
            "Deploying OpenStack Hypervisor",
        )
        self.openstack_tfhelper = openstack_tfhelper
        self.cinder_volume_tfhelper = cinder_volume_tfhelper

    def extra_tfvars(self) -> dict:
        """Extra terraform vars to pass to terraform apply."""
        openstack_tf_output = self.openstack_tfhelper.output()

        storage_nodes = self.client.cluster.list_nodes_by_role("storage")
        # Always pass Offer URLs as extravars instead of terraform backend
        # so that sunbeam has control to remove the CMR integrations by passing
        # null value.
        # If offer URL is retrieved directly by terraform plan itself from
        # openstack backend, removing CMR integration results in errros.
        # see https://bugs.launchpad.net/juju/+bug/2085310

        juju_offers = {
            "rabbitmq-offer-url",
            "keystone-offer-url",
            "cert-distributor-offer-url",
            "ca-offer-url",
            "nova-offer-url",
        }
        extra_tfvars = {offer: openstack_tf_output.get(offer) for offer in juju_offers}

        # Cluster-wide encrypted compute-storage configuration, if any.
        vault_offer_url = get_vault_kv_offer_url(self.client)
        extra_tfvars["vault-kv-offer-url"] = vault_offer_url
        extra_tfvars["vault-kv-offering-controller"] = get_vault_kv_offering_controller(
            vault_offer_url
        )

        if len(storage_nodes) > 0:
            cinder_volume_tf_output = self.cinder_volume_tfhelper.output()

            app_name_key = "cinder-volume-ceph-application-name"
            if app_name := cinder_volume_tf_output.get(app_name_key):
                extra_tfvars[app_name_key] = app_name

        extra_tfvars.update(
            {
                "endpoint_bindings": [
                    {"space": self.deployment.get_space(Networks.MANAGEMENT)},
                    {
                        "endpoint": "ceph-access",
                        "space": self.deployment.get_space(Networks.STORAGE),
                    },
                    {
                        "endpoint": "migration",
                        "space": self.deployment.get_space(Networks.DATA),
                    },
                    {
                        "endpoint": "data",
                        "space": self.deployment.get_space(Networks.DATA),
                    },
                    {
                        "endpoint": "amqp",
                        "space": self.deployment.get_space(Networks.INTERNAL),
                    },
                    {
                        "endpoint": "ceilometer-service",
                        "space": self.deployment.get_space(Networks.INTERNAL),
                    },
                    {
                        "endpoint": "certificates",
                        "space": self.deployment.get_space(Networks.INTERNAL),
                    },
                    {
                        "endpoint": "cos-agent",
                        "space": self.deployment.get_space(Networks.INTERNAL),
                    },
                    {
                        "endpoint": "identity-credentials",
                        "space": self.deployment.get_space(Networks.INTERNAL),
                    },
                    {
                        "endpoint": "nova-service",
                        "space": self.deployment.get_space(Networks.INTERNAL),
                    },
                    {
                        "endpoint": "receive-ca-cert",
                        "space": self.deployment.get_space(Networks.INTERNAL),
                    },
                ],
            }
        )

        return extra_tfvars

    def get_application_timeout(self) -> int:
        """Return application timeout in seconds."""
        return HYPERVISOR_APP_TIMEOUT


class ReapplyHypervisorOptionalIntegrationsStep(DeployHypervisorApplicationStep):
    """Reapply openstack-hypervisor optional integrations using Terraform cloud.

    The optional integrations related to storage or any features will be reapplied
    at this step. This is to ensure the integrations are created irrespective of
    the order of the roles joining the cluster.

    This class is similar to DeployHypervisorApplicationStep but it refreshes only
    integrations after getting the necessary CMR offer URLs.
    """

    def tf_apply_extra_args(self) -> list:
        """Extra args for the terraform apply command."""
        return [
            "-target=juju_integration.hypervisor-cert-distributor",
            "-target=juju_integration.hypervisor-certs",
            "-target=juju_integration.hypervisor-ceilometer",
            "-target=juju_integration.hypervisor-cinder-ceph",
            "-target=juju_integration.hypervisor-masakari",
            "-target=juju_integration.hypervisor-barbican",
        ]


class RemoveHypervisorUnitStep(BaseStep, JujuStepHelper):
    def __init__(
        self,
        client: Client,
        jhelper: JujuHelper,
        deployment: "Deployment | None",
        name: str,
        model: str,
        force: bool = False,
    ):
        super().__init__(
            "Remove openstack-hypervisor unit",
            "Remove openstack-hypervisor unit from machine",
        )
        self.client = client
        self.node_name = name
        self.jhelper = jhelper
        self.model = model
        self.force = force
        self.deployment = deployment
        self.unit: str | None = None
        self.machine_id = ""

    def is_skip(self, context: StepContext) -> Result:
        """Determines if the step should be skipped or not.

        :return: ResultType.SKIPPED if the Step should be skipped,
                ResultType.COMPLETED or ResultType.FAILED otherwise
        """
        try:
            node = self.client.cluster.get_node_info(self.node_name)
            self.machine_id = str(node.get("machineid"))
        except NodeNotExistInClusterException:
            LOG.debug("Machine %s does not exist, skipping", self.node_name)
            return Result(ResultType.SKIPPED)

        try:
            application = self.jhelper.get_application(APPLICATION, self.model)
        except ApplicationNotFoundException as e:
            LOG.debug("Failed to get hypervisor application: %r", e)
            return Result(
                ResultType.SKIPPED, "Hypervisor application has not been deployed yet"
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
        try:
            results = self.jhelper.run_action(self.unit, self.model, "running-guests")
        except ActionFailedException:
            LOG.debug("Failed to run action on hypervisor unit", exc_info=True)
            return Result(ResultType.FAILED, "Failed to run action on hypervisor unit")

        if result := results.get("result"):
            guests = json.loads(result)
            LOG.debug("Found guests on hypervisor: %s", guests)
            if guests and not self.force:
                return Result(
                    ResultType.FAILED,
                    "Guests are running on hypervisor, aborting",
                )
        return Result(ResultType.COMPLETED)

    def remove_machine_id_from_tfvar(self) -> None:
        """Remove machine if from terraform vars saved in cluster db."""
        try:
            tfvars = read_config(self.client, CONFIG_KEY)
        except ConfigItemNotFoundException:
            tfvars = {}

        machine_ids = tfvars.get("machine_ids", [])
        if self.machine_id in machine_ids:
            machine_ids.remove(self.machine_id)
            tfvars.update({"machine_ids": machine_ids})
            update_config(self.client, CONFIG_KEY, tfvars)

    def run(self, context: StepContext) -> Result:
        """Remove unit from openstack-hypervisor application on Juju model."""
        if not self.unit:
            return Result(ResultType.FAILED, "Unit not found on machine")
        try:
            self.jhelper.run_action(self.unit, self.model, "disable")
        except ActionFailedException as e:
            LOG.debug("Failed to disable hypervisor unit: %r", e)
            return Result(ResultType.FAILED, "Failed to disable hypervisor unit")
        try:
            self.jhelper.remove_unit(APPLICATION, self.unit, self.model)
            self.remove_machine_id_from_tfvar()
            self.jhelper.wait_units_gone(
                [self.unit],
                self.model,
                timeout=HYPERVISOR_UNIT_TIMEOUT,
            )
            self.jhelper.wait_application_ready(
                APPLICATION,
                self.model,
                accepted_status=["active", "unknown"],
                timeout=HYPERVISOR_UNIT_TIMEOUT,
            )
        except (ApplicationNotFoundException, TimeoutError) as e:
            LOG.warning("Failed to remove hypervisor unit: %r", e)
            return Result(ResultType.FAILED, str(e))
        try:
            if self.deployment:
                remove_hypervisor(self.jhelper, self.deployment, self.node_name)
        except openstack.exceptions.SDKException as e:
            LOG.error(
                "Encountered error removing hypervisor references from control plane"
            )
            if self.force:
                LOG.warning(
                    "Force mode set, ignoring following exceptions", exc_info=True
                )
            else:
                return Result(ResultType.FAILED, str(e))

        return Result(ResultType.COMPLETED)


class RemoveHypervisorReferencesStep(BaseStep):
    """Remove Nova and Neutron references to a hypervisor."""

    def __init__(
        self,
        jhelper: JujuHelper,
        deployment: Deployment,
        hostname: str,
        fqdn: str,
        force: bool = False,
    ):
        super().__init__(
            "Remove openstack-hypervisor references",
            "Remove openstack-hypervisor references from the control plane",
        )
        self.jhelper = jhelper
        self.deployment = deployment
        self.force = force
        self._hostnames = tuple(dict.fromkeys((hostname, fqdn)))

    def _remove_references(self) -> None:
        """Remove references and raise while records remain."""
        conn = get_admin_connection(self.jhelper, self.deployment)
        for hostname in self._hostnames:
            remove_compute_service(hostname, conn)
            remove_network_service(hostname, conn)
        remaining_hosts = []
        for hostname in self._hostnames:
            compute_services = list(conn.compute.services(host=hostname))
            network_agents = list(conn.network.agents(host=hostname))
            if compute_services or network_agents:
                remaining_hosts.append(hostname)
        if remaining_hosts:
            raise tenacity.TryAgain(
                f"Hypervisor references remain for {', '.join(remaining_hosts)}"
            )

    def run(self, context: StepContext) -> Result:
        """Remove references until Nova and Neutron report none remain."""
        client_exceptions = (
            click.ClickException,
            openstack.exceptions.SDKException,
            keystoneauth_exceptions.ClientException,
        )
        retry_exceptions: tuple[type[BaseException], ...] = (tenacity.TryAgain,)
        if not self.force:
            retry_exceptions += client_exceptions
        try:
            for attempt in tenacity.Retrying(
                stop=tenacity.stop_after_delay(HYPERVISOR_REFERENCES_TIMEOUT),
                wait=tenacity.wait_fixed(HYPERVISOR_REFERENCES_POLL_INTERVAL),
                retry=tenacity.retry_if_exception_type(retry_exceptions),
                reraise=True,
            ):
                with attempt:
                    self._remove_references()
        except (ModelNotFoundException, ApplicationNotFoundException) as e:
            LOG.debug("OpenStack control plane not deployed, skipping: %r", e)
            return Result(ResultType.SKIPPED)
        except client_exceptions as e:
            LOG.error("Control plane error while removing hypervisor references")
            if self.force:
                LOG.warning(
                    "Force mode set, skipping hypervisor reference cleanup",
                    exc_info=True,
                )
                return Result(ResultType.SKIPPED, str(e))
            return Result(ResultType.FAILED, str(e))
        except tenacity.TryAgain as e:
            LOG.error(
                "Hypervisor references still present after %ss",
                HYPERVISOR_REFERENCES_TIMEOUT,
            )
            return Result(ResultType.FAILED, str(e))

        return Result(ResultType.COMPLETED)


class ReapplyHypervisorTerraformPlanStep(BaseStep):
    """Reapply openstack-hyervisor terraform plan."""

    _CONFIG = CONFIG_KEY

    def __init__(
        self,
        client: Client,
        tfhelper: TerraformHelper,
        jhelper: JujuHelper,
        manifest: Manifest,
        model: str,
        extra_tfvars: dict | None = None,
    ):
        super().__init__(
            "Reapply OpenStack Hypervisor Terraform plan",
            "Reapply OpenStack Hypervisor Terraform plan",
        )
        self.client = client
        self.tfhelper = tfhelper
        self.jhelper = jhelper
        self.manifest = manifest
        self.model = model
        self.extra_tfvars = extra_tfvars if extra_tfvars is not None else {}

    def is_skip(self, context: StepContext) -> Result:
        """Determines if the step should be skipped or not.

        :return: ResultType.SKIPPED if the Step should be skipped,
                ResultType.COMPLETED or ResultType.FAILED otherwise
        """
        if self.client.cluster.list_nodes_by_role("compute"):
            return Result(ResultType.COMPLETED)

        return Result(ResultType.SKIPPED)

    def should_wait_for_hypervisor_status(self) -> bool:
        """Return whether to wait for every hypervisor after applying Terraform."""
        return True

    @tenacity.retry(
        wait=tenacity.wait_fixed(60),
        stop=tenacity.stop_after_delay(300),
        retry=tenacity.retry_if_exception_type(TerraformStateLockedException),
        retry_error_callback=convert_retry_failure_as_result,
    )
    def run(self, context: StepContext) -> Result:
        """Apply terraform configuration to deploy hypervisor."""
        # Refresh model related variables
        self.extra_tfvars["machine_model_uuid"] = self.jhelper.get_model_uuid(
            self.model
        )

        # Override any offer left in Terraform's saved inputs by a failed attempt.
        if "vault-kv-offer-url" not in self.extra_tfvars:
            self.extra_tfvars["vault-kv-offer-url"] = get_vault_kv_offer_url(
                self.client
            )
        self.extra_tfvars["vault-kv-offering-controller"] = (
            get_vault_kv_offering_controller(self.extra_tfvars["vault-kv-offer-url"])
        )

        # Apply Network configs everytime reapply is called
        network_configs = get_external_network_configs(self.client)
        if "charm_config" not in self.extra_tfvars:
            self.extra_tfvars["charm_config"] = {}

        if network_configs:
            LOG.debug(
                "Adding external network configs from DemoSetup to extra tfvars: %s",
                network_configs,
            )
            self.extra_tfvars["charm_config"].update(network_configs)

        pci_allowedlist_config = get_pci_allowedlist_config(self.client)
        LOG.debug("Adding PCI allowedlist configuration: %s", pci_allowedlist_config)
        self.extra_tfvars["charm_config"].update(pci_allowedlist_config)

        dpdk_config = get_dpdk_config(self.client)
        LOG.debug("Adding DPDK configuration: %s", dpdk_config)
        self.extra_tfvars["charm_config"].update(dpdk_config)

        statuses = ["active", "unknown"]
        if len(self.client.cluster.list_nodes_by_role("storage")) < 1:
            LOG.debug("No storage nodes found, allowing hypervisor waiting status")
            statuses.append("waiting")
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

        # Wait for more time since parallel node joins will take time
        # for openstack-hypervisor application to get settled
        if self.should_wait_for_hypervisor_status():
            try:
                self.jhelper.wait_until_desired_status(
                    self.model,
                    [APPLICATION],
                    status=statuses,
                    agent_status=["idle"],
                    timeout=HYPERVISOR_UNIT_TIMEOUT,
                )
            except TimeoutError as e:
                LOG.warning("Timed out waiting for hypervisor application: %r", e)
                return Result(ResultType.FAILED, str(e))

        return Result(ResultType.COMPLETED)


class DeployVaultlockerStep(ReapplyHypervisorTerraformPlanStep, JujuStepHelper):
    """Deploy and relate Vaultlocker before encrypted storage enrollment."""

    def __init__(
        self,
        client: Client,
        tfhelper: TerraformHelper,
        jhelper: JujuHelper,
        manifest: Manifest,
        model: str,
        vault_offer_url: str | None = None,
        target_nodes: list[str] | None = None,
    ):
        super().__init__(client, tfhelper, jhelper, manifest, model, extra_tfvars={})
        self.name = "Deploy Vaultlocker"
        self.description = "Deploying Vaultlocker"
        self.vault_offer_url = vault_offer_url
        self.target_nodes = target_nodes

    def should_wait_for_hypervisor_status(self) -> bool:
        """Use the target-unit readiness check instead of an all-unit wait."""
        return False

    def has_prompts(self) -> bool:
        """Allow the local command to collect an external Vault offer."""
        return True

    def prompt(self, console: Console | None = None, show_hint: bool = False) -> None:
        """Reuse the configured Vault offer or ask for one before deployment."""
        if self.vault_offer_url is None:
            self.vault_offer_url = get_vault_kv_offer_url(self.client)
        if self.vault_offer_url is None and console is not None:
            self.vault_offer_url = sunbeam.core.questions.PromptQuestion(
                "External vault-kv offer URL"
            ).ask()

    @staticmethod
    def _is_vaultlocker_ready(
        status: "jubilant.Status",
        target_machine_ids: set[str] | None = None,
        require_vault_integration: bool = False,
    ) -> bool:
        """Require relations and idle subordinates on each selected compute."""
        vaultlocker = status.apps.get(VAULTLOCKER_APPLICATION)
        hypervisor = status.apps.get(APPLICATION)
        if vaultlocker is None or hypervisor is None or not hypervisor.units:
            return False
        if not any(
            relation.related_app == APPLICATION
            for relation in vaultlocker.relations.get("encrypted-device", [])
        ) or not vaultlocker.relations.get("vault-kv"):
            return False

        # A failed enrollment can leave either workload blocked. The action
        # must still run so the operator can retry it. Check relation wiring
        # and idle agents here; the action proves the mount separately.
        units = list(hypervisor.units.values())
        if target_machine_ids is not None:
            units = [unit for unit in units if unit.machine in target_machine_ids]
            if {unit.machine for unit in units} != target_machine_ids:
                return False
        for unit in units:
            if unit.juju_status.current != "idle":
                return False
            subordinates = [
                subordinate
                for name, subordinate in unit.subordinates.items()
                if name.startswith(f"{VAULTLOCKER_APPLICATION}/")
            ]
            if not subordinates:
                return False
            for subordinate in subordinates:
                if subordinate.juju_status.current != "idle":
                    return False
                workload = subordinate.workload_status
                if require_vault_integration:
                    if (
                        workload.current != "active"
                        or workload.message != "Vault integration ready"
                    ):
                        return False
                elif workload.current not in {"active", "blocked"}:
                    return False
        return True

    def run(self, context: StepContext) -> Result:
        """Apply the offer and confirm it once Vaultlocker can use Vault."""
        if self.target_nodes == []:
            return Result(ResultType.FAILED, "At least one compute host is required")
        vault_offer_url = (
            self.vault_offer_url
            if self.vault_offer_url is not None
            else get_vault_kv_offer_url(self.client)
        )
        if not vault_offer_url or not vault_offer_url.strip():
            return Result(ResultType.FAILED, "External vault-kv offer URL is required")
        confirmed_offer = get_vault_kv_offer_url(self.client)
        if confirmed_offer is not None and confirmed_offer != vault_offer_url:
            return Result(
                ResultType.FAILED,
                "A different vault-kv offer URL is already configured: "
                f"{confirmed_offer}",
            )

        try:
            offering_controller = get_vault_kv_offering_controller(vault_offer_url)
        except ValueError as e:
            return Result(ResultType.FAILED, str(e))
        if offering_controller:
            check = JujuControllerRegistrationCheck(
                offering_controller, Snap().paths.user_data
            )
            if not check.run():
                return Result(ResultType.FAILED, check.message)

        try:
            target_machine_ids = (
                {
                    str(self.client.cluster.get_node_info(node)["machineid"])
                    for node in self.target_nodes
                }
                if self.target_nodes is not None
                else None
            )
        except (NodeNotExistInClusterException, KeyError) as e:
            return Result(ResultType.FAILED, str(e))

        self.extra_tfvars["vault-kv-offer-url"] = vault_offer_url
        self.extra_tfvars["vault-kv-offering-controller"] = offering_controller
        result = super().run(context)
        if result.result_type != ResultType.COMPLETED:
            return result

        if offering_controller:
            try:
                # Use the Juju CLI for remote offers, as observability does.
                self.integrate(
                    self.model, f"{VAULTLOCKER_APPLICATION}:vault-kv", vault_offer_url
                )
            except subprocess.CalledProcessError as e:
                return Result(
                    ResultType.FAILED, f"Failed to integrate Vault offer: {e.stderr}"
                )

        self.update_status(context, "waiting for Vaultlocker relations and units")
        try:
            with self.jhelper._model(self.model) as juju:
                self.jhelper._wait(
                    lambda status: self._is_vaultlocker_ready(
                        status,
                        target_machine_ids,
                        require_vault_integration=confirmed_offer is None,
                    ),
                    juju,
                    delay=MODEL_DELAY,
                    timeout=HYPERVISOR_UNIT_TIMEOUT,
                )
        except TimeoutError as e:
            LOG.warning("Timed out waiting for Vaultlocker readiness: %r", e)
            return Result(ResultType.FAILED, f"Vaultlocker is not ready: {e}")

        if confirmed_offer is None:
            # Pin only after Vaultlocker receives usable Vault credentials.
            # This happens before enrollment, so later retries keep one offer.
            try:
                set_vault_kv_offer_url(self.client, vault_offer_url)
            except ValueError as e:
                return Result(ResultType.FAILED, str(e))

        return Result(ResultType.COMPLETED)


class DestroyHypervisorApplicationStep(DestroyMachineApplicationStep):
    """Destroy Hypervisor application using Terraform."""

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
            "Destroy Hypervisor",
            "Destroying Hypervisor",
        )

    def get_application_timeout(self) -> int:
        """Return application timeout in seconds."""
        return HYPERVISOR_DESTROY_TIMEOUT


class EnableHypervisorStep(BaseStep, JujuStepHelper):
    """Enable hypervisor service."""

    def __init__(
        self,
        client: Client,
        node: str,
        jhelper: JujuHelper,
        model: str,
    ):
        super().__init__(
            "Enable hypervisor service",
            "Enable hypervisor service for unit",
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
        except NodeNotExistInClusterException:
            LOG.debug("Machine %s does not exist, skipping", self.node)
            return Result(ResultType.SKIPPED)

        try:
            application = self.jhelper.get_application(APPLICATION, self.model)
        except ApplicationNotFoundException as e:
            LOG.debug("Failed to get hypervisor application: %r", e)
            return Result(
                ResultType.SKIPPED, "Hypervisor application has not been deployed yet"
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
        """Enable hypervisor service on node."""
        if not self.unit:
            return Result(ResultType.FAILED, "Unit not found on machine")
        try:
            self.jhelper.run_action(self.unit, self.model, "enable")
        except ActionFailedException as e:
            LOG.debug("Failed to enable hypervisor service for %s: %r", self.unit, e)
            return Result(
                ResultType.FAILED,
                f"Failed to enable hypervisor service for unit {self.unit}",
            )
        return Result(ResultType.COMPLETED)
