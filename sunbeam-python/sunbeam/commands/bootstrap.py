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
from rich.console import Console
from snaphelpers import Snap

from sunbeam import utils
from sunbeam.commands.clusterd import (
    ClusterAddJujuUserStep,
    ClusterInitStep,
    ClusterUpdateJujuControllerStep,
)
from sunbeam.commands.hypervisor import (
    AddHypervisorUnitStep,
    DeployHypervisorApplicationStep,
)
from sunbeam.commands.juju import (
    BackupBootstrapUserStep,
    BootstrapJujuStep,
    CreateJujuUserStep,
    JujuLoginStep,
    RegisterJujuUserStep,
    SaveJujuUserLocallyStep,
)
from sunbeam.commands.microceph import (
    AddMicrocephUnitStep,
    ConfigureMicrocephOSDStep,
    DeployMicrocephApplicationStep,
)
from sunbeam.commands.microk8s import (
    AddMicrok8sCloudStep,
    AddMicrok8sUnitStep,
    DeployMicrok8sApplicationStep,
    StoreMicrok8sConfigStep,
)
from sunbeam.commands.mysql import ConfigureMySQLStep
from sunbeam.commands.openstack import (
    DeployControlPlaneStep,
    PatchLoadBalancerServicesStep,
)
from sunbeam.commands.rocks import (
    ConfigureKubeletOptionsStep,
    ConfigurePullThroughCacheStep,
    PreseedRocksStep,
)
from sunbeam.commands.sunbeam_machine import (
    AddSunbeamMachineUnitStep,
    DeploySunbeamMachineApplicationStep,
)
from sunbeam.commands.terraform import TerraformHelper, TerraformInitStep
from sunbeam.jobs.checks import (
    DaemonGroupCheck,
    JujuSnapCheck,
    LocalShareCheck,
    SshKeysConnectedCheck,
    VerifyHypervisorHostnameCheck,
)
from sunbeam.jobs.common import (
    Role,
    click_option_topology,
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


@click.command()
@click.option("-a", "--accept-defaults", help="Accept all defaults.", is_flag=True)
@click.option(
    "-p",
    "--preseed",
    help="Preseed file.",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
)
@click.option(
    "--role",
    "roles",
    multiple=True,
    default=["control", "compute"],
    type=click.Choice(["control", "compute", "storage"], case_sensitive=False),
    callback=validate_roles,
    help="Specify whether the node will be a control node, a "
    "compute node or a storage node. Defaults to control and "
    "compute roles.",
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
def bootstrap(
    roles: List[Role],
    topology: str,
    database: str,
    preseed: Optional[Path] = None,
    accept_defaults: bool = False,
) -> None:
    """Bootstrap the local node.

    Initialize the sunbeam cluster.
    """
    is_control_node = any(role.is_control_node() for role in roles)
    is_compute_node = any(role.is_compute_node() for role in roles)
    is_storage_node = any(role.is_storage_node() for role in roles)

    fqdn = utils.get_fqdn()

    roles_str = ",".join(role.name for role in roles)
    pretty_roles = ", ".join(role.name.lower() for role in roles)
    LOG.debug(f"Bootstrap node: roles {roles_str}")

    cloud_type = snap.config.get("juju.cloud.type")
    cloud_name = snap.config.get("juju.cloud.name")

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

    preflight_checks = []
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

    plan = []
    plan.append(JujuLoginStep(data_location))
    plan.append(ClusterInitStep(roles_to_str_list(roles)))
    plan.append(
        BootstrapJujuStep(
            cloud_name,
            cloud_type,
            CONTROLLER,
            accept_defaults=accept_defaults,
            preseed_file=preseed,
        )
    )
    run_plan(plan, console)

    plan2 = []
    plan2.append(CreateJujuUserStep(fqdn))
    plan2.append(ClusterUpdateJujuControllerStep(CONTROLLER))
    plan2_results = run_plan(plan2, console)

    token = get_step_message(plan2_results, CreateJujuUserStep)

    plan3 = []
    plan3.append(ClusterAddJujuUserStep(fqdn, token))
    plan3.append(BackupBootstrapUserStep(fqdn, data_location))
    plan3.append(SaveJujuUserLocallyStep(fqdn, data_location))
    run_plan(plan3, console)

    tfhelper = TerraformHelper(
        path=snap.paths.user_common / "etc" / "deploy-microk8s",
        plan="microk8s-plan",
        backend="http",
        data_location=data_location,
    )
    tfhelper_openstack_deploy = TerraformHelper(
        path=snap.paths.user_common / "etc" / "deploy-openstack",
        plan="openstack-plan",
        backend="http",
        data_location=data_location,
    )
    tfhelper_hypervisor_deploy = TerraformHelper(
        path=snap.paths.user_common / "etc" / "deploy-openstack-hypervisor",
        plan="hypervisor-plan",
        backend="http",
        data_location=data_location,
    )
    tfhelper_microceph_deploy = TerraformHelper(
        path=snap.paths.user_common / "etc" / "deploy-microceph",
        plan="microceph-plan",
        backend="http",
        data_location=data_location,
    )
    tfhelper_sunbeam_machine = TerraformHelper(
        path=snap.paths.user_common / "etc" / "deploy-sunbeam-machine",
        plan="sunbeam-machine-plan",
        backend="http",
        data_location=data_location,
    )
    jhelper = JujuHelper(data_location)

    plan4 = []
    plan4.append(RegisterJujuUserStep(fqdn, CONTROLLER, data_location, replace=True))
    # Deploy sunbeam machine charm
    plan4.append(TerraformInitStep(tfhelper_sunbeam_machine))
    plan4.append(DeploySunbeamMachineApplicationStep(tfhelper_sunbeam_machine, jhelper))
    plan4.append(AddSunbeamMachineUnitStep(fqdn, jhelper))
    # Deploy Microk8s application during bootstrap irrespective of node role.
    plan4.append(TerraformInitStep(tfhelper))
    plan4.append(
        DeployMicrok8sApplicationStep(
            tfhelper, jhelper, accept_defaults=accept_defaults, preseed_file=preseed
        )
    )
    plan4.append(AddMicrok8sUnitStep(fqdn, jhelper))
    plan4.append(StoreMicrok8sConfigStep(jhelper))
    plan4.append(AddMicrok8sCloudStep(jhelper))
    # Deploy Microceph application during bootstrap irrespective of node role.
    plan4.append(TerraformInitStep(tfhelper_microceph_deploy))
    plan4.append(DeployMicrocephApplicationStep(tfhelper_microceph_deploy, jhelper))

    if is_storage_node:
        plan4.append(AddMicrocephUnitStep(fqdn, jhelper))
        plan4.append(
            ConfigureMicrocephOSDStep(
                fqdn, jhelper, accept_defaults=accept_defaults, preseed_file=preseed
            )
        )

    if is_control_node:
        plan4.append(ConfigureKubeletOptionsStep(fqdn))
        plan4.append(ConfigurePullThroughCacheStep(fqdn))
        plan4.append(PreseedRocksStep(fqdn))
        plan4.append(TerraformInitStep(tfhelper_openstack_deploy))
        plan4.append(
            DeployControlPlaneStep(
                tfhelper_openstack_deploy, jhelper, topology, database
            )
        )

    run_plan(plan4, console)

    plan5 = []

    if is_control_node:
        plan5.append(ConfigureMySQLStep(jhelper))
        plan5.append(PatchLoadBalancerServicesStep())

    if is_compute_node:
        plan5.append(TerraformInitStep(tfhelper_hypervisor_deploy))
        plan5.append(
            DeployHypervisorApplicationStep(
                tfhelper_hypervisor_deploy, tfhelper_openstack_deploy, jhelper
            )
        )
        plan5.append(AddHypervisorUnitStep(fqdn, jhelper))

    run_plan(plan5, console)

    click.echo(f"Node has been bootstrapped with roles: {pretty_roles}")


if __name__ == "__main__":
    bootstrap()
