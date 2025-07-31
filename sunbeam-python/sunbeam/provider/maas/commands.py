# SPDX-FileCopyrightText: 2024 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import json
import logging
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Sequence, Tuple, Type

import click
import yaml
from click.core import ParameterSource
from rich.console import Console
from rich.table import Table
from snaphelpers import Snap

from sunbeam.clusterd.service import ConfigItemNotFoundException
from sunbeam.commands import resize as resize_cmds
from sunbeam.commands.configure import (
    DemoSetup,
    TerraformDemoInitStep,
    UserOpenRCStep,
    retrieve_admin_credentials,
)
from sunbeam.commands.dashboard_url import retrieve_dashboard_url
from sunbeam.commands.proxy import PromptForProxyStep
from sunbeam.core.checks import (
    Check,
    DiagnosticResultType,
    DiagnosticsCheck,
    DiagnosticsResult,
    JujuSnapCheck,
    LocalShareCheck,
    VerifyBootstrappedCheck,
    VerifyClusterdNotBootstrappedCheck,
    run_preflight_checks,
)
from sunbeam.core.common import (
    CLICK_FAIL,
    CLICK_OK,
    CLICK_WARN,
    CONTEXT_SETTINGS,
    FORMAT_TABLE,
    FORMAT_YAML,
    BaseStep,
    click_option_database,
    click_option_topology,
    get_step_message,
    run_plan,
    str_presenter,
)
from sunbeam.core.deployment import PROXY_CONFIG_KEY, Deployment, Networks
from sunbeam.core.deployments import DeploymentsConfig, deployment_path
from sunbeam.core.juju import (
    CONTROLLER_APPLICATION,
    CONTROLLER_MODEL,
    JujuHelper,
)
from sunbeam.core.manifest import AddManifestStep
from sunbeam.core.openstack import OPENSTACK_MODEL
from sunbeam.core.terraform import TerraformInitStep
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
    map_spaces,
    unmap_spaces,
)
from sunbeam.provider.maas.deployment import MAAS_TYPE
from sunbeam.provider.maas.steps import (
    AddMaasDeployment,
    DeploymentMachinesCheck,
    DeploymentNetworkingCheck,
    DeploymentTopologyCheck,
    JujuControllerCheck,
    MaasAddMachinesToClusterdStep,
    MaasBootstrapJujuStep,
    MaasClusterStatusStep,
    MaasConfigSRIOVStep,
    MaasConfigureMicrocephOSDStep,
    MaasCreateLoadBalancerIPPoolsStep,
    MaasDeployInfraMachinesStep,
    MaasDeployK8SApplicationStep,
    MaasDeployMachinesStep,
    MaasRemoveDeploymentCredentialsStep,
    MaasRemoveMachineFromClusterdStep,
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
from sunbeam.steps import cluster_status
from sunbeam.steps.bootstrap_state import SetBootstrapped
from sunbeam.steps.certificates import APPLICATION as CERTIFICATES_APPLICATION
from sunbeam.steps.certificates import DeployCertificatesProviderApplicationStep
from sunbeam.steps.cinder_volume import (
    AddCinderVolumeUnitsStep,
    CheckCinderVolumeDistributionStep,
    DeployCinderVolumeApplicationStep,
    DestroyCinderVolumeApplicationStep,
    RemoveCinderVolumeUnitsStep,
)
from sunbeam.steps.clusterd import APPLICATION as CLUSTERD_APPLICATION
from sunbeam.steps.clusterd import (
    DeploySunbeamClusterdApplicationStep,
)
from sunbeam.steps.features import DisableEnabledFeatures
from sunbeam.steps.hypervisor import (
    AddHypervisorUnitsStep,
    DeployHypervisorApplicationStep,
    DestroyHypervisorApplicationStep,
    ReapplyHypervisorTerraformPlanStep,
    RemoveHypervisorUnitStep,
)
from sunbeam.steps.juju import (
    AddCloudJujuStep,
    AddCredentialsJujuStep,
    AddJujuModelStep,
    DestroyJujuControllerStep,
    DestroyJujuModelStep,
    DownloadJujuControllerCharmStep,
    IntegrateJujuApplicationsStep,
    JujuLoginStep,
    RemoveJujuMachineStep,
    RemoveSaasApplicationsStep,
    UpdateJujuMachineIDStep,
)
from sunbeam.steps.k8s import (
    AddK8SCloudStep,
    AddK8SUnitsStep,
    CheckMysqlK8SDistributionStep,
    CheckOvnK8SDistributionStep,
    CheckRabbitmqK8SDistributionStep,
    CordonK8SUnitStep,
    DestroyK8SApplicationStep,
    DrainK8SUnitStep,
    EnsureDefaultL2AdvertisementMutedStep,
    EnsureK8SUnitsTaggedStep,
    EnsureL2AdvertisementByHostStep,
    MigrateK8SKubeconfigStep,
    PatchCoreDNSStep,
    RemoveK8SUnitsStep,
    StoreK8SKubeConfigStep,
    UpdateK8SCloudStep,
)
from sunbeam.steps.microceph import (
    AddMicrocephUnitsStep,
    CheckMicrocephDistributionStep,
    DeployMicrocephApplicationStep,
    DestroyMicrocephApplicationStep,
    RemoveMicrocephUnitsStep,
    SetCephMgrPoolSizeStep,
)
from sunbeam.steps.openstack import (
    DeployControlPlaneStep,
    DestroyControlPlaneStep,
    OpenStackPatchLoadBalancerServicesIPPoolStep,
    OpenStackPatchLoadBalancerServicesIPStep,
    PromptDatabaseTopologyStep,
    PromptRegionStep,
)
from sunbeam.steps.sso import (
    DeployIdentityProvidersStep,
    ValidateIdentityManifest,
)
from sunbeam.steps.sunbeam_machine import (
    AddSunbeamMachineUnitsStep,
    DeploySunbeamMachineApplicationStep,
    DestroySunbeamMachineApplicationStep,
    RemoveSunbeamMachineUnitsStep,
)
from sunbeam.steps.terraform import CleanTerraformPlansStep
from sunbeam.utils import (
    CatchGroup,
    DefaultableMappingParameter,
    click_option_show_hints,
)

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
        cluster.add_command(remove_node)
        cluster.add_command(destroy_deployment_cmd)
        configure.add_command(configure_cmd)
        configure.add_command(configure_sriov)
        deployment.add_command(machine)
        machine.add_command(list_machines_cmd)
        machine.add_command(show_machine_cmd)
        machine.add_command(validate_machine_cmd)
        deployment.add_command(zone)
        zone.add_command(list_zones_cmd)
        deployment.add_command(space)
        space.add_command(list_spaces_cmd)
        space.add_command(map_spaces_cmd)
        space.add_command(unmap_spaces_cmd)
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
@click.option(
    "-c",
    "--controller",
    "juju_controller",
    type=str,
    help="Juju controller name",
)
@click_option_show_hints
@click.pass_context
def bootstrap(
    ctx: click.Context,
    juju_controller: str | None = None,
    manifest_path: Path | None = None,
    accept_defaults: bool = False,
    show_hints: bool = False,
) -> None:
    """Bootstrap the MAAS-backed deployment.

    Initialize the sunbeam cluster.
    """
    deployment: MaasDeployment = ctx.obj
    # Validate manifest file
    manifest = deployment.get_manifest(manifest_path)

    LOG.debug(f"Manifest used for deployment - core: {manifest.core}")
    LOG.debug(f"Manifest used for deployment - features: {manifest.features}")

    deployments = DeploymentsConfig.load(deployment_path(Snap()))

    preflight_checks: list[Check] = []
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
    preflight_checks.append(JujuControllerCheck(deployment, juju_controller))
    run_preflight_checks(preflight_checks, console)

    data_location = Snap().paths.user_data
    cloud_definition = JujuHelper.maas_cloud(deployment.name, deployment.url)
    credentials_definition = JujuHelper.maas_credential(
        cloud=deployment.name,
        credential=deployment.name,
        maas_apikey=deployment.token,
    )

    # Handle proxy settings
    plan: list[BaseStep] = []
    plan.append(
        MaasSaveControllerStep(
            juju_controller,
            deployment.name,
            deployments,
            data_location,
            bool(juju_controller),
        )
    )
    plan.append(
        PromptForProxyStep(
            deployment, accept_defaults=accept_defaults, manifest=manifest
        )
    )
    plan_results = run_plan(plan, console, show_hints)

    # Reload deployment to get credentials
    deployment = deployments.refresh_deployment(deployment)

    proxy_from_user = get_step_message(plan_results, PromptForProxyStep)
    if (
        isinstance(proxy_from_user, dict)
        and (proxy := proxy_from_user.get("proxy", {}))
        and proxy.get("proxy_required")
    ):
        proxy_settings = {
            p.upper(): v
            for p in ("http_proxy", "https_proxy", "no_proxy")
            if (v := proxy.get(p))
        }
    else:
        proxy_settings = {}

    plan = []
    plan.append(AddCloudJujuStep(deployment.name, cloud_definition, juju_controller))
    plan.append(
        AddCredentialsJujuStep(
            cloud=deployment.name,
            credentials=deployment.name,
            definition=credentials_definition,
            controller=juju_controller,
        )
    )

    if not juju_controller:
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
                deployment.juju_account.password,  # type: ignore
                manifest.core.software.juju.bootstrap_args,
                proxy_settings=proxy_settings,
            )
        )
        plan.append(
            MaasScaleJujuStep(
                maas_client,
                deployment.controller,
                manifest.core.software.juju.scale_args,
            )
        )
        plan.append(
            MaasSaveControllerStep(
                deployment.controller,
                deployment.name,
                deployments,
                data_location,
                False,
            )
        )

    run_plan(plan, console, show_hints)

    # Reload deployment to get credentials
    deployment = deployments.refresh_deployment(deployment)

    if deployment.juju_account is None:
        console.print("Juju account should have been saved in previous step.")
        sys.exit(1)
    if deployment.juju_controller is None:
        console.print("Controller should have been saved in previous step.")
        sys.exit(1)

    jhelper = JujuHelper(deployment.juju_controller)
    plan2: list[BaseStep] = []
    plan2.append(
        AddJujuModelStep(
            jhelper,
            deployment.infra_model,
            deployment.name,
            deployment.name,
            proxy_settings,
            manifest.core.software.juju.bootstrap_model_configs.get(
                deployment.infra_model
            ),
        )
    )
    plan2.append(
        MaasDeployInfraMachinesStep(maas_client, jhelper, deployment.infra_model)
    )
    plan2.append(
        DeployCertificatesProviderApplicationStep(
            jhelper, manifest, deployment.infra_model
        )
    )
    plan2.append(
        DeploySunbeamClusterdApplicationStep(jhelper, manifest, deployment.infra_model)
    )
    plan2.append(
        IntegrateJujuApplicationsStep(
            jhelper,
            deployment.infra_model,
            CERTIFICATES_APPLICATION,
            CLUSTERD_APPLICATION,
            "certificates",
        )
    )
    plan2.append(MaasSaveClusterdCredentialsStep(jhelper, deployment.name, deployments))

    run_plan(plan2, console, show_hints)
    # reload deployment to get clusterd address
    deployment = deployments.refresh_deployment(deployment)
    client_url = deployment.clusterd_address
    if not client_url:
        console.print("Clusterd address should have been saved in previous step.")
        sys.exit(1)

    client = deployment.get_client()
    plan3: list[BaseStep] = []
    plan3.append(AddManifestStep(client, manifest_path))
    plan3.append(MaasAddMachinesToClusterdStep(client, maas_client))
    if not juju_controller:
        plan3.append(
            UpdateJujuMachineIDStep(
                client, jhelper, CONTROLLER_MODEL.split("/")[-1], CONTROLLER_APPLICATION
            )
        )

    plan3.append(
        UpdateJujuMachineIDStep(
            client, jhelper, deployment.infra_model, CLUSTERD_APPLICATION
        )
    )
    run_plan(plan3, console, show_hints)

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
@click_option_topology
@click_option_database
@click_option_show_hints
@click.pass_context
def deploy(
    ctx: click.Context,
    manifest_path: Path | None = None,
    accept_defaults: bool = False,
    topology: str = "auto",
    database: str = "auto",
    show_hints: bool = False,
) -> None:
    """Deploy the MAAS-backed deployment.

    Deploy the sunbeam cluster.
    """
    deployment: MaasDeployment = ctx.obj

    parameter_source = click.get_current_context().get_parameter_source("database")
    if parameter_source == ParameterSource.COMMANDLINE:
        LOG.warning(
            "WARNING: Option --database is deprecated and the value is ignored. "
            "Instead user is prompted to select the database topology. "
            "The database topology can also be set via manifest."
        )

    preflight_checks: list[Check] = []
    preflight_checks.append(JujuSnapCheck())
    preflight_checks.append(LocalShareCheck())
    preflight_checks.append(VerifyClusterdNotBootstrappedCheck())
    run_preflight_checks(preflight_checks, console)

    if (
        deployment.clusterd_address is None
        or deployment.juju_account is None
        or deployment.juju_controller is None
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
    jhelper = JujuHelper(deployment.juju_controller)
    clusterd_plan = [
        MaasSaveClusterdCredentialsStep(jhelper, deployment.name, deployments)
    ]
    run_plan(clusterd_plan, console, show_hints)  # type: ignore

    client = deployment.get_client()
    preflight_checks = []
    preflight_checks.append(NetworkMappingCompleteCheck(deployment))
    run_preflight_checks(preflight_checks, console)

    manifest = deployment.get_manifest(manifest_path)

    LOG.debug(f"Manifest used for deployment - core: {manifest.core}")
    LOG.debug(f"Manifest used for deployment - features: {manifest.features}")
    proxy_settings = deployment.get_proxy_settings()

    tfhelper_sunbeam_machine = deployment.get_tfhelper("sunbeam-machine-plan")
    tfhelper_k8s = deployment.get_tfhelper("k8s-plan")
    tfhelper_microceph = deployment.get_tfhelper("microceph-plan")
    tfhelper_cinder_volume = deployment.get_tfhelper("cinder-volume-plan")
    tfhelper_openstack_deploy = deployment.get_tfhelper("openstack-plan")
    tfhelper_hypervisor_deploy = deployment.get_tfhelper("hypervisor-plan")

    plan: list[BaseStep] = []
    plan.append(AddManifestStep(client, manifest_path))
    plan.append(
        AddJujuModelStep(
            jhelper,
            deployment.openstack_machines_model,
            deployment.name,
            deployment.name,
            proxy_settings,
            manifest.core.software.juju.bootstrap_model_configs.get(
                deployment.openstack_machines_model
            ),
        )
    )
    plan.append(MaasAddMachinesToClusterdStep(client, maas_client))
    plan.append(
        MaasDeployMachinesStep(client, jhelper, deployment.openstack_machines_model)
    )
    run_plan(plan, console, show_hints)

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

    plan2: list[BaseStep] = []

    plan2.append(PromptDatabaseTopologyStep(client, manifest, accept_defaults))
    plan2.append(PromptRegionStep(client, manifest, accept_defaults))
    plan2.append(ValidateIdentityManifest(client, manifest))
    plan2.append(TerraformInitStep(tfhelper_sunbeam_machine))
    plan2.append(
        DeploySunbeamMachineApplicationStep(
            deployment,
            client,
            tfhelper_sunbeam_machine,
            jhelper,
            manifest,
            deployment.openstack_machines_model,
            refresh=True,
            proxy_settings=proxy_settings,
        )
    )
    plan2.append(
        AddSunbeamMachineUnitsStep(
            client, workers, jhelper, deployment.openstack_machines_model
        )
    )
    plan2.append(TerraformInitStep(tfhelper_k8s))
    plan2.append(
        MaasDeployK8SApplicationStep(
            deployment,
            client,
            maas_client,
            tfhelper_k8s,
            jhelper,
            manifest,
            deployment.openstack_machines_model,
            accept_defaults,
        )
    )
    plan2.append(
        AddK8SUnitsStep(client, control, jhelper, deployment.openstack_machines_model)
    )
    plan2.append(
        StoreK8SKubeConfigStep(
            deployment, client, jhelper, deployment.openstack_machines_model
        )
    )
    plan2.append(
        EnsureK8SUnitsTaggedStep(
            deployment, client, jhelper, deployment.openstack_machines_model
        )
    )
    plan2.append(EnsureDefaultL2AdvertisementMutedStep(deployment, client, jhelper))
    plan2.append(MaasCreateLoadBalancerIPPoolsStep(deployment, client, maas_client))
    plan2.append(
        EnsureL2AdvertisementByHostStep(
            deployment,
            client,
            jhelper,
            deployment.openstack_machines_model,
            Networks.INTERNAL,
            deployment.internal_ip_pool,
        ),
    )
    plan2.append(
        EnsureL2AdvertisementByHostStep(
            deployment,
            client,
            jhelper,
            deployment.openstack_machines_model,
            Networks.PUBLIC,
            deployment.public_ip_pool,
        )
    )
    plan2.append(AddK8SCloudStep(deployment, jhelper))
    plan2.append(PatchCoreDNSStep(deployment, jhelper))

    plan2.append(TerraformInitStep(tfhelper_microceph))
    plan2.append(
        DeployMicrocephApplicationStep(
            deployment,
            client,
            tfhelper_microceph,
            jhelper,
            manifest,
            deployment.openstack_machines_model,
        )
    )
    plan2.append(
        AddMicrocephUnitsStep(
            client, storage, jhelper, deployment.openstack_machines_model
        )
    )
    plan2.append(
        MaasConfigureMicrocephOSDStep(
            client,
            maas_client,
            jhelper,
            storage,
            deployment.openstack_machines_model,
        )
    )
    plan2.append(TerraformInitStep(tfhelper_cinder_volume))
    plan2.append(
        DeployCinderVolumeApplicationStep(
            deployment,
            client,
            tfhelper_cinder_volume,
            jhelper,
            manifest,
            deployment.openstack_machines_model,
        )
    )
    plan2.append(TerraformInitStep(tfhelper_openstack_deploy))
    plan2.append(
        AddCinderVolumeUnitsStep(
            client,
            storage,
            jhelper,
            deployment.openstack_machines_model,
            tfhelper_openstack_deploy,
        )
    )
    plan2.append(
        DeployControlPlaneStep(
            deployment,
            tfhelper_openstack_deploy,
            jhelper,
            manifest,
            topology,
            deployment.openstack_machines_model,
            proxy_settings=proxy_settings,
        )
    )
    plan2.append(
        DeployIdentityProvidersStep(
            deployment,
            tfhelper_openstack_deploy,
            jhelper,
            manifest,
        )
    )
    plan2.append(
        OpenStackPatchLoadBalancerServicesIPPoolStep(
            client, deployment.public_api_label
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
            deployment.openstack_machines_model,
            refresh=True,
        )
    )
    # Fill AMQP / Keystone / MySQL offers from openstack model
    plan2.append(
        DeployCinderVolumeApplicationStep(
            deployment,
            client,
            tfhelper_cinder_volume,
            jhelper,
            manifest,
            deployment.openstack_machines_model,
            refresh=True,
        )
    )
    plan2.append(OpenStackPatchLoadBalancerServicesIPStep(client))
    plan2.append(TerraformInitStep(tfhelper_hypervisor_deploy))
    plan2.append(
        DeployHypervisorApplicationStep(
            deployment,
            client,
            tfhelper_hypervisor_deploy,
            tfhelper_openstack_deploy,
            tfhelper_cinder_volume,
            jhelper,
            manifest,
            deployment.openstack_machines_model,
        )
    )
    plan2.append(
        AddHypervisorUnitsStep(
            client, compute, jhelper, deployment.openstack_machines_model
        )
    )
    plan2.append(SetBootstrapped(client))
    run_plan(plan2, console, show_hints)

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
@click_option_show_hints
def configure_cmd(
    ctx: click.Context,
    openrc: Path | None = None,
    manifest_path: Path | None = None,
    accept_defaults: bool = False,
    show_hints: bool = False,
) -> None:
    deployment: Deployment = ctx.obj
    client = deployment.get_client()
    maas_client = MaasClient.from_deployment(deployment)
    preflight_checks = []
    preflight_checks.append(VerifyBootstrappedCheck(client))
    run_preflight_checks(preflight_checks, console)

    # Validate manifest file
    manifest = deployment.get_manifest(manifest_path)

    LOG.debug(f"Manifest used for deployment - core: {manifest.core}")
    LOG.debug(f"Manifest used for deployment - features: {manifest.features}")

    jhelper = JujuHelper(deployment.juju_controller)
    if not jhelper.model_exists(OPENSTACK_MODEL):
        LOG.error(f"Expected model {OPENSTACK_MODEL} missing")
        raise click.ClickException("Please run `sunbeam cluster bootstrap` first")
    admin_credentials = retrieve_admin_credentials(jhelper, OPENSTACK_MODEL)
    # Add OS_INSECURE as https not working with terraform openstack provider.
    admin_credentials["OS_INSECURE"] = "true"

    tfplan = "demo-setup"
    tfhelper = deployment.get_tfhelper(tfplan)
    tfhelper.env = (tfhelper.env or {}) | admin_credentials
    answer_file = tfhelper.path / "config.auto.tfvars.json"
    tfhelper_hypervisor = deployment.get_tfhelper("hypervisor-plan")
    compute = list(
        map(_name_mapper, client.cluster.list_nodes_by_role(RoleTags.COMPUTE.value))
    )
    plan = [
        AddManifestStep(client, manifest_path),
        JujuLoginStep(deployment.juju_account),
        MaasUserQuestions(
            client,
            maas_client,
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
        TerraformInitStep(tfhelper_hypervisor),
        ReapplyHypervisorTerraformPlanStep(
            client,
            tfhelper_hypervisor,
            jhelper,
            manifest,
            model=deployment.openstack_machines_model,
        ),
        MaasSetHypervisorUnitsOptionsStep(
            client,
            maas_client,
            compute,
            jhelper,
            deployment.openstack_machines_model,
            manifest,
        ),
    ]

    run_plan(plan, console, show_hints)
    dashboard_url = retrieve_dashboard_url(jhelper)
    console.print("The cloud has been configured for sample usage.")
    console.print(
        "You can start using the OpenStack client"
        f" or access the OpenStack dashboard at {dashboard_url}"
    )


@click.command("list")
@click.option(
    "-f",
    "--format",
    type=click.Choice([FORMAT_TABLE, FORMAT_YAML]),
    default=FORMAT_TABLE,
    help="Output format.",
)
@click_option_show_hints
@click.pass_context
def list_nodes(ctx: click.Context, format: str, show_hints: bool) -> None:
    """List nodes in the custer."""
    deployment: MaasDeployment = ctx.obj
    jhelper = JujuHelper(deployment.juju_controller)
    step = MaasClusterStatusStep(deployment, jhelper)
    results = run_plan([step], console, show_hints)
    msg = get_step_message(results, MaasClusterStatusStep)
    renderables = cluster_status.format_status(deployment, msg, format)
    for renderable in renderables:
        console.print(renderable)


@click.command("maas")
@click.argument("name", type=str)
@click.argument("token", type=str)
@click.argument("url", type=str)
@click_option_show_hints
def add_maas(name: str, token: str, url: str, show_hints: bool) -> None:
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
    try:
        deployment = MaasDeployment(name=name, token=token, url=url)
    except Exception as e:
        console.print("Error: " + str(e))
        sys.exit(1)
    plan.append(
        AddMaasDeployment(
            deployments,
            deployment,
        )
    )
    run_plan(plan, console, show_hints)
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
        role_count: Counter[str] = Counter()
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


def _validate_mapping(
    ctx: click.Context, param: click.Parameter, value: Sequence[tuple[str, str]]
) -> dict[Networks, str]:
    mapping: dict[Networks, str] = {}
    default_space = None
    for space, network in value:
        if network in mapping:
            raise click.BadParameter(f"Duplicate network '{network}' found.")
        if network != "default" and network not in Networks.values():
            raise click.BadParameter(f"Network '{network}' is not a valid network.")
        if network == "default":
            default_space = space
            continue
        mapping[Networks(network)] = space

    if default_space is not None:
        for network_member in Networks:
            if network_member not in mapping:
                mapping[network_member] = default_space

    return mapping


@click.command("map")
@click.argument(
    "mapping",
    nargs=-1,
    type=DefaultableMappingParameter("space", "network"),
    required=True,
    callback=_validate_mapping,
)
@click.pass_context
def map_spaces_cmd(ctx: click.Context, mapping: dict[Networks, str]):
    """Map space to network.

    Takes a list of mapping of space to network in the form of 'space:network'.
    If a space is given alone, it will be considered as the default space.
    """
    preflight_checks = [
        LocalShareCheck(),
    ]
    run_preflight_checks(preflight_checks, console)

    snap = Snap()
    deployment_location = deployment_path(snap)
    deployments_config = DeploymentsConfig.load(deployment_location)
    deployment: MaasDeployment = ctx.obj
    client = MaasClient.from_deployment(deployment)
    map_spaces(deployments_config, deployment, client, mapping)
    console.print("Space network mapping updated.")


@click.command("unmap")
@click.argument(
    "networks", type=click.Choice(Networks.values()), required=True, nargs=-1
)
@click.pass_context
def unmap_spaces_cmd(ctx: click.Context, networks: Sequence[str]) -> None:
    """Unmap space from network."""
    preflight_checks = [
        LocalShareCheck(),
    ]
    run_preflight_checks(preflight_checks, console)

    snap = Snap()
    deployment_location = deployment_path(snap)
    deployments_config = DeploymentsConfig.load(deployment_location)
    deployment: MaasDeployment = ctx.obj
    unmap_spaces(
        deployments_config, deployment, [Networks(network) for network in networks]
    )
    console.print("Space(s) unmapped from network.")


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


def _colorize_result(result: DiagnosticsResult) -> str:
    """Return a colorize string of the result status."""
    match result.passed:
        case DiagnosticResultType.SUCCESS:
            status = CLICK_OK
        case DiagnosticResultType.FAILURE:
            status = CLICK_FAIL
        case DiagnosticResultType.WARNING:
            status = CLICK_WARN
    return status


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
                passed = result.passed.value
                LOG.debug(f"{result.name=!r}, {passed=!r}, {result.message=!r}")
                console.print(
                    message,
                    result.message,
                    "-",
                    _colorize_result(result),
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
            console.print(message, _colorize_result(results[-1]))
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


@click.command("remove")
@click.option(
    "--force",
    type=bool,
    help="Skip safety checks and ignore cleanup errors for some tasks",
    is_flag=True,
)
@click.argument("name", type=str)
@click_option_show_hints
@click.pass_context
def remove_node(ctx: click.Context, name: str, force: bool, show_hints: bool) -> None:
    """Remove a node from the cluster."""
    deployment: MaasDeployment = ctx.obj
    client = deployment.get_client()
    jhelper = JujuHelper(deployment.juju_controller)

    preflight_checks = [
        LocalShareCheck(),
    ]
    run_preflight_checks(preflight_checks, console)

    check_plan: list[BaseStep] = [
        JujuLoginStep(deployment.juju_account),
        CheckCinderVolumeDistributionStep(
            client,
            name,
            jhelper,
            deployment.openstack_machines_model,
            force=force,
        ),
        CheckMicrocephDistributionStep(
            client,
            name,
            jhelper,
            deployment.openstack_machines_model,
            force=force,
        ),
        CheckMysqlK8SDistributionStep(
            client,
            name,
            jhelper,
            deployment.openstack_machines_model,
            force=force,
        ),
        CheckOvnK8SDistributionStep(
            client,
            name,
            jhelper,
            deployment.openstack_machines_model,
            force=force,
        ),
        CheckRabbitmqK8SDistributionStep(
            client,
            name,
            jhelper,
            deployment.openstack_machines_model,
            force=force,
        ),
    ]

    run_plan(check_plan, console, show_hints)

    plan = [
        MigrateK8SKubeconfigStep(
            client, name, jhelper, deployment.openstack_machines_model
        ),
        UpdateK8SCloudStep(deployment, jhelper),
        RemoveHypervisorUnitStep(
            client, name, jhelper, deployment.openstack_machines_model, force
        ),
        RemoveCinderVolumeUnitsStep(
            client, name, jhelper, deployment.openstack_machines_model
        ),
        RemoveMicrocephUnitsStep(
            client, name, jhelper, deployment.openstack_machines_model
        ),
        CordonK8SUnitStep(client, name, jhelper, deployment.openstack_machines_model),
        DrainK8SUnitStep(
            client, name, jhelper, deployment.openstack_machines_model, remove_pvc=True
        ),
        RemoveK8SUnitsStep(client, name, jhelper, deployment.openstack_machines_model),
        EnsureL2AdvertisementByHostStep(
            deployment,
            client,
            jhelper,
            deployment.openstack_machines_model,
            Networks.INTERNAL,
            deployment.internal_ip_pool,
        ),
        EnsureL2AdvertisementByHostStep(
            deployment,
            client,
            jhelper,
            deployment.openstack_machines_model,
            Networks.PUBLIC,
            deployment.public_ip_pool,
        ),
        RemoveSunbeamMachineUnitsStep(
            client, name, jhelper, deployment.openstack_machines_model
        ),
        RemoveJujuMachineStep(
            client, name, jhelper, deployment.openstack_machines_model
        ),
        MaasRemoveMachineFromClusterdStep(client, name),
        SetCephMgrPoolSizeStep(client, jhelper, deployment.openstack_machines_model),
    ]

    run_plan(plan, console, show_hints)
    click.echo(
        f"Removed node {name} from the cluster."
        " Run `sunbeam cluster resize` to scale down the cluster"
    )


@click.command("destroy")
@click.option(
    "--no-prompt",
    is_flag=True,
    help="Do not prompt for confirmation.",
)
@click.option(
    "-m",
    "--manifest",
    "manifest_path",
    help="Manifest file.",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
)
@click.pass_context
def destroy_deployment_cmd(
    ctx: click.Context,
    no_prompt: bool,
    manifest_path: Path | None,
) -> None:
    """Destroy deployment.

    Destroy the deployment, leaving the deployment on a clean slate to
    be re-deployed as-is.
    """
    if not no_prompt:
        click.confirm("This will destroy the deployment. Are you sure?", abort=True)
    preflight_checks = [
        JujuSnapCheck(),
        LocalShareCheck(),
    ]
    run_preflight_checks(preflight_checks, console)
    deployments = DeploymentsConfig.load(deployment_path(Snap()))
    deployment: MaasDeployment = ctx.obj
    plan = []

    client = None
    try:
        client = deployment.get_client()
    except ValueError:
        pass

    if client:
        client.cluster.timeout = 5
        try:
            client.cluster.get_config("test")
        except ConfigItemNotFoundException:
            clusterd_up = True
            # Back to default timeout
            client.cluster.timeout = None
        except Exception as e:
            if "timeout" in str(e):
                LOG.debug("Cluster is not reachable, skipping cleanup")
                client.cluster.timeout = 0.1
            else:
                raise e

    jhelper = None
    manifest = deployment.get_manifest(manifest_path)
    try:
        jhelper = JujuHelper(deployment.juju_controller)
        hypervisor_tfhelper = deployment.get_tfhelper("hypervisor-plan")
        sunbeam_machine_tfhelper = deployment.get_tfhelper("sunbeam-machine-plan")

        openstack_tfhelper = deployment.get_tfhelper("openstack-plan")
        microceph_tfhelper = deployment.get_tfhelper("microceph-plan")
        cinder_volume_tfhelper = deployment.get_tfhelper("cinder-volume-plan")
        k8s_tfhelper = deployment.get_tfhelper("k8s-plan")
        if client and clusterd_up:
            # note(gboutry): can't use terraform if no clusterd is up
            plan.extend(
                [
                    DisableEnabledFeatures(deployment, no_prompt),
                    TerraformInitStep(hypervisor_tfhelper),
                    DestroyHypervisorApplicationStep(
                        client,
                        hypervisor_tfhelper,
                        jhelper,
                        manifest,
                        deployment.openstack_machines_model,
                    ),
                    TerraformInitStep(cinder_volume_tfhelper),
                    DestroyCinderVolumeApplicationStep(
                        client,
                        cinder_volume_tfhelper,
                        jhelper,
                        manifest,
                        deployment.openstack_machines_model,
                    ),
                    TerraformInitStep(openstack_tfhelper),
                    DestroyControlPlaneStep(deployment, openstack_tfhelper, jhelper),
                    RemoveSaasApplicationsStep(
                        jhelper, deployment.openstack_machines_model, OPENSTACK_MODEL
                    ),
                    TerraformInitStep(microceph_tfhelper),
                    DestroyMicrocephApplicationStep(
                        client,
                        microceph_tfhelper,
                        jhelper,
                        manifest,
                        deployment.openstack_machines_model,
                    ),
                    TerraformInitStep(k8s_tfhelper),
                    DestroyK8SApplicationStep(
                        client,
                        k8s_tfhelper,
                        jhelper,
                        manifest,
                        deployment.openstack_machines_model,
                    ),
                    TerraformInitStep(sunbeam_machine_tfhelper),
                    DestroySunbeamMachineApplicationStep(
                        client,
                        sunbeam_machine_tfhelper,
                        jhelper,
                        manifest,
                        deployment.openstack_machines_model,
                    ),
                ]
            )
        plan.extend(
            [
                DestroyJujuModelStep(jhelper, deployment.openstack_machines_model),
                DestroyJujuModelStep(jhelper, deployment.infra_model),
            ]
        )
    except (ValueError, TimeoutError):
        LOG.debug(
            "Failed to initialize different helpers,"
            " has juju controller already been destroyed?",
            exc_info=True,
        )
    plan.append(
        DestroyJujuControllerStep(
            deployment,
            manifest.core.software.juju.destroy_args,
        ),
    )
    plan.append(
        MaasRemoveDeploymentCredentialsStep(deployment.name, deployments),
    )
    plan.append(CleanTerraformPlansStep(deployment))
    run_plan(plan, console, no_hint=False)
    click.echo("Deployment destroyed.")


@click.command("sriov")
@click.option("-a", "--accept-defaults", help="Accept all defaults.", is_flag=True)
@click.option(
    "-m",
    "--manifest",
    "manifest_path",
    help="Manifest file.",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
)
@click_option_show_hints
@click.pass_context
def configure_sriov(
    ctx: click.Context,
    manifest_path: Path | None = None,
    accept_defaults: bool = False,
    show_hints: bool = False,
) -> None:
    """Configure SR-IOV."""
    deployment: MaasDeployment = ctx.obj
    client = deployment.get_client()
    manifest = deployment.get_manifest(manifest_path)
    jhelper = JujuHelper(deployment.juju_controller)

    admin_credentials = retrieve_admin_credentials(jhelper, OPENSTACK_MODEL)
    admin_credentials["OS_INSECURE"] = "true"

    tfhelper_hypervisor = deployment.get_tfhelper("hypervisor-plan")

    plan: list[BaseStep] = [
        MaasConfigSRIOVStep(
            deployment,
            client,
            jhelper,
            deployment.openstack_machines_model,
            manifest,
            accept_defaults,
        ),
        ReapplyHypervisorTerraformPlanStep(
            client,
            tfhelper_hypervisor,
            jhelper,
            manifest,
            model=deployment.openstack_machines_model,
        ),
    ]
    run_plan(plan, console, show_hints)
