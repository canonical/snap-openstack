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


import itertools
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Optional

import pexpect
import pwgen
import tenacity
import yaml
from packaging import version
from rich.status import Status
from snaphelpers import Snap

from sunbeam.clusterd.client import Client
from sunbeam.clusterd.service import (
    JujuUserNotFoundException,
    NodeNotExistInClusterException,
)
from sunbeam.jobs.common import (
    BaseStep,
    Result,
    ResultType,
    convert_proxy_to_model_configs,
)
from sunbeam.jobs.juju import (
    CONTROLLER_MODEL,
    ApplicationNotFoundException,
    ControllerNotFoundException,
    JujuAccount,
    JujuAccountNotFound,
    JujuException,
    JujuHelper,
    JujuWaitException,
    ModelNotFoundException,
    TimeoutException,
    run_sync,
)
from sunbeam.versions import JUJU_BASE, JUJU_CHANNEL

LOG = logging.getLogger(__name__)
PEXPECT_TIMEOUT = 60
BOOTSTRAP_CONFIG_KEY = "BootstrapAnswers"
JUJU_CONTROLLER_CHARM = "juju-controller.charm"


class JujuStepHelper:
    def _get_juju_binary(self) -> str:
        """Get juju binary path."""
        snap = Snap()
        juju_binary = snap.paths.snap / "juju" / "bin" / "juju"
        return str(juju_binary)

    def _juju_cmd(self, *args):
        """Runs the specified juju command line command.

        The command will be run using the json formatter. Invoking functions
        do not need to worry about the format or the juju command that should
        be used.

        For example, to run the juju bootstrap k8s, this method should
        be invoked as:

          self._juju_cmd('bootstrap', 'k8s')

        Any results from running with json are returned after being parsed.
        Subprocess execution errors are raised to the calling code.

        :param args: command to run
        :return:
        """
        cmd = [self._get_juju_binary()]
        cmd.extend(args)
        cmd.extend(["--format", "json"])

        LOG.debug(f'Running command {" ".join(cmd)}')
        process = subprocess.run(cmd, capture_output=True, text=True, check=True)
        LOG.debug(f"Command finished. stdout={process.stdout}, stderr={process.stderr}")

        return json.loads(process.stdout.strip())

    def check_model_present(self, model_name) -> bool:
        """Determines if the step should be skipped or not.

        :return: True if the Step should be skipped, False otherwise
        """
        try:
            run_sync(self.jhelper.get_model(model_name))
            return True
        except ModelNotFoundException:
            LOG.debug(f"Model {model_name} not found")
            return False

    def get_clouds(self, cloud_type: str, local: bool = False) -> list:
        """Get clouds based on cloud type."""
        clouds = []
        cmd = ["clouds"]
        if local:
            cmd.append("--client")
        clouds_from_juju_cmd = self._juju_cmd(*cmd)
        LOG.debug(f"Available clouds in juju are {clouds_from_juju_cmd.keys()}")

        for name, details in clouds_from_juju_cmd.items():
            if details["type"] == cloud_type:
                clouds.append(name)

        LOG.debug(f"There are {len(clouds)} {cloud_type} clouds available: {clouds}")

        return clouds

    def get_credentials(
        self, cloud: str | None = None, local: bool = False
    ) -> dict[str, dict]:
        """Get credentials."""
        cmd = ["credentials"]
        if local:
            cmd.append("--client")
        if cloud:
            cmd.append(cloud)
        return self._juju_cmd(*cmd)

    def get_controllers(self, clouds: list) -> list:
        """Get controllers hosted on given clouds."""
        existing_controllers = []

        controllers = self._juju_cmd("controllers")
        LOG.debug(f"Found controllers: {controllers.keys()}")
        LOG.debug(controllers)

        controllers = controllers.get("controllers", {})
        if controllers:
            for name, details in controllers.items():
                if details["cloud"] in clouds:
                    existing_controllers.append(name)

        LOG.debug(
            f"There are {len(existing_controllers)} existing k8s "
            f"controllers running: {existing_controllers}"
        )
        return existing_controllers

    def get_controller(self, controller: str) -> dict:
        """Get controller definition."""
        try:
            return self._juju_cmd("show-controller", controller)[controller]
        except subprocess.CalledProcessError as e:
            LOG.debug(e)
            raise ControllerNotFoundException() from e

    def add_cloud(self, name: str, cloud: dict) -> bool:
        """Add cloud to client clouds."""
        if cloud["clouds"][name]["type"] not in ("manual", "maas"):
            return False

        with tempfile.NamedTemporaryFile() as temp:
            temp.write(yaml.dump(cloud).encode("utf-8"))
            temp.flush()
            cmd = [
                self._get_juju_binary(),
                "add-cloud",
                name,
                "--file",
                temp.name,
                "--client",
            ]
            LOG.debug(f'Running command {" ".join(cmd)}')
            process = subprocess.run(cmd, capture_output=True, text=True, check=True)
            LOG.debug(
                f"Command finished. stdout={process.stdout}, stderr={process.stderr}"
            )

        return True

    def add_credential(self, cloud: str, credential: dict):
        """Add credential to client credentials."""
        with tempfile.NamedTemporaryFile() as temp:
            temp.write(yaml.dump(credential).encode("utf-8"))
            temp.flush()
            cmd = [
                self._get_juju_binary(),
                "add-credential",
                cloud,
                "--file",
                temp.name,
                "--client",
            ]
            LOG.debug(f'Running command {" ".join(cmd)}')
            process = subprocess.run(cmd, capture_output=True, text=True, check=True)
            LOG.debug(
                f"Command finished. stdout={process.stdout}, stderr={process.stderr}"
            )

    def revision_update_needed(
        self, application_name: str, model: str, status: dict | None = None
    ) -> bool:
        """Check if a revision update is available for an applicaton.

        :param application_name: Name of application to check for updates for
        :param model: Model application is in
        :param status: Dictionay of model status
        """
        if not status:
            _status = run_sync(self.jhelper.get_model_status_full(model))
            status = json.loads(_status.to_json())
        app_status = status["applications"].get(application_name, {})
        if not app_status:
            LOG.debug(f"{application_name} not present in model")
            return False
        deployed_revision = int(self._extract_charm_revision(app_status["charm"]))
        charm_name = self._extract_charm_name(app_status["charm"])
        deployed_channel = self.normalise_channel(app_status["charm-channel"])
        if len(deployed_channel.split("/")) > 2:
            LOG.debug(f"Cannot calculate upgrade for {application_name}, branch in use")
            return False
        available_revision = run_sync(
            self.jhelper.get_available_charm_revision(
                model, charm_name, deployed_channel
            )
        )
        return bool(available_revision > deployed_revision)

    def get_charm_deployed_versions(self, model: str) -> dict:
        """Return charm deployed info for all the applications in model.

        For each application, return a tuple of charm name, channel and revision.
        Example output:
        {"keystone": ("keystone-k8s", "2023.2/stable", 234)}
        """
        _status = run_sync(self.jhelper.get_model_status_full(model))
        status = json.loads(_status.to_json())

        apps = {}
        for app_name, app_status in status.get("applications", {}).items():
            charm_name = self._extract_charm_name(app_status["charm"])
            deployed_channel = self.normalise_channel(app_status["charm-channel"])
            deployed_revision = int(self._extract_charm_revision(app_status["charm"]))
            apps[app_name] = (charm_name, deployed_channel, deployed_revision)

        return apps

    def get_apps_filter_by_charms(self, model: str, charms: list) -> list:
        """Return apps filtered by given charms.

        Get all apps from the model and return only the apps deployed with
        charms in the provided list.
        """
        deployed_all_apps = self.get_charm_deployed_versions(model)
        return [
            app_name
            for app_name, (charm, channel, revision) in deployed_all_apps.items()
            if charm in charms
        ]

    def normalise_channel(self, channel: str) -> str:
        """Expand channel if it is using abbreviation.

        Juju supports abbreviating latest/{risk} to {risk}. This expands it.

        :param channel: Channel string to normalise
        """
        if channel in ["stable", "candidate", "beta", "edge"]:
            channel = f"latest/{channel}"
        return channel

    def _extract_charm_name(self, charm_url: str) -> str:
        """Extract charm name from charm url.

        :param charm_url: Url to examine
        """
        # XXX There must be a better way. ch:amd64/jammy/cinder-k8s-50 -> cinder-k8s
        return charm_url.split("/")[-1].rsplit("-", maxsplit=1)[0]

    def _extract_charm_revision(self, charm_url: str) -> str:
        """Extract charm revision from charm url.

        :param charm_url: Url to examine
        """
        return charm_url.split("-")[-1]

    def channel_update_needed(self, channel: str, new_channel: str) -> bool:
        """Compare two channels and see if the second is 'newer'.

        :param current_channel: Current channel
        :param new_channel: Proposed new channel
        """
        risks = ["stable", "candidate", "beta", "edge"]
        current_channel = self.normalise_channel(channel)
        current_track, current_risk = current_channel.split("/")
        new_track, new_risk = new_channel.split("/")
        if current_track != new_track:
            try:
                return version.parse(current_track) < version.parse(new_track)
            except version.InvalidVersion:
                LOG.error("Error: Could not compare tracks")
                return False
        if risks.index(current_risk) < risks.index(new_risk):
            return True
        else:
            return False


class AddCloudJujuStep(BaseStep, JujuStepHelper):
    """Add cloud definition to juju client."""

    def __init__(self, cloud: str, definition: dict):
        super().__init__("Add Cloud", "Adding cloud to Juju client")

        self.cloud = cloud
        self.definition = definition

    def is_skip(self, status: Optional["Status"] = None) -> Result:
        """Determines if the step should be skipped or not.

        :return: ResultType.SKIPPED if the Step should be skipped,
                 ResultType.COMPLETED or ResultType.FAILED otherwise
        """
        cloud_type = self.definition["clouds"][self.cloud]["type"]
        try:
            juju_clouds = self.get_clouds(cloud_type, local=True)
        except subprocess.CalledProcessError as e:
            LOG.exception(
                "Error determining whether to skip the bootstrap "
                "process. Defaulting to not skip."
            )
            LOG.warning(e.stderr)
            return Result(ResultType.FAILED, str(e))
        if self.cloud in juju_clouds:
            return Result(ResultType.SKIPPED)
        return Result(ResultType.COMPLETED)

    def run(self, status: Optional["Status"] = None) -> Result:
        """Run the step to completion.

        Invoked when the step is run and returns a ResultType to indicate

        :return:
        """
        try:
            result = self.add_cloud(self.cloud, self.definition)
            if not result:
                return Result(ResultType.FAILED, "Unable to create cloud")
        except subprocess.CalledProcessError as e:
            LOG.exception("Error adding cloud to Juju")
            LOG.warning(e.stderr)
            return Result(ResultType.FAILED, str(e))
        return Result(ResultType.COMPLETED)


class AddCredentialsJujuStep(BaseStep, JujuStepHelper):
    """Add credentials definition to juju client."""

    def __init__(self, cloud: str, credentials: str, definition: dict):
        super().__init__("Add Credentials", "Adding credentials to Juju client")

        self.cloud = cloud
        self.credentials_name = credentials
        self.definition = definition

    def is_skip(self, status: Optional["Status"] = None) -> Result:
        """Determines if the step should be skipped or not.

        :return: ResultType.SKIPPED if the Step should be skipped,
                 ResultType.COMPLETED or ResultType.FAILED otherwise
        """
        try:
            credentials = self.get_credentials(self.cloud, local=True)
        except subprocess.CalledProcessError as e:
            LOG.exception(
                "Error determining whether to skip the bootstrap "
                "process. Defaulting to not skip."
            )
            LOG.warning(e.stderr)
            return Result(ResultType.FAILED, str(e))
        client_creds = credentials.get("client-credentials", {})
        cloud_credentials = client_creds.get(self.cloud, {}).get(
            "cloud-credentials", {}
        )
        if not cloud_credentials or self.credentials_name not in cloud_credentials:
            return Result(ResultType.COMPLETED)
        return Result(ResultType.SKIPPED)

    def run(self, status: Optional["Status"] = None) -> Result:
        """Run the step to completion.

        Invoked when the step is run and returns a ResultType to indicate

        :return:
        """
        try:
            self.add_credential(self.cloud, self.definition)
        except subprocess.CalledProcessError as e:
            LOG.exception("Error adding credentials to Juju")
            LOG.warning(e.stderr)
            return Result(ResultType.FAILED, str(e))
        return Result(ResultType.COMPLETED)


class BootstrapJujuStep(BaseStep, JujuStepHelper):
    """Bootstraps the Juju controller."""

    def __init__(
        self,
        client: Client,
        cloud: str,
        cloud_type: str,
        controller: str,
        bootstrap_args: list[str] | None = None,
        proxy_settings: dict | None = None,
    ):
        super().__init__("Bootstrap Juju", "Bootstrapping Juju onto machine")

        self.client = client
        self.cloud = cloud
        self.cloud_type = cloud_type
        self.controller = controller
        self.bootstrap_args = bootstrap_args or []
        self.proxy_settings = proxy_settings or {}
        self.juju_clouds = []

        home = os.environ.get("SNAP_REAL_HOME")
        os.environ["JUJU_DATA"] = f"{home}/.local/share/juju"

    def is_skip(self, status: Optional["Status"] = None) -> Result:
        """Determines if the step should be skipped or not.

        :return: ResultType.SKIPPED if the Step should be skipped,
                 ResultType.COMPLETED or ResultType.FAILED otherwise
        """
        try:
            self.juju_clouds = self.get_clouds(self.cloud_type)
        except subprocess.CalledProcessError as e:
            LOG.exception(
                "Error determining whether to skip the bootstrap "
                "process. Defaulting to not skip."
            )
            LOG.warning(e.stderr)
            return Result(ResultType.FAILED, str(e))
        if self.cloud not in self.juju_clouds:
            return Result(
                ResultType.FAILED,
                f"Cloud {self.cloud} of type {self.cloud_type!r} not found.",
            )
        try:
            self.get_controller(self.controller)
            return Result(ResultType.SKIPPED)
        except ControllerNotFoundException as e:
            LOG.debug(str(e))
        return Result(ResultType.COMPLETED)

    def run(self, status: Optional["Status"] = None) -> Result:
        """Run the step to completion.

        Invoked when the step is run and returns a ResultType to indicate

        :return:
        """
        try:
            cmd = [
                self._get_juju_binary(),
                "bootstrap",
            ]
            cmd.extend(self.bootstrap_args)
            cmd.extend([self.cloud, self.controller])
            if "HTTP_PROXY" in self.proxy_settings:
                cmd.extend(
                    [
                        "--config",
                        f"juju-http-proxy={self.proxy_settings.get('HTTP_PROXY')}",
                        "--config",
                        f"snap-http-proxy={self.proxy_settings.get('HTTP_PROXY')}",
                    ]
                )
            if "HTTPS_PROXY" in self.proxy_settings:
                cmd.extend(
                    [
                        "--config",
                        f"juju-https-proxy={self.proxy_settings.get('HTTPS_PROXY')}",
                        "--config",
                        f"snap-https-proxy={self.proxy_settings.get('HTTPS_PROXY')}",
                    ]
                )
            if "NO_PROXY" in self.proxy_settings:
                cmd.extend(
                    ["--config", f"juju-no-proxy={self.proxy_settings.get('NO_PROXY')}"]
                )

            hidden_cmd = []
            for arg in cmd:
                if "admin-secret" in arg:
                    option, _ = arg.split("=")
                    arg = "=".join((option, "********"))
                hidden_cmd.append(arg)
            LOG.debug(f'Running command {" ".join(hidden_cmd)}')
            env = os.environ.copy()
            env.update(self.proxy_settings)
            process = subprocess.run(
                cmd, capture_output=True, text=True, check=True, env=env
            )
            LOG.debug(
                f"Command finished. stdout={process.stdout}, stderr={process.stderr}"
            )

            return Result(ResultType.COMPLETED)
        except subprocess.CalledProcessError as e:
            LOG.exception("Error bootstrapping Juju")
            LOG.warning(e.stderr)
            return Result(ResultType.FAILED, str(e))


class ScaleJujuStep(BaseStep, JujuStepHelper):
    """Enable Juju HA."""

    def __init__(
        self, controller: str, n: int = 3, extra_args: list[str] | None = None
    ):
        super().__init__("Juju HA", "Enable Juju High Availability")
        self.controller = controller
        self.n = n
        self.extra_args = extra_args or []

    def is_skip(self, status: Status | None = None) -> Result:
        """Determines if the step should be skipped or not."""
        return Result(ResultType.COMPLETED)

    def run(self, status: Status | None = None) -> Result:
        """Enable Juju HA."""
        cmd = [
            self._get_juju_binary(),
            "enable-ha",
            "-n",
            str(self.n),
            *self.extra_args,
        ]
        LOG.debug(f'Running command {" ".join(cmd)}')
        process = subprocess.run(cmd, capture_output=True, text=True, check=True)
        LOG.debug(f"Command finished. stdout={process.stdout}, stderr={process.stderr}")
        cmd = [
            self._get_juju_binary(),
            "wait-for",
            "application",
            "-m",
            "controller",
            "controller",
            "--timeout",
            "15m",
        ]
        self.update_status(status, "scaling controller")
        LOG.debug("Waiting for HA to be enabled")
        LOG.debug(f'Running command {" ".join(cmd)}')
        process = subprocess.run(cmd, capture_output=True, text=True, check=True)
        LOG.debug(f"Command finished. stdout={process.stdout}, stderr={process.stderr}")
        return Result(ResultType.COMPLETED)


class CreateJujuUserStep(BaseStep, JujuStepHelper):
    """Create user in juju and grant superuser access."""

    def __init__(self, name: str):
        super().__init__("Create User", "Creating user for machine in Juju")
        self.username = name
        self.registration_token_regex = r"juju register (.*?)\n"

        home = os.environ.get("SNAP_REAL_HOME")
        os.environ["JUJU_DATA"] = f"{home}/.local/share/juju"

    def is_skip(self, status: Optional["Status"] = None) -> Result:
        """Determines if the step should be skipped or not.

        :return: ResultType.SKIPPED if the Step should be skipped,
                 ResultType.COMPLETED or ResultType.FAILED otherwise
        """
        try:
            users = self._juju_cmd("list-users")
            user_names = [user.get("user-name") for user in users]
            if self.username in user_names:
                return Result(ResultType.SKIPPED)
        except subprocess.CalledProcessError as e:
            LOG.exception("Error getting users list from juju.")
            LOG.warning(e.stderr)
            return Result(ResultType.FAILED, str(e))

        return Result(ResultType.COMPLETED)

    def run(self, status: Optional["Status"] = None) -> Result:
        """Run the step to completion.

        Invoked when the step is run and returns a ResultType to indicate

        :return:
        """
        try:
            cmd = [self._get_juju_binary(), "add-user", self.username]
            LOG.debug(f'Running command {" ".join(cmd)}')
            process = subprocess.run(cmd, capture_output=True, text=True, check=True)
            LOG.debug(
                f"Command finished. stdout={process.stdout}, stderr={process.stderr}"
            )

            re_groups = re.search(
                self.registration_token_regex, process.stdout, re.MULTILINE
            )
            token = re_groups.group(1)
            if not token:
                return Result(ResultType.FAILED, "Not able to parse registration token")

            # Grant superuser access to user.
            cmd = [self._get_juju_binary(), "grant", self.username, "superuser"]
            LOG.debug(f'Running command {" ".join(cmd)}')
            process = subprocess.run(cmd, capture_output=True, text=True, check=True)
            LOG.debug(
                f"Command finished. stdout={process.stdout}, stderr={process.stderr}"
            )

            # Grant write access to controller model
            # Without this step, the user is not able to view controller model
            cmd = [
                self._get_juju_binary(),
                "grant",
                self.username,
                "admin",
                CONTROLLER_MODEL,
            ]
            LOG.debug(f'Running command {" ".join(cmd)}')
            process = subprocess.run(cmd, capture_output=True, text=True, check=True)
            LOG.debug(
                f"Command finished. stdout={process.stdout}, stderr={process.stderr}"
            )

            return Result(ResultType.COMPLETED, message=token)
        except subprocess.CalledProcessError as e:
            LOG.exception(f"Error creating user {self.username} in Juju")
            LOG.warning(e.stderr)
            return Result(ResultType.FAILED, str(e))


class JujuGrantModelAccessStep(BaseStep, JujuStepHelper):
    """Grant model access to user in juju."""

    def __init__(self, jhelper: JujuHelper, name: str, model: str):
        super().__init__(
            "Grant access on model",
            f"Granting user {name} admin access to model {model}",
        )

        self.jhelper = jhelper
        self.username = name
        self.model = model

        home = os.environ.get("SNAP_REAL_HOME")
        os.environ["JUJU_DATA"] = f"{home}/.local/share/juju"

    def run(self, status: Optional["Status"] = None) -> Result:
        """Run the step to completion.

        Invoked when the step is run and returns a ResultType to indicate

        :return:
        """
        try:
            model_with_owner = run_sync(
                self.jhelper.get_model_name_with_owner(self.model)
            )
            # Grant write access to the model
            # Without this step, the user is not able to view the model created
            # by other users.
            cmd = [
                self._get_juju_binary(),
                "grant",
                self.username,
                "admin",
                model_with_owner,
            ]
            LOG.debug(f'Running command {" ".join(cmd)}')
            process = subprocess.run(cmd, capture_output=True, text=True, check=True)
            LOG.debug(
                f"Command finished. stdout={process.stdout}, stderr={process.stderr}"
            )

            return Result(ResultType.COMPLETED)
        except ModelNotFoundException as e:
            return Result(ResultType.FAILED, str(e))
        except subprocess.CalledProcessError as e:
            LOG.debug(e.stderr)
            if 'user already has "admin" access or greater' in e.stderr:
                return Result(ResultType.COMPLETED)

            LOG.exception(
                f"Error granting user {self.username} admin access on model "
                f"{self.model}"
            )
            return Result(ResultType.FAILED, str(e))


class RemoveJujuUserStep(BaseStep, JujuStepHelper):
    """Remove user in juju."""

    def __init__(self, name: str):
        super().__init__("Remove User", f"Removing machine user {name} from Juju")
        self.username = name

        home = os.environ.get("SNAP_REAL_HOME")
        os.environ["JUJU_DATA"] = f"{home}/.local/share/juju"

    def is_skip(self, status: Optional["Status"] = None) -> Result:
        """Determines if the step should be skipped or not.

        :return: ResultType.SKIPPED if the Step should be skipped,
                 ResultType.COMPLETED or ResultType.FAILED otherwise
        """
        try:
            users = self._juju_cmd("list-users")
            user_names = [user.get("user-name") for user in users]
            if self.username not in user_names:
                return Result(ResultType.SKIPPED)
        except subprocess.CalledProcessError as e:
            LOG.exception("Error getting list of users from Juju.")
            LOG.warning(e.stderr)
            return Result(ResultType.FAILED, str(e))

        return Result(ResultType.COMPLETED)

    def run(self, status: Optional["Status"] = None) -> Result:
        """Run the step to completion.

        Invoked when the step is run and returns a ResultType to indicate

        :return:
        """
        try:
            cmd = [self._get_juju_binary(), "remove-user", self.username, "--yes"]
            LOG.debug(f'Running command {" ".join(cmd)}')
            process = subprocess.run(cmd, capture_output=True, text=True, check=True)
            LOG.debug(
                f"Command finished. stdout={process.stdout}, stderr={process.stderr}"
            )

            return Result(ResultType.COMPLETED)
        except subprocess.CalledProcessError as e:
            LOG.exception(f"Error removing user {self.username} from Juju")
            LOG.warning(e.stderr)
            return Result(ResultType.FAILED, str(e))


class RegisterJujuUserStep(BaseStep, JujuStepHelper):
    """Register user in juju."""

    def __init__(
        self,
        client: Client,
        name: str,
        controller: str,
        data_location: Path,
        replace: bool = False,
    ):
        super().__init__(
            "Register Juju User", f"Registering machine user {name} using token"
        )
        self.client = client
        self.username = name
        self.controller = controller
        self.data_location = data_location
        self.replace = replace
        self.registration_token = None
        self.juju_account = None

        home = os.environ.get("SNAP_REAL_HOME")
        os.environ["JUJU_DATA"] = f"{home}/.local/share/juju"

    def is_skip(self, status: Optional["Status"] = None) -> Result:
        """Determines if the step should be skipped or not.

        :return: ResultType.SKIPPED if the Step should be skipped,
                 ResultType.COMPLETED or ResultType.FAILED otherwise
        """
        try:
            self.juju_account = JujuAccount.load(self.data_location)
            LOG.debug(f"Local account found: {self.juju_account.user}")
        except JujuAccountNotFound as e:
            LOG.warning(e)
            return Result(ResultType.FAILED, "Account was not registered locally")
        try:
            user = self._juju_cmd("show-user")
            LOG.debug(f"Found user: {user['user-name']}")
            username = user["user-name"]
            if username == self.juju_account.user:
                return Result(ResultType.SKIPPED)
        except subprocess.CalledProcessError as e:
            if "No controllers registered" not in e.stderr:
                LOG.exception("Error retrieving authenticated user from Juju.")
                LOG.warning(e.stderr)
                return Result(ResultType.FAILED, str(e))
            # Error is about no controller register, which is okay is this case
            pass

        try:
            user = self.client.cluster.get_juju_user(self.username)
        except JujuUserNotFoundException:
            LOG.debug("User %r not found in cluster database", self.username)
            return Result(
                ResultType.FAILED,
                f"Juju registration token for host {self.username!r} not found in"
                " cluster database, was this token issued for this host?",
            )
        self.registration_token = user.get("token")
        return Result(ResultType.COMPLETED)

    def run(self, status: Optional["Status"] = None) -> Result:
        """Run the step to completion.

        Invoked when the step is run and returns a ResultType to indicate

        :return:
        """
        if not self.registration_token:
            return Result(
                ResultType.FAILED, "No registration token found in Cluster database"
            )

        snap = Snap()
        log_file = Path(f"register_juju_user_{self.username}_{self.controller}.log")
        log_file = snap.paths.user_common / log_file
        new_password_re = r"Enter a new password"
        confirm_password_re = r"Confirm password"
        controller_name_re = r"Enter a name for this controller"
        # NOTE(jamespage)
        # Sometimes the register command fails to actually log the user in and the
        # user is prompted to enter the password they literally just set.
        # https://bugs.launchpad.net/juju/+bug/2020360
        please_enter_password_re = r"please enter password"
        expect_list = [
            new_password_re,
            confirm_password_re,
            controller_name_re,
            please_enter_password_re,
            pexpect.EOF,
        ]

        # TOCHK: password is saved as a macroon with 24hours shelf life and juju
        # client need to login/logout?
        # Does saving the password in $HOME/.local/share/juju/accounts.yaml
        # avoids login/logout?
        register_args = ["register", self.registration_token]
        if self.replace:
            register_args.append("--replace")

        try:
            child = pexpect.spawn(
                self._get_juju_binary(),
                register_args,
                PEXPECT_TIMEOUT,
            )
            with open(log_file, "wb+") as f:
                # Record the command output, but only the contents streaming from the
                # process, don't record anything sent to the process as it may contain
                # sensitive information.
                child.logfile_read = f
                while True:
                    index = child.expect(expect_list, PEXPECT_TIMEOUT)
                    LOG.debug(
                        "Juju registraton: expect got regex related to "
                        f"{expect_list[index]}"
                    )
                    if index in (0, 1, 3):
                        child.sendline(self.juju_account.password)
                    elif index == 2:
                        child.sendline(self.controller)
                    elif index == 4:
                        result = child.before.decode()
                        if "ERROR" in result:
                            str_index = result.find("ERROR")
                            return Result(ResultType.FAILED, result[str_index:])

                        LOG.debug("User registration completed")
                        break
        except pexpect.TIMEOUT as e:
            LOG.exception(f"Error registering user {self.username} in Juju")
            LOG.warning(e)
            return Result(ResultType.FAILED, str(e))

        return Result(ResultType.COMPLETED)


class AddJujuMachineStep(BaseStep, JujuStepHelper):
    """Add machine in juju."""

    def __init__(self, ip: str):
        super().__init__("Add machine", "Adding machine to Juju model")

        self.machine_ip = ip

        home = os.environ.get("SNAP_REAL_HOME")
        os.environ["JUJU_DATA"] = f"{home}/.local/share/juju"

    def is_skip(self, status: Optional["Status"] = None) -> Result:
        """Determines if the step should be skipped or not.

        :return: ResultType.SKIPPED if the Step should be skipped,
                 ResultType.COMPLETED or ResultType.FAILED otherwise
        """
        try:
            machines = self._juju_cmd("machines", "-m", CONTROLLER_MODEL)
            LOG.debug(f"Found machines: {machines}")
            machines = machines.get("machines", {})

            for machine, details in machines.items():
                if self.machine_ip in details.get("ip-addresses"):
                    LOG.debug("Machine already exists")
                    return Result(ResultType.SKIPPED, machine)

        except subprocess.CalledProcessError as e:
            LOG.exception("Error retrieving machines list from Juju")
            LOG.warning(e.stderr)
            return Result(ResultType.FAILED, str(e))

        return Result(ResultType.COMPLETED)

    def run(self, status: Optional["Status"] = None) -> Result:
        """Run the step to completion.

        Invoked when the step is run and returns a ResultType to indicate

        :return:
        """
        snap = Snap()
        log_file = snap.paths.user_common / f"add_juju_machine_{self.machine_ip}.log"
        auth_message_re = "Are you sure you want to continue connecting"
        expect_list = [auth_message_re, pexpect.EOF]
        try:
            child = pexpect.spawn(
                self._get_juju_binary(),
                ["add-machine", "-m", CONTROLLER_MODEL, f"ssh:{self.machine_ip}"],
                PEXPECT_TIMEOUT * 5,  # 5 minutes
            )
            with open(log_file, "wb+") as f:
                # Record the command output, but only the contents streaming from the
                # process, don't record anything sent to the process as it may contain
                # sensitive information.
                child.logfile_read = f
                while True:
                    index = child.expect(expect_list)
                    LOG.debug(
                        "Juju add-machine: expect got regex related to "
                        f"{expect_list[index]}"
                    )
                    if index == 0:
                        child.sendline("yes")
                    elif index == 1:
                        result = child.before.decode()
                        if "ERROR" in result:
                            str_index = result.find("ERROR")
                            return Result(ResultType.FAILED, result[str_index:])

                        LOG.debug("Add machine successful")
                        break

            # TODO(hemanth): Need to wait until machine comes to started state
            # from planned state?

            machines = self._juju_cmd("machines", "-m", CONTROLLER_MODEL)
            LOG.debug(f"Found machines: {machines}")
            machines = machines.get("machines", {})
            for machine, details in machines.items():
                if self.machine_ip in details.get("ip-addresses"):
                    return Result(ResultType.COMPLETED, machine)

            # respond with machine id as -1 if machine is not reflected in juju
            return Result(ResultType.COMPLETED, "-1")
        except pexpect.TIMEOUT as e:
            LOG.exception("Error adding machine {self.machine_ip} to Juju")
            LOG.warning(e)
            return Result(ResultType.FAILED, "TIMED OUT to add machine")


class RemoveJujuMachineStep(BaseStep, JujuStepHelper):
    """Remove machine in juju."""

    def __init__(self, client: Client, name: str):
        super().__init__("Remove machine", f"Removing machine {name} from Juju model")

        self.client = client
        self.name = name
        self.machine_id = -1

        home = os.environ.get("SNAP_REAL_HOME")
        os.environ["JUJU_DATA"] = f"{home}/.local/share/juju"

    def is_skip(self, status: Optional["Status"] = None) -> Result:
        """Determines if the step should be skipped or not.

        :return: ResultType.SKIPPED if the Step should be skipped,
                 ResultType.COMPLETED or ResultType.FAILED otherwise
        """
        try:
            node = self.client.cluster.get_node_info(self.name)
            self.machine_id = node.get("machineid")
        except NodeNotExistInClusterException as e:
            return Result(ResultType.FAILED, str(e))

        try:
            machines = self._juju_cmd("machines", "-m", CONTROLLER_MODEL)
            LOG.debug(f"Found machines: {machines}")
            machines = machines.get("machines", {})

            if str(self.machine_id) not in machines:
                LOG.debug("Machine does not exist")
                return Result(ResultType.SKIPPED)
        except subprocess.CalledProcessError as e:
            LOG.exception("Error retrieving machine list from Juju")
            LOG.warning(e.stderr)
            return Result(ResultType.FAILED, str(e))

        return Result(ResultType.COMPLETED)

    def run(self, status: Optional["Status"] = None) -> Result:
        """Run the step to completion.

        Invoked when the step is run and returns a ResultType to indicate

        :return:
        """
        try:
            if self.machine_id == -1:
                return Result(
                    ResultType.FAILED,
                    "Not able to retrieve machine id from Cluster database",
                )

            cmd = [
                self._get_juju_binary(),
                "remove-machine",
                "-m",
                CONTROLLER_MODEL,
                str(self.machine_id),
                "--no-prompt",
            ]
            LOG.debug(f'Running command {" ".join(cmd)}')
            process = subprocess.run(cmd, capture_output=True, text=True, check=True)
            LOG.debug(
                f"Command finished. stdout={process.stdout}, stderr={process.stderr}"
            )

            return Result(ResultType.COMPLETED)
        except subprocess.CalledProcessError as e:
            LOG.exception(f"Error removing machine {self.machine_id} from Juju")
            LOG.warning(e.stderr)
            return Result(ResultType.FAILED, str(e))


class BackupBootstrapUserStep(BaseStep, JujuStepHelper):
    """Backup bootstrap user credentials."""

    def __init__(self, name: str, data_location: Path):
        super().__init__("Backup Bootstrap User", "Saving bootstrap user credentials")
        self.username = name
        self.data_location = data_location

        home = os.environ.get("SNAP_REAL_HOME")
        self.juju_data = Path(f"{home}/.local/share/juju")

    def is_skip(self, status: Optional["Status"] = None) -> Result:
        """Determines if the step should be skipped or not.

        :return: ResultType.SKIPPED if the Step should be skipped,
                 ResultType.COMPLETED or ResultType.FAILED otherwise
        """
        try:
            user = self._juju_cmd("show-user")
            LOG.debug(f"Found user: {user['user-name']}")
            username = user["user-name"]
            if username == "admin":
                return Result(ResultType.COMPLETED)
        except subprocess.CalledProcessError as e:
            LOG.exception("Error retrieving user from Juju")
            LOG.warning(e.stderr)
            return Result(ResultType.FAILED, str(e))

        return Result(ResultType.SKIPPED)

    def run(self, status: Optional["Status"] = None) -> Result:
        """Run the step to completion.

        Invoked when the step is run and returns a ResultType to indicate

        :return:
        """
        original_accounts = self.juju_data / "accounts.yaml"
        backup_accounts = self.data_location / "accounts.yaml.bk"

        shutil.copy(original_accounts, backup_accounts)
        backup_accounts.chmod(0o660)

        return Result(ResultType.COMPLETED)


class SaveJujuUserLocallyStep(BaseStep):
    """Save user locally."""

    def __init__(self, name: str, data_location: Path):
        super().__init__("Save User", f"Saving machine user {name} for local usage")
        self.username = name
        self.data_location = data_location

    def is_skip(self, status: Optional["Status"] = None) -> Result:
        """Determines if the step should be skipped or not.

        :return: ResultType.SKIPPED if the Step should be skipped,
                 ResultType.COMPLETED or ResultType.FAILED otherwise
        """
        try:
            juju_account = JujuAccount.load(self.data_location)
            LOG.debug(f"Local account found: {juju_account.user}")
            # TODO(gboutry): make user password updateable ?
            return Result(ResultType.SKIPPED)
        except JujuAccountNotFound:
            LOG.debug("Local account not found")
            pass

        return Result(ResultType.COMPLETED)

    def run(self, status: Optional["Status"] = None) -> Result:
        """Run the step to completion.

        Invoked when the step is run and returns a ResultType to indicate

        :return:
        """
        password = pwgen.pwgen(12)

        juju_account = JujuAccount(
            user=self.username,
            password=password,
        )
        juju_account.write(self.data_location)

        return Result(ResultType.COMPLETED)


class WriteJujuStatusStep(BaseStep, JujuStepHelper):
    """Get the status of the specified model."""

    def __init__(
        self,
        jhelper: JujuHelper,
        model: str,
        file_path: Path,
    ):
        super().__init__("Write Model status", f"Recording status of model {model}")

        self.jhelper = jhelper
        self.model = model
        self.file_path = file_path

    def is_skip(self, status: Optional["Status"] = None) -> Result:
        """Determines if the step should be skipped or not.

        :return: ResultType.SKIPPED if the Step should be skipped,
                 ResultType.COMPLETED or ResultType.FAILED otherwise
        """
        try:
            run_sync(self.jhelper.get_model(self.model))
            return Result(ResultType.COMPLETED)
        except ModelNotFoundException:
            LOG.debug(f"Model {self.model} not found")
            return Result(ResultType.SKIPPED)

    def run(self, status: Optional["Status"] = None) -> Result:
        """Run the step to completion.

        Invoked when the step is run and returns a ResultType to indicate

        :return:
        """
        try:
            LOG.debug(f"Getting juju status for model {self.model}")
            _status = run_sync(self.jhelper.get_model_status_full(self.model))
            # Running json.dump directly on the json returned by to_json
            # results in a single line. There is probably a better way of
            # doing this.
            LOG.debug(_status)
            status = json.loads(_status.to_json())

            if not self.file_path.exists():
                self.file_path.touch()
            self.file_path.chmod(0o660)
            with self.file_path.open("w") as file:
                json.dump(status, file, ensure_ascii=False, indent=4)
            return Result(ResultType.COMPLETED, "Inspecting Model Status")
        except Exception as e:  # noqa
            return Result(ResultType.FAILED, str(e))


class WriteCharmLogStep(BaseStep, JujuStepHelper):
    """Get logs for the specified model."""

    def __init__(
        self,
        jhelper: JujuHelper,
        model: str,
        file_path: Path,
    ):
        super().__init__(
            "Get charm logs model", f"Retrieving charm logs for {model} model"
        )
        self.jhelper = jhelper
        self.model = model
        self.file_path = file_path
        self.model_uuid = None

    def is_skip(self, status: Optional["Status"] = None) -> Result:
        """Determines if the step should be skipped or not.

        :return: ResultType.SKIPPED if the Step should be skipped,
                 ResultType.COMPLETED or ResultType.FAILED otherwise
        """
        try:
            model = run_sync(self.jhelper.get_model(self.model))
            self.model_uuid = model.info.uuid
            return Result(ResultType.COMPLETED)
        except ModelNotFoundException:
            LOG.debug(f"Model {self.model} not found")
            return Result(ResultType.SKIPPED)

    def run(self, status: Optional["Status"] = None) -> Result:
        """Run the step to completion.

        Invoked when the step is run and returns a ResultType to indicate

        :return:
        """
        LOG.debug(f"Getting debug logs for model {self.model}")
        try:
            # libjuju model.debug_log is broken.
            cmd = [
                self._get_juju_binary(),
                "debug-log",
                "--model",
                self.model_uuid,
                "--replay",
                "--no-tail",
            ]
            # Stream output directly to the file to avoid holding the entire
            # blob of data in RAM.

            if not self.file_path.exists():
                self.file_path.touch()
            self.file_path.chmod(0o660)
            with self.file_path.open("wb") as file:
                subprocess.check_call(cmd, stdout=file)
        except subprocess.CalledProcessError as e:
            return Result(ResultType.FAILED, str(e))

        return Result(ResultType.COMPLETED, "Inspecting Charm Log")


class JujuLoginStep(BaseStep, JujuStepHelper):
    """Login to Juju Controller."""

    def __init__(self, juju_account: JujuAccount | None):
        super().__init__(
            "Login to Juju controller", "Authenticating with Juju controller"
        )
        self.juju_account = juju_account

    def is_skip(self, status: Optional["Status"] = None) -> Result:
        """Determines if the step should be skipped or not.

        :return: ResultType.SKIPPED if the Step should be skipped,
                 ResultType.COMPLETED or ResultType.FAILED otherwise
        """
        if self.juju_account is None:
            LOG.debug("Local account not found, most likely not bootstrapped / joined")
            return Result(ResultType.SKIPPED)

        cmd = " ".join(
            [
                self._get_juju_binary(),
                "show-user",
            ]
        )
        LOG.debug(f"Running command {cmd}")
        expect_list = ["^please enter password", "{}", pexpect.EOF]
        with pexpect.spawn(cmd) as process:
            try:
                index = process.expect(expect_list, timeout=PEXPECT_TIMEOUT)
            except pexpect.TIMEOUT as e:
                LOG.debug("Process timeout")
                return Result(ResultType.FAILED, str(e))
            LOG.debug(f"Command stdout={process.before}")
        if index in (0, 1):
            return Result(ResultType.COMPLETED)
        elif index == 2:
            return Result(ResultType.SKIPPED)

        return Result(ResultType.COMPLETED)

    def run(self, status: Optional["Status"] = None) -> Result:
        """Run the step to completion.

        Invoked when the step is run and returns a ResultType to indicate

        :return:
        """
        if self.juju_account is None:
            return Result(
                ResultType.FAILED,
                "Juju account was supposed to be checked for in is_skip method.",
            )
        cmd = " ".join(
            [
                self._get_juju_binary(),
                "login",
                "--user",
                self.juju_account.user,
            ]
        )
        LOG.debug(f"Running command {cmd}")
        process = pexpect.spawn(cmd)
        try:
            process.expect("^please enter password", timeout=PEXPECT_TIMEOUT)
            process.sendline(self.juju_account.password)
            process.expect(pexpect.EOF, timeout=PEXPECT_TIMEOUT)
            process.close()
        except pexpect.TIMEOUT as e:
            LOG.debug("Process timeout")
            return Result(ResultType.FAILED, str(e))
        LOG.debug(f"Command stdout={process.before}")
        if process.exitstatus != 0:
            return Result(ResultType.FAILED, "Failed to login to Juju Controller")
        return Result(ResultType.COMPLETED)


class AddInfrastructureModelStep(BaseStep):
    """Add infrastructure model."""

    def __init__(
        self, jhelper: JujuHelper, model: str, proxy_settings: dict | None = None
    ):
        super().__init__("Add infrastructure model", "Adding infrastructure model")
        self.jhelper = jhelper
        self.model = model
        self.proxy_settings = proxy_settings or {}

    def is_skip(self, status: Optional["Status"] = None) -> Result:
        """Determines if the step should be skipped or not."""
        try:
            run_sync(self.jhelper.get_model(self.model))
            return Result(ResultType.SKIPPED)
        except ModelNotFoundException:
            LOG.debug(f"Model {self.model} not found")
        return Result(ResultType.COMPLETED)

    def run(self, status: Optional["Status"] = None) -> Result:
        """Add infrastructure model."""
        try:
            model_config = convert_proxy_to_model_configs(self.proxy_settings)
            run_sync(self.jhelper.add_model(self.model, config=model_config))
            return Result(ResultType.COMPLETED)
        except Exception as e:
            return Result(ResultType.FAILED, str(e))


class UpdateJujuModelConfigStep(BaseStep):
    """Update Model Config for the given models."""

    def __init__(self, jhelper: JujuHelper, model: str, model_configs: dict):
        super().__init__("Update Model Config", f"Updating model config for {model}")
        self.jhelper = jhelper
        self.model = model
        self.model_configs = model_configs

    def run(self, status: Optional["Status"] = None) -> Result:
        """Run the step to completion.

        Invoked when the step is run and returns a ResultType to indicate

        :return:
        """
        try:
            run_sync(self.jhelper.set_model_config(self.model, self.model_configs))
        except ModelNotFoundException as e:
            message = f"Update Model config on controller failed: {str(e)}"
            return Result(ResultType.FAILED, message)

        return Result(ResultType.COMPLETED)


class DownloadJujuControllerCharmStep(BaseStep, JujuStepHelper):
    """Download Juju Controller Charm."""

    def __init__(self, proxy_settings: dict | None = None):
        super().__init__(
            "Download Controller Charm", "Downloading Juju Controller Charm"
        )
        self.proxy_settings = proxy_settings

    def is_skip(self, status: Optional["Status"] = None) -> Result:
        """Determines if the step should be skipped or not."""
        if not self.proxy_settings:
            return Result(ResultType.SKIPPED)

        return Result(ResultType.COMPLETED)

    def run(self, status: Optional["Status"] = None) -> Result:
        """Run the step to completion.

        Invoked when the step is run and returns a ResultType to indicate

        :return:
        """
        try:
            snap = Snap()
            download_dir = snap.paths.user_common / "downloads"
            download_dir.mkdir(parents=True, exist_ok=True)
            rename_file = download_dir / JUJU_CONTROLLER_CHARM
            for charm_file in download_dir.glob("juju-controller*.charm"):
                charm_file.unlink()

            cmd = [
                self._get_juju_binary(),
                "download",
                "juju-controller",
                "--channel",
                JUJU_CHANNEL,
                "--base",
                JUJU_BASE,
            ]
            LOG.debug(f'Running command {" ".join(cmd)}')
            env = os.environ.copy()
            env.update(self.proxy_settings)
            process = subprocess.run(
                cmd,
                capture_output=True,
                cwd=download_dir,
                text=True,
                check=True,
                env=env,
            )
            LOG.debug(
                f"Command finished. stdout={process.stdout}, stderr={process.stderr}"
            )

            for charm_file in download_dir.glob("juju-controller*.charm"):
                charm_file.rename(rename_file)

            return Result(ResultType.COMPLETED)
        except subprocess.CalledProcessError as e:
            LOG.exception("Error downloading Juju Controller charm")
            return Result(ResultType.FAILED, str(e))


class AddJujuSpaceStep(BaseStep):
    def __init__(self, jhelper: JujuHelper, model: str, space: str, subnets: list[str]):
        super().__init__("Add Juju Space", f"Adding Juju Space to model {model}")
        self.jhelper = jhelper
        self.model = model
        self.space = space
        self.subnets = subnets

    async def _get_spaces_subnets_mapping(self, model: str) -> dict[str, list[str]]:
        """Return a mapping of all spaces with associated subnets."""
        spaces = await self.jhelper.get_spaces(model)
        return {
            space["name"]: [subnet["cidr"] for subnet in space["subnets"]]
            for space in spaces
        }

    @tenacity.retry(
        wait=tenacity.wait_fixed(10),
        stop=tenacity.stop_after_delay(300),
        retry=tenacity.retry_if_exception_type(ValueError),
        reraise=True,
    )
    def _wait_for_spaces(self, model: str) -> dict:
        spaces_subnets = run_sync(self._get_spaces_subnets_mapping(model))
        LOG.debug("Spaces and subnets mapping: %s", spaces_subnets)
        all_subnets = set(itertools.chain.from_iterable(spaces_subnets.values()))
        if not all_subnets:
            raise ValueError("No subnets available in any space")
        return spaces_subnets

    def is_skip(self, status: Status | None = None) -> Result:
        """Determines if the step should be skipped or not."""
        self.update_status(status, "Checking if spaces are populated")
        try:
            spaces_subnets = self._wait_for_spaces(self.model)
        except ValueError as e:
            return Result(ResultType.FAILED, str(e))

        all_subnets = set(itertools.chain.from_iterable(spaces_subnets.values()))

        self.update_status(status, "Checking spaces configuration")

        wanted_subnets = set(self.subnets)
        missing_from_all = wanted_subnets - all_subnets
        if missing_from_all:
            LOG.debug(f"Subnets {missing_from_all} are not available in any space")
            return Result(ResultType.FAILED)

        space_subnets = spaces_subnets.get(self.space)

        if space_subnets and set(space_subnets).issuperset(wanted_subnets):
            LOG.debug("Wanted subnets are already in use by the space")
            return Result(ResultType.SKIPPED)

        for space, subnets in spaces_subnets.items():
            if space == self.space or space == "alpha":
                continue
            if intersect := wanted_subnets.intersection(subnets):
                return Result(
                    ResultType.FAILED,
                    f"Subnets {intersect} are already in use by space {space}",
                )

        return Result(ResultType.COMPLETED)

    def run(self, status: Optional["Status"] = None) -> Result:
        """Run the step to completion.

        Invoked when the step is run and returns a ResultType to indicate

        :return:
        """
        self.update_status(status, "Adding space to model")
        try:
            run_sync(self.jhelper.add_space(self.model, self.space, self.subnets))
        except JujuException as e:
            message = f"Failed to create space : {str(e)}"
            return Result(ResultType.FAILED, message)

        return Result(ResultType.COMPLETED)


class BindJujuApplicationStep(BaseStep):
    """Bind all application endpoints to a space."""

    def __init__(self, jhelper: JujuHelper, model: str, app: str, space: str):
        super().__init__(
            "Bind Application", f"Binding application {app} to space {space}"
        )
        self.jhelper = jhelper
        self.model = model
        self.app = app
        self.space = space
        self._bindings = None

    def is_skip(self, status: Status | None = None) -> Result:
        """Determines if the step should be skipped or not."""
        current_bindings = run_sync(
            self.jhelper.get_application_bindings(self.model, self.app)
        )
        new_bindings = current_bindings.copy()
        for endpoint, space in current_bindings.items():
            if space != self.space:
                LOG.debug(
                    "Application's endpoint %r is bound to space %s",
                    endpoint,
                    space,
                )
                new_bindings[endpoint] = self.space

        if new_bindings == current_bindings:
            LOG.debug("Application's endpoints already bound to right spaces")
            return Result(ResultType.SKIPPED)

        self._bindings = new_bindings

        return Result(ResultType.COMPLETED)

    def run(self, status: Optional["Status"] = None) -> Result:
        """Bind application to space."""
        if not self._bindings:
            return Result(ResultType.FAILED, "Bindings not set")
        self.update_status(status, "Binding application to space")
        try:
            run_sync(self.jhelper.merge_bindings(self.model, self.app, self._bindings))
        except JujuException as e:
            message = f"Failed to bind application to space: {str(e)}"
            return Result(ResultType.FAILED, message)

        return Result(ResultType.COMPLETED)


class IntegrateJujuApplicationsStep(BaseStep):
    """Integrate two applications together."""

    def __init__(
        self,
        jhelper: JujuHelper,
        model: str,
        provider: str,
        requirer: str,
        relation: str,
    ):
        super().__init__(
            "Integrate Applications",
            f"Integrating {requirer} and {provider}",
        )
        self.jhelper = jhelper
        self.model = model
        self.provider = provider
        self.requirer = requirer
        self.relation = relation

    def is_skip(self, status: Status | None) -> Result:
        """Determines if the step should be skipped or not."""
        are_integrated = run_sync(
            self.jhelper.are_integrated(
                self.model, self.requirer, self.provider, self.relation
            )
        )
        if are_integrated:
            return Result(ResultType.SKIPPED)
        return Result(ResultType.COMPLETED)

    def run(self, status: Status | None) -> Result:
        """Integrate applications."""
        try:
            run_sync(
                self.jhelper.integrate(
                    self.model, self.requirer, self.provider, self.relation
                )
            )
        except ApplicationNotFoundException as e:
            return Result(ResultType.FAILED, str(e))
        # Juju might take a while to start integrate the applications
        time.sleep(15)
        apps = [self.requirer, self.provider]
        try:
            run_sync(
                self.jhelper.wait_until_active(
                    self.model,
                    apps,
                    timeout=1200,
                )
            )
        except (JujuWaitException, TimeoutException) as e:
            LOG.warning(str(e))
            return Result(ResultType.FAILED, str(e))
        return Result(ResultType.COMPLETED)


class UpdateJujuMachineIDStep(BaseStep):
    """Make sure machines IDs have been updated in Juju."""

    def __init__(
        self,
        client: Client,
        jhelper: JujuHelper,
        model: str,
        application: str,
    ):
        super().__init__("Update Machine ID", f"Updating machine ID for {application}")
        self.client = client
        self.jhelper = jhelper
        self.model = model
        self.application = application
        self.nodes_to_update = []
        self.hostname_id = {}

    def is_skip(self, status: Status | None) -> Result:
        """Determines if the step should be skipped or not."""
        clusterd_nodes = self.client.cluster.list_nodes()
        if len(clusterd_nodes) == 0:
            return Result(ResultType.SKIPPED)

        model_status = run_sync(
            self.jhelper.get_model_status(self.model, [self.application])
        )
        juju_machines = run_sync(self.jhelper.get_machines(self.model))

        if self.application not in model_status["applications"]:
            return Result(ResultType.FAILED, "Application not found in model")

        app_status = model_status["applications"][self.application]

        machines_ids = []
        for name, unit in app_status["units"].items():
            machines_ids.append(int(unit["machine"]))
        hostname_id = {}
        for id, machine in juju_machines.items():
            if int(id) in machines_ids:
                hostname_id[machine.hostname] = int(id)

        if len(hostname_id) != len(machines_ids):
            return Result(ResultType.FAILED, "Not all machines found in Juju")

        nodes_to_update = []
        for node in clusterd_nodes:
            node_machine_id = node["machineid"]
            for id, machine in juju_machines.items():
                if node["name"] == machine.hostname:
                    if int(id) != node_machine_id and node_machine_id != -1:
                        return Result(
                            ResultType.FAILED,
                            f"Machine {node['name']} already exists in model"
                            f" {self.model} with id {id},"
                            f" expected the id {node['machineid']}.",
                        )
                    if (
                        node["systemid"] != machine.instance_id
                        and node["systemid"] != ""  # noqa: W503
                    ):
                        return Result(
                            ResultType.FAILED,
                            f"Machine {node['name']} already exists in model"
                            f" {self.model} with systemid {machine.instance_id},"
                            f" expected the systemid {node['systemid']}.",
                        )
                    if node_machine_id == -1:
                        nodes_to_update.append(node)
                    break

        self.hostname_id = hostname_id
        self.nodes_to_update = nodes_to_update

        if not nodes_to_update:
            return Result(ResultType.SKIPPED)

        return Result(ResultType.COMPLETED)

    def run(self, status: Status | None) -> Result:
        """Integrate applications."""
        for node in self.nodes_to_update:
            LOG.debug(f"Updating machine {node['name']} in model {self.model}")
            if (machine_id := self.hostname_id.get(node["name"])) is not None:
                self.client.cluster.update_node_info(node["name"], machineid=machine_id)
            else:
                return Result(
                    ResultType.FAILED, f"Machine ID not found for node {node['name']}"
                )
        return Result(ResultType.COMPLETED)
