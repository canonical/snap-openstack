# SPDX-FileCopyrightText: 2022 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import logging
import os
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

import click
import click.core
import yaml
from rich.console import Console

import sunbeam.core.questions
from sunbeam.clusterd.client import Client
from sunbeam.commands.configure import CLOUD_CONFIG_SECTION, retrieve_admin_credentials
from sunbeam.core.checks import VerifyBootstrappedCheck, run_preflight_checks
from sunbeam.core.common import (
    BaseStep,
    Result,
    ResultType,
    Status,
    run_plan,
)
from sunbeam.core.deployment import Deployment
from sunbeam.core.juju import JujuHelper
from sunbeam.core.openstack import OPENSTACK_MODEL
from sunbeam.core.terraform import TerraformHelper
from sunbeam.utils import click_option_show_hints

LOG = logging.getLogger(__name__)
console = Console()


class GenerateCloudConfigStep(BaseStep):
    """Generate clouds yaml for created cloud user."""

    def __init__(
        self,
        client: Client,
        tfhelper: TerraformHelper,
        admin_credentials: dict,
        cloud: str,
        is_admin: bool,
        update: bool,
        cloudfile: Path | None = None,
    ):
        super().__init__(
            "Generate clouds.yaml", "Generating clouds.yaml for cloud access"
        )
        self.client = client
        self.tfhelper = tfhelper
        self.admin_credentials = admin_credentials
        self.cloud = cloud
        self.is_admin = is_admin
        self.update = update
        if cloudfile is None:
            home = os.environ["SNAP_REAL_HOME"]
            cloudfile = Path(home) / ".config" / "openstack" / "clouds.yaml"
        self.cloudfile = cloudfile

    def is_skip(self, status: Status | None = None) -> Result:
        """Determines if the step should be skipped or not.

        :return: ResultType.SKIPPED if the Step should be skipped,
                 ResultType.COMPLETED or ResultType.FAILED otherwise
        """
        if self.is_admin:
            return Result(ResultType.COMPLETED)

        # Check if run_demo_setup is done to get demo user information
        self.variables = sunbeam.core.questions.load_answers(
            self.client, CLOUD_CONFIG_SECTION
        )
        if "user" not in self.variables:
            LOG.debug("Demo setup not yet done")
            return Result(ResultType.SKIPPED)
        if self.variables["user"]["run_demo_setup"]:
            return Result(ResultType.COMPLETED)
        else:
            return Result(ResultType.SKIPPED)

    def run(self, status: Status | None = None) -> Result:
        """Generate cloud-config yaml for cloud access."""
        try:
            if self.is_admin:
                # pass emptydictionary for tf_output
                self._print_cloud_config()
            else:
                tf_output = self.tfhelper.output(hide_output=True)
                self._print_cloud_config(tf_output)
            return Result(ResultType.COMPLETED)
        except subprocess.CalledProcessError as e:
            LOG.exception("Error initializing Terraform")
            return Result(ResultType.FAILED, str(e))

    def _generate_cloud_config(self, is_admin: bool, tf_output: dict) -> dict:
        """Generate cloud data in clouds.yaml format."""
        if is_admin:
            cloud_data = {
                self.cloud: {
                    "auth": {
                        "auth_url": self.admin_credentials["OS_AUTH_URL"],
                        "username": self.admin_credentials["OS_USERNAME"],
                        "password": self.admin_credentials["OS_PASSWORD"],
                        "user_domain_name": self.admin_credentials[
                            "OS_USER_DOMAIN_NAME"
                        ],
                        "project_domain_name": self.admin_credentials[
                            "OS_PROJECT_DOMAIN_NAME"
                        ],
                        "project_name": self.admin_credentials["OS_PROJECT_NAME"],
                    },
                },
            }
        else:
            cloud_data = {
                self.cloud: {
                    "auth": {
                        "auth_url": self.admin_credentials["OS_AUTH_URL"],
                        "username": tf_output["OS_USERNAME"],
                        "password": tf_output["OS_PASSWORD"],
                        "user_domain_name": tf_output["OS_USER_DOMAIN_NAME"],
                        "project_domain_name": tf_output["OS_PROJECT_DOMAIN_NAME"],
                        "project_name": tf_output["OS_PROJECT_NAME"],
                    },
                },
            }

        if self.admin_credentials.get("OS_CACERT"):
            cloud_data[self.cloud]["cacert"] = self.admin_credentials["OS_CACERT"]

        return cloud_data

    def _get_cloud_config_from_file(self, clouds_yaml: Path) -> dict:
        """Get cloud config from yaml file.

        If cloud yaml is not present, create a file along with parent
        directories.
        """
        LOG.debug(f"Creating {clouds_yaml} if it does not exist")
        clouds_yaml.parent.mkdir(mode=0o775, parents=True, exist_ok=True)
        if not clouds_yaml.exists():
            clouds_yaml.touch()
        clouds_yaml.chmod(0o660)

        with clouds_yaml.open("r") as file:
            clouds_data_from_file = yaml.safe_load(file) or {}

        clouds_data_from_file.setdefault("clouds", {})
        return clouds_data_from_file

    def _create_backup_file(self, clouds_yaml: Path) -> None:
        """Create backup file for clouds_yaml.

        Create backup file in same directory with extension
        bk.{timestamp}
        """
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        clouds_yaml_backup = Path(clouds_yaml.parent) / f"clouds.yaml.bk.{timestamp}"
        LOG.debug(f"Backing up clouds.yaml to {clouds_yaml_backup}")
        shutil.copy(clouds_yaml, clouds_yaml_backup)
        clouds_yaml_backup.chmod(0o660)

    def _print_cloud_config(self, tf_output: dict = {}) -> None:
        """Prints cloud config on stdout or cloud-file."""
        generated_cloud_config = self._generate_cloud_config(self.is_admin, tf_output)
        if not self.update:
            cloud_config = {"clouds": generated_cloud_config}
            console.print(yaml.safe_dump(cloud_config))
            return

        cloud_config_from_file = self._get_cloud_config_from_file(self.cloudfile)

        write_cloud_data = True
        backup_cloud_yaml = False
        if self.cloud in cloud_config_from_file.get("clouds", {}):
            backup_cloud_yaml = True
            # Check if clouds.yaml already contains cloud information
            if cloud_config_from_file.get("clouds", {}).get(
                self.cloud
            ) == generated_cloud_config.get(self.cloud):
                LOG.debug(
                    "clouds.yaml already contains necessary information, "
                    "no need to update"
                )
                write_cloud_data = False

        if write_cloud_data:
            message = f"Writing cloud information to {self.cloudfile} ... "
            console.status(message)

            # Create backup of clouds.yaml
            if backup_cloud_yaml:
                self._create_backup_file(self.cloudfile)

            # Update clouds.yaml with the generated information
            cloud_config_from_file.setdefault("clouds", {})
            cloud_config_from_file["clouds"].update(generated_cloud_config)
            with self.cloudfile.open("w") as file:
                yaml.safe_dump(cloud_config_from_file, file)
            console.print(f"{message}[green]done[/green]")


@click.command()
@click.pass_context
@click.option("-c", "--cloud", help="Name of the cloud", type=str, default="sunbeam")
@click.option(
    "-a",
    "--admin",
    help="Generate cloud-config for cloud admin user. If not specified cloud-config"
    " is generated for the demonstration user setup during configure.",
    is_flag=True,
    default=False,
)
@click.option(
    "-u",
    "--update",
    help="Create/update config in the file specified in cloud-file option",
    is_flag=True,
    default=False,
)
@click.option(
    "-f",
    "--cloud-file",
    help="Output file for cloud yaml, defaults to $HOME/.config/openstack/clouds.yaml",
    type=click.Path(dir_okay=False, path_type=Path),
)
@click_option_show_hints
def cloud_config(
    ctx: click.Context,
    cloud: str,
    admin: bool,
    update: bool,
    cloud_file: Path | None = None,
    show_hints: bool = True,
) -> None:
    """Generate or Update clouds.yaml."""
    parameter_source = ctx.get_parameter_source("cloud")
    if parameter_source == click.core.ParameterSource.DEFAULT and admin:
        cloud += "-admin"

    deployment: Deployment = ctx.obj
    client = deployment.get_client()
    preflight_checks = []
    preflight_checks.append(VerifyBootstrappedCheck(client))
    run_preflight_checks(preflight_checks, console)
    jhelper = JujuHelper(deployment.juju_controller)
    if not jhelper.model_exists(OPENSTACK_MODEL):
        LOG.error(f"Expected model {OPENSTACK_MODEL} missing")
        raise click.ClickException("Please run `sunbeam cluster bootstrap` first")
    admin_credentials = retrieve_admin_credentials(jhelper, OPENSTACK_MODEL)
    tfplan = "demo-setup"
    tfhelper = deployment.get_tfhelper(tfplan)
    tfhelper.env = (tfhelper.env or {}) | admin_credentials
    plan = [
        GenerateCloudConfigStep(
            client=client,
            tfhelper=tfhelper,
            admin_credentials=admin_credentials,
            cloud=cloud,
            is_admin=admin,
            update=update,
            cloudfile=cloud_file,
        ),
    ]
    run_plan(plan, console, show_hints)
