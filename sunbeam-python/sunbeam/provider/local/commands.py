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
from snaphelpers import Snap

from sunbeam import utils
from sunbeam.clusterd.service import (
    ClusterServiceUnavailableException,
    ConfigItemNotFoundException,
)
from sunbeam.commands import refresh as refresh_cmds
from sunbeam.commands import resize as resize_cmds
from sunbeam.commands.configure import (
    DemoSetup,
    SetHypervisorCharmConfigStep,
    TerraformDemoInitStep,
    UserOpenRCStep,
    UserQuestions,
    retrieve_admin_credentials,
)
from sunbeam.commands.dashboard_url import retrieve_dashboard_url
from sunbeam.commands.proxy import PromptForProxyStep
from sunbeam.core.checks import (
    Check,
    DaemonGroupCheck,
    JujuControllerRegistrationCheck,
    JujuSnapCheck,
    LocalShareCheck,
    SshKeysConnectedCheck,
    SystemRequirementsCheck,
    TokenCheck,
    VerifyBootstrappedCheck,
    VerifyFQDNCheck,
    VerifyHypervisorHostnameCheck,
    run_preflight_checks,
)
from sunbeam.core.common import (
    CONTEXT_SETTINGS,
    FORMAT_DEFAULT,
    FORMAT_TABLE,
    FORMAT_VALUE,
    FORMAT_YAML,
    BaseStep,
    ResultType,
    Role,
    click_option_topology,
    get_step_message,
    get_step_result,
    read_config,
    roles_to_str_list,
    run_plan,
    update_config,
    validate_roles,
)
from sunbeam.core.deployment import Deployment, Networks
from sunbeam.core.deployments import DeploymentsConfig, deployment_path
from sunbeam.core.juju import (
    JujuHelper,
    JujuStepHelper,
    ModelNotFoundException,
    run_sync,
)
from sunbeam.core.manifest import AddManifestStep
from sunbeam.core.openstack import OPENSTACK_MODEL
from sunbeam.core.terraform import TerraformInitStep
from sunbeam.provider.base import ProviderBase
from sunbeam.provider.local.deployment import LOCAL_TYPE, LocalDeployment
from sunbeam.provider.local.steps import (
    LocalClusterStatusStep,
    LocalSetHypervisorUnitsOptionsStep,
)
from sunbeam.steps import cluster_status
from sunbeam.steps.bootstrap_state import SetBootstrapped
from sunbeam.steps.clusterd import (
    AskManagementCidrStep,
    ClusterAddJujuUserStep,
    ClusterAddNodeStep,
    ClusterInitStep,
    ClusterJoinNodeStep,
    ClusterRemoveNodeStep,
    ClusterUpdateJujuControllerStep,
    ClusterUpdateNodeStep,
    SaveManagementCidrStep,
)
from sunbeam.steps.hypervisor import (
    AddHypervisorUnitsStep,
    DeployHypervisorApplicationStep,
    RemoveHypervisorUnitStep,
)
from sunbeam.steps.juju import (
    AddCloudJujuStep,
    AddJujuMachineStep,
    AddJujuModelStep,
    AddJujuSpaceStep,
    BackupBootstrapUserStep,
    BindJujuApplicationStep,
    BootstrapJujuStep,
    CreateJujuUserStep,
    JujuGrantModelAccessStep,
    JujuLoginStep,
    RegisterJujuUserStep,
    RemoveJujuMachineStep,
    SaveControllerStep,
    SaveJujuRemoteUserLocallyStep,
    SaveJujuUserLocallyStep,
    UpdateJujuModelConfigStep,
)
from sunbeam.steps.k8s import (
    AddK8SCloudStep,
    AddK8SCredentialStep,
    AddK8SUnitsStep,
    DeployK8SApplicationStep,
    EnableK8SFeatures,
    RemoveK8SUnitsStep,
    StoreK8SKubeConfigStep,
)
from sunbeam.steps.microceph import (
    AddMicrocephUnitsStep,
    ConfigureMicrocephOSDStep,
    DeployMicrocephApplicationStep,
    RemoveMicrocephUnitsStep,
)
from sunbeam.steps.microk8s import (
    AddMicrok8sCloudStep,
    AddMicrok8sCredentialStep,
    AddMicrok8sUnitsStep,
    DeployMicrok8sApplicationStep,
    RemoveMicrok8sUnitsStep,
    StoreMicrok8sConfigStep,
)
from sunbeam.steps.openstack import (
    DeployControlPlaneStep,
    OpenStackPatchLoadBalancerServicesStep,
    PromptRegionStep,
)
from sunbeam.steps.sunbeam_machine import (
    AddSunbeamMachineUnitsStep,
    DeploySunbeamMachineApplicationStep,
    RemoveSunbeamMachineUnitsStep,
)
from sunbeam.utils import CatchGroup, argument_with_deprecated_option

LOG = logging.getLogger(__name__)
console = Console()
DEPLOYMENTS_CONFIG_KEY = "deployments"


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
        cluster.add_command(list_nodes)
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
    callback=validate_roles,
    help="Specify additional roles, compute or storage, for the "
    "bootstrap node. Defaults to the compute role."
    " Can be repeated and comma separated.",
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
@click.option(
    "-c",
    "--controller",
    "juju_controller",
    type=str,
    help="Juju controller name",
)
@click.pass_context
def bootstrap(
    ctx: click.Context,
    roles: list[Role],
    topology: str,
    database: str,
    juju_controller: str | None = None,
    manifest_path: Path | None = None,
    accept_defaults: bool = False,
) -> None:
    """Bootstrap the local node.

    Initialize the sunbeam cluster.
    """
    deployment: LocalDeployment = ctx.obj
    client = deployment.get_client()
    snap = Snap()

    path = deployment_path(snap)
    deployments = DeploymentsConfig.load(path)
    manifest = deployment.get_manifest(manifest_path)

    LOG.debug(f"Manifest used for deployment - core: {manifest.core}")
    LOG.debug(f"Manifest used for deployment - features: {manifest.features}")

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

    k8s_provider = snap.config.get("k8s.provider")
    juju_bootstrap_args = manifest.core.software.juju.bootstrap_args
    data_location = snap.paths.user_data

    preflight_checks: list[Check] = []
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
    if juju_controller:
        preflight_checks.append(
            JujuControllerRegistrationCheck(juju_controller, data_location)
        )

    run_preflight_checks(preflight_checks, console)

    # Mark deployment as active if not yet already
    try:
        deployments.add_deployment(deployment)
    except ValueError:
        # Deployment already added, ignore
        # This case arises when bootstrap command is run multiple times
        pass

    cidr_plan = []
    cidr_plan.append(AskManagementCidrStep(client, manifest, accept_defaults))
    results = run_plan(cidr_plan, console)
    management_cidr = get_step_message(results, AskManagementCidrStep)

    try:
        local_management_ip = utils.get_local_ip_by_cidr(management_cidr)
    except ValueError:
        LOG.debug(
            "Failed to find local address matching join token addresses"
            ", picking local address from default route",
            exc_info=True,
        )
        local_management_ip = utils.get_local_cidr_by_default_route()

    if juju_controller:
        controller_details = JujuStepHelper().get_controller(juju_controller)
        endpoints = controller_details.get("details", {}).get("api-endpoints", [])
        controller_ip_port = utils.first_connected_server(endpoints)
        if not controller_ip_port:
            raise click.ClickException("Juju Controller not reachable")

        controller_ip = controller_ip_port.rsplit(":", 1)[0]
    else:
        controller_ip = local_management_ip

    LOG.debug(f"Juju Controller IP: {controller_ip}")
    cloud_definition = JujuHelper.manual_cloud(deployment.name, controller_ip)

    plan: list[BaseStep] = []
    plan.append(
        SaveControllerStep(
            juju_controller,
            deployment.name,
            deployments,
            data_location,
            bool(juju_controller),
        )
    )
    plan.append(JujuLoginStep(deployment.juju_account))
    # bootstrapped node is always machine 0 in controller model
    plan.append(ClusterInitStep(client, roles_to_str_list(roles), 0, management_cidr))
    plan.append(SaveManagementCidrStep(client, management_cidr))
    plan.append(AddManifestStep(client, manifest_path))
    plan.append(
        PromptForProxyStep(
            deployment, accept_defaults=accept_defaults, manifest=manifest
        )
    )
    run_plan(plan, console)

    update_config(client, DEPLOYMENTS_CONFIG_KEY, deployments.get_minimal_info())
    proxy_settings = deployment.get_proxy_settings()
    LOG.debug(f"Proxy settings: {proxy_settings}")

    plan1: list[BaseStep]
    plan2: list[BaseStep]
    plan3: list[BaseStep]
    plan4: list[BaseStep]
    if juju_controller:
        plan1 = []
        plan1.append(
            AddCloudJujuStep(deployment.name, cloud_definition, juju_controller)
        )
        run_plan(plan1, console)

        # Not creating Juju user in external controller case because of below juju bug
        # https://bugs.launchpad.net/juju/+bug/2073741
        plan2 = []
        plan2.append(
            ClusterUpdateJujuControllerStep(client, deployment.controller, True)
        )
        plan2.append(SaveJujuRemoteUserLocallyStep(juju_controller, data_location))
        run_plan(plan2, console)

        deployment.reload_credentials()
        jhelper = JujuHelper(deployment.get_connected_controller())

        plan3 = []
        plan3.append(
            AddJujuModelStep(
                jhelper,
                deployment.openstack_machines_model,
                deployment.name,
                None,
                proxy_settings,
            )
        )
        plan3.append(
            AddJujuMachineStep(
                local_management_ip, deployment.openstack_machines_model, jhelper
            )
        )
        run_plan(plan3, console)

        plan4 = []
        plan4.append(
            AddJujuSpaceStep(
                jhelper,
                deployment.openstack_machines_model,
                deployment.get_space(Networks.MANAGEMENT),
                [management_cidr],
            )
        )
        plan4.append(
            UpdateJujuModelConfigStep(
                jhelper,
                deployment.openstack_machines_model,
                {
                    "default-space": deployment.get_space(Networks.MANAGEMENT),
                },
            )
        )
    else:
        plan1 = []
        plan1.append(
            AddCloudJujuStep(deployment.name, cloud_definition, juju_controller)
        )
        plan1.append(
            BootstrapJujuStep(
                client,
                deployment.name,
                cloud_definition["clouds"][deployment.name]["type"],
                deployment.controller,
                bootstrap_args=juju_bootstrap_args,
                proxy_settings=proxy_settings,
            )
        )
        run_plan(plan1, console)

        plan2 = []
        plan2.append(CreateJujuUserStep(fqdn))
        plan2.append(
            ClusterUpdateJujuControllerStep(client, deployment.controller, False)
        )
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
                deployment.openstack_machines_model,
                deployment.get_space(Networks.MANAGEMENT),
                [management_cidr],
            )
        )
        plan4.append(
            UpdateJujuModelConfigStep(
                jhelper,
                deployment.openstack_machines_model,
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
                deployment.openstack_machines_model,
                "controller",
                deployment.get_space(Networks.MANAGEMENT),
            )
        )
        plan4.append(
            SaveControllerStep(
                deployment.controller,
                deployment.name,
                deployments,
                data_location,
                bool(juju_controller),
                force=True,
            )
        )

    plan4.append(PromptRegionStep(client, manifest, accept_defaults))
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
            deployment.openstack_machines_model,
            refresh=True,
            proxy_settings=proxy_settings,
        )
    )
    plan4.append(
        AddSunbeamMachineUnitsStep(
            client, fqdn, jhelper, deployment.openstack_machines_model
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
                deployment.openstack_machines_model,
                accept_defaults=accept_defaults,
            )
        )
        plan4.append(
            AddK8SUnitsStep(client, fqdn, jhelper, deployment.openstack_machines_model)
        )
        plan4.append(
            EnableK8SFeatures(client, jhelper, deployment.openstack_machines_model)
        )
        plan4.append(
            StoreK8SKubeConfigStep(client, jhelper, deployment.openstack_machines_model)
        )
        plan4.append(AddK8SCloudStep(deployment, jhelper))
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
                deployment.openstack_machines_model,
                accept_defaults=accept_defaults,
            )
        )
        plan4.append(
            AddMicrok8sUnitsStep(
                client, fqdn, jhelper, deployment.openstack_machines_model
            )
        )
        plan4.append(
            StoreMicrok8sConfigStep(
                client, jhelper, deployment.openstack_machines_model
            )
        )
        plan4.append(AddMicrok8sCloudStep(deployment, jhelper))

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
            deployment.openstack_machines_model,
        )
    )

    if is_storage_node:
        plan4.append(
            AddMicrocephUnitsStep(
                client, fqdn, jhelper, deployment.openstack_machines_model
            )
        )
        plan4.append(
            ConfigureMicrocephOSDStep(
                client,
                fqdn,
                jhelper,
                deployment.openstack_machines_model,
                accept_defaults=accept_defaults,
                manifest=manifest,
            )
        )

    openstack_tfhelper = deployment.get_tfhelper("openstack-plan")
    if is_control_node:
        plan4.append(TerraformInitStep(openstack_tfhelper))
        plan4.append(
            DeployControlPlaneStep(
                deployment,
                openstack_tfhelper,
                jhelper,
                manifest,
                topology,
                database,
                deployment.openstack_machines_model,
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
                deployment.openstack_machines_model,
                refresh=True,
            )
        )

    run_plan(plan4, console)

    plan5: list[BaseStep] = []

    if is_control_node:
        plan5.append(OpenStackPatchLoadBalancerServicesStep(client))

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
            deployment.openstack_machines_model,
        )
    )
    if is_compute_node:
        plan5.append(
            AddHypervisorUnitsStep(
                client, fqdn, jhelper, deployment.openstack_machines_model
            )
        )

    plan5.append(SetBootstrapped(client))
    run_plan(plan5, console)

    click.echo(f"Node has been bootstrapped with roles: {pretty_roles}")


def _print_output(token: str, format: str, name: str):
    """Helper for printing formatted output."""
    if format == FORMAT_DEFAULT:
        console.print(f"Token for the Node {name}: {token}", soft_wrap=True)
    elif format == FORMAT_YAML:
        click.echo(yaml.dump({"token": token}))
    elif format == FORMAT_VALUE:
        click.echo(token)


def _write_to_file(token: str, output: Path):
    """Helper for writing token to file."""
    try:
        with output.open("w") as f:
            f.write(token)
    except OSError as e:
        raise click.ClickException(str(e)) from e
    console.print(f"Token written to file: {str(output)}")


@click.command()
@argument_with_deprecated_option("name", type=str, help="Fully qualified node name.")
@click.option(
    "-f",
    "--format",
    type=click.Choice([FORMAT_DEFAULT, FORMAT_VALUE, FORMAT_YAML]),
    default=FORMAT_DEFAULT,
    help="Output format.",
)
@click.option(
    "-o",
    "--output",
    type=click.Path(
        file_okay=True,
        dir_okay=False,
        writable=True,
        resolve_path=True,
        path_type=Path,
    ),
    help="Output file for join token.",
)
@click.pass_context
def add(
    ctx: click.Context,
    name: str,
    format: str,
    output: Path | None,
) -> None:
    """Generate a token for a new node to join the cluster.

    NAME must be a fully qualified domain name.
    """
    preflight_checks = [DaemonGroupCheck(), VerifyFQDNCheck(name)]
    run_preflight_checks(preflight_checks, console)
    name = remove_trailing_dot(name)

    deployment: LocalDeployment = ctx.obj
    client = deployment.get_client()
    jhelper = JujuHelper(deployment.get_connected_controller())

    plan1: list[BaseStep] = [
        JujuLoginStep(deployment.juju_account),
        ClusterAddNodeStep(client, name),
        CreateJujuUserStep(name),
        JujuGrantModelAccessStep(jhelper, name, deployment.openstack_machines_model),
        JujuGrantModelAccessStep(jhelper, name, OPENSTACK_MODEL),
    ]

    plan1_results = run_plan(plan1, console)

    user_token = get_step_message(plan1_results, CreateJujuUserStep)

    plan2 = [ClusterAddJujuUserStep(client, name, user_token)]
    run_plan(plan2, console)

    add_node_step_result = get_step_result(plan1_results, ClusterAddNodeStep)
    if add_node_step_result.result_type == ResultType.COMPLETED:
        token = str(add_node_step_result.message)
        if output:
            _write_to_file(token, output)
        else:
            _print_output(token, format, name)
    elif add_node_step_result.result_type == ResultType.SKIPPED:
        if add_node_step_result.message:
            token = str(add_node_step_result.message)
            if output:
                _write_to_file(token, output)
            else:
                _print_output(token, format, name)
        else:
            console.print("Node already a member of the Sunbeam cluster")


@click.command()
@argument_with_deprecated_option("token", type=str, help="Join token.")
@click.option("-a", "--accept-defaults", help="Accept all defaults.", is_flag=True)
@click.option(
    "--role",
    "roles",
    multiple=True,
    default=["control", "compute"],
    callback=validate_roles,
    help=(
        f"Specify which roles ({', '.join(role.lower() for role in Role.__members__)})"
        " the node will be assigned in the cluster."
        " Can be repeated and comma separated."
    ),
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
    Use `-` as token to read from stdin.
    """
    if token == "-":
        token = click.get_text_stream("stdin").readline().strip()
    is_control_node = any(role.is_control_node() for role in roles)
    is_compute_node = any(role.is_compute_node() for role in roles)
    is_storage_node = any(role.is_storage_node() for role in roles)

    # Register juju user with same name as Node fqdn
    name = utils.get_fqdn()

    roles_str = roles_to_str_list(roles)
    pretty_roles = ", ".join(role_.name.lower() for role_ in roles)
    LOG.debug(f"Node joining the cluster with roles: {pretty_roles}")

    k8s_provider = Snap().config.get("k8s.provider")

    preflight_checks: list[Check] = []
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
    client = deployment.get_client()
    snap = Snap()

    data_location = snap.paths.user_data
    path = deployment_path(snap)
    deployments = DeploymentsConfig.load(path)

    plan1 = [ClusterJoinNodeStep(client, token, ip, name, roles_str)]
    run_plan(plan1, console)

    try:
        deployments_from_db = read_config(client, DEPLOYMENTS_CONFIG_KEY)
        deployment.name = deployments_from_db.get("active", "local")
        deployments.add_deployment(deployment)
    except (ConfigItemNotFoundException, ClusterServiceUnavailableException) as e:
        raise click.ClickException(
            f"Error in getting deployment details from cluster db: {str(e)}"
        )
    except ValueError:
        # Deployment already added, ignore
        # This case arises when bootstrap command is run multiple times
        pass

    # Loads juju controller
    deployment.reload_credentials()
    plan2 = [
        JujuLoginStep(deployment.juju_account),
        SaveJujuUserLocallyStep(name, data_location),
        RegisterJujuUserStep(client, name, deployment.controller, data_location),
    ]
    run_plan(plan2, console)

    # Loads juju account
    deployment.reload_credentials()
    deployments.write()
    jhelper = JujuHelper(deployment.get_connected_controller())
    plan3 = [AddJujuMachineStep(ip, deployment.openstack_machines_model, jhelper)]
    plan3_results = run_plan(plan3, console)

    deployment.reload_credentials()
    # Get manifest object once the cluster is joined
    manifest = deployment.get_manifest()

    machine_id = -1
    machine_id_result = get_step_message(plan3_results, AddJujuMachineStep)
    if machine_id_result is not None:
        machine_id = int(machine_id_result)

    plan4: list[BaseStep] = []
    plan4.append(ClusterUpdateNodeStep(client, name, machine_id=machine_id))
    plan4.append(
        AddSunbeamMachineUnitsStep(
            client, name, jhelper, deployment.openstack_machines_model
        ),
    )

    if is_control_node:
        if k8s_provider == "k8s":
            plan4.append(
                AddK8SUnitsStep(
                    client, name, jhelper, deployment.openstack_machines_model
                )
            )
            plan4.append(AddK8SCredentialStep(deployment, jhelper))
        else:
            plan4.append(
                AddMicrok8sUnitsStep(
                    client, name, jhelper, deployment.openstack_machines_model
                )
            )
            plan4.append(AddMicrok8sCredentialStep(deployment, jhelper))

    if is_storage_node:
        plan4.append(
            AddMicrocephUnitsStep(
                client, name, jhelper, deployment.openstack_machines_model
            )
        )
        plan4.append(
            ConfigureMicrocephOSDStep(
                client,
                name,
                jhelper,
                deployment.openstack_machines_model,
                accept_defaults=accept_defaults,
                manifest=manifest,
            )
        )

    if is_compute_node:
        plan4.extend(
            [
                AddHypervisorUnitsStep(
                    client, name, jhelper, deployment.openstack_machines_model
                ),
                LocalSetHypervisorUnitsOptionsStep(
                    client,
                    name,
                    jhelper,
                    deployment.openstack_machines_model,
                    join_mode=True,
                    manifest=manifest,
                ),
            ]
        )

    run_plan(plan4, console)

    click.echo(f"Node joined cluster with roles: {pretty_roles}")


@click.command("list")
@click.option(
    "-f",
    "--format",
    type=click.Choice([FORMAT_TABLE, FORMAT_YAML]),
    default=FORMAT_TABLE,
    help="Output format.",
)
@click.pass_context
def list_nodes(
    ctx: click.Context,
    format: str,
) -> None:
    """List nodes in the cluster."""
    preflight_checks = [DaemonGroupCheck()]
    run_preflight_checks(preflight_checks, console)
    deployment: LocalDeployment = ctx.obj
    jhelper = JujuHelper(deployment.get_connected_controller())
    step = LocalClusterStatusStep(deployment, jhelper)
    results = run_plan([step], console)
    msg = get_step_message(results, LocalClusterStatusStep)
    renderables = cluster_status.format_status(deployment, msg, format)
    for renderable in renderables:
        console.print(renderable)


@click.command()
@click.option(
    "--force",
    type=bool,
    help=("Skip safety checks and ignore cleanup errors for some tasks"),
    is_flag=True,
)
@argument_with_deprecated_option("name", type=str, help="Fully qualified node name.")
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
        RemoveSunbeamMachineUnitsStep(
            client, name, jhelper, deployment.openstack_machines_model
        ),
    ]

    if k8s_provider == "k8s":
        plan.append(
            RemoveK8SUnitsStep(
                client, name, jhelper, deployment.openstack_machines_model
            )
        )
    else:
        plan.append(
            RemoveMicrok8sUnitsStep(
                client, name, jhelper, deployment.openstack_machines_model
            )
        )

    plan.extend(
        [
            RemoveMicrocephUnitsStep(
                client, name, jhelper, deployment.openstack_machines_model
            ),
            RemoveHypervisorUnitStep(
                client, name, jhelper, deployment.openstack_machines_model, force
            ),
            RemoveJujuMachineStep(
                client, name, jhelper, deployment.openstack_machines_model
            ),
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
    preflight_checks: list[Check] = []
    preflight_checks.append(DaemonGroupCheck())
    preflight_checks.append(VerifyBootstrappedCheck(client))
    run_preflight_checks(preflight_checks, console)

    # Validate manifest file
    manifest = deployment.get_manifest(manifest_path)

    LOG.debug(f"Manifest used for deployment - core: {manifest.core}")
    LOG.debug(f"Manifest used for deployment - features: {manifest.features}")

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
            manifest=manifest,
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
            model=deployment.openstack_machines_model,
        ),
    ]
    node = client.cluster.get_node_info(name)

    if "compute" in node["role"]:
        plan.append(
            LocalSetHypervisorUnitsOptionsStep(
                client,
                name,
                jhelper,
                deployment.openstack_machines_model,
                # Accept preseed file but do not allow 'accept_defaults' as nic
                # selection may vary from machine to machine and is potentially
                # destructive if it takes over an unintended nic.
                manifest=manifest,
            )
        )
    run_plan(plan, console)
    dashboard_url = retrieve_dashboard_url(jhelper)
    console.print("The cloud has been configured for sample usage.")
    console.print(
        "You can start using the OpenStack client"
        f" or access the OpenStack dashboard at {dashboard_url}"
    )
