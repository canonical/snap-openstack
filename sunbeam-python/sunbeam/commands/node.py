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

import logging
import shutil
from pathlib import Path
from typing import List, Optional

import click
import yaml
from rich.console import Console
from rich.table import Table
from snaphelpers import Snap

from sunbeam import utils
from sunbeam.commands.clusterd import (
    ClusterAddJujuUserStep,
    ClusterAddNodeStep,
    ClusterJoinNodeStep,
    ClusterListNodeStep,
    ClusterRemoveNodeStep,
    ClusterUpdateNodeStep,
)
from sunbeam.commands.configure import SetLocalHypervisorOptions
from sunbeam.commands.hypervisor import (
    AddHypervisorUnitStep,
    DeployHypervisorApplicationStep,
)
from sunbeam.commands.juju import AddJujuMachineStep  # RemoveJujuUserStep,
from sunbeam.commands.juju import (
    CreateJujuUserStep,
    JujuGrantModelAccessStep,
    RegisterJujuUserStep,
    RemoveJujuMachineStep,
    SaveJujuUserLocallyStep,
)
from sunbeam.commands.microceph import (
    AddMicrocephUnitStep,
    ConfigureMicrocephOSDStep,
    RemoveMicrocephUnitStep,
)
from sunbeam.commands.microk8s import AddMicrok8sUnitStep, RemoveMicrok8sUnitStep
from sunbeam.commands.openstack import OPENSTACK_MODEL
from sunbeam.commands.rocks import (
    ConfigureKubeletOptionsStep,
    ConfigurePullThroughCacheStep,
    PreseedRocksStep,
)
from sunbeam.commands.sunbeam_machine import (
    AddSunbeamMachineUnitStep,
)
from sunbeam.commands.terraform import TerraformHelper, TerraformInitStep
from sunbeam.jobs.checks import (
    DaemonGroupCheck,
    JujuSnapCheck,
    LocalShareCheck,
    SshKeysConnectedCheck,
    VerifyFQDNCheck,
)
from sunbeam.jobs.common import (
    ResultType,
    Role,
    get_step_message,
    roles_to_str_list,
    run_plan,
    run_preflight_checks,
    validate_roles,
)
from sunbeam.jobs.juju import CONTROLLER, JujuHelper

LOG = logging.getLogger(__name__)
console = Console()
snap = Snap()

FORMAT_TABLE = "table"
FORMAT_YAML = "yaml"
FORMAT_DEFAULT = "default"
FORMAT_VALUE = "value"


def remove_trailing_dot(value: str) -> str:
    """Remove trailing dot from the value."""
    return value.rstrip(".")


@click.command()
@click.option(
    "--name",
    type=str,
    prompt=True,
    help="Fully qualified node name",
)
@click.option(
    "-f",
    "--format",
    type=click.Choice([FORMAT_DEFAULT, FORMAT_VALUE, FORMAT_YAML]),
    default=FORMAT_DEFAULT,
    help="Output format.",
)
def add(name: str, format: str) -> None:
    """Generate a token for a new node to join the cluster."""
    preflight_checks = [DaemonGroupCheck(), VerifyFQDNCheck(name)]
    run_preflight_checks(preflight_checks, console)

    name = remove_trailing_dot(name)
    data_location = snap.paths.user_data
    jhelper = JujuHelper(data_location)

    plan1 = [
        ClusterAddNodeStep(name),
        CreateJujuUserStep(name),
        JujuGrantModelAccessStep(jhelper, name, OPENSTACK_MODEL),
    ]

    plan1_results = run_plan(plan1, console)

    user_token = get_step_message(plan1_results, CreateJujuUserStep)

    plan2 = [ClusterAddJujuUserStep(name, user_token)]
    run_plan(plan2, console)

    def _print_output(token):
        """Helper for printing formatted output."""
        if format == FORMAT_DEFAULT:
            console.print(f"Token for the Node {name}: {token}", soft_wrap=True)
        elif format == FORMAT_YAML:
            click.echo(yaml.dump({"token": token}))
        elif format == FORMAT_VALUE:
            click.echo(token)

    add_node_step_result = plan1_results.get("ClusterAddNodeStep")
    if add_node_step_result.result_type == ResultType.COMPLETED:
        _print_output(add_node_step_result.message)
    elif add_node_step_result.result_type == ResultType.SKIPPED:
        if add_node_step_result.message:
            _print_output(add_node_step_result.message)
        else:
            console.print("Node already a member of the Sunbeam cluster")


@click.command()
@click.option("-a", "--accept-defaults", help="Accept all defaults.", is_flag=True)
@click.option(
    "-p",
    "--preseed",
    help="Preseed file.",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
)
@click.option("--token", type=str, help="Join token")
@click.option(
    "--role",
    "roles",
    multiple=True,
    default=["control", "compute"],
    type=click.Choice(["control", "compute", "storage"], case_sensitive=False),
    callback=validate_roles,
    help="Specify which roles the node will be assigned in the cluster.",
)
def join(
    token: str,
    roles: List[Role],
    preseed: Optional[Path] = None,
    accept_defaults: bool = False,
) -> None:
    """Join node to the cluster.

    Join the node to the cluster.
    """
    is_control_node = any(role.is_control_node() for role in roles)
    is_compute_node = any(role.is_compute_node() for role in roles)
    is_storage_node = any(role.is_storage_node() for role in roles)

    # Register juju user with same name as Node fqdn
    name = utils.get_fqdn()
    ip = utils.get_local_ip_by_default_route()

    roles_str = roles_to_str_list(roles)
    pretty_roles = ", ".join(role_.name.lower() for role_ in roles)
    LOG.debug(f"Node joining the cluster with roles: {pretty_roles}")

    preflight_checks = []
    preflight_checks.append(JujuSnapCheck())
    preflight_checks.append(SshKeysConnectedCheck())
    preflight_checks.append(DaemonGroupCheck())
    preflight_checks.append(LocalShareCheck())

    run_preflight_checks(preflight_checks, console)

    controller = CONTROLLER
    data_location = snap.paths.user_data

    # NOTE: install to user writable location
    tfplan_dirs = ["deploy-sunbeam-machine"]
    if is_control_node:
        tfplan_dirs.extend(["deploy-microk8s", "deploy-microceph", "deploy-openstack"])
    if is_compute_node:
        tfplan_dirs.extend(["deploy-openstack-hypervisor"])
    for tfplan_dir in tfplan_dirs:
        src = snap.paths.snap / "etc" / tfplan_dir
        dst = snap.paths.user_common / "etc" / tfplan_dir
        LOG.debug(f"Updating {dst} from {src}...")
        shutil.copytree(src, dst, dirs_exist_ok=True)

    tfhelper_openstack_deploy = TerraformHelper(
        path=snap.paths.user_common / "etc" / "deploy-openstack",
        plan="openstack-plan",
        parallelism=1,
        backend="http",
        data_location=data_location,
    )
    tfhelper_hypervisor_deploy = TerraformHelper(
        path=snap.paths.user_common / "etc" / "deploy-openstack-hypervisor",
        plan="hypervisor-plan",
        parallelism=1,
        backend="http",
        data_location=data_location,
    )
    jhelper = JujuHelper(data_location)

    plan1 = [
        ClusterJoinNodeStep(token, roles_str),
        SaveJujuUserLocallyStep(name, data_location),
        RegisterJujuUserStep(name, controller, data_location),
        AddJujuMachineStep(ip),
    ]
    plan1_results = run_plan(plan1, console)

    machine_id = -1
    machine_id_result = get_step_message(plan1_results, AddJujuMachineStep)
    if machine_id_result is not None:
        machine_id = int(machine_id_result)

    jhelper = JujuHelper(data_location)
    plan2 = []
    plan2.append(ClusterUpdateNodeStep(name, machine_id=machine_id))
    plan2.append(
        AddSunbeamMachineUnitStep(name, jhelper),
    )

    if is_control_node:
        plan2.append(AddMicrok8sUnitStep(name, jhelper))
        plan2.append(ConfigureKubeletOptionsStep(name))
        plan2.append(ConfigurePullThroughCacheStep(name))
        plan2.append(PreseedRocksStep(name))

    if is_storage_node:
        plan2.append(AddMicrocephUnitStep(name, jhelper))
        plan2.append(
            ConfigureMicrocephOSDStep(
                name, jhelper, accept_defaults=accept_defaults, preseed_file=preseed
            )
        )

    if is_compute_node:
        plan2.extend(
            [
                TerraformInitStep(tfhelper_hypervisor_deploy),
                DeployHypervisorApplicationStep(
                    tfhelper_hypervisor_deploy, tfhelper_openstack_deploy, jhelper
                ),
                AddHypervisorUnitStep(name, jhelper),
                SetLocalHypervisorOptions(
                    name, jhelper, join_mode=True, preseed_file=preseed
                ),
            ]
        )

    run_plan(plan2, console)

    click.echo(f"Node joined cluster with roles: {pretty_roles}")


@click.command()
@click.option(
    "-f",
    "--format",
    type=click.Choice([FORMAT_TABLE, FORMAT_YAML]),
    default=FORMAT_TABLE,
    help="Output format.",
)
def list(format: str) -> None:
    """List nodes in the cluster."""
    preflight_checks = [DaemonGroupCheck()]
    run_preflight_checks(preflight_checks, console)

    plan = [ClusterListNodeStep()]
    results = run_plan(plan, console)

    list_node_step_result = results.get("ClusterListNodeStep")
    nodes = list_node_step_result.message

    if format == FORMAT_TABLE:
        table = Table()
        table.add_column("Node", justify="left")
        table.add_column("Status", justify="center")
        table.add_column("Control", justify="center")
        table.add_column("Compute", justify="center")
        table.add_column("Storage", justify="center")
        for name, node in nodes.items():
            table.add_row(
                name,
                "[green]up[/green]"
                if node.get("status") == "ONLINE"
                else "[red]down[/red]",
                "x" if "control" in node.get("roles", []) else "",
                "x" if "compute" in node.get("roles", []) else "",
                "x" if "storage" in node.get("roles", []) else "",
            )
        console.print(table)
    elif format == FORMAT_YAML:
        click.echo(yaml.dump(nodes, sort_keys=True))


@click.command()
@click.option("--name", type=str, prompt=True, help="Fully qualified node name")
def remove(name: str) -> None:
    """Remove a node from the cluster."""
    data_location = snap.paths.user_data
    jhelper = JujuHelper(data_location)

    preflight_checks = [DaemonGroupCheck()]
    run_preflight_checks(preflight_checks, console)

    plan = [
        RemoveMicrok8sUnitStep(name, jhelper),
        RemoveMicrocephUnitStep(name, jhelper),
        RemoveJujuMachineStep(name),
        # Cannot remove user as the same user name cannot be resued,
        # so commenting the RemoveJujuUserStep
        # RemoveJujuUserStep(name),
        ClusterRemoveNodeStep(name),
    ]
    run_plan(plan, console)

    click.echo(f"Removed node {name} from the cluster")
    # Removing machine does not clean up all deployed juju components. This is
    # deliberate, see https://bugs.launchpad.net/juju/+bug/1851489.
    # Without the workaround mentioned in LP#1851489, it is not possible to
    # reprovision the machine back.
    click.echo(
        f"Run command 'sudo /sbin/remove-juju-services' on node {name} "
        "to reuse the machine."
    )
