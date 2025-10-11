# SPDX-FileCopyrightText: 2024 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import logging
from pathlib import Path
from typing import Tuple, Type

import click
import yaml
from click.core import ParameterSource
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
    TerraformDemoInitStep,
    UserOpenRCStep,
    UserQuestions,
    get_external_network_configs,
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
    LxdGroupCheck,
    LXDJujuControllerRegistrationCheck,
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
    click_option_database,
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
)
from sunbeam.core.k8s import K8S_CLOUD_SUFFIX
from sunbeam.core.manifest import AddManifestStep, Manifest
from sunbeam.core.openstack import OPENSTACK_MODEL
from sunbeam.core.questions import get_stdin_reopen_tty
from sunbeam.core.terraform import TerraformInitStep
from sunbeam.provider.base import ProviderBase
from sunbeam.provider.local.deployment import LOCAL_TYPE, LocalDeployment
from sunbeam.provider.local.steps import (
    LocalClusterStatusStep,
    LocalConfigDPDKStep,
    LocalConfigSRIOVStep,
    LocalConfigureOpenStackNetworkAgentsStep,
    LocalEndpointsConfigurationStep,
    LocalSetHypervisorUnitsOptionsStep,
)
from sunbeam.steps import cluster_status
from sunbeam.steps.bootstrap_state import SetBootstrapped
from sunbeam.steps.cinder_volume import (
    CheckCinderVolumeDistributionStep,
    DeployCinderVolumeApplicationStep,
    RemoveCinderVolumeUnitsStep,
)
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
    DeployHypervisorApplicationStep,
    ReapplyHypervisorOptionalIntegrationsStep,
    ReapplyHypervisorTerraformPlanStep,
    RemoveHypervisorUnitStep,
)
from sunbeam.steps.juju import (
    AddCloudJujuStep,
    AddCredentialsJujuStep,
    AddJujuMachineStep,
    AddJujuModelStep,
    AddJujuSpaceStep,
    BackupBootstrapUserStep,
    BootstrapJujuStep,
    CheckJujuReachableStep,
    CreateJujuUserStep,
    JujuGrantModelAccessStep,
    JujuLoginStep,
    MigrateModelStep,
    RegisterJujuUserStep,
    RemoveJujuMachineStep,
    SaveControllerStep,
    SaveJujuAdminUserLocallyStep,
    SaveJujuRemoteUserLocallyStep,
    SaveJujuUserLocallyStep,
    SwitchToController,
    UpdateJujuModelConfigStep,
)
from sunbeam.steps.k8s import (
    AddK8SCloudInClientStep,
    AddK8SCloudStep,
    AddK8SCredentialStep,
    CheckMysqlK8SDistributionStep,
    CheckOvnK8SDistributionStep,
    CheckRabbitmqK8SDistributionStep,
    CordonK8SUnitStep,
    DeployK8SApplicationStep,
    DrainK8SUnitStep,
    EnsureDefaultL2AdvertisementMutedStep,
    EnsureK8SUnitsTaggedStep,
    EnsureL2AdvertisementByHostStep,
    MigrateK8SKubeconfigStep,
    PatchCoreDNSStep,
    PatchServiceExternalTrafficStep,
    RemoveK8SUnitsStep,
    StoreK8SKubeConfigStep,
    UpdateK8SCloudStep,
)
from sunbeam.steps.microceph import (
    CheckMicrocephDistributionStep,
    ConfigureMicrocephOSDStep,
    DeployMicrocephApplicationStep,
    RemoveMicrocephUnitsStep,
)
from sunbeam.steps.microovn import (
    DeployMicroOVNApplicationStep,
    ReapplyMicroOVNOptionalIntegrationsStep,
    RemoveMicroOVNUnitsStep,
)
from sunbeam.steps.openstack import (
    DeployControlPlaneStep,
    OpenStackPatchLoadBalancerServicesIPStep,
    PromptDatabaseTopologyStep,
    PromptRegionStep,
    ReapplyOpenStackTerraformPlanStep,
)
from sunbeam.steps.sso import (
    DeployIdentityProvidersStep,
    SetKeystoneSAMLCertAndKeyStep,
    ValidateIdentityManifest,
)
from sunbeam.steps.sunbeam_machine import (
    DeploySunbeamMachineApplicationStep,
    RemoveSunbeamMachineUnitsStep,
)
from sunbeam.utils import (
    CatchGroup,
    click_option_show_hints,
)

LOG = logging.getLogger(__name__)
console = Console()
DEPLOYMENTS_CONFIG_KEY = "deployments"
DEFAULT_LXD_CLOUD = "localhost"


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
        configure.add_command(configure_sriov)
        configure.add_command(configure_dpdk)
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


def get_sunbeam_machine_plans(
    deployment: Deployment, jhelper: JujuHelper, manifest: Manifest
) -> list[BaseStep]:
    plans: list[BaseStep] = []
    client = deployment.get_client()
    proxy_settings = deployment.get_proxy_settings()

    sunbeam_machine_tfhelper = deployment.get_tfhelper("sunbeam-machine-plan")
    plans.extend(
        [
            TerraformInitStep(sunbeam_machine_tfhelper),
            DeploySunbeamMachineApplicationStep(
                deployment,
                client,
                sunbeam_machine_tfhelper,
                jhelper,
                manifest,
                deployment.openstack_machines_model,
                proxy_settings=proxy_settings,
            ),
        ]
    )

    return plans


def get_k8s_plans(
    deployment: Deployment,
    jhelper: JujuHelper,
    manifest: Manifest,
    accept_defaults: bool,
) -> list[BaseStep]:
    plans: list[BaseStep] = []
    client = deployment.get_client()
    fqdn = utils.get_fqdn()

    k8s_tfhelper = deployment.get_tfhelper("k8s-plan")
    plans.extend(
        [
            TerraformInitStep(k8s_tfhelper),
            DeployK8SApplicationStep(
                deployment,
                client,
                k8s_tfhelper,
                jhelper,
                manifest,
                deployment.openstack_machines_model,
                accept_defaults=accept_defaults,
            ),
            StoreK8SKubeConfigStep(
                deployment, client, jhelper, deployment.openstack_machines_model
            ),
            EnsureK8SUnitsTaggedStep(
                deployment,
                client,
                jhelper,
                deployment.openstack_machines_model,
                fqdn,
            ),
            EnsureL2AdvertisementByHostStep(
                deployment,
                client,
                jhelper,
                deployment.openstack_machines_model,
                Networks.MANAGEMENT,
                deployment.internal_ip_pool,
                fqdn,
            ),
            EnsureDefaultL2AdvertisementMutedStep(deployment, client, jhelper),
            PatchCoreDNSStep(deployment, jhelper),
        ]
    )

    return plans


def get_juju_controller_plans(
    deployment: Deployment,
    controller: str,
    data_location: Path,
    external_controller: bool = False,
) -> list[BaseStep]:
    """Get Juju controller related plans.

    Plans include the following:
    * Add cloud to juju controller
    * Add credentials if required to juju controller
    * Update Juju controller details to cluster db
    * Save Juju credentials locally
    """
    client = deployment.get_client()
    controller_ip = JujuStepHelper().get_controller_ip(controller)
    cloud_definition = JujuHelper.manual_cloud(deployment.name, controller_ip)
    credential_definition = JujuHelper.empty_credential(deployment.name)

    if external_controller:
        return [
            AddCloudJujuStep(deployment.name, cloud_definition, controller),
            # Not creating Juju user in external controller case because of juju bug
            # https://bugs.launchpad.net/juju/+bug/2073741
            ClusterUpdateJujuControllerStep(client, deployment.controller, True),
            SaveJujuRemoteUserLocallyStep(controller, data_location),
        ]
    else:
        return [
            AddCloudJujuStep(deployment.name, cloud_definition, controller),
            AddCredentialsJujuStep(
                deployment.name, "empty-creds", credential_definition, controller
            ),
            BackupBootstrapUserStep("admin", data_location),
            ClusterUpdateJujuControllerStep(client, controller, False),
            SaveJujuAdminUserLocallyStep(controller, data_location),
        ]


def get_juju_model_machine_plans(
    deployment: Deployment,
    jhelper: JujuHelper,
    local_management_ip: str,
    credential_name: str | None,
    manifest: Manifest,
) -> list[BaseStep]:
    """Get Juju model and machine related plans."""
    proxy_settings = deployment.get_proxy_settings()
    model = deployment.openstack_machines_model
    bootstrap_model_config = manifest.core.software.juju.bootstrap_model_configs.get(
        model
    )
    return [
        AddJujuModelStep(
            jhelper,
            model,
            deployment.name,
            credential_name,
            proxy_settings,
            bootstrap_model_config,
        ),
        AddJujuMachineStep(local_management_ip, model, jhelper),
    ]


def get_juju_spaces_plans(
    deployment: Deployment,
    jhelper: JujuHelper,
    management_cidr: str,
) -> list[BaseStep]:
    """Get Juju spaces related plans."""
    return [
        AddJujuSpaceStep(
            jhelper,
            deployment.openstack_machines_model,
            deployment.get_space(Networks.MANAGEMENT),
            [management_cidr],
        ),
        UpdateJujuModelConfigStep(
            jhelper,
            deployment.openstack_machines_model,
            {
                "default-space": deployment.get_space(Networks.MANAGEMENT),
            },
        ),
    ]


def get_juju_migrate_plans(
    deployment: Deployment,
    from_controller: str,
    to_controller: str,
    data_location: Path,
) -> list[BaseStep]:
    """Get Juju migrate related plans."""
    client = deployment.get_client()
    controller_ip = JujuStepHelper().get_controller_ip(to_controller)
    cloud_definition = JujuHelper.manual_cloud(deployment.name, controller_ip)
    credential_definition = JujuHelper.empty_credential(deployment.name)

    return [
        SwitchToController(deployment.controller),
        AddCloudJujuStep(deployment.name, cloud_definition, deployment.controller),
        AddCredentialsJujuStep(
            deployment.name,
            "empty-creds",
            credential_definition,
            deployment.controller,
        ),
        MigrateModelStep(
            deployment.openstack_machines_model,
            from_controller,
            to_controller,
        ),
        ClusterUpdateJujuControllerStep(client, deployment.controller, False),
        SaveJujuAdminUserLocallyStep(deployment.controller, data_location),
    ]


def get_juju_user_plans(
    deployment: Deployment,
    jhelper: JujuHelper,
    data_location: Path,
    token: str,
) -> list[BaseStep]:
    """Get Juju User related plans."""
    client = deployment.get_client()
    fqdn = utils.get_fqdn()

    return [
        ClusterAddJujuUserStep(client, fqdn, token),
        JujuGrantModelAccessStep(jhelper, fqdn, deployment.openstack_machines_model),
        BackupBootstrapUserStep(fqdn, data_location),
        SaveJujuUserLocallyStep(fqdn, data_location),
        RegisterJujuUserStep(
            client, fqdn, deployment.controller, data_location, replace=True
        ),
    ]


def get_juju_bootstrap_plans(
    deployment: Deployment,
    bootstrap_args: list,
):
    """Get Juju Bootstrap related plans."""
    client = deployment.get_client()
    proxy_settings = deployment.get_proxy_settings()

    # If the secret backend is left to defaults i.e., auto, the secret backend
    # is set to k8s if the controller is k8s based and non-k8s machines cannot
    # get secrets as they try to connect to k8s on service_ip which is not
    # reachable from non-k8s machines.
    # https://bugs.launchpad.net/snap-openstack/+bug/2091724
    # To avoid the bug, change the default secret backend to internal
    bootstrap_args.extend(
        [
            "--config",
            "controller-service-type=loadbalancer",
            "--model-default=secret-backend=internal",
        ]
    )

    return [
        AddK8SCloudInClientStep(deployment),
        BootstrapJujuStep(
            client,
            f"{deployment.name}{K8S_CLOUD_SUFFIX}",
            "k8s",
            deployment.controller,
            bootstrap_args=bootstrap_args,
            proxy_settings=proxy_settings,
        ),
        # note(gboutry): workaround for LP#2111922
        # Find more permanent solution when juju supports HA on k8s
        PatchServiceExternalTrafficStep(
            deployment,
            "controller-service",
            "controller-" + deployment.controller,
        ),
    ]


def deploy_and_migrate_juju_controller(
    deployment: LocalDeployment,
    manifest: Manifest,
    local_management_ip: str,
    management_cidr: str,
    data_location: Path,
    accept_defaults: bool,
    show_hints: bool = False,
):
    """Deploy LXD controller and migrate to k8s."""
    plan1: list[BaseStep] = []
    plan2: list[BaseStep] = []
    plan3: list[BaseStep] = []
    plan4: list[BaseStep] = []
    plan5: list[BaseStep] = []
    plan6: list[BaseStep] = []
    plan7: list[BaseStep] = []

    client = deployment.get_client()
    fqdn = utils.get_fqdn()
    juju_bootstrap_args = []
    if manifest.core.software.juju.bootstrap_args:
        juju_bootstrap_args = manifest.core.software.juju.bootstrap_args.copy()

    # Atmost one controller will be returned as one cloud is passed as argument.
    # lxd_controllers cannot be empty list since this is verified in preflight check.
    lxd_controllers = JujuStepHelper().get_controllers([DEFAULT_LXD_CLOUD])
    lxd_controller = lxd_controllers[0]

    if not client.cluster.check_juju_controller_migrated():
        plan1 = get_juju_controller_plans(
            deployment, lxd_controller, data_location, external_controller=False
        )
        run_plan(plan1, console, show_hints)

    # Reload deployment with lxd controller admin user credentials
    deployment.reload_credentials()
    jhelper = JujuHelper(deployment.juju_controller)

    plan2 = get_juju_model_machine_plans(
        deployment, jhelper, local_management_ip, "empty-creds", manifest
    )
    run_plan(plan2, console, show_hints)

    plan3 = get_juju_spaces_plans(deployment, jhelper, management_cidr)
    plan3.extend(get_sunbeam_machine_plans(deployment, jhelper, manifest))
    plan3.extend(get_k8s_plans(deployment, jhelper, manifest, accept_defaults))
    run_plan(plan3, console, show_hints)
    # Disconnect all pylibjuju connections before bootstrapping new controller

    plan4 = get_juju_bootstrap_plans(deployment, juju_bootstrap_args)
    run_plan(plan4, console, show_hints)

    plan5 = get_juju_migrate_plans(
        deployment, lxd_controller, deployment.controller, data_location
    )
    run_plan(plan5, console, show_hints)
    client.cluster.set_juju_controller_migrated()

    # Reload deployment with sunbeam-controller admin user credentials
    deployment.reload_credentials()
    jhelper = JujuHelper(deployment.juju_controller)

    plan6 = [
        CreateJujuUserStep(fqdn),
    ]
    plan6_results = run_plan(plan6, console, show_hints)
    token = get_step_message(plan6_results, CreateJujuUserStep)

    plan7 = get_juju_user_plans(deployment, jhelper, data_location, token)
    run_plan(plan7, console, show_hints)


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
    help="Specify additional roles, compute, storage or network, for the "
    "bootstrap node. Defaults to the compute role."
    " Can be repeated and comma separated.",
)
@click_option_topology
@click_option_database
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
    roles: list[Role],
    topology: str,
    database: str,
    juju_controller: str | None = None,
    manifest_path: Path | None = None,
    accept_defaults: bool = False,
    show_hints: bool = False,
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

    parameter_source = click.get_current_context().get_parameter_source("database")
    if parameter_source == ParameterSource.COMMANDLINE:
        LOG.warning(
            "WARNING: Option --database is deprecated and the value is ignored. "
            "Instead user is prompted to select the database topology. "
            "The database topology can also be set via manifest."
        )

    LOG.debug(f"Manifest used for deployment - core: {manifest.core}")
    LOG.debug(f"Manifest used for deployment - features: {manifest.features}")

    # Bootstrap node must always have the control role
    if Role.CONTROL not in roles:
        LOG.debug("Enabling control role for bootstrap")
        roles.append(Role.CONTROL)
    is_control_node = any(role.is_control_node() for role in roles)
    is_compute_node = any(role.is_compute_node() for role in roles)
    is_storage_node = any(role.is_storage_node() for role in roles)
    is_network_node = any(role.is_network_node() for role in roles)

    if is_network_node and is_compute_node:
        raise click.ClickException(
            "A node cannot be both a compute and network node at the same time."
        )

    fqdn = utils.get_fqdn()

    roles_str = ",".join(role.name for role in roles)
    pretty_roles = ", ".join(role.name.lower() for role in roles)
    LOG.debug(f"Bootstrap node: roles {roles_str}")

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
    else:
        preflight_checks.append(LxdGroupCheck())
        preflight_checks.append(LXDJujuControllerRegistrationCheck())

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
    results = run_plan(cidr_plan, console, show_hints)
    management_cidr = get_step_message(results, AskManagementCidrStep)

    try:
        local_management_ip = _resolve_local_ip_from_cidr(management_cidr)
    except ValueError as e:
        LOG.error(f"Error resolving local management ip from cidr: {e}")
        raise click.ClickException("Cannot resolve local management ip") from e

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
    plan.append(PromptDatabaseTopologyStep(client, manifest, accept_defaults))
    plan.append(PromptRegionStep(client, manifest, accept_defaults))
    plan.append(ValidateIdentityManifest(client, manifest))
    run_plan(plan, console, show_hints)

    update_config(client, DEPLOYMENTS_CONFIG_KEY, deployments.get_minimal_info())
    proxy_settings = deployment.get_proxy_settings()
    LOG.debug(f"Proxy settings: {proxy_settings}")

    if juju_controller:
        plan11: list[BaseStep] = []
        plan12: list[BaseStep] = []
        plan13: list[BaseStep] = []

        plan11 = get_juju_controller_plans(
            deployment, juju_controller, data_location, external_controller=True
        )
        run_plan(plan11, console, show_hints)

        deployment.reload_credentials()
        jhelper = JujuHelper(deployment.juju_controller)

        plan12 = get_juju_model_machine_plans(
            deployment, jhelper, local_management_ip, None, manifest
        )
        run_plan(plan12, console, show_hints)

        plan13 = get_juju_spaces_plans(deployment, jhelper, management_cidr)
        plan13.extend(get_sunbeam_machine_plans(deployment, jhelper, manifest))
        plan13.extend(get_k8s_plans(deployment, jhelper, manifest, accept_defaults))
        plan13.append(AddK8SCloudStep(deployment, jhelper))
        run_plan(plan13, console, show_hints)
    else:
        plan21: list[BaseStep] = []

        deploy_and_migrate_juju_controller(
            deployment,
            manifest,
            local_management_ip,
            management_cidr,
            data_location,
            accept_defaults,
            show_hints,
        )

        # Reload deployment with sunbeam-controller {fqdn} user credentials
        deployment.reload_credentials()
        deployments.update_deployment(deployment)
        deployment.reload_tfhelpers()
        jhelper = JujuHelper(deployment.juju_controller)

        plan21.append(AddK8SCredentialStep(deployment, jhelper))
        run_plan(plan21, console, show_hints)

    plan1: list[BaseStep] = []
    # Deploy Microceph application during bootstrap irrespective of node role.
    microceph_tfhelper = deployment.get_tfhelper("microceph-plan")
    plan1.append(TerraformInitStep(microceph_tfhelper))
    plan1.append(
        DeployMicrocephApplicationStep(
            deployment,
            client,
            microceph_tfhelper,
            jhelper,
            manifest,
            deployment.openstack_machines_model,
        )
    )
    cinder_volume_tfhelper = deployment.get_tfhelper("cinder-volume-plan")
    plan1.append(TerraformInitStep(cinder_volume_tfhelper))
    plan1.append(
        DeployCinderVolumeApplicationStep(
            deployment,
            client,
            cinder_volume_tfhelper,
            jhelper,
            manifest,
            deployment.openstack_machines_model,
        )
    )

    openstack_tfhelper = deployment.get_tfhelper("openstack-plan")
    plan1.append(TerraformInitStep(openstack_tfhelper))

    if is_storage_node:
        plan1.append(
            ConfigureMicrocephOSDStep(
                client,
                fqdn,
                jhelper,
                deployment.openstack_machines_model,
                accept_defaults=accept_defaults,
                manifest=manifest,
            )
        )

    if is_control_node:
        plan1.append(
            LocalEndpointsConfigurationStep(
                client,
                manifest,
                accept_defaults=accept_defaults,
            )
        )
        plan1.append(
            DeployControlPlaneStep(
                deployment,
                openstack_tfhelper,
                jhelper,
                manifest,
                topology,
                deployment.openstack_machines_model,
                proxy_settings=proxy_settings,
            )
        )
        plan1.append(
            SetKeystoneSAMLCertAndKeyStep(
                deployment=deployment,
                tfhelper=openstack_tfhelper,
                jhelper=jhelper,
                manifest=manifest,
            )
        )
        plan1.append(
            DeployIdentityProvidersStep(
                deployment,
                openstack_tfhelper,
                jhelper,
                manifest,
            )
        )
        # Redeploy of Microceph is required to fill terraform vars
        # related to traefik-rgw/keystone-endpoints offers from
        # openstack model
        plan1.append(
            DeployMicrocephApplicationStep(
                deployment,
                client,
                microceph_tfhelper,
                jhelper,
                manifest,
                deployment.openstack_machines_model,
            )
        )
        # Fill AMQP / Keystone / MySQL offers from openstack model
        plan1.append(
            DeployCinderVolumeApplicationStep(
                deployment,
                client,
                cinder_volume_tfhelper,
                jhelper,
                manifest,
                deployment.openstack_machines_model,
            )
        )

    run_plan(plan1, console, show_hints)

    plan2: list[BaseStep] = []

    if is_control_node:
        plan2.append(OpenStackPatchLoadBalancerServicesIPStep(client))

    # NOTE(jamespage):
    # As with MicroCeph, always deploy the openstack-hypervisor charm
    # and add a unit to the bootstrap node if required.
    hypervisor_tfhelper = deployment.get_tfhelper("hypervisor-plan")
    plan2.append(TerraformInitStep(hypervisor_tfhelper))
    plan2.append(
        DeployHypervisorApplicationStep(
            deployment,
            client,
            hypervisor_tfhelper,
            openstack_tfhelper,
            cinder_volume_tfhelper,
            jhelper,
            manifest,
            deployment.openstack_machines_model,
        )
    )
    if is_network_node:
        microovn_tfhelper = deployment.get_tfhelper("microovn-plan")
        plan2.append(TerraformInitStep(microovn_tfhelper))
        plan2.append(
            DeployMicroOVNApplicationStep(
                deployment,
                client,
                microovn_tfhelper,
                jhelper,
                manifest,
                deployment.openstack_machines_model,
            )
        )
        plan2.append(
            ReapplyMicroOVNOptionalIntegrationsStep(
                deployment,
                client,
                microovn_tfhelper,
                jhelper,
                manifest,
                deployment.openstack_machines_model,
            )
        )

    plan2.append(SetBootstrapped(client))
    run_plan(plan2, console, show_hints)

    click.echo(f"Node has been bootstrapped with roles: {pretty_roles}")


@click.command("sriov")
@click.option("-a", "--accept-defaults", help="Accept all defaults.", is_flag=True)
@click.option(
    "--clear-previous-config",
    help="Do not merge the new settings with the previous answers.",
    is_flag=True,
)
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
    clear_previous_config: bool = False,
) -> None:
    """Configure SR-IOV."""
    deployment: LocalDeployment = ctx.obj
    client = deployment.get_client()
    fqdn = utils.get_fqdn()

    node = client.cluster.get_node_info(fqdn)

    if "compute" not in node["role"]:
        LOG.info("SR-IOV can only be configured on compute nodes.")
        return

    manifest = deployment.get_manifest(manifest_path)
    jhelper = JujuHelper(deployment.juju_controller)

    admin_credentials = retrieve_admin_credentials(jhelper, OPENSTACK_MODEL)
    admin_credentials["OS_INSECURE"] = "true"

    tfhelper_hypervisor = deployment.get_tfhelper("hypervisor-plan")
    tfhelper_openstack = deployment.get_tfhelper("openstack-plan")

    plan: list[BaseStep] = [
        LocalConfigSRIOVStep(
            client,
            fqdn,
            jhelper,
            deployment.openstack_machines_model,
            manifest,
            accept_defaults,
            show_initial_prompt=False,
            clear_previous_config=clear_previous_config,
        ),
        ReapplyHypervisorTerraformPlanStep(
            client,
            tfhelper_hypervisor,
            jhelper,
            manifest,
            model=deployment.openstack_machines_model,
        ),
    ]
    if manifest and manifest.core.config.pci and manifest.core.config.pci.aliases:
        plan.append(
            ReapplyOpenStackTerraformPlanStep(
                client,
                tfhelper_openstack,
                jhelper,
                manifest,
            )
        )
    run_plan(plan, console, show_hints)


@click.command("dpdk")
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
def configure_dpdk(
    ctx: click.Context,
    manifest_path: Path | None = None,
    accept_defaults: bool = False,
    show_hints: bool = False,
) -> None:
    """Configure DPDK."""
    deployment: LocalDeployment = ctx.obj
    client = deployment.get_client()
    fqdn = utils.get_fqdn()

    node = client.cluster.get_node_info(fqdn)

    if "compute" not in node["role"]:
        LOG.info("DPDK can only be configured on compute nodes.")
        return

    manifest = deployment.get_manifest(manifest_path)
    jhelper = JujuHelper(deployment.juju_controller)

    admin_credentials = retrieve_admin_credentials(jhelper, OPENSTACK_MODEL)
    admin_credentials["OS_INSECURE"] = "true"

    tfhelper_hypervisor = deployment.get_tfhelper("hypervisor-plan")

    plan: list[BaseStep] = [
        LocalConfigDPDKStep(
            client,
            fqdn,
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
@click.argument("name", type=str)
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
@click_option_show_hints
@click.pass_context
def add(
    ctx: click.Context,
    name: str,
    format: str,
    output: Path | None,
    show_hints: bool,
) -> None:
    """Generate a token for a new node to join the cluster.

    NAME must be a fully qualified domain name.
    """
    preflight_checks = [DaemonGroupCheck(), VerifyFQDNCheck(name)]
    run_preflight_checks(preflight_checks, console)
    name = remove_trailing_dot(name)

    deployment: LocalDeployment = ctx.obj
    client = deployment.get_client()
    jhelper = JujuHelper(deployment.juju_controller)

    plan1: list[BaseStep] = [
        JujuLoginStep(deployment.juju_account),
        ClusterAddNodeStep(client, name),
        CreateJujuUserStep(name),
        JujuGrantModelAccessStep(jhelper, name, deployment.openstack_machines_model),
        JujuGrantModelAccessStep(jhelper, name, OPENSTACK_MODEL),
    ]

    plan1_results = run_plan(plan1, console, show_hints)

    user_token = get_step_message(plan1_results, CreateJujuUserStep)

    plan2 = [ClusterAddJujuUserStep(client, name, user_token)]
    run_plan(plan2, console, show_hints)

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
@click.argument("token", type=str)
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
@click_option_show_hints
@click.pass_context
def join(
    ctx: click.Context,
    token: str,
    roles: list[Role],
    accept_defaults: bool = False,
    show_hints: bool = False,
) -> None:
    """Join node to the cluster.

    Join the node to the cluster.
    Use `-` as token to read from stdin.
    """
    if token == "-":
        token = get_stdin_reopen_tty()
    is_control_node = any(role.is_control_node() for role in roles)
    is_compute_node = any(role.is_compute_node() for role in roles)
    is_storage_node = any(role.is_storage_node() for role in roles)
    is_network_node = any(role.is_network_node() for role in roles)

    if is_network_node and is_compute_node:
        raise click.ClickException(
            "A node cannot be both a compute and network node at the same time."
        )

    # Register juju user with same name as Node fqdn
    name = utils.get_fqdn()

    roles_str = roles_to_str_list(roles)
    pretty_roles = ", ".join(role_.name.lower() for role_ in roles)
    LOG.debug(f"Node joining the cluster with roles: {pretty_roles}")

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

    management_cidr = None
    try:
        management_cidr = utils.get_local_cidr_matching_token(token)
    except ValueError as e:
        LOG.debug(
            "Failed to find local cidr matching join token addresses",
            exc_info=True,
        )
        raise click.ClickException(
            f"Error in resolving management CIDR: {str(e)}"
        ) from e
    ip = _resolve_local_ip_from_cidr(management_cidr)

    deployment: LocalDeployment = ctx.obj
    client = deployment.get_client()
    snap = Snap()

    data_location = snap.paths.user_data
    path = deployment_path(snap)
    deployments = DeploymentsConfig.load(path)

    plan1 = [ClusterJoinNodeStep(client, token, ip, name, roles_str)]
    run_plan(plan1, console, show_hints)

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
        CheckJujuReachableStep(deployment.juju_controller),
        JujuLoginStep(deployment.juju_account),
        SaveJujuUserLocallyStep(name, data_location),
        RegisterJujuUserStep(client, name, deployment.controller, data_location),
    ]
    run_plan(plan2, console, show_hints)

    # Loads juju account
    deployment.reload_credentials()
    deployments.write()
    jhelper = JujuHelper(deployment.juju_controller)
    plan3 = [AddJujuMachineStep(ip, deployment.openstack_machines_model, jhelper)]
    plan3_results = run_plan(plan3, console, show_hints)

    deployment.reload_credentials()
    # Get manifest object once the cluster is joined
    manifest = deployment.get_manifest()
    proxy_settings = deployment.get_proxy_settings()
    sunbeam_machine_tfhelper = deployment.get_tfhelper("sunbeam-machine-plan")
    k8s_tfhelper = deployment.get_tfhelper("k8s-plan")
    cinder_volume_tfhelper = deployment.get_tfhelper("cinder-volume-plan")
    microceph_tfhelper = deployment.get_tfhelper("microceph-plan")
    openstack_tfhelper = deployment.get_tfhelper("openstack-plan")
    hypervisor_tfhelper = deployment.get_tfhelper("hypervisor-plan")

    machine_id = -1
    machine_id_result = get_step_message(plan3_results, AddJujuMachineStep)
    if machine_id_result is not None:
        machine_id = int(machine_id_result)

    plan4: list[BaseStep] = []
    plan4.append(ClusterUpdateNodeStep(client, name, machine_id=machine_id))
    plan4.append(TerraformInitStep(sunbeam_machine_tfhelper))
    plan4.append(
        DeploySunbeamMachineApplicationStep(
            deployment,
            client,
            sunbeam_machine_tfhelper,
            jhelper,
            manifest,
            deployment.openstack_machines_model,
            proxy_settings=proxy_settings,
        )
    )

    if is_control_node:
        # accept_defaults True to pick from manifest saved ones??
        plan4.append(TerraformInitStep(k8s_tfhelper))
        plan4.append(
            DeployK8SApplicationStep(
                deployment,
                client,
                k8s_tfhelper,
                jhelper,
                manifest,
                deployment.openstack_machines_model,
                accept_defaults=True,
                refresh=True,
            )
        )
        plan4.append(
            EnsureK8SUnitsTaggedStep(
                deployment,
                client,
                jhelper,
                deployment.openstack_machines_model,
                name,
            )
        )
        plan4.append(EnsureDefaultL2AdvertisementMutedStep(deployment, client, jhelper))
        plan4.append(
            EnsureL2AdvertisementByHostStep(
                deployment,
                client,
                jhelper,
                deployment.openstack_machines_model,
                Networks.MANAGEMENT,
                deployment.internal_ip_pool,
                name,
            ),
        )
        plan4.append(AddK8SCredentialStep(deployment, jhelper))

    plan4.append(TerraformInitStep(openstack_tfhelper))
    plan4.append(TerraformInitStep(hypervisor_tfhelper))
    plan4.append(TerraformInitStep(cinder_volume_tfhelper))
    microovn_tfhelper = deployment.get_tfhelper("microovn-plan")
    if is_network_node:
        microovn_tfhelper = deployment.get_tfhelper("microovn-plan")
        plan4.append(TerraformInitStep(microovn_tfhelper))
        plan4.append(
            DeployMicroOVNApplicationStep(
                deployment,
                client,
                microovn_tfhelper,
                jhelper,
                manifest,
                deployment.openstack_machines_model,
            )
        )
        plan4.append(
            ReapplyMicroOVNOptionalIntegrationsStep(
                deployment,
                client,
                microovn_tfhelper,
                jhelper,
                manifest,
                deployment.openstack_machines_model,
            )
        )

    if is_storage_node:
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
        plan4.append(
            DeployCinderVolumeApplicationStep(
                deployment,
                client,
                cinder_volume_tfhelper,
                jhelper,
                manifest,
                deployment.openstack_machines_model,
            )
        )

        # Re-deploy control plane if this is the first storage node joining
        # the cluster to enable mandatory storage services
        storage_nodes = client.cluster.list_nodes_by_role(Role.STORAGE.name.lower())
        if len(storage_nodes) == 1:
            plan4.append(
                DeployControlPlaneStep(
                    deployment,
                    openstack_tfhelper,
                    jhelper,
                    manifest,
                    "auto",
                    deployment.openstack_machines_model,
                )
            )

            # Redeploy of Microceph is required to fill terraform vars
            # related to traefik-rgw/keystone-endpoints offers from
            # openstack model
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
            # Fill AMQP / Keystone / MySQL offers from openstack model
            plan4.append(
                DeployCinderVolumeApplicationStep(
                    deployment,
                    client,
                    cinder_volume_tfhelper,
                    jhelper,
                    manifest,
                    deployment.openstack_machines_model,
                )
            )

        plan4.append(
            ReapplyHypervisorOptionalIntegrationsStep(
                deployment,
                client,
                hypervisor_tfhelper,
                openstack_tfhelper,
                cinder_volume_tfhelper,
                jhelper,
                manifest,
                deployment.openstack_machines_model,
            )
        )

    if is_compute_node:
        hypervisor_tfhelper = deployment.get_tfhelper("hypervisor-plan")
        plan4.extend(
            [
                TerraformInitStep(hypervisor_tfhelper),
                DeployHypervisorApplicationStep(
                    deployment,
                    client,
                    hypervisor_tfhelper,
                    openstack_tfhelper,
                    cinder_volume_tfhelper,
                    jhelper,
                    manifest,
                    deployment.openstack_machines_model,
                ),
                LocalSetHypervisorUnitsOptionsStep(
                    client,
                    name,
                    jhelper,
                    deployment.openstack_machines_model,
                    join_mode=True,
                    manifest=manifest,
                ),
                LocalConfigSRIOVStep(
                    client,
                    name,
                    jhelper,
                    deployment.openstack_machines_model,
                    manifest,
                    accept_defaults,
                ),
                LocalConfigDPDKStep(
                    client,
                    name,
                    jhelper,
                    deployment.openstack_machines_model,
                    manifest,
                    accept_defaults,
                ),
                ReapplyHypervisorTerraformPlanStep(
                    client,
                    hypervisor_tfhelper,
                    jhelper,
                    manifest,
                    model=deployment.openstack_machines_model,
                ),
            ]
        )
        if manifest and manifest.core.config.pci and manifest.core.config.pci.aliases:
            plan4.append(
                ReapplyOpenStackTerraformPlanStep(
                    client,
                    openstack_tfhelper,
                    jhelper,
                    manifest,
                )
            )

    run_plan(plan4, console, show_hints)

    click.echo(f"Node joined cluster with roles: {pretty_roles}")


def _resolve_local_ip_from_cidr(cidr: str) -> str:
    try:
        local_ip = utils.get_local_ip_by_cidr(cidr)
    except ValueError:
        LOG.debug(
            "Failed to find local address matching management CIDR %s",
            cidr,
            exc_info=True,
        )
        raise
    return local_ip


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
def list_nodes(
    ctx: click.Context,
    format: str,
    show_hints: bool,
) -> None:
    """List nodes in the cluster."""
    preflight_checks = [DaemonGroupCheck()]
    run_preflight_checks(preflight_checks, console)
    deployment: LocalDeployment = ctx.obj
    jhelper = JujuHelper(deployment.juju_controller)
    step = LocalClusterStatusStep(deployment, jhelper)
    results = run_plan([step], console, show_hints)
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
@click.argument("name", type=str)
@click_option_show_hints
@click.pass_context
def remove(ctx: click.Context, name: str, force: bool, show_hints: bool) -> None:
    """Remove a node from the cluster."""
    deployment: LocalDeployment = ctx.obj
    client = deployment.get_client()
    jhelper = JujuHelper(deployment.juju_controller)

    preflight_checks = [DaemonGroupCheck()]
    run_preflight_checks(preflight_checks, console)

    plan = [
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
        RemoveMicroOVNUnitsStep(
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
            Networks.MANAGEMENT,
            deployment.internal_ip_pool,
        ),
        RemoveSunbeamMachineUnitsStep(
            client, name, jhelper, deployment.openstack_machines_model
        ),
        RemoveJujuMachineStep(
            client, name, jhelper, deployment.openstack_machines_model
        ),
        # Cannot remove user as the same user name cannot be resued,
        # so commenting the RemoveJujuUserStep
        # RemoveJujuUserStep(name),
        ClusterRemoveNodeStep(client, name),
    ]

    run_plan(plan, console, show_hints)
    click.echo(f"Removed node {name} from the cluster")
    # Removing machine does not clean up all deployed juju components. This is
    # deliberate, see https://bugs.launchpad.net/juju/+bug/1851489.
    # Without the workaround mentioned in LP#1851489, it is not possible to
    # reprovision the machine back.
    click.echo(
        f"Run command `sudo /sbin/remove-juju-services` on node {name} "
        "to reuse the machine."
    )
    click.echo("Run `sunbeam cluster resize` to scale down the cluster")


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
@click_option_show_hints
@click.pass_context
def configure_cmd(
    ctx: click.Context,
    openrc: Path | None = None,
    manifest_path: Path | None = None,
    accept_defaults: bool = False,
    show_hints: bool = False,
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
    jhelper = JujuHelper(deployment.juju_controller)
    if not jhelper.model_exists(OPENSTACK_MODEL):
        LOG.error(f"Expected model {OPENSTACK_MODEL} missing")
        raise click.ClickException("Please run `sunbeam cluster bootstrap` first")
    # Check if the node is a network node
    node = client.cluster.get_node_info(name)

    plan = [
        AddManifestStep(client, manifest_path),
        JujuLoginStep(deployment.juju_account),
    ]
    node = client.cluster.get_node_info(name)

    if "compute" in node["role"]:
        admin_credentials = retrieve_admin_credentials(jhelper, OPENSTACK_MODEL)
        # Add OS_INSECURE as https not working with terraform openstack provider.
        admin_credentials["OS_INSECURE"] = "true"
        tfplan = "demo-setup"
        tfhelper = deployment.get_tfhelper(tfplan)
        tfhelper.env = (tfhelper.env or {}) | admin_credentials
        answer_file = tfhelper.path / "config.auto.tfvars.json"
        tfhelper_hypervisor = deployment.get_tfhelper("hypervisor-plan")
        machine_id = str(node.get("machineid"))
        jhelper.get_unit_from_machine(
            "openstack-hypervisor", machine_id, deployment.openstack_machines_model
        )
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
        plan.append(TerraformInitStep(tfhelper_hypervisor))
        plan.append(
            ReapplyHypervisorTerraformPlanStep(
                client,
                tfhelper_hypervisor,
                jhelper,
                manifest,
                model=deployment.openstack_machines_model,
            )
        )

        plan.extend(
            [
                UserQuestions(
                    client,
                    answer_file=answer_file,
                    manifest=manifest,
                    accept_defaults=accept_defaults,
                ),
                TerraformDemoInitStep(client, tfhelper),
                DemoSetup(client=client, tfhelper=tfhelper, answer_file=answer_file),
                UserOpenRCStep(
                    client=client,
                    tfhelper=tfhelper,
                    auth_url=admin_credentials["OS_AUTH_URL"],
                    auth_version=admin_credentials["OS_AUTH_VERSION"],
                    cacert=admin_credentials.get("OS_CACERT"),
                    openrc=openrc,
                ),
            ]
        )

    if "network" in node["role"]:
        # Get external network configuration from user answers
        ext_net_config = get_external_network_configs(client)
        bridge_name = ext_net_config.get("external-bridge", "br-ex")
        physnet_name = ext_net_config.get("physnet-name", "physnet1")

        plan.append(
            LocalConfigureOpenStackNetworkAgentsStep(
                client,
                name,
                jhelper,
                deployment.openstack_machines_model,
                manifest,
                accept_defaults,
                bridge_name=bridge_name,
                physnet_name=physnet_name,
                enable_chassis_as_gw=True,
            )
        )
    run_plan(plan, console, show_hints)
    dashboard_url = retrieve_dashboard_url(jhelper)
    console.print("The cloud has been configured for sample usage.")
    console.print(
        "You can start using the OpenStack client"
        f" or access the OpenStack dashboard at {dashboard_url}"
    )
