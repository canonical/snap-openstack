# Copyright (c) 2024 Canonical Ltd.
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
from pathlib import Path
from typing import Tuple, Type

import click
import yaml
from rich.console import Console
from rich.table import Table
from snaphelpers import Snap

from sunbeam import utils
from sunbeam.commands import refresh as refresh_cmds
from sunbeam.commands import resize as resize_cmds
from sunbeam.commands.bootstrap_state import SetBootstrapped
from sunbeam.commands.clusterd import (
    AskManagementCidrStep,
    ClusterAddJujuUserStep,
    ClusterAddNodeStep,
    ClusterInitStep,
    ClusterJoinNodeStep,
    ClusterListNodeStep,
    ClusterRemoveNodeStep,
    ClusterUpdateJujuControllerStep,
    ClusterUpdateNodeStep,
    SaveManagementCidrStep,
)
from sunbeam.commands.configure import (
    DemoSetup,
    SetHypervisorCharmConfigStep,
    TerraformDemoInitStep,
    UserOpenRCStep,
    UserQuestions,
    retrieve_admin_credentials,
)
from sunbeam.commands.hypervisor import (
    AddHypervisorUnitsStep,
    DeployHypervisorApplicationStep,
    RemoveHypervisorUnitStep,
)
from sunbeam.commands.juju import (
    AddCloudJujuStep,
    AddJujuMachineStep,
    AddJujuSpaceStep,
    BackupBootstrapUserStep,
    BindJujuApplicationStep,
    BootstrapJujuStep,
    CreateJujuUserStep,
    JujuGrantModelAccessStep,
    JujuLoginStep,
    RegisterJujuUserStep,
    RemoveJujuMachineStep,
    SaveJujuUserLocallyStep,
    UpdateJujuModelConfigStep,
)
from sunbeam.commands.k8s import (
    AddK8SCloudStep,
    AddK8SUnitsStep,
    DeployK8SApplicationStep,
    EnableK8SFeatures,
    RemoveK8SUnitStep,
    StoreK8SKubeConfigStep,
)
from sunbeam.commands.microceph import (
    AddMicrocephUnitsStep,
    ConfigureMicrocephOSDStep,
    DeployMicrocephApplicationStep,
    RemoveMicrocephUnitStep,
)
from sunbeam.commands.microk8s import (
    AddMicrok8sCloudStep,
    AddMicrok8sUnitsStep,
    DeployMicrok8sApplicationStep,
    RemoveMicrok8sUnitStep,
    StoreMicrok8sConfigStep,
)
from sunbeam.commands.openstack import (
    OPENSTACK_MODEL,
    DeployControlPlaneStep,
    PatchLoadBalancerServicesStep,
    PromptRegionStep,
)
from sunbeam.commands.proxy import PromptForProxyStep
from sunbeam.commands.sunbeam_machine import (
    AddSunbeamMachineUnitsStep,
    DeploySunbeamMachineApplicationStep,
    RemoveSunbeamMachineStep,
)
from sunbeam.commands.terraform import TerraformInitStep
from sunbeam.jobs.checks import (
    DaemonGroupCheck,
    JujuSnapCheck,
    LocalShareCheck,
    SshKeysConnectedCheck,
    SystemRequirementsCheck,
    TokenCheck,
    VerifyBootstrappedCheck,
    VerifyFQDNCheck,
    VerifyHypervisorHostnameCheck,
)
from sunbeam.jobs.common import (
    CONTEXT_SETTINGS,
    FORMAT_DEFAULT,
    FORMAT_TABLE,
    FORMAT_VALUE,
    FORMAT_YAML,
    ResultType,
    Role,
    click_option_topology,
    get_step_message,
    roles_to_str_list,
    run_plan,
    run_preflight_checks,
    validate_roles,
)
from sunbeam.jobs.deployment import Deployment, Networks
from sunbeam.jobs.juju import JujuHelper, ModelNotFoundException, run_sync
from sunbeam.jobs.manifest import AddManifestStep
from sunbeam.provider.base import ProviderBase
from sunbeam.provider.local.deployment import LOCAL_TYPE, LocalDeployment
from sunbeam.provider.local.steps import LocalSetHypervisorUnitsOptionsStep
from sunbeam.utils import CatchGroup

LOG = logging.getLogger(__name__)
console = Console()


@click.group("cluster", context_settings=CONTEXT_SETTINGS, cls=CatchGroup)
@click.pass_context
def cluster(ctx):
    """Manage the Sunbeam Cluster."""


def remove_trailing_dot(value: str) -> str:
    """Remove trailing dot from the value."""
    return value.rstrip(".")


class LocalProvider(ProviderBase):
    def register_add_cli(self, add: click.Group) -> None:
        """A local provider cannot add deployments."""
        pass

    def register_cli(
        self,
        init: click.Group,
        configure: click.Group,
        deployment: click.Group,
    ):
        """Register local provider commands to CLI.

        Local provider does not add commands to the deployment group.
        """
        init.add_command(cluster)
        configure.add_command(configure_cmd)
        cluster.add_command(bootstrap)
        cluster.add_command(add)
        cluster.add_command(join)
        cluster.add_command(list)
        cluster.add_command(remove)
        cluster.add_command(resize_cmds.resize)
        cluster.add_command(refresh_cmds.refresh)

    def deployment_type(self) -> Tuple[str, Type[Deployment]]:
        """Retrieve the deployment type and class."""
        return LOCAL_TYPE, LocalDeployment


@click.command()
@click.option("-a", "--accept-defaults", help="Accept all defaults.", is_flag=True)
@click.option(
    "-m",
    "--manifest",
    "manifest_path",
    help="Manifest file.",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
)
@click.option(
    "--role",
    "roles",
    multiple=True,
    default=["control", "compute"],
    type=click.Choice(["control", "compute", "storage"], case_sensitive=False),
    callback=validate_roles,
    help="Specify additional roles, compute or storage, for the "
    "bootstrap node. Defaults to the compute role.",
)
@click_option_topology
@click.option(
    "--database",
    default="auto",
    type=click.Choice(
        [
            "auto",
            "single",
            "multi",
        ],
        case_sensitive=False,
    ),
    help=(
        "Allows definition of the intended cluster configuration: "
        "'auto' for automatic determination, "
        "'single' for a single database, "
        "'multi' for a database per service, "
    ),
)
@click.pass_context
def bootstrap(
    ctx: click.Context,
    roles: list[Role],
    topology: str,
    database: str,
    manifest_path: Path | None = None,
    accept_defaults: bool = False,
) -> None:
    """Bootstrap the local node.

    Initialize the sunbeam cluster.
    """
    deployment: LocalDeployment = ctx.obj
    client = deployment.get_client()
    snap = Snap()

    manifest = deployment.get_manifest(manifest_path)

    LOG.debug(f"Manifest used for deployment - preseed: {manifest.deployment}")
    LOG.debug(f"Manifest used for deployment - software: {manifest.software}")
    preseed = manifest.deployment

    # Bootstrap node must always have the control role
    if Role.CONTROL not in roles:
        LOG.debug("Enabling control role for bootstrap")
        roles.append(Role.CONTROL)
    is_control_node = any(role.is_control_node() for role in roles)
    is_compute_node = any(role.is_compute_node() for role in roles)
    is_storage_node = any(role.is_storage_node() for role in roles)

    fqdn = utils.get_fqdn()

    roles_str = ",".join(role.name for role in roles)
    pretty_roles = ", ".join(role.name.lower() for role in roles)
    LOG.debug(f"Bootstrap node: roles {roles_str}")

    cloud_type = snap.config.get("juju.cloud.type")
    cloud_name = snap.config.get("juju.cloud.name")
    k8s_provider = snap.config.get("k8s.provider")
    juju_bootstrap_args = manifest.software.juju.bootstrap_args
    data_location = snap.paths.user_data

    preflight_checks = []
    preflight_checks.append(SystemRequirementsCheck())
    preflight_checks.append(JujuSnapCheck())
    preflight_checks.append(SshKeysConnectedCheck())
    preflight_checks.append(DaemonGroupCheck())
    preflight_checks.append(LocalShareCheck())
    if is_compute_node:
        hypervisor_hostname = utils.get_hypervisor_hostname()
        preflight_checks.append(
            VerifyHypervisorHostnameCheck(fqdn, hypervisor_hostname)
        )

    run_preflight_checks(preflight_checks, console)

    cidr_plan = []
    cidr_plan.append(AskManagementCidrStep(client, preseed, accept_defaults))
    results = run_plan(cidr_plan, console)
    management_cidr = get_step_message(results, AskManagementCidrStep)

    cloud_definition = JujuHelper.manual_cloud(
        cloud_name, utils.get_local_ip_by_cidr(management_cidr)
    )

    plan = []
    plan.append(JujuLoginStep(deployment.juju_account))
    # bootstrapped node is always machine 0 in controller model
    plan.append(ClusterInitStep(client, roles_to_str_list(roles), 0, management_cidr))
    plan.append(SaveManagementCidrStep(client, management_cidr))
    plan.append(AddManifestStep(client, manifest_path))
    plan.append(
        PromptForProxyStep(
            deployment, accept_defaults=accept_defaults, deployment_preseed=preseed
        )
    )
    run_plan(plan, console)

    proxy_settings = deployment.get_proxy_settings()
    LOG.debug(f"Proxy settings: {proxy_settings}")

    plan1 = []
    plan1.append(AddCloudJujuStep(cloud_name, cloud_definition))
    plan1.append(
        BootstrapJujuStep(
            client,
            cloud_name,
            cloud_type,
            deployment.controller,
            bootstrap_args=juju_bootstrap_args,
            proxy_settings=proxy_settings,
        )
    )
    run_plan(plan1, console)

    plan2 = []
    plan2.append(CreateJujuUserStep(fqdn))
    plan2.append(ClusterUpdateJujuControllerStep(client, deployment.controller))
    plan2_results = run_plan(plan2, console)

    token = get_step_message(plan2_results, CreateJujuUserStep)

    plan3 = []
    plan3.append(ClusterAddJujuUserStep(client, fqdn, token))
    plan3.append(BackupBootstrapUserStep(fqdn, data_location))
    plan3.append(SaveJujuUserLocallyStep(fqdn, data_location))
    plan3.append(
        RegisterJujuUserStep(
            client, fqdn, deployment.controller, data_location, replace=True
        )
    )
    run_plan(plan3, console)

    deployment.reload_credentials()
    jhelper = JujuHelper(deployment.get_connected_controller())
    plan4 = []
    plan4.append(
        AddJujuSpaceStep(
            jhelper,
            deployment.infrastructure_model,
            deployment.get_space(Networks.MANAGEMENT),
            [management_cidr],
        )
    )
    plan4.append(
        UpdateJujuModelConfigStep(
            jhelper,
            deployment.infrastructure_model,
            {
                "default-space": deployment.get_space(Networks.MANAGEMENT),
            },
        )
    )
    plan4.append(
        # TODO(gboutry): fix when LP#2067617 is released
        # This should be replaced by a juju controller set config
        # when the previous bug is fixed
        # Binding controller's endpoints to the management space
        BindJujuApplicationStep(
            jhelper,
            deployment.infrastructure_model,
            "controller",
            deployment.get_space(Networks.MANAGEMENT),
        )
    )
    plan4.append(PromptRegionStep(client, preseed, accept_defaults))
    # Deploy sunbeam machine charm
    sunbeam_machine_tfhelper = deployment.get_tfhelper("sunbeam-machine-plan")
    plan4.append(TerraformInitStep(sunbeam_machine_tfhelper))
    plan4.append(
        DeploySunbeamMachineApplicationStep(
            deployment,
            client,
            sunbeam_machine_tfhelper,
            jhelper,
            manifest,
            deployment.infrastructure_model,
            refresh=True,
            proxy_settings=proxy_settings,
        )
    )
    plan4.append(
        AddSunbeamMachineUnitsStep(
            client, fqdn, jhelper, deployment.infrastructure_model
        )
    )

    if k8s_provider == "k8s":
        k8s_tfhelper = deployment.get_tfhelper("k8s-plan")
        plan4.append(TerraformInitStep(k8s_tfhelper))
        plan4.append(
            DeployK8SApplicationStep(
                deployment,
                client,
                k8s_tfhelper,
                jhelper,
                manifest,
                deployment.infrastructure_model,
                accept_defaults=accept_defaults,
                deployment_preseed=preseed,
            )
        )
        plan4.append(
            AddK8SUnitsStep(client, fqdn, jhelper, deployment.infrastructure_model)
        )
        plan4.append(
            EnableK8SFeatures(client, jhelper, deployment.infrastructure_model)
        )
        plan4.append(
            StoreK8SKubeConfigStep(client, jhelper, deployment.infrastructure_model)
        )
        plan4.append(AddK8SCloudStep(client, jhelper))
    else:
        k8s_tfhelper = deployment.get_tfhelper("microk8s-plan")
        plan4.append(TerraformInitStep(k8s_tfhelper))
        plan4.append(
            DeployMicrok8sApplicationStep(
                deployment,
                client,
                k8s_tfhelper,
                jhelper,
                manifest,
                deployment.infrastructure_model,
                accept_defaults=accept_defaults,
                deployment_preseed=preseed,
            )
        )
        plan4.append(
            AddMicrok8sUnitsStep(client, fqdn, jhelper, deployment.infrastructure_model)
        )
        plan4.append(
            StoreMicrok8sConfigStep(client, jhelper, deployment.infrastructure_model)
        )
        plan4.append(AddMicrok8sCloudStep(client, jhelper))

    # Deploy Microceph application during bootstrap irrespective of node role.
    microceph_tfhelper = deployment.get_tfhelper("microceph-plan")
    plan4.append(TerraformInitStep(microceph_tfhelper))
    plan4.append(
        DeployMicrocephApplicationStep(
            deployment,
            client,
            microceph_tfhelper,
            jhelper,
            manifest,
            deployment.infrastructure_model,
        )
    )

    if is_storage_node:
        plan4.append(
            AddMicrocephUnitsStep(
                client, fqdn, jhelper, deployment.infrastructure_model
            )
        )
        plan4.append(
            ConfigureMicrocephOSDStep(
                client,
                fqdn,
                jhelper,
                deployment.infrastructure_model,
                accept_defaults=accept_defaults,
                deployment_preseed=preseed,
            )
        )

    openstack_tfhelper = deployment.get_tfhelper("openstack-plan")
    if is_control_node:
        plan4.append(TerraformInitStep(openstack_tfhelper))
        plan4.append(
            DeployControlPlaneStep(
                client,
                openstack_tfhelper,
                jhelper,
                manifest,
                topology,
                database,
                deployment.infrastructure_model,
                proxy_settings=proxy_settings,
            )
        )
        # Redeploy of Microceph is required to fill terraform vars
        # related to traefik-rgw/keystone-endpoints offers from
        # openstack model
        plan4.append(
            DeployMicrocephApplicationStep(
                deployment,
                client,
                microceph_tfhelper,
                jhelper,
                manifest,
                deployment.infrastructure_model,
                refresh=True,
            )
        )

    run_plan(plan4, console)

    plan5 = []

    if is_control_node:
        plan5.append(PatchLoadBalancerServicesStep(client))

    # NOTE(jamespage):
    # As with MicroCeph, always deploy the openstack-hypervisor charm
    # and add a unit to the bootstrap node if required.
    hypervisor_tfhelper = deployment.get_tfhelper("hypervisor-plan")
    plan5.append(TerraformInitStep(hypervisor_tfhelper))
    plan5.append(
        DeployHypervisorApplicationStep(
            deployment,
            client,
            hypervisor_tfhelper,
            openstack_tfhelper,
            jhelper,
            manifest,
            deployment.infrastructure_model,
        )
    )
    if is_compute_node:
        plan5.append(
            AddHypervisorUnitsStep(
                client, fqdn, jhelper, deployment.infrastructure_model
            )
        )

    plan5.append(SetBootstrapped(client))
    run_plan(plan5, console)

    click.echo(f"Node has been bootstrapped with roles: {pretty_roles}")


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
@click.pass_context
def add(ctx: click.Context, name: str, format: str) -> None:
    """Generate a token for a new node to join the cluster."""
    preflight_checks = [DaemonGroupCheck(), VerifyFQDNCheck(name)]
    run_preflight_checks(preflight_checks, console)
    name = remove_trailing_dot(name)

    deployment: LocalDeployment = ctx.obj
    client = deployment.get_client()
    jhelper = JujuHelper(deployment.get_connected_controller())

    plan1 = [
        JujuLoginStep(deployment.juju_account),
        ClusterAddNodeStep(client, name),
        CreateJujuUserStep(name),
        JujuGrantModelAccessStep(jhelper, name, OPENSTACK_MODEL),
    ]

    plan1_results = run_plan(plan1, console)

    user_token = get_step_message(plan1_results, CreateJujuUserStep)

    plan2 = [ClusterAddJujuUserStep(client, name, user_token)]
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
@click.pass_context
def join(
    ctx: click.Context,
    token: str,
    roles: list[Role],
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

    roles_str = roles_to_str_list(roles)
    pretty_roles = ", ".join(role_.name.lower() for role_ in roles)
    LOG.debug(f"Node joining the cluster with roles: {pretty_roles}")

    k8s_provider = Snap().config.get("k8s.provider")

    preflight_checks = []
    preflight_checks.append(SystemRequirementsCheck())
    preflight_checks.append(JujuSnapCheck())
    preflight_checks.append(SshKeysConnectedCheck())
    preflight_checks.append(DaemonGroupCheck())
    preflight_checks.append(LocalShareCheck())
    preflight_checks.append(TokenCheck(token))
    if is_compute_node:
        hypervisor_hostname = utils.get_hypervisor_hostname()
        preflight_checks.append(
            VerifyHypervisorHostnameCheck(name, hypervisor_hostname)
        )

    run_preflight_checks(preflight_checks, console)

    try:
        management_cidr = utils.get_local_cidr_matching_token(token)
        ip = utils.get_local_ip_by_cidr(management_cidr)
    except ValueError:
        LOG.debug(
            "Failed to find local address matching join token addresses"
            ", picking local address from default route",
            exc_info=True,
        )
        ip = utils.get_local_cidr_by_default_route()

    deployment: LocalDeployment = ctx.obj
    data_location = Snap().paths.user_data
    client = deployment.get_client()

    plan1 = [
        JujuLoginStep(deployment.juju_account),
        ClusterJoinNodeStep(client, token, ip, name, roles_str),
        SaveJujuUserLocallyStep(name, data_location),
        RegisterJujuUserStep(client, name, deployment.controller, data_location),
        AddJujuMachineStep(ip),
    ]
    plan1_results = run_plan(plan1, console)

    deployment.reload_credentials()

    # Get manifest object once the cluster is joined
    manifest = deployment.get_manifest()
    preseed = manifest.deployment

    machine_id = -1
    machine_id_result = get_step_message(plan1_results, AddJujuMachineStep)
    if machine_id_result is not None:
        machine_id = int(machine_id_result)

    jhelper = JujuHelper(deployment.get_connected_controller())
    plan2 = []
    plan2.append(ClusterUpdateNodeStep(client, name, machine_id=machine_id))
    plan2.append(
        AddSunbeamMachineUnitsStep(
            client, name, jhelper, deployment.infrastructure_model
        ),
    )

    if is_control_node:
        if k8s_provider == "k8s":
            plan2.append(
                AddK8SUnitsStep(client, name, jhelper, deployment.infrastructure_model)
            )
        else:
            plan2.append(
                AddMicrok8sUnitsStep(
                    client, name, jhelper, deployment.infrastructure_model
                )
            )

    if is_storage_node:
        plan2.append(
            AddMicrocephUnitsStep(
                client, name, jhelper, deployment.infrastructure_model
            )
        )
        plan2.append(
            ConfigureMicrocephOSDStep(
                client,
                name,
                jhelper,
                deployment.infrastructure_model,
                accept_defaults=accept_defaults,
                deployment_preseed=preseed,
            )
        )

    if is_compute_node:
        plan2.extend(
            [
                AddHypervisorUnitsStep(
                    client, name, jhelper, deployment.infrastructure_model
                ),
                LocalSetHypervisorUnitsOptionsStep(
                    client,
                    name,
                    jhelper,
                    deployment.infrastructure_model,
                    join_mode=True,
                    deployment_preseed=preseed,
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
@click.pass_context
def list(ctx: click.Context, format: str) -> None:
    """List nodes in the cluster."""
    preflight_checks = [DaemonGroupCheck()]
    run_preflight_checks(preflight_checks, console)
    deployment: LocalDeployment = ctx.obj
    client = deployment.get_client()
    plan = [ClusterListNodeStep(client)]
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
                (
                    "[green]up[/green]"
                    if node.get("status") == "ONLINE"
                    else "[red]down[/red]"
                ),
                "x" if "control" in node.get("roles", []) else "",
                "x" if "compute" in node.get("roles", []) else "",
                "x" if "storage" in node.get("roles", []) else "",
            )
        console.print(table)
    elif format == FORMAT_YAML:
        click.echo(yaml.dump(nodes, sort_keys=True))


@click.command()
@click.option(
    "--force",
    type=bool,
    help=("Skip safety checks and ignore cleanup errors for some tasks"),
    is_flag=True,
)
@click.option("--name", type=str, prompt=True, help="Fully qualified node name")
@click.pass_context
def remove(ctx: click.Context, name: str, force: bool) -> None:
    """Remove a node from the cluster."""
    deployment: LocalDeployment = ctx.obj
    client = deployment.get_client()
    jhelper = JujuHelper(deployment.get_connected_controller())

    k8s_provider = Snap().config.get("k8s.provider")

    preflight_checks = [DaemonGroupCheck()]
    run_preflight_checks(preflight_checks, console)

    plan = [
        JujuLoginStep(deployment.juju_account),
        RemoveSunbeamMachineStep(
            client, name, jhelper, deployment.infrastructure_model
        ),
    ]

    if k8s_provider == "k8s":
        plan.append(
            RemoveK8SUnitStep(client, name, jhelper, deployment.infrastructure_model)
        )
    else:
        plan.append(
            RemoveMicrok8sUnitStep(
                client, name, jhelper, deployment.infrastructure_model
            )
        )

    plan.extend(
        [
            RemoveMicrocephUnitStep(
                client, name, jhelper, deployment.infrastructure_model
            ),
            RemoveHypervisorUnitStep(
                client, name, jhelper, deployment.infrastructure_model, force
            ),
            RemoveJujuMachineStep(client, name),
            # Cannot remove user as the same user name cannot be resued,
            # so commenting the RemoveJujuUserStep
            # RemoveJujuUserStep(name),
            ClusterRemoveNodeStep(client, name),
        ]
    )
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


@click.command("deployment")
@click.option("-a", "--accept-defaults", help="Accept all defaults.", is_flag=True)
@click.option(
    "-m",
    "--manifest",
    "manifest_path",
    help="Manifest file.",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
)
@click.option(
    "-o",
    "--openrc",
    help="Output file for cloud access details.",
    type=click.Path(dir_okay=False, path_type=Path),
)
@click.pass_context
def configure_cmd(
    ctx: click.Context,
    openrc: Path | None = None,
    manifest_path: Path | None = None,
    accept_defaults: bool = False,
) -> None:
    deployment: LocalDeployment = ctx.obj
    client = deployment.get_client()
    preflight_checks = []
    preflight_checks.append(DaemonGroupCheck())
    preflight_checks.append(VerifyBootstrappedCheck(client))
    run_preflight_checks(preflight_checks, console)

    # Validate manifest file
    manifest = deployment.get_manifest(manifest_path)

    LOG.debug(f"Manifest used for deployment - preseed: {manifest.deployment}")
    LOG.debug(f"Manifest used for deployment - software: {manifest.software}")
    preseed = manifest.deployment

    name = utils.get_fqdn(deployment.get_management_cidr())
    jhelper = JujuHelper(deployment.get_connected_controller())
    try:
        run_sync(jhelper.get_model(OPENSTACK_MODEL))
    except ModelNotFoundException:
        LOG.error(f"Expected model {OPENSTACK_MODEL} missing")
        raise click.ClickException("Please run `sunbeam cluster bootstrap` first")
    admin_credentials = retrieve_admin_credentials(jhelper, OPENSTACK_MODEL)
    # Add OS_INSECURE as https not working with terraform openstack provider.
    admin_credentials["OS_INSECURE"] = "true"

    tfplan = "demo-setup"
    tfhelper = deployment.get_tfhelper(tfplan)
    tfhelper.env = (tfhelper.env or {}) | admin_credentials
    answer_file = tfhelper.path / "config.auto.tfvars.json"
    plan = [
        AddManifestStep(client, manifest_path),
        JujuLoginStep(deployment.juju_account),
        UserQuestions(
            client,
            answer_file=answer_file,
            deployment_preseed=preseed,
            accept_defaults=accept_defaults,
        ),
        TerraformDemoInitStep(client, tfhelper),
        DemoSetup(
            client=client,
            tfhelper=tfhelper,
            answer_file=answer_file,
        ),
        UserOpenRCStep(
            client=client,
            tfhelper=tfhelper,
            auth_url=admin_credentials["OS_AUTH_URL"],
            auth_version=admin_credentials["OS_AUTH_VERSION"],
            cacert=admin_credentials.get("OS_CACERT"),
            openrc=openrc,
        ),
        SetHypervisorCharmConfigStep(
            client,
            jhelper,
            ext_network=answer_file,
            model=deployment.infrastructure_model,
        ),
    ]
    node = client.cluster.get_node_info(name)

    if "compute" in node["role"]:
        plan.append(
            LocalSetHypervisorUnitsOptionsStep(
                client,
                name,
                jhelper,
                deployment.infrastructure_model,
                # Accept preseed file but do not allow 'accept_defaults' as nic
                # selection may vary from machine to machine and is potentially
                # destructive if it takes over an unintended nic.
                deployment_preseed=preseed,
            )
        )
    run_plan(plan, console)
