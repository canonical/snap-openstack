# SPDX-FileCopyrightText: 2024 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import abc
import functools
import logging
from typing import Sequence

import rich.console
import rich.table
import yaml
from rich.status import Status

from sunbeam.clusterd.service import ClusterServiceUnavailableException
from sunbeam.core.common import (
    FORMAT_TABLE,
    FORMAT_YAML,
    Result,
    ResultType,
    SunbeamException,
)
from sunbeam.core.deployment import Deployment
from sunbeam.core.juju import JujuHelper, ModelNotFoundException
from sunbeam.core.steps import BaseStep
from sunbeam.steps import clusterd, hypervisor, k8s, microceph
from sunbeam.utils import merge_dict

LOG = logging.getLogger(__name__)

GREEN = "[green]{}[/green]"
ORANGE = "[orange1]{}[/orange1]"
RED = "[red]{}[/red]"


def color_status(status: str | None) -> str:
    match status:
        case "active" | "running" | "ONLINE":
            return GREEN.format(status)
        case "waiting" | "maintenance":
            return ORANGE.format(status)
        case None:
            return ""
        case _:
            return RED.format(status)


def _cmp(a: str, b: str) -> int:
    if a in {"cluster", "machine"}:
        return -1
    if b in {"cluster", "machine"}:
        return 1
    if a == b:
        return 0
    if a > b:
        return 1
    return -1


def _capitalize(s: str) -> str:
    return " ".join(word.capitalize() for word in s.split("-"))


def format_status(
    deployment: Deployment,
    status: dict,
    format: str,
    mandatory_columns: frozenset[str] = frozenset(("compute", "storage", "control")),
) -> Sequence[rich.console.RenderableType]:
    """Return a list renderables for the status.

    Mandatory columns are always displayed on the openstack machines model, even if no
    member of the cluster has that role.

    Status format is:
    <model>:
        <machine_id>:
            machineid: <machineid>
            role: [<role>, ...]
            status:
                <role>: <status>
    """
    if format == FORMAT_TABLE:
        tables = []
        for model, model_status in sorted(status.items()):
            table = rich.table.Table(
                title=model,
            )
            table.add_column("Node", justify="left")
            column_set: set[str] = set()
            for status_name in model_status.values():
                column_set.update(status_name.get("status", {}).keys())
            if model == deployment.openstack_machines_model:
                column_set.update(mandatory_columns)
            columns: list[str] = sorted(column_set, key=functools.cmp_to_key(_cmp))
            for column in columns:
                table.add_column(_capitalize(column), justify="center")
            for id, node in model_status.items():
                table.add_row(
                    node.get("hostname", id),
                    *(
                        color_status(node.get("status", {}).get(column))
                        for column in columns
                    ),
                )
            tables.append(table)
        return tables
    elif format == FORMAT_YAML:
        return [yaml.dump(status, sort_keys=True)]
    else:
        return [str(status)]


class ClusterStatusStep(abc.ABC, BaseStep):
    def __init__(self, deployment: Deployment, jhelper: JujuHelper):
        super().__init__("Cluster Status", "Querying cluster status")
        self.deployment = deployment
        self.jhelper = jhelper

    @abc.abstractmethod
    def models(self) -> list[str]:
        """List of models to query status from."""
        raise NotImplementedError

    def applications_to_columns(self) -> dict:
        """Mapping of applications to columns."""
        return {
            clusterd.APPLICATION: "clusterd",
            k8s.APPLICATION: "control",
            hypervisor.APPLICATION: "compute",
            microceph.APPLICATION: "storage",
        }

    @abc.abstractmethod
    def _update_microcluster_status(self, status: dict, microcluster_status: dict):
        """How to update microcluster status in the status dict."""
        raise NotImplementedError

    def _get_microcluster_status(self) -> dict:
        client = self.deployment.get_client()
        try:
            cluster_status = client.cluster.get_status()
        except ClusterServiceUnavailableException:
            LOG.debug("Failed to query cluster status", exc_info=True)
            raise SunbeamException("Cluster service is not yet bootstrapped.")
        status = {}
        for node, _status in cluster_status.items():
            status[node] = _status.get("status")
        return status

    def _get_application_status_per_machine(self, model: str) -> dict:
        """Return status of every units of applications in a given model.

        <machine_id>:
            applications:
                <application>:
                    name: <unit_name>
                    status: <status>
        """
        machine_status: dict = {}
        status = self.jhelper.get_model_status(model)
        for app, app_status in status.apps.items():
            for unit, unit_status in app_status.units.items():
                _machine_pointer = machine_status.setdefault(
                    unit_status.machine, {"applications": {}}
                )
                _machine_pointer["applications"][app] = {
                    "name": unit,
                    "status": unit_status.workload_status.current,
                }
        return machine_status

    def _get_machines_status(self, model: str) -> dict:
        """Return status of every machine in a given model.

        <machine_id>:
            name: <machine_hostname>
            status: <status>
        """
        machines_status = {}
        status = self.jhelper.get_model_status(model)
        for machine, machine_status in status.machines.items():
            machine_name = machine_status.hostname
            if not machine_name:
                machine_name = machine_status.dns_name
            machines_status[machine] = {
                "name": machine_name,
                "status": machine_status.machine_status.current,
            }
        return machines_status

    def _to_status(self, status: dict, application_column_mapping: dict) -> dict:
        """Return dict to the correct status format.

        Return format:
        <model>:
            <machine_id>:
                hostname: <machine_hostname>
                status:
                    <role>: <status>
        """
        formatted_status: dict = {}
        for model, model_status in status.items():
            formatted_status[model] = {}
            for machine, machine_status in model_status.items():
                _status = {}
                if mac_status := machine_status.get("status"):
                    _status["machine"] = mac_status
                if clusterd_status := machine_status.get("clusterd-status"):
                    _status["cluster"] = clusterd_status
                if machines_applications := machine_status.get("applications", {}):
                    for app, app_status in machines_applications.items():
                        column = application_column_mapping.get(app)
                        if column is None:
                            continue
                        _status[column] = self.map_application_status(
                            app, app_status["status"]
                        )
                formatted_status[model][machine] = {
                    "hostname": machine_status["name"],
                    "status": _status,
                }
        return formatted_status

    def map_application_status(self, application: str, status: str) -> str:
        """Callback to map application status to a column.

        This callback is called for every unit status with the name of its application.
        """
        return status

    def _compute_status(self) -> dict:
        status = {}
        for model in self.models():
            try:
                _status_model = self._get_machines_status(model)
            except ModelNotFoundException as e:
                LOG.debug(f"Model {model} not found", exc_info=True)
                raise SunbeamException("Failed to query model status.") from e
            status[model] = merge_dict(
                _status_model,
                self._get_application_status_per_machine(model),
            )
        self._update_microcluster_status(status, self._get_microcluster_status())
        return self._to_status(status, self.applications_to_columns())

    def run(self, status: Status | None) -> Result:
        """Run the step to completion."""
        self.update_status(status, "Computing cluster status")
        try:
            cluster_status = self._compute_status()
        except SunbeamException as e:
            return Result(ResultType.FAILED, str(e))
        return Result(ResultType.COMPLETED, cluster_status)
