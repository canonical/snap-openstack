# Copyright (c) 2022 Canonical Ltd.
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
import os
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Optional

import click
import yaml
from rich.console import Console
from snaphelpers import Snap

import sunbeam.jobs.questions
from sunbeam.clusterd.client import Client
from sunbeam.commands.configure import CLOUD_CONFIG_SECTION, retrieve_admin_credentials
from sunbeam.commands.openstack import OPENSTACK_MODEL
from sunbeam.jobs.checks import VerifyBootstrappedCheck
from sunbeam.jobs.common import (
    BaseStep,
    Result,
    ResultType,
    Status,
    run_plan,
    run_preflight_checks,
)
from sunbeam.jobs.juju import JujuHelper, ModelNotFoundException, run_sync

LOG = logging.getLogger(__name__)
console = Console()


class GenerateCloudConfigStep(BaseStep):
    """Generate clouds yaml for created cloud user."""

    def __init__(
        self,
        admin_credentials: dict,
        cloud: str,
        is_admin: bool,
        update: bool,
        cloudfile: Path,
    ):
        super().__init__(
            "Generate clouds.yaml", "Generating clouds.yaml for cloud access"
        )
        self.admin_credentials = admin_credentials
        self.cloud = cloud
        self.is_admin = is_admin
        self.update = update
        self.cloudfile = cloudfile
        self.client = Client()

        if not self.cloudfile:
            home = os.environ.get("SNAP_REAL_HOME")
            self.cloudfile = Path(home) / ".config" / "openstack" / "clouds.yaml"

    def is_skip(self, status: Optional[Status] = None) -> Result:
        """Determines if the step should be skipped or not.

        :return: ResultType.SKIPPED if the Step should be skipped,
                 ResultType.COMPLETED or ResultType.FAILED otherwise
        """
        if self.is_admin:
            return Result(ResultType.COMPLETED)

        # Check if run_demo_setup is done to get demo user information
        self.variables = sunbeam.jobs.questions.load_answers(
            self.client, CLOUD_CONFIG_SECTION
        )
        if "user" not in self.variables:
            LOG.debug("Demo setup not yet done")
            return Result(ResultType.SKIPPED)
        if self.variables["user"]["run_demo_setup"]:
            return Result(ResultType.COMPLETED)
        else:
            return Result(ResultType.SKIPPED)

    def run(self, status: Optional["Status"] = None) -> Result:
        try:
            if self.is_admin:
                # pass emptydictionary for tf_output
                self._print_cloud_config()
            else:
                snap = Snap()
                terraform = str(snap.paths.snap / "bin" / "terraform")
                cmd = [terraform, "output", "-json"]
                LOG.debug(f'Running command {" ".join(cmd)}')
                process = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    check=True,
                    cwd=snap.paths.user_common / "etc" / "demo-setup",
                )
                # Mask any passwords before printing process.stdout
                tf_output = json.loads(process.stdout)
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
                        "username": tf_output["OS_USERNAME"]["value"],
                        "password": tf_output["OS_PASSWORD"]["value"],
                        "user_domain_name": tf_output["OS_USER_DOMAIN_NAME"]["value"],
                        "project_domain_name": tf_output["OS_PROJECT_DOMAIN_NAME"][
                            "value"
                        ],
                        "project_name": tf_output["OS_PROJECT_NAME"]["value"],
                    },
                },
            }

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
            if cloud_config_from_file.get("clouds").get(
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
def cloud_config(
    cloud: str, admin: bool, update: bool, cloud_file: Optional[Path] = None
) -> None:
    """Generate or Update clouds.yaml."""
    preflight_checks = []
    preflight_checks.append(VerifyBootstrappedCheck())
    run_preflight_checks(preflight_checks, console)
    snap = Snap()
    data_location = snap.paths.user_data
    jhelper = JujuHelper(data_location)
    try:
        run_sync(jhelper.get_model(OPENSTACK_MODEL))
    except ModelNotFoundException:
        LOG.error(f"Expected model {OPENSTACK_MODEL} missing")
        raise click.ClickException("Please run `sunbeam cluster bootstrap` first")
    admin_credentials = retrieve_admin_credentials(jhelper, OPENSTACK_MODEL)
    plan = [
        GenerateCloudConfigStep(
            admin_credentials=admin_credentials,
            cloud=cloud,
            is_admin=admin,
            update=update,
            cloudfile=cloud_file,
        ),
    ]
    run_plan(plan, console)
