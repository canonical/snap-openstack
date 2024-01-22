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
import sys
from collections import Counter
from datetime import datetime

import click
import yaml
from rich.console import Console
from rich.table import Table
from snaphelpers import Snap

from sunbeam.clusterd.client import Client
from sunbeam.commands import resize as resize_cmds
from sunbeam.commands.deployment import deployment_path, get_active_deployment
from sunbeam.commands.maas import (
    AddMaasDeployment,
    DeploymentMachinesCheck,
    DeploymentTopologyCheck,
    MaasClient,
    MachineNetworkCheck,
    MachineRequirementsCheck,
    MachineRolesCheck,
    MachineStorageCheck,
    Networks,
    get_machine,
    get_network_mapping,
    list_machines,
    list_machines_by_zone,
    list_spaces,
    map_space,
    str_presenter,
    unmap_space,
)
from sunbeam.jobs.checks import (
    DiagnosticsCheck,
    DiagnosticsResult,
    JujuSnapCheck,
    LocalShareCheck,
    VerifyClusterdNotBootstrappedCheck,
)
from sunbeam.jobs.common import (
    CLICK_FAIL,
    CLICK_OK,
    CONTEXT_SETTINGS,
    FORMAT_TABLE,
    FORMAT_YAML,
    run_plan,
    run_preflight_checks,
)
from sunbeam.provider.base import ProviderBase
from sunbeam.utils import CatchGroup

LOG = logging.getLogger(__name__)
console = Console()


@click.group("cluster", context_settings=CONTEXT_SETTINGS, cls=CatchGroup)
@click.pass_context
def cluster(ctx):
    """Manage the Sunbeam Cluster"""


@click.group("machine", context_settings=CONTEXT_SETTINGS, cls=CatchGroup)
@click.pass_context
def machine(ctx):
    """Manage machines."""
    pass


@click.group("zone", context_settings=CONTEXT_SETTINGS, cls=CatchGroup)
@click.pass_context
def zone(ctx):
    """Manage zones."""
    pass


@click.group("space", context_settings=CONTEXT_SETTINGS, cls=CatchGroup)
@click.pass_context
def space(ctx):
    """Manage spaces."""
    pass


@click.group("network", context_settings=CONTEXT_SETTINGS, cls=CatchGroup)
@click.pass_context
def network(ctx):
    """Manage networks."""
    pass


class MaasProvider(ProviderBase):
    def register_add_cli(self, add: click.Group) -> None:
        add.add_command(add_maas)

    def register_cli(
        self,
        init: click.Group,
        deployment: click.Group,
    ):
        init.add_command(cluster)
        cluster.add_command(bootstrap)
        cluster.add_command(list_nodes)
        cluster.add_command(resize_cmds.resize)
        deployment.add_command(machine)
        machine.add_command(list_machines_cmd)
        machine.add_command(show_machine_cmd)
        machine.add_command(validate_machine_cmd)
        deployment.add_command(zone)
        zone.add_command(list_zones_cmd)
        deployment.add_command(space)
        space.add_command(list_spaces_cmd)
        space.add_command(map_space_cmd)
        space.add_command(unmap_space_cmd)
        deployment.add_command(network)
        network.add_command(list_networks_cmd)
        deployment.add_command(validate_deployment_cmd)

    def get_clusterd_client(self) -> Client:
        """Get cluster client for active deployment."""
        return NotImplemented


@click.command()
def bootstrap() -> None:
    """Bootstrap the MAAS-backed deployment.

    Initialize the sunbeam cluster.
    """
    preflight_checks = []
    preflight_checks.append(JujuSnapCheck())
    preflight_checks.append(LocalShareCheck())
    preflight_checks.append(VerifyClusterdNotBootstrappedCheck())
    run_preflight_checks(preflight_checks, console)

    # snap = Snap()

    # client = MaasClient.active(snap)


@click.command("list")
@click.option(
    "-f",
    "--format",
    type=click.Choice([FORMAT_TABLE, FORMAT_YAML]),
    default=FORMAT_TABLE,
    help="Output format.",
)
def list_nodes(format: str) -> None:
    """List nodes in the custer."""
    raise NotImplementedError


@click.command("maas")
@click.option("-n", "--name", type=str, prompt=True, help="Name of the deployment")
@click.option("-t", "--token", type=str, prompt=True, help="API token")
@click.option("-u", "--url", type=str, prompt=True, help="API URL")
@click.option("-r", "--resource-pool", type=str, prompt=True, help="Resource pool")
def add_maas(name: str, token: str, url: str, resource_pool: str) -> None:
    """Add MAAS-backed deployment to registered deployments."""
    preflight_checks = [
        LocalShareCheck(),
        VerifyClusterdNotBootstrappedCheck(),
    ]
    run_preflight_checks(preflight_checks, console)

    snap = Snap()
    path = deployment_path(snap)
    plan = []
    plan.append(AddMaasDeployment(name, token, url, resource_pool, path))
    run_plan(plan, console)
    click.echo(f"MAAS deployment {name} added.")


@click.command("list")
@click.option(
    "--format",
    type=click.Choice([FORMAT_TABLE, FORMAT_YAML]),
    default=FORMAT_TABLE,
    help="Output format",
)
def list_machines_cmd(format: str) -> None:
    """List machines in active deployment."""
    preflight_checks = [
        LocalShareCheck(),
    ]
    run_preflight_checks(preflight_checks, console)

    snap = Snap()

    client = MaasClient.active(snap)
    machines = list_machines(client)
    if format == FORMAT_TABLE:
        table = Table()
        table.add_column("Machine")
        table.add_column("Roles")
        table.add_column("Zone")
        table.add_column("Status")
        for machine in machines:
            hostname = machine["hostname"]
            status = machine["status"]
            zone = machine["zone"]
            roles = ", ".join(machine["roles"])
            table.add_row(hostname, roles, zone, status)
        console.print(table)
    elif format == FORMAT_YAML:
        console.print(yaml.dump(machines), end="")


@click.command("show")
@click.argument("hostname", type=str)
@click.option(
    "--format",
    type=click.Choice([FORMAT_TABLE, FORMAT_YAML]),
    default=FORMAT_TABLE,
    help="Output format",
)
def show_machine_cmd(hostname: str, format: str) -> None:
    """Show machine in active deployment."""
    preflight_checks = [
        LocalShareCheck(),
    ]
    run_preflight_checks(preflight_checks, console)

    snap = Snap()
    client = MaasClient.active(snap)
    machine = get_machine(client, hostname)
    header = "[bold]{}[/bold]"
    if format == FORMAT_TABLE:
        table = Table(show_header=False)
        table.add_row(header.format("Name"), machine["hostname"])
        table.add_row(header.format("Roles"), ", ".join(machine["roles"]))
        table.add_row(header.format("Network Spaces"), ", ".join(machine["spaces"]))
        table.add_row(
            header.format(
                "Storage Devices",
            ),
            ", ".join(f"{tag}({count})" for tag, count in machine["storage"].items()),
        )
        table.add_row(header.format("Zone"), machine["zone"])
        table.add_row(header.format("Status"), machine["status"])
        console.print(table)
    elif format == FORMAT_YAML:
        console.print(yaml.dump(machine), end="")


def _zones_table(zone_machines: dict[str, list[dict]]) -> Table:
    table = Table()
    table.add_column("Zone")
    table.add_column("Machines")
    for zone, machines in zone_machines.items():
        table.add_row(zone, str(len(machines)))
    return table


def _zones_roles_table(zone_machines: dict[str, list[dict]]) -> Table:
    table = Table(padding=(0, 0), show_header=False)

    zone_table = Table(
        title="\u00A0",  # non-breaking space to have zone same height as roles
        show_edge=False,
        show_header=False,
        expand=True,
    )
    zone_table.add_column("#not_shown#", justify="center")
    zone_table.add_row("[bold]Zone[/bold]", end_section=True)

    machine_table = Table(
        show_edge=False,
        show_header=True,
        title="Machines",
        title_style="bold",
        expand=True,
    )
    machine_table.add_column("control", justify="center")
    machine_table.add_column("compute", justify="center")
    machine_table.add_column("storage", justify="center")
    machine_table.add_column("total", justify="center")
    for zone, machines in zone_machines.items():
        zone_table.add_row(zone)
        role_count = Counter()
        for machine in machines:
            role_count.update(machine["roles"])
        control = role_count.get("control", 0)
        compute = role_count.get("compute", 0)
        storage = role_count.get("storage", 0)
        machine_table.add_row(
            str(control),
            str(compute),
            str(storage),
            str(len(machines)),
        )

    table.add_row(zone_table, machine_table)
    return table


@click.command("list")
@click.option(
    "--roles",
    is_flag=True,
    show_default=True,
    default=False,
    help="List roles",
)
@click.option(
    "--format",
    type=click.Choice([FORMAT_TABLE, FORMAT_YAML]),
    default=FORMAT_TABLE,
    help="Output format",
)
def list_zones_cmd(roles: bool, format: str) -> None:
    """List zones in active deployment."""
    preflight_checks = [
        LocalShareCheck(),
    ]
    run_preflight_checks(preflight_checks, console)

    snap = Snap()

    client = MaasClient.active(snap)
    zones_machines = list_machines_by_zone(client)
    if format == FORMAT_TABLE:
        if roles:
            table = _zones_roles_table(zones_machines)
        else:
            table = _zones_table(zones_machines)
        console.print(table)
    elif format == FORMAT_YAML:
        console.print(yaml.dump(zones_machines), end="")


@click.command("list")
@click.option(
    "--format",
    type=click.Choice([FORMAT_TABLE, FORMAT_YAML]),
    default=FORMAT_TABLE,
    help="Output format",
)
def list_spaces_cmd(format: str) -> None:
    """List spaces in MAAS deployment."""
    preflight_checks = [
        LocalShareCheck(),
    ]
    run_preflight_checks(preflight_checks, console)

    snap = Snap()

    client = MaasClient.active(snap)
    spaces = list_spaces(client)
    if format == FORMAT_TABLE:
        table = Table()
        table.add_column("Space")
        table.add_column("Subnets", max_width=80)
        for space in spaces:
            table.add_row(space["name"], ", ".join(space["subnets"]))
        console.print(table)
    elif format == FORMAT_YAML:
        console.print(yaml.dump(spaces), end="")


@click.command("map")
@click.argument("space")
@click.argument("network", type=click.Choice(Networks.values()))
def map_space_cmd(space: str, network: str) -> None:
    """Map space to network."""
    preflight_checks = [
        LocalShareCheck(),
    ]
    run_preflight_checks(preflight_checks, console)

    snap = Snap()

    client = MaasClient.active(snap)
    map_space(snap, client, space, network)
    console.print(f"Space {space} mapped to network {network}.")


@click.command("unmap")
@click.argument("network", type=click.Choice(Networks.values()))
def unmap_space_cmd(network: str) -> None:
    """Unmap space from network."""
    preflight_checks = [
        LocalShareCheck(),
    ]
    run_preflight_checks(preflight_checks, console)

    snap = Snap()

    unmap_space(snap, network)
    console.print(f"Space unmapped from network {network}.")


@click.command("list")
@click.option(
    "--format",
    type=click.Choice([FORMAT_TABLE, FORMAT_YAML]),
    default=FORMAT_TABLE,
    help="Output format",
)
def list_networks_cmd(format: str):
    """List networks and associated spaces."""
    preflight_checks = [
        LocalShareCheck(),
    ]
    run_preflight_checks(preflight_checks, console)

    snap = Snap()

    mapping = get_network_mapping(snap)
    if format == FORMAT_TABLE:
        table = Table()
        table.add_column("Network")
        table.add_column("MAAS Space")
        for network, space in mapping.items():
            table.add_row(network, space or "[italic]<unmapped>[italic]")
        console.print(table)
    elif format == FORMAT_YAML:
        console.print(yaml.dump(mapping), end="")


def _run_maas_checks(checks: list[DiagnosticsCheck], console: Console) -> list[dict]:
    """Run checks sequentially.

    Runs each checks, logs whether the check passed or failed.
    Prints to console every result.
    """
    check_results = []
    for check in checks:
        LOG.debug(f"Starting check {check.name!r}")
        message = f"{check.description}..."
        with console.status(message):
            results = check.run()
            if not results:
                raise ValueError(f"{check.name!r} returned no results.")

            if isinstance(results, DiagnosticsResult):
                results = [results]

            for result in results:
                LOG.debug(f"{result.name=!r}, {result.passed=!r}, {result.message=!r}")
                console.print(
                    message,
                    result.message,
                    "-",
                    CLICK_OK if result.passed else CLICK_FAIL,
                )
                check_results.append(result.to_dict())
    return check_results


def _run_maas_meta_checks(
    checks: list[DiagnosticsCheck], console: Console
) -> list[dict]:
    """Run checks sequentially.

    Runs each checks, logs whether the check passed or failed.
    Only prints to console last check result.
    """
    check_results = []

    for check in checks:
        LOG.debug(f"Starting check {check.name!r}")
        message = f"{check.description}..."
        with console.status(message):
            results = check.run()
            if not results:
                raise ValueError(f"{check.name!r} returned no results.")
            if isinstance(results, DiagnosticsResult):
                results = [results]
            for result in results:
                check_results.append(result.to_dict())
            console.print(message, CLICK_OK if results[-1].passed else CLICK_FAIL)
    return check_results


def _save_report(snap: Snap, name: str, report: list[dict]) -> str:
    """Save report to filesystem."""
    reports = snap.paths.user_common / "reports"
    if not reports.exists():
        reports.mkdir(parents=True)
    report_path = reports / f"{name}-{datetime.now():%Y%m%d-%H%M%S.%f}.yaml"
    with report_path.open("w") as fd:
        yaml.add_representer(str, str_presenter)
        yaml.dump(report, fd)
    return str(report_path.absolute())


@click.command("validate")
@click.argument("machine", type=str)
def validate_machine_cmd(machine: str):
    """Validate machine configuration."""
    preflight_checks = [
        LocalShareCheck(),
    ]
    run_preflight_checks(preflight_checks, console)

    snap = Snap()
    client = MaasClient.active(snap)
    with console.status(f"Fetching {machine} ..."):
        try:
            machine_obj = get_machine(client, machine)
            LOG.debug(f"{machine_obj=!r}")
        except ValueError as e:
            console.print("Error:", e)
            sys.exit(1)
    validation_checks = [
        MachineRolesCheck(machine_obj),
        MachineNetworkCheck(snap, machine_obj),
        MachineStorageCheck(machine_obj),
        MachineRequirementsCheck(machine_obj),
    ]
    report = _run_maas_checks(validation_checks, console)
    report_path = _save_report(snap, "validate-machine-" + machine, report)
    console.print(f"Report saved to {report_path!r}")


@click.command("validate")
def validate_deployment_cmd():
    """Validate deployment."""
    preflight_checks = [
        LocalShareCheck(),
    ]
    run_preflight_checks(preflight_checks, console)
    snap = Snap()
    path = deployment_path(snap)
    deployment = get_active_deployment(path)
    client = MaasClient.active(snap)
    with console.status(f"Fetching {deployment['name']} machines ..."):
        try:
            machines = list_machines(client)
        except ValueError as e:
            console.print("Error:", e)
            sys.exit(1)
    validation_checks = [
        DeploymentMachinesCheck(snap, machines),
        DeploymentTopologyCheck(snap, machines),
    ]
    report = _run_maas_meta_checks(validation_checks, console)
    report_path = _save_report(
        snap, "validate-deployment-" + deployment["name"], report
    )
    console.print(f"Report saved to {report_path!r}")
