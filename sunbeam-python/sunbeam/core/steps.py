# SPDX-FileCopyrightText: 2023 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import abc
import logging
import queue
import typing

import tenacity
from rich.status import Status

from sunbeam.clusterd.client import Client
from sunbeam.clusterd.service import (
    ConfigItemNotFoundException,
)
from sunbeam.core.common import (
    BaseStep,
    Result,
    ResultType,
    convert_retry_failure_as_result,
    read_config,
    update_config,
    update_status_background,
)
from sunbeam.core.deployment import Deployment
from sunbeam.core.juju import (
    ApplicationNotFoundException,
    JujuException,
    JujuHelper,
    JujuWaitException,
    ModelNotFoundException,
)
from sunbeam.core.k8s import K8SHelper
from sunbeam.core.manifest import Manifest
from sunbeam.core.terraform import (
    TerraformException,
    TerraformHelper,
    TerraformStateLockedException,
)
from sunbeam.lazy import LazyImport

if typing.TYPE_CHECKING:
    import lightkube.config.kubeconfig as l_kubeconfig
    import lightkube.core.client as l_client
    import lightkube.core.exceptions as l_exceptions
    from lightkube.models import meta_v1
    from lightkube.resources import core_v1
else:
    l_kubeconfig = LazyImport("lightkube.config.kubeconfig")
    l_client = LazyImport("lightkube.core.client")
    l_exceptions = LazyImport("lightkube.core.exceptions")
    meta_v1 = LazyImport("lightkube.models.meta_v1")
    core_v1 = LazyImport("lightkube.resources.core_v1")


LOG = logging.getLogger(__name__)


class DeployMachineApplicationStep(BaseStep):
    """Base class to deploy machine application using Terraform cloud."""

    def __init__(
        self,
        deployment: Deployment,
        client: Client,
        tfhelper: TerraformHelper,
        jhelper: JujuHelper,
        manifest: Manifest,
        config: str,
        application: str,
        model: str,
        banner: str = "",
        description: str = "",
        refresh: bool = False,
    ):
        super().__init__(banner, description)
        self.deployment = deployment
        self.client = client
        self.tfhelper = tfhelper
        self.jhelper = jhelper
        self.manifest = manifest
        self.config = config
        self.application = application
        self.model = model
        # Set refresh flag to True to redeploy the application
        self.refresh = refresh

    def extra_tfvars(self) -> dict:
        """Extra terraform vars to pass to terraform apply."""
        return {}

    def tf_apply_extra_args(self) -> list:
        """Extra args for the terraform apply command."""
        return []

    def get_application_timeout(self) -> int:
        """Application timeout in seconds."""
        return 600

    def is_skip(self, status: Status | None = None) -> Result:
        """Determines if the step should be skipped or not.

        :return: ResultType.SKIPPED if the Step should be skipped,
                ResultType.COMPLETED or ResultType.FAILED otherwise
        """
        if self.refresh:
            return Result(ResultType.COMPLETED)

        try:
            self.jhelper.get_application(self.application, self.model)
        except ApplicationNotFoundException:
            return Result(ResultType.COMPLETED)

        return Result(ResultType.SKIPPED)

    def get_accepted_application_status(self) -> list[str]:
        """Accepted status to pass wait_application_ready function."""
        return ["active", "unknown"]

    @tenacity.retry(
        wait=tenacity.wait_fixed(60),
        stop=tenacity.stop_after_delay(300),
        retry=tenacity.retry_if_exception_type(TerraformStateLockedException),
        retry_error_callback=convert_retry_failure_as_result,
    )
    def run(self, status: Status | None = None) -> Result:
        """Apply terraform configuration to deploy sunbeam machine."""
        machine_ids: list[str] = []

        try:
            app = self.jhelper.get_application(self.application, self.model)
            machine_ids.extend(unit.machine for unit in app.units.values())
        except ApplicationNotFoundException as e:
            LOG.debug(str(e))

        try:
            extra_tfvars = self.extra_tfvars()
            extra_tfvars.update(
                {
                    "machine_ids": machine_ids,
                    "machine_model": self.model,
                }
            )
            self.tfhelper.update_tfvars_and_apply_tf(
                self.client,
                self.manifest,
                tfvar_config=self.config,
                override_tfvars=extra_tfvars,
                tf_apply_extra_args=self.tf_apply_extra_args(),
            )
        except TerraformException as e:
            return Result(ResultType.FAILED, str(e))

        # Note(gboutry): application is in state unknown when it's deployed
        # without units
        try:
            self.jhelper.wait_application_ready(
                self.application,
                self.model,
                accepted_status=self.get_accepted_application_status(),
                timeout=self.get_application_timeout(),
            )
        except TimeoutError as e:
            LOG.warning(str(e))
            return Result(ResultType.FAILED, str(e))

        return Result(ResultType.COMPLETED)


class AddMachineUnitsStep(BaseStep):
    """Base class to add units of machine application."""

    def __init__(
        self,
        client: Client,
        names: list[str] | str,
        jhelper: JujuHelper,
        config: str,
        application: str,
        model: str,
        banner: str = "",
        description: str = "",
        subordinate_applications: list[str] | None = None,
    ):
        super().__init__(banner, description)
        self.client = client
        if isinstance(names, str):
            names = [names]
        self.names = names
        self.jhelper = jhelper
        self.config = config
        self.application = application
        self.model = model
        self.to_deploy: set[str] = set()
        self.subordinate_applications = subordinate_applications or []

    def get_unit_timeout(self) -> int:
        """Return unit timeout in seconds."""
        return 600  # 10 minutes

    def is_skip(self, status: Status | None = None) -> Result:
        """Determines if the step should be skipped or not.

        :return: ResultType.SKIPPED if the Step should be skipped,
                ResultType.COMPLETED or ResultType.FAILED otherwise
        """
        if len(self.names) == 0:
            return Result(ResultType.SKIPPED)
        nodes: list[dict] = self.client.cluster.list_nodes()

        filtered_nodes = list(filter(lambda node: node["name"] in self.names, nodes))
        if len(filtered_nodes) != len(self.names):
            filtered_node_names = [node["name"] for node in filtered_nodes]
            missing_nodes = set(self.names) - set(filtered_node_names)
            return Result(
                ResultType.FAILED,
                f"Nodes '{','.join(missing_nodes)}' do not exist in cluster database",
            )

        nodes_without_machine_id = []

        for node in filtered_nodes:
            node_machine_id = node.get("machineid", -1)
            if node_machine_id == -1:
                nodes_without_machine_id.append(node["name"])
                continue
            self.to_deploy.add(str(node_machine_id))

        if len(nodes_without_machine_id) > 0:
            return Result(
                ResultType.FAILED,
                f"Nodes '{','.join(nodes_without_machine_id)}' do not have machine id,"
                " are they deployed?",
            )
        try:
            app = self.jhelper.get_application(self.application, self.model)
            deployed_units_machine_ids = {unit.machine for unit in app.units.values()}
        except ApplicationNotFoundException:
            return Result(
                ResultType.FAILED,
                f"Application {self.application} has not been deployed",
            )

        self.to_deploy -= deployed_units_machine_ids
        if len(self.to_deploy) == 0:
            return Result(ResultType.SKIPPED, "No new units to deploy")

        return Result(ResultType.COMPLETED)

    def add_machine_id_to_tfvar(self) -> None:
        """Add machine id to terraform vars saved in cluster db."""
        try:
            tfvars = read_config(self.client, self.config)
        except ConfigItemNotFoundException:
            tfvars = {}

        machine_ids = set(tfvars.get("machine_ids", []))

        if len(self.to_deploy) > 0 and self.to_deploy.issubset(machine_ids):
            LOG.debug("All machine ids are already in tfvars, skipping update")
            return

        machine_ids.update(self.to_deploy)
        tfvars.update({"machine_ids": sorted(machine_ids)})
        update_config(self.client, self.config, tfvars)

    def get_accepted_unit_status(self) -> dict[str, list[str]]:
        """Accepted status to pass wait_units_ready function."""
        return {"agent": ["idle"], "workload": ["active"]}

    def run(self, status: Status | None = None) -> Result:
        """Add unit to machine application on Juju model."""
        try:
            unit_names = self.jhelper.add_unit(
                self.model, self.application, list(self.to_deploy)
            )
            self.add_machine_id_to_tfvar()
        except ApplicationNotFoundException as e:
            LOG.warning(str(e))
            return Result(ResultType.FAILED, str(e))

        apps = [self.application, *self.subordinate_applications]
        status_queue: queue.Queue[str] = queue.Queue(maxsize=len(apps))
        task = update_status_background(self, apps, status_queue, status)
        accepted_status = self.get_accepted_unit_status()
        try:
            self.jhelper.wait_until_desired_status(
                self.model,
                apps,
                units=unit_names,
                status=accepted_status["workload"],
                agent_status=accepted_status["agent"],
                timeout=self.get_unit_timeout(),
                queue=status_queue,
            )
        except (JujuWaitException, TimeoutError) as e:
            LOG.warning(str(e))
            return Result(ResultType.FAILED, str(e))
        finally:
            task.stop()
        return Result(ResultType.COMPLETED)


class RemoveMachineUnitsStep(BaseStep):
    """Base class to remove unit of machine application."""

    units_to_remove: set[str]

    def __init__(
        self,
        client: Client,
        names: list[str] | str,
        jhelper: JujuHelper,
        config: str,
        application: str,
        model: str,
        banner: str = "",
        description: str = "",
    ):
        super().__init__(banner, description)
        self.client = client
        if isinstance(names, str):
            names = [names]
        self.names = names
        self.jhelper = jhelper
        self.config = config
        self.application = application
        self.model = model
        self.machine_id = ""
        self.unit = None
        self.units_to_remove = set()

    def get_unit_timeout(self) -> int:
        """Return unit timeout in seconds."""
        return 600  # 10 minutes

    def is_skip(self, status: Status | None = None) -> Result:
        """Determines if the step should be skipped or not.

        :return: ResultType.SKIPPED if the Step should be skipped,
                ResultType.COMPLETED or ResultType.FAILED otherwise
        """
        if len(self.names) == 0:
            return Result(ResultType.SKIPPED)
        nodes: list[dict] = self.client.cluster.list_nodes()

        filtered_nodes = list(filter(lambda node: node["name"] in self.names, nodes))
        if len(filtered_nodes) != len(self.names):
            filtered_node_names = [node["name"] for node in filtered_nodes]
            missing_nodes = set(self.names) - set(filtered_node_names)
            LOG.debug(
                f"Nodes '{','.join(missing_nodes)}' do not exist in cluster database"
            )

        try:
            app = self.jhelper.get_application(self.application, self.model)
        except ApplicationNotFoundException:
            LOG.debug("Failed to get application", exc_info=True)
            return Result(
                ResultType.SKIPPED,
                f"Application {self.application} has not been deployed yet",
            )

        to_remove_node_ids = {str(node["machineid"]) for node in filtered_nodes}

        for name, unit in app.units.items():
            if unit.machine in to_remove_node_ids:
                LOG.debug(f"Unit {name} is deployed on machine: {self.machine_id}")
                self.units_to_remove.add(name)

        if len(self.units_to_remove) == 0:
            return Result(ResultType.SKIPPED)

        return Result(ResultType.COMPLETED)

    def run(self, status: Status | None = None) -> Result:
        """Remove unit from machine application on Juju model."""
        try:
            self.update_status(status, "Removing units")
            for unit in self.units_to_remove:
                LOG.debug(f"Removing unit {unit} from application {self.application}")
                self.jhelper.remove_unit(self.application, unit, self.model)
            self.update_status(status, "Waiting for units to be removed")
            self.jhelper.wait_units_gone(
                list(self.units_to_remove), self.model, self.get_unit_timeout()
            )
            self.jhelper.wait_application_ready(
                self.application,
                self.model,
                accepted_status=["active", "unknown"],
                timeout=self.get_unit_timeout(),
            )
        except (ApplicationNotFoundException, TimeoutError) as e:
            LOG.warning(str(e))
            return Result(ResultType.FAILED, str(e))

        return Result(ResultType.COMPLETED)


class DestroyMachineApplicationStep(BaseStep):
    """Base class to destroy machine application using Terraform."""

    def __init__(
        self,
        client: Client,
        tfhelper: TerraformHelper,
        jhelper: JujuHelper,
        manifest: Manifest,
        config: str,
        applications: list[str],
        model: str,
        banner: str = "",
        description: str = "",
    ):
        super().__init__(banner, description)
        self.client = client
        self.tfhelper = tfhelper
        self.jhelper = jhelper
        self.manifest = manifest
        self.config = config
        self.applications = applications
        self.model = model
        self._has_tf_resources = False

    def get_application_timeout(self) -> int:
        """Application timeout in seconds."""
        return 600

    def _list_applications(self, model: str) -> list[str]:
        """List applications managed by this step."""
        apps = []
        _model = self.jhelper.get_model_status(model)

        for app in self.applications:
            if app in _model.apps:
                apps.append(app)
                LOG.debug("Found application %s", app)

        return apps

    def _wait_applications_gone(self, timeout: int) -> None:
        """Wait for applications to be removed."""
        self.jhelper.wait_application_gone(
            self.applications, self.model, timeout=timeout
        )

    def is_skip(self, status: Status | None = None) -> Result:
        """Determines if the step should be skipped or not.

        :return: ResultType.SKIPPED if the Step should be skipped,
                ResultType.COMPLETED or ResultType.FAILED otherwise
        """
        try:
            state = self.tfhelper.pull_state()
            self._has_tf_resources = bool(state.get("resources"))
        except TerraformException:
            LOG.debug("Failed to pull state", exc_info=True)

        try:
            _has_juju_resources = len(self._list_applications(self.model)) > 0
        except ModelNotFoundException:
            LOG.debug("Model not found", exc_info=True)
            _has_juju_resources = False

        if not self._has_tf_resources and not _has_juju_resources:
            return Result(ResultType.SKIPPED)

        return Result(ResultType.COMPLETED)

    @tenacity.retry(
        wait=tenacity.wait_fixed(60),
        stop=tenacity.stop_after_delay(300),
        retry=tenacity.retry_if_exception_type(TerraformStateLockedException),
        retry_error_callback=convert_retry_failure_as_result,
    )
    def run(self, status: Status | None = None) -> Result:
        """Destroy machine application using Terraform."""
        if self._has_tf_resources:
            try:
                self.tfhelper.update_tfvars_and_apply_tf(
                    self.client,
                    self.manifest,
                    tfvar_config=self.config,
                    override_tfvars={
                        "machine_model": self.model,
                    },
                    tf_apply_extra_args=["-input=false", "-destroy"],
                )
            except TerraformException as e:
                return Result(ResultType.FAILED, str(e))

        timeout_factor = 0.8

        try:
            self._wait_applications_gone(
                int(self.get_application_timeout() * timeout_factor)
            )
        except TimeoutError:
            LOG.warning("Failed to destroy applications, trying through provider sdk")
            apps = self._list_applications(self.model)
            try:
                self.jhelper.remove_application(
                    *apps, model=self.model, destroy_storage=True, force=True
                )
            except JujuException:
                LOG.debug("Failed to destroy applications", exc_info=True)
            try:
                self._wait_applications_gone(
                    int(self.get_application_timeout() * (1 - timeout_factor))
                )
            except TimeoutError:
                return Result(
                    ResultType.FAILED, "Timed out destroying applications, try manually"
                )

        return Result(ResultType.COMPLETED)


class PatchLoadBalancerServicesIPStep(BaseStep, abc.ABC):
    def __init__(
        self,
        client: Client,
    ):
        super().__init__(
            "Patch LoadBalancer services",
            "Patch LoadBalancer service IP annotation",
        )
        self.client = client
        self.lb_ip_annotation = K8SHelper.get_loadbalancer_ip_annotation()

    @abc.abstractmethod
    def services(self) -> list[str]:
        """List of services to patch."""
        pass

    @abc.abstractmethod
    def model(self) -> str:
        """Name of the model to use.

        This must resolve to a namespaces in the cluster.
        """
        pass

    def _get_service(
        self, service_name: str, find_lb: bool = True
    ) -> "core_v1.Service":
        """Look up a service by name, optionally looking for a LoadBalancer service."""
        search_service = service_name
        if find_lb:
            search_service += "-lb"
        try:
            return self.kube.get(core_v1.Service, search_service)
        except l_exceptions.ApiError as e:
            if e.status.code == 404 and search_service.endswith("-lb"):
                return self._get_service(service_name, find_lb=False)
            raise e

    def is_skip(self, status: Status | None = None) -> Result:
        """Determines if the step should be skipped or not.

        :return: ResultType.SKIPPED if the Step should be skipped,
                ResultType.COMPLETED or ResultType.FAILED otherwise
        """
        try:
            self.kubeconfig = read_config(self.client, K8SHelper.get_kubeconfig_key())
        except ConfigItemNotFoundException:
            LOG.debug("K8S kubeconfig not found", exc_info=True)
            return Result(ResultType.FAILED, "K8S kubeconfig not found")

        kubeconfig = l_kubeconfig.KubeConfig.from_dict(self.kubeconfig)
        try:
            self.kube = l_client.Client(kubeconfig, self.model(), trust_env=False)
        except l_exceptions.ConfigError as e:
            LOG.debug("Error creating k8s client", exc_info=True)
            return Result(ResultType.FAILED, str(e))

        for service_name in self.services():
            try:
                service = self._get_service(service_name, find_lb=True)
            except l_exceptions.ApiError as e:
                return Result(ResultType.FAILED, str(e))
            if not service.metadata:
                return Result(
                    ResultType.FAILED, f"k8s service {service_name!r} has no metadata"
                )
            service_annotations = service.metadata.annotations
            if (
                service_annotations is None
                or self.lb_ip_annotation not in service_annotations
            ):
                return Result(ResultType.COMPLETED)

        return Result(ResultType.SKIPPED)

    def run(self, status: Status | None = None) -> Result:
        """Patch LoadBalancer services annotations with LB IP."""
        for service_name in self.services():
            try:
                service = self._get_service(service_name, find_lb=True)
            except l_exceptions.ApiError as e:
                return Result(ResultType.FAILED, str(e))
            if not service.metadata:
                return Result(
                    ResultType.FAILED, f"k8s service {service_name!r} has no metadata"
                )
            service_name = str(service.metadata.name)
            service_annotations = service.metadata.annotations
            if service_annotations is None:
                service_annotations = {}
            if self.lb_ip_annotation not in service_annotations:
                if not service.status:
                    return Result(
                        ResultType.FAILED, f"k8s service {service_name!r} has no status"
                    )
                if not service.status.loadBalancer:
                    return Result(
                        ResultType.FAILED,
                        f"k8s service {service_name!r} has no loadBalancer status",
                    )
                if not service.status.loadBalancer.ingress:
                    return Result(
                        ResultType.FAILED,
                        f"k8s service {service_name!r} has no loadBalancer ingress",
                    )
                loadbalancer_ip = service.status.loadBalancer.ingress[0].ip
                service_annotations[self.lb_ip_annotation] = loadbalancer_ip
                service.metadata.annotations = service_annotations
                LOG.debug(f"Patching {service_name!r} to use IP {loadbalancer_ip!r}")
                self.kube.patch(core_v1.Service, service_name, obj=service)

        return Result(ResultType.COMPLETED)


class PatchLoadBalancerServicesIPPoolStep(BaseStep, abc.ABC):
    def __init__(
        self,
        client: Client,
        pool_name: str,
    ):
        super().__init__(
            "Patch LoadBalancer services",
            "Patch LoadBalancer service IP pool annotation",
        )
        self.client = client
        self.pool_name = pool_name
        self.lb_pool_annotation = K8SHelper.get_loadbalancer_address_pool_annotation()
        self.lb_ip_annotation = K8SHelper.get_loadbalancer_ip_annotation()
        self.lb_allocated_pool_annotation = (
            K8SHelper.get_loadbalancer_allocated_pool_annotation()
        )

    @abc.abstractmethod
    def services(self) -> list[str]:
        """List of services to patch."""
        pass

    @abc.abstractmethod
    def model(self) -> str:
        """Name of the model to use.

        This must resolve to a namespaces in the cluster.
        """
        pass

    def _get_service(
        self, service_name: str, find_lb: bool = True
    ) -> "core_v1.Service":
        """Look up a service by name, optionally looking for a LoadBalancer service."""
        search_service = service_name
        if find_lb:
            search_service += "-lb"
        try:
            return self.kube.get(core_v1.Service, search_service)
        except l_exceptions.ApiError as e:
            if e.status.code == 404 and search_service.endswith("-lb"):
                return self._get_service(service_name, find_lb=False)
            raise e

    def check_lb_pool_exists_in_annotations(
        self, service_annotations: dict, lb_pool: str
    ) -> bool:
        """Check if loadbalancer pool is already in annotations.

        Also check if ip address is allocated from same pool.
        """
        if (
            service_annotations.get(self.lb_pool_annotation) == lb_pool
            and service_annotations.get(self.lb_allocated_pool_annotation) == lb_pool
        ):
            return True

        return False

    @tenacity.retry(
        wait=tenacity.wait_fixed(10),
        stop=tenacity.stop_after_delay(300),
        retry=tenacity.retry_if_exception_type(ValueError),
        reraise=True,
    )
    def _wait_for_ip_allocated_from_pool_annotation_update(
        self, service_name: str, pool_name: str
    ):
        """Wait until metallb.io/ip-allocated-from-pool is updated.

        Wait until the ip-allocated-from-pool annotation is updated to pool_name
        for the service
        Raises ApiError from lightkube if not connected to k8s
        """
        service = self._get_service(service_name, find_lb=False)
        LOG.debug(f"Waiting for service {service} annotations to get updated")

        if not service.metadata:
            raise ValueError(f"Service {service_name} has no metadata")

        service_annotations = service.metadata.annotations
        if service_annotations is None:
            service_annotations = {}

        if service_annotations.get(self.lb_allocated_pool_annotation) != pool_name:
            raise ValueError(
                f"Service {service_name} annotation {self.lb_allocated_pool_annotation}"
                f" is not updated to {pool_name}"
            )

    def run(self, status: Status | None = None) -> Result:
        """Patch LoadBalancer services annotations with LB IP pool."""
        try:
            self.kubeconfig = read_config(self.client, K8SHelper.get_kubeconfig_key())
        except ConfigItemNotFoundException:
            LOG.debug("K8S kubeconfig not found", exc_info=True)
            return Result(ResultType.FAILED, "K8S kubeconfig not found")

        kubeconfig = l_kubeconfig.KubeConfig.from_dict(self.kubeconfig)
        try:
            self.kube = l_client.Client(kubeconfig, self.model(), trust_env=False)
        except l_exceptions.ConfigError as e:
            LOG.debug("Error creating k8s client", exc_info=True)
            return Result(ResultType.FAILED, str(e))

        for service_name in self.services():
            try:
                service = self._get_service(service_name, find_lb=True)
            except l_exceptions.ApiError as e:
                return Result(ResultType.FAILED, str(e))
            if not service.metadata:
                return Result(
                    ResultType.FAILED, f"k8s service {service_name!r} has no metadata"
                )
            service_name = str(service.metadata.name)
            service_annotations = service.metadata.annotations
            if service_annotations is None:
                service_annotations = {}

            if not self.check_lb_pool_exists_in_annotations(
                service_annotations, self.pool_name
            ):
                if not service.status:
                    return Result(
                        ResultType.FAILED, f"k8s service {service_name!r} has no status"
                    )
                if not service.status.loadBalancer:
                    return Result(
                        ResultType.FAILED,
                        f"k8s service {service_name!r} has no loadBalancer status",
                    )
                if not service.status.loadBalancer.ingress:
                    return Result(
                        ResultType.FAILED,
                        f"k8s service {service_name!r} has no loadBalancer ingress",
                    )

                service_annotations[self.lb_pool_annotation] = self.pool_name
                if self.lb_ip_annotation in service_annotations:
                    LOG.debug(
                        f"Removing {self.lb_ip_annotation!r} for service "
                        f"{service_name!r}"
                    )
                    service_annotations.pop(self.lb_ip_annotation)
                if self.lb_allocated_pool_annotation in service_annotations:
                    LOG.debug(
                        f"Removing {self.lb_allocated_pool_annotation!r} for service"
                        f" {service_name!r}"
                    )
                    service_annotations.pop(self.lb_allocated_pool_annotation)
                LOG.debug(
                    f"Patching {service_name!r} to use annotation "
                    f"{self.lb_pool_annotation!r} with value {self.pool_name!r}"
                )
                self.kube.patch(core_v1.Service, service_name, obj=service)

                try:
                    self._wait_for_ip_allocated_from_pool_annotation_update(
                        service_name, self.pool_name
                    )
                except ValueError as e:
                    return Result(ResultType.FAILED, str(e))

        return Result(ResultType.COMPLETED)


class CreateLoadBalancerIPPoolsStep(BaseStep, abc.ABC):
    """Create IPPool and L2Advertisement resources."""

    def __init__(
        self,
        client: Client,
    ):
        super().__init__(
            "Create LoadBalancer pool",
            "Creating LoadBalancer pool",
        )
        self.client = client
        self.lbpool_resource = K8SHelper.get_lightkube_loadbalancer_resource()
        self.l2_advertisement_resource = (
            K8SHelper.get_lightkube_l2_advertisement_resource()
        )
        self.model = K8SHelper.get_loadbalancer_namespace()

    @abc.abstractmethod
    def ippools(self) -> dict[str, list[str]]:
        """IPAddress pools.

        Pools should be in format of
        {<pool name>: <List of ipaddresses>}
        """
        pass

    def handle_lb_pools(self, name: str, addresses: list[str]):
        """Manage Loadbalancer IP Address pool."""
        pool = None
        try:
            pool = self.kube.get(self.lbpool_resource, name=name, namespace=self.model)
        except l_exceptions.ApiError as e:
            if e.status.code != 404:
                raise e

        # Pool already exists in k8s, replace the pool if addresses vary
        if pool:
            if pool.spec["addresses"] != addresses:
                LOG.debug(f"Update IP Address pool {name} addresses with {addresses}")
                pool.spec["addresses"] = addresses
                self.kube.replace(pool)
        else:
            LOG.debug(f"Create new IP Address Pool {name} with addresses {addresses}")
            new_ippool = self.lbpool_resource(
                metadata=meta_v1.ObjectMeta(name=name),
                spec={"addresses": addresses, "autoAssign": False},
            )
            self.kube.create(new_ippool)

    def handle_l2_advertisement(self, name: str):
        """Manage L2Advertisement resource.

        Kept for backward compatibility, deleting the resource on
        upgraded versions of Sunbeam.
        """
        try:
            self.kube.get(
                self.l2_advertisement_resource, name=name, namespace=self.model
            )
            self.kube.delete(self.l2_advertisement_resource, name, namespace=self.model)
        except l_exceptions.ApiError as e:
            if e.status.code != 404:
                raise

    def run(self, status: Status | None = None) -> Result:
        """Create Loadbalancer IPPool."""
        try:
            self.kubeconfig = read_config(self.client, K8SHelper.get_kubeconfig_key())
        except ConfigItemNotFoundException:
            LOG.debug("K8S kubeconfig not found", exc_info=True)
            return Result(ResultType.FAILED, "K8S kubeconfig not found")

        kubeconfig = l_kubeconfig.KubeConfig.from_dict(self.kubeconfig)
        try:
            self.kube = l_client.Client(kubeconfig, self.model, trust_env=False)
        except l_exceptions.ConfigError as e:
            LOG.debug("Error creating k8s client", exc_info=True)
            return Result(ResultType.FAILED, str(e))

        for name, addresses in self.ippools().items():
            try:
                self.handle_lb_pools(name, addresses)
            except l_exceptions.ApiError as e:
                return Result(
                    ResultType.FAILED,
                    f"Error in processing LoadBalancer pool {name}: {str(e)}",
                )

            try:
                self.handle_l2_advertisement(name)
            except l_exceptions.ApiError as e:
                return Result(
                    ResultType.FAILED,
                    f"Error in processing L2Advertisement {name}: {str(e)}",
                )

        return Result(ResultType.COMPLETED)
