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


import json
import logging
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Tuple, Type

import click
import yaml
from rich.console import Console
from rich.table import Table
from snaphelpers import Snap

from sunbeam.commands import resize as resize_cmds
from sunbeam.commands import utils
from sunbeam.commands.bootstrap_state import SetBootstrapped
from sunbeam.commands.certificates import APPLICATION as CERTIFICATES_APPLICATION
from sunbeam.commands.certificates import DeployCertificatesProviderApplicationStep
from sunbeam.commands.clusterd import APPLICATION as CLUSTERD_APPLICATION
from sunbeam.commands.clusterd import DeploySunbeamClusterdApplicationStep
from sunbeam.commands.configure import (
    DemoSetup,
    SetHypervisorCharmConfigStep,
    TerraformDemoInitStep,
    UserOpenRCStep,
    retrieve_admin_credentials,
)
from sunbeam.commands.hypervisor import (
    AddHypervisorUnitsStep,
    DeployHypervisorApplicationStep,
)
from sunbeam.commands.juju import (
    AddCloudJujuStep,
    AddCredentialsJujuStep,
    AddInfrastructureModelStep,
    DownloadJujuControllerCharmStep,
    IntegrateJujuApplicationsStep,
    JujuLoginStep,
)
from sunbeam.commands.k8s import (
    AddK8SCloudStep,
    AddK8SUnitsStep,
    StoreK8SKubeConfigStep,
)
from sunbeam.commands.microceph import (
    AddMicrocephUnitsStep,
    DeployMicrocephApplicationStep,
)
from sunbeam.commands.microk8s import (
    AddMicrok8sCloudStep,
    AddMicrok8sUnitsStep,
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
)
from sunbeam.commands.terraform import TerraformInitStep
from sunbeam.jobs.checks import (
    DaemonGroupCheck,
    DiagnosticsCheck,
    DiagnosticsResult,
    JujuSnapCheck,
    LocalShareCheck,
    VerifyBootstrappedCheck,
    VerifyClusterdNotBootstrappedCheck,
)
from sunbeam.jobs.common import (
    CLICK_FAIL,
    CLICK_OK,
    CONTEXT_SETTINGS,
    FORMAT_TABLE,
    FORMAT_YAML,
    get_step_message,
    run_plan,
    run_preflight_checks,
    str_presenter,
)
from sunbeam.jobs.deployment import PROXY_CONFIG_KEY, Deployment, Networks
from sunbeam.jobs.deployments import DeploymentsConfig, deployment_path
from sunbeam.jobs.juju import (
    CONTROLLER_MODEL,
    JujuAccount,
    JujuHelper,
    ModelNotFoundException,
    run_sync,
)
from sunbeam.jobs.manifest import AddManifestStep
from sunbeam.provider.base import ProviderBase
from sunbeam.provider.maas.client import (
    MaasClient,
    MaasDeployment,
    RoleTags,
    get_machine,
    get_network_mapping,
    is_maas_deployment,
    list_machines,
    list_machines_by_zone,
    list_spaces,
    map_space,
    unmap_space,
)
from sunbeam.provider.maas.deployment import MAAS_TYPE
from sunbeam.provider.maas.steps import (
    AddMaasDeployment,
    DeploymentMachinesCheck,
    DeploymentNetworkingCheck,
    DeploymentTopologyCheck,
    MaasAddMachinesToClusterdStep,
    MaasBootstrapJujuStep,
    MaasConfigureMicrocephOSDStep,
    MaasDeployK8SApplicationStep,
    MaasDeployMachinesStep,
    MaasDeployMicrok8sApplicationStep,
    MaasEnableK8SFeatures,
    MaasSaveClusterdCredentialsStep,
    MaasSaveControllerStep,
    MaasScaleJujuStep,
    MaasSetHypervisorUnitsOptionsStep,
    MaasUserQuestions,
    MachineComputeNicCheck,
    MachineNetworkCheck,
    MachineRequirementsCheck,
    MachineRolesCheck,
    MachineRootDiskCheck,
    MachineStorageCheck,
    NetworkMappingCompleteCheck,
)
from sunbeam.utils import CatchGroup

LOG = logging.getLogger(__name__)
console = Console()


@click.group("cluster", context_settings=CONTEXT_SETTINGS, cls=CatchGroup)
@click.pass_context
def cluster(ctx):
    """Manage the Sunbeam Cluster."""


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
        """Register to cli add commands."""
        add.add_command(add_maas)

    def register_cli(
        self,
        init: click.Group,
        configure: click.Group,
        deployment: click.Group,
    ):
        """Register to main cli maas commands."""
        init.add_command(cluster)
        cluster.add_command(bootstrap)
        cluster.add_command(deploy)
        cluster.add_command(list_nodes)
        cluster.add_command(resize_cmds.resize)
        configure.add_command(configure_cmd)
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

    def deployment_type(self) -> Tuple[str, Type[Deployment]]:
        """Return the deployment type."""
        return MAAS_TYPE, MaasDeployment


@click.command()
@click.option("-a", "--accept-defaults", help="Accept all defaults.", is_flag=True)
@click.option(
    "-m",
    "--manifest",
    "manifest_path",
    help="Manifest file.",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
)
@click.pass_context
def bootstrap(
    ctx: click.Context,
    manifest_path: Path | None = None,
    accept_defaults: bool = False,
) -> None:
    """Bootstrap the MAAS-backed deployment.

    Initialize the sunbeam cluster.
    """
    deployment: MaasDeployment = ctx.obj
    # Validate manifest file
    manifest = deployment.get_manifest(manifest_path)

    LOG.debug(f"Manifest used for deployment - preseed: {manifest.deployment}")
    LOG.debug(f"Manifest used for deployment - software: {manifest.software}")
    preseed = manifest.deployment

    deployments = DeploymentsConfig.load(deployment_path(Snap()))

    preflight_checks = []
    preflight_checks.append(JujuSnapCheck())
    preflight_checks.append(LocalShareCheck())
    preflight_checks.append(VerifyClusterdNotBootstrappedCheck())
    run_preflight_checks(preflight_checks, console)

    maas_client = MaasClient.from_deployment(deployment)

    if not is_maas_deployment(deployment):
        click.echo("Not a MAAS deployment.", sys.stderr)
        sys.exit(1)

    preflight_checks = []
    preflight_checks.append(NetworkMappingCompleteCheck(deployment))
    run_preflight_checks(preflight_checks, console)

    cloud_definition = JujuHelper.maas_cloud(deployment.name, deployment.url)
    credentials_definition = JujuHelper.maas_credential(
        cloud=deployment.name,
        credential=deployment.name,
        maas_apikey=deployment.token,
    )
    if deployment.juju_account is None:
        password = utils.random_string(32)
        deployment.juju_account = JujuAccount(user="admin", password=password)
        deployments.update_deployment(deployment)
        deployments.write()

    # Handle proxy settings
    plan = []
    plan.append(
        PromptForProxyStep(
            deployment, accept_defaults=accept_defaults, deployment_preseed=preseed
        )
    )
    plan_results = run_plan(plan, console)
    proxy_from_user = get_step_message(plan_results, PromptForProxyStep)
    if (
        isinstance(proxy_from_user, dict)
        and (proxy := proxy_from_user.get("proxy", {}))  # noqa: W503
        and proxy.get("proxy_required")  # noqa: W503
    ):
        proxy_settings = {
            p.upper(): v
            for p in ("http_proxy", "https_proxy", "no_proxy")
            if (v := proxy.get(p))
        }
    else:
        proxy_settings = {}

    plan = []
    plan.append(AddCloudJujuStep(deployment.name, cloud_definition))
    plan.append(
        AddCredentialsJujuStep(
            cloud=deployment.name,
            credentials=deployment.name,
            definition=credentials_definition,
        )
    )
    # Workaround for bug https://bugs.launchpad.net/juju/+bug/2044481
    # Remove the below step and dont pass controller charm as bootstrap
    # arguments once the above bug is fixed
    if proxy_settings:
        plan.append(DownloadJujuControllerCharmStep(proxy_settings))
    plan.append(
        MaasBootstrapJujuStep(
            maas_client,
            deployment.name,
            cloud_definition["clouds"][deployment.name]["type"],
            deployment.controller,
            deployment.juju_account.password,
            manifest.software.juju.bootstrap_args,
            proxy_settings=proxy_settings,
        )
    )
    plan.append(
        MaasScaleJujuStep(
            maas_client,
            deployment.controller,
            manifest.software.juju.scale_args,
        )
    )
    plan.append(
        MaasSaveControllerStep(deployment.controller, deployment.name, deployments)
    )
    run_plan(plan, console)

    # Reload deployment to get credentials
    deployment = deployments.get_deployment(deployment.name)

    if deployment.juju_account is None:
        console.print("Juju account should have been saved in previous step.")
        sys.exit(1)
    if deployment.juju_controller is None:
        console.print("Controller should have been saved in previous step.")
        sys.exit(1)
    jhelper = JujuHelper(deployment.get_connected_controller())
    plan2 = []
    plan2.append(
        DeployCertificatesProviderApplicationStep(
            jhelper, manifest, CONTROLLER_MODEL.split("/")[-1]
        )
    )
    plan2.append(
        DeploySunbeamClusterdApplicationStep(
            jhelper, manifest, CONTROLLER_MODEL.split("/")[-1]
        )
    )
    plan2.append(
        IntegrateJujuApplicationsStep(
            jhelper,
            CONTROLLER_MODEL.split("/")[-1],
            CERTIFICATES_APPLICATION,
            CLUSTERD_APPLICATION,
            "certificates",
        )
    )
    plan2.append(MaasSaveClusterdCredentialsStep(jhelper, deployment.name, deployments))

    run_plan(plan2, console)
    # reload deployment to get clusterd address
    deployment = deployments.get_deployment(deployment.name)
    client_url = deployment.clusterd_address
    if not client_url:
        console.print("Clusterd address should have been saved in previous step.")
        sys.exit(1)

    client = deployment.get_client()
    plan3 = []
    plan3.append(AddManifestStep(client, manifest_path))
    run_plan(plan3, console)

    if proxy_from_user and isinstance(proxy_from_user, dict):
        LOG.debug(f"Writing proxy information to clusterdb: {proxy_from_user}")
        client.cluster.update_config(PROXY_CONFIG_KEY, json.dumps(proxy_from_user))

    console.print("Bootstrap controller components complete.")


def _name_mapper(node: dict) -> str:
    return node["name"]


@click.command()
@click.option("-a", "--accept-defaults", help="Accept all defaults.", is_flag=True)
@click.option(
    "-m",
    "--manifest",
    "manifest_path",
    help="Manifest file.",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
)
@click.pass_context
def deploy(
    ctx: click.Context,
    manifest_path: Path | None = None,
    accept_defaults: bool = False,
    topology: str = "auto",
) -> None:
    """Deploy the MAAS-backed deployment.

    Deploy the sunbeam cluster.
    """
    deployment: MaasDeployment = ctx.obj
    snap = Snap()
    k8s_provider = snap.config.get("k8s.provider")

    preflight_checks = []
    preflight_checks.append(JujuSnapCheck())
    preflight_checks.append(LocalShareCheck())
    preflight_checks.append(VerifyClusterdNotBootstrappedCheck())
    run_preflight_checks(preflight_checks, console)

    if (
        deployment.clusterd_address is None
        or deployment.juju_account is None  # noqa: W503
        or deployment.juju_controller is None  # noqa: W503
    ):
        LOG.error(
            "Clusterd address: %r, Juju account: %r, Juju controller: %r",
            deployment.clusterd_address,
            deployment.juju_account,
            deployment.juju_controller,
        )
        console.print(
            f"{deployment.name!r} deployment is not complete, was bootstrap completed ?"
        )
        sys.exit(1)

    deployment_location = deployment_path(Snap())
    deployments = DeploymentsConfig.load(deployment_location)
    maas_client = MaasClient.from_deployment(deployment)
    try:
        controller = deployment.get_connected_controller()
    except OSError as e:
        console.print(f"Could not connect to controller: {e}")
        sys.exit(1)
    jhelper = JujuHelper(controller)
    clusterd_plan = [
        MaasSaveClusterdCredentialsStep(jhelper, deployment.name, deployments)
    ]
    run_plan(clusterd_plan, console)  # type: ignore

    client = deployment.get_client()
    preflight_checks = []
    preflight_checks.append(NetworkMappingCompleteCheck(deployment))
    run_preflight_checks(preflight_checks, console)

    manifest = deployment.get_manifest(manifest_path)

    LOG.debug(f"Manifest used for deployment - preseed: {manifest.deployment}")
    LOG.debug(f"Manifest used for deployment - software: {manifest.software}")
    preseed = manifest.deployment
    proxy_settings = deployment.get_proxy_settings()

    tfhelper_sunbeam_machine = deployment.get_tfhelper("sunbeam-machine-plan")
    if k8s_provider == "k8s":
        tfhelper_k8s = deployment.get_tfhelper("k8s-plan")
    else:
        tfhelper_k8s = deployment.get_tfhelper("microk8s-plan")
    tfhelper_microceph = deployment.get_tfhelper("microceph-plan")
    tfhelper_openstack_deploy = deployment.get_tfhelper("openstack-plan")
    tfhelper_hypervisor_deploy = deployment.get_tfhelper("hypervisor-plan")

    plan = []
    plan.append(
        AddInfrastructureModelStep(
            jhelper, deployment.infrastructure_model, proxy_settings
        )
    )
    plan.append(MaasAddMachinesToClusterdStep(client, maas_client))
    plan.append(
        MaasDeployMachinesStep(client, jhelper, deployment.infrastructure_model)
    )
    run_plan(plan, console)

    control = list(
        map(_name_mapper, client.cluster.list_nodes_by_role(RoleTags.CONTROL.value))
    )
    nb_control = len(control)
    compute = list(
        map(_name_mapper, client.cluster.list_nodes_by_role(RoleTags.COMPUTE.value))
    )
    nb_compute = len(compute)
    storage = list(
        map(_name_mapper, client.cluster.list_nodes_by_role(RoleTags.STORAGE.value))
    )
    nb_storage = len(storage)
    workers = list(set(compute + control + storage))

    if nb_control < 1 or nb_compute < 1 or nb_storage < 1:
        console.print(
            "Deployments needs at least one of each role to work correctly:"
            f"\n\tcontrol: {len(control)}"
            f"\n\tcompute: {len(compute)}"
            f"\n\tstorage: {len(storage)}"
        )
        sys.exit(1)

    plan2 = []

    plan2.append(PromptRegionStep(client, preseed, accept_defaults))
    plan2.append(TerraformInitStep(tfhelper_sunbeam_machine))
    plan2.append(
        DeploySunbeamMachineApplicationStep(
            deployment,
            client,
            tfhelper_sunbeam_machine,
            jhelper,
            manifest,
            deployment.infrastructure_model,
            refresh=True,
            proxy_settings=proxy_settings,
        )
    )
    plan2.append(
        AddSunbeamMachineUnitsStep(
            client, workers, jhelper, deployment.infrastructure_model
        )
    )
    plan2.append(TerraformInitStep(tfhelper_k8s))
    if k8s_provider == "k8s":
        plan2.append(
            MaasDeployK8SApplicationStep(
                deployment,
                client,
                maas_client,
                tfhelper_k8s,
                jhelper,
                manifest,
                deployment.infrastructure_model,
                preseed,
                accept_defaults,
            )
        )
        plan2.append(
            AddK8SUnitsStep(client, control, jhelper, deployment.infrastructure_model)
        )
        plan2.append(
            MaasEnableK8SFeatures(
                client,
                maas_client,
                jhelper,
                str(deployment.network_mapping[Networks.PUBLIC.value]),
                deployment.public_api_label,
                deployment.infrastructure_model,
            )
        )
        plan2.append(
            StoreK8SKubeConfigStep(client, jhelper, deployment.infrastructure_model)
        )
        plan2.append(AddK8SCloudStep(client, jhelper))
    else:
        plan2.append(
            MaasDeployMicrok8sApplicationStep(
                deployment,
                client,
                maas_client,
                tfhelper_k8s,
                jhelper,
                manifest,
                deployment.infrastructure_model,
                preseed,
                accept_defaults,
            )
        )
        plan2.append(
            AddMicrok8sUnitsStep(
                client, control, jhelper, deployment.infrastructure_model
            )
        )
        plan2.append(
            StoreMicrok8sConfigStep(client, jhelper, deployment.infrastructure_model)
        )
        plan2.append(AddMicrok8sCloudStep(client, jhelper))

    plan2.append(TerraformInitStep(tfhelper_microceph))
    plan2.append(
        DeployMicrocephApplicationStep(
            deployment,
            client,
            tfhelper_microceph,
            jhelper,
            manifest,
            deployment.infrastructure_model,
        )
    )
    plan2.append(
        AddMicrocephUnitsStep(client, storage, jhelper, deployment.infrastructure_model)
    )
    plan2.append(
        MaasConfigureMicrocephOSDStep(
            client,
            maas_client,
            jhelper,
            storage,
            deployment.infrastructure_model,
        )
    )
    plan2.append(TerraformInitStep(tfhelper_openstack_deploy))
    plan2.append(
        DeployControlPlaneStep(
            client,
            tfhelper_openstack_deploy,
            jhelper,
            manifest,
            topology,
            # maas deployment always deploys multiple databases
            "large",
            deployment.infrastructure_model,
            proxy_settings=proxy_settings,
        )
    )
    # Redeploy of Microceph is required to fill terraform vars
    # related to traefik-rgw/keystone-endpoints offers from
    # openstack model
    plan2.append(
        DeployMicrocephApplicationStep(
            deployment,
            client,
            tfhelper_microceph,
            jhelper,
            manifest,
            deployment.infrastructure_model,
            refresh=True,
        )
    )
    plan2.append(PatchLoadBalancerServicesStep(client))
    plan2.append(TerraformInitStep(tfhelper_hypervisor_deploy))
    plan2.append(
        DeployHypervisorApplicationStep(
            deployment,
            client,
            tfhelper_hypervisor_deploy,
            tfhelper_openstack_deploy,
            jhelper,
            manifest,
            deployment.infrastructure_model,
        )
    )
    plan2.append(
        AddHypervisorUnitsStep(
            client, compute, jhelper, deployment.infrastructure_model
        )
    )
    plan2.append(SetBootstrapped(client))
    run_plan(plan2, console)

    console.print(
        f"Deployment complete with {nb_control} control,"
        f" {nb_compute} compute and {nb_storage} storage nodes."
        f" Total nodes in cluster: {len(workers)}"
    )


@click.command("deployment")
@click.pass_context
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
def configure_cmd(
    ctx: click.Context,
    openrc: Path | None = None,
    manifest_path: Path | None = None,
    accept_defaults: bool = False,
) -> None:
    deployment: Deployment = ctx.obj
    client = deployment.get_client()
    maas_client = MaasClient.from_deployment(deployment)
    preflight_checks = []
    preflight_checks.append(DaemonGroupCheck())
    preflight_checks.append(VerifyBootstrappedCheck(client))
    run_preflight_checks(preflight_checks, console)

    # Validate manifest file
    manifest = deployment.get_manifest(manifest_path)

    LOG.debug(f"Manifest used for deployment - preseed: {manifest.deployment}")
    LOG.debug(f"Manifest used for deployment - software: {manifest.software}")
    preseed = manifest.deployment

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
    compute = list(
        map(_name_mapper, client.cluster.list_nodes_by_role(RoleTags.COMPUTE.value))
    )
    plan = [
        AddManifestStep(client, manifest_path),
        JujuLoginStep(deployment.juju_account),
        MaasUserQuestions(
            client,
            maas_client,
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
        MaasSetHypervisorUnitsOptionsStep(
            client,
            maas_client,
            compute,
            jhelper,
            deployment.infrastructure_model,
            preseed,
        ),
    ]

    run_plan(plan, console)


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
    deployments = DeploymentsConfig.load(path)
    plan = []
    plan.append(
        AddMaasDeployment(
            deployments,
            MaasDeployment(
                name=name, token=token, url=url, resource_pool=resource_pool
            ),
        )
    )
    run_plan(plan, console)
    click.echo(f"MAAS deployment {name} added.")


@click.command("list")
@click.option(
    "--format",
    type=click.Choice([FORMAT_TABLE, FORMAT_YAML]),
    default=FORMAT_TABLE,
    help="Output format",
)
@click.pass_context
def list_machines_cmd(ctx: click.Context, format: str) -> None:
    """List machines in active deployment."""
    preflight_checks = [
        LocalShareCheck(),
    ]
    run_preflight_checks(preflight_checks, console)

    deployment: MaasDeployment = ctx.obj
    client = MaasClient.from_deployment(deployment)
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
@click.pass_context
def show_machine_cmd(ctx: click.Context, hostname: str, format: str) -> None:
    """Show machine in active deployment."""
    preflight_checks = [
        LocalShareCheck(),
    ]
    run_preflight_checks(preflight_checks, console)

    deployment: MaasDeployment = ctx.obj
    client = MaasClient.from_deployment(deployment)
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
            ", ".join(
                f"{tag}({len(devices)})" for tag, devices in machine["storage"].items()
            ),
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
        title="\u00a0",  # non-breaking space to have zone same height as roles
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
    for role in RoleTags.values():
        machine_table.add_column(role, justify="center")
    machine_table.add_column("total", justify="center")
    for zone, machines in zone_machines.items():
        zone_table.add_row(zone)
        role_count = Counter()
        for machine in machines:
            role_count.update(machine["roles"])
        role_nb = [str(role_count.get(role, 0)) for role in RoleTags.values()]
        role_nb += [str(len(machines))]  # total
        machine_table.add_row(*role_nb)

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
@click.pass_context
def list_zones_cmd(ctx: click.Context, roles: bool, format: str) -> None:
    """List zones in active deployment."""
    preflight_checks = [
        LocalShareCheck(),
    ]
    run_preflight_checks(preflight_checks, console)

    deployment: MaasDeployment = ctx.obj
    client = MaasClient.from_deployment(deployment)

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
@click.pass_context
def list_spaces_cmd(ctx: click.Context, format: str) -> None:
    """List spaces in MAAS deployment."""
    preflight_checks = [
        LocalShareCheck(),
    ]
    run_preflight_checks(preflight_checks, console)

    deployment: MaasDeployment = ctx.obj
    client = MaasClient.from_deployment(deployment)
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
@click.pass_context
def map_space_cmd(ctx: click.Context, space: str, network: str) -> None:
    """Map space to network."""
    preflight_checks = [
        LocalShareCheck(),
    ]
    run_preflight_checks(preflight_checks, console)

    snap = Snap()
    deployment_location = deployment_path(snap)
    deployments_config = DeploymentsConfig.load(deployment_location)
    deployment: MaasDeployment = ctx.obj
    client = MaasClient.from_deployment(deployment)
    map_space(deployments_config, deployment, client, space, Networks(network))
    console.print(f"Space {space} mapped to network {network}.")


@click.command("unmap")
@click.argument("network", type=click.Choice(Networks.values()))
@click.pass_context
def unmap_space_cmd(ctx: click.Context, network: str) -> None:
    """Unmap space from network."""
    preflight_checks = [
        LocalShareCheck(),
    ]
    run_preflight_checks(preflight_checks, console)

    snap = Snap()
    deployment_location = deployment_path(snap)
    deployments_config = DeploymentsConfig.load(deployment_location)
    deployment: MaasDeployment = ctx.obj
    unmap_space(deployments_config, deployment, Networks(network))
    console.print(f"Space unmapped from network {network}.")


@click.command("list")
@click.option(
    "--format",
    type=click.Choice([FORMAT_TABLE, FORMAT_YAML]),
    default=FORMAT_TABLE,
    help="Output format",
)
@click.pass_context
def list_networks_cmd(ctx: click.Context, format: str):
    """List networks and associated spaces."""
    preflight_checks = [
        LocalShareCheck(),
    ]
    run_preflight_checks(preflight_checks, console)

    deployment: MaasDeployment = ctx.obj
    mapping = get_network_mapping(deployment)
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
@click.pass_context
def validate_machine_cmd(ctx: click.Context, machine: str):
    """Validate machine configuration."""
    preflight_checks = [
        LocalShareCheck(),
    ]
    run_preflight_checks(preflight_checks, console)

    deployment: MaasDeployment = ctx.obj
    snap = Snap()

    client = MaasClient.from_deployment(deployment)
    with console.status(f"Fetching {machine} ..."):
        try:
            machine_obj = get_machine(client, machine)
            LOG.debug(f"{machine_obj=!r}")
        except ValueError as e:
            console.print("Error:", e)
            sys.exit(1)
    validation_checks = [
        MachineRolesCheck(machine_obj),
        MachineNetworkCheck(deployment, machine_obj),
        MachineStorageCheck(machine_obj),
        MachineComputeNicCheck(machine_obj),
        MachineRootDiskCheck(machine_obj),
        MachineRequirementsCheck(machine_obj),
    ]
    report = _run_maas_checks(validation_checks, console)
    report_path = _save_report(snap, "validate-machine-" + machine, report)
    console.print(f"Report saved to {report_path!r}")


@click.command("validate")
@click.pass_context
def validate_deployment_cmd(ctx: click.Context):
    """Validate deployment."""
    preflight_checks = [
        LocalShareCheck(),
    ]
    run_preflight_checks(preflight_checks, console)
    snap = Snap()
    deployment: MaasDeployment = ctx.obj
    client = MaasClient.from_deployment(deployment)
    with console.status(f"Fetching {deployment.name} machines ..."):
        try:
            machines = list_machines(client)
        except ValueError as e:
            console.print("Error:", e)
            sys.exit(1)
    validation_checks = [
        DeploymentMachinesCheck(deployment, machines),
        DeploymentTopologyCheck(machines),
        DeploymentNetworkingCheck(client, deployment),
    ]
    report = _run_maas_meta_checks(validation_checks, console)
    report_path = _save_report(snap, "validate-deployment-" + deployment.name, report)
    console.print(f"Report saved to {report_path!r}")
