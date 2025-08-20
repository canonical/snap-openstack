# SPDX-FileCopyrightText: 2023 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import copy
import itertools
import json
import logging
import os
import re
import shutil
import socket
import subprocess
import tempfile
import textwrap
import time
import typing
from pathlib import Path

import jubilant
import pexpect  # type: ignore [import-untyped]
import tenacity
import yaml
from rich.status import Status
from snaphelpers import Snap

from sunbeam.clusterd.client import Client
from sunbeam.clusterd.service import (
    JujuUserNotFoundException,
    NodeNotExistInClusterException,
)
from sunbeam.core.common import (
    BaseStep,
    Result,
    ResultType,
    convert_proxy_to_model_configs,
)
from sunbeam.core.deployment import Deployment
from sunbeam.core.deployments import DeploymentsConfig
from sunbeam.core.juju import (
    CONTROLLER_MODEL,
    ApplicationNotFoundException,
    ControllerNotFoundException,
    JujuAccount,
    JujuAccountNotFound,
    JujuController,
    JujuException,
    JujuHelper,
    JujuStepHelper,
    JujuWaitException,
    ModelNotFoundException,
)
from sunbeam.utils import random_string
from sunbeam.versions import JUJU_BASE, JUJU_CHANNEL

LOG = logging.getLogger(__name__)
PEXPECT_TIMEOUT = 60
BOOTSTRAP_CONFIG_KEY = "BootstrapAnswers"
JUJU_CONTROLLER_CHARM = "juju-controller.charm"


class AddCloudJujuStep(BaseStep, JujuStepHelper):
    """Add cloud definition to juju client."""

    def __init__(self, cloud: str, definition: dict, controller: str | None = None):
        super().__init__("Add Cloud", "Adding cloud to Juju")

        self.cloud = cloud
        self.definition = definition
        self.controller = controller

    def is_skip(self, status: Status | None = None) -> Result:
        """Determines if the step should be skipped or not.

        :return: ResultType.SKIPPED if the Step should be skipped,
                 ResultType.COMPLETED or ResultType.FAILED otherwise
        """
        cloud_type = self.definition["clouds"][self.cloud]["type"]

        # Check clouds on client
        try:
            juju_clouds = self.get_clouds(cloud_type, local=True)
        except subprocess.CalledProcessError as e:
            LOG.exception(
                "Error determining whether to skip the bootstrap "
                "process. Defaulting to not skip."
            )
            LOG.warning(e.stderr)
            return Result(ResultType.FAILED, str(e))

        if self.cloud not in juju_clouds:
            return Result(ResultType.COMPLETED)

        if not self.controller:
            return Result(ResultType.SKIPPED)

        # Check clouds on controller
        try:
            juju_clouds_on_controller = self.get_clouds(
                cloud_type, local=False, controller=self.controller
            )
        except subprocess.CalledProcessError as e:
            LOG.exception(
                "Error determining whether to skip the bootstrap "
                "process. Defaulting to not skip."
            )
            LOG.warning(e.stderr)
            return Result(ResultType.FAILED, str(e))

        if self.cloud in juju_clouds_on_controller:
            return Result(ResultType.SKIPPED)
        return Result(ResultType.COMPLETED)

    def run(self, status: Status | None = None) -> Result:
        """Run the step to completion.

        Invoked when the step is run and returns a ResultType to indicate

        :return:
        """
        try:
            result = self.add_cloud(self.cloud, self.definition, self.controller)
            if not result:
                return Result(ResultType.FAILED, "Unable to create cloud")
        except subprocess.CalledProcessError as e:
            LOG.debug(e.stderr)
            LOG.debug(str(e))

            message = None
            if "already exists" in e.stderr:
                return Result(ResultType.COMPLETED)
            elif (
                "Could not upload cloud to a controller: permission denied" in e.stderr
            ):
                message = (
                    "Error in adding cloud: Missing user permissions to add cloud. "
                    "User should have either admin or superuser privileges"
                )
            else:
                message = f"Error in adding cloud: {e.stderr}"

            return Result(ResultType.FAILED, message)
        return Result(ResultType.COMPLETED)


class AddCredentialsJujuStep(BaseStep, JujuStepHelper):
    """Add credentials definition to juju client."""

    def __init__(
        self, cloud: str, credentials: str, definition: dict, controller: str | None
    ):
        super().__init__("Add Credentials", "Adding credentials to Juju client")

        self.cloud = cloud
        self.credentials_name = credentials
        self.definition = definition
        self.controller = controller

    def is_skip(self, status: Status | None = None) -> Result:
        """Determines if the step should be skipped or not.

        :return: ResultType.SKIPPED if the Step should be skipped,
                 ResultType.COMPLETED or ResultType.FAILED otherwise
        """
        local = not bool(self.controller)
        try:
            credentials = self.get_credentials(self.cloud, local=local)
        except subprocess.CalledProcessError as e:
            # credentials not found
            if "not found" in e.stderr:
                return Result(ResultType.COMPLETED)

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

    def run(self, status: Status | None = None) -> Result:
        """Run the step to completion.

        Invoked when the step is run and returns a ResultType to indicate

        :return:
        """
        try:
            self.add_credential(self.cloud, self.definition, self.controller)
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
        self.juju_clouds: list = []

        home = os.environ.get("SNAP_REAL_HOME")
        os.environ["JUJU_DATA"] = f"{home}/.local/share/juju"

    def is_skip(self, status: Status | None = None) -> Result:
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

    def run(self, status: Status | None = None) -> Result:
        """Run the step to completion.

        Invoked when the step is run and returns a ResultType to indicate

        :return:
        """
        try:
            config_args: dict[str, typing.Any] = {}
            bootstrap_args_without_configs: list[str] = []

            # Write all --config args to a file and pass
            # --config <configfile> option to bootstrap command
            # Separate bootstrap config args to config_args and rest of the
            # bootstrap args to bootstrap_args_without_configs
            index = 0
            while index < len(self.bootstrap_args):
                arg = self.bootstrap_args[index]
                if arg == "--config":
                    index += 1

                    try:
                        k, v = self.bootstrap_args[index].split("=", 1)
                    except ValueError as e:
                        LOG.exception("Error bootstrapping Juju")
                        LOG.debug(str(e))
                        msg = (
                            "Value Error: Expected boostrap argument in form of "
                            f"key=value, got {self.bootstrap_args[index]}"
                        )
                        return Result(ResultType.FAILED, msg)

                    config_args[k] = v
                else:
                    bootstrap_args_without_configs.append(arg)

                index += 1

            if "HTTP_PROXY" in self.proxy_settings:
                config_args["juju-http-proxy"] = self.proxy_settings.get("HTTP_PROXY")
                config_args["snap-http-proxy"] = self.proxy_settings.get("HTTP_PROXY")
            if "HTTPS_PROXY" in self.proxy_settings:
                config_args["juju-https-proxy"] = self.proxy_settings.get("HTTPS_PROXY")
                config_args["snap-https-proxy"] = self.proxy_settings.get("HTTPS_PROXY")
            if "NO_PROXY" in self.proxy_settings:
                config_args["juju-no-proxy"] = self.proxy_settings.get("NO_PROXY")

            configs_to_print = copy.deepcopy(config_args)
            if "admin-secret" in configs_to_print:
                configs_to_print["admin-secret"] = "********"

            # Write config args into a temporary file
            with tempfile.NamedTemporaryFile(delete=False) as config_file:
                config_file.write(yaml.dump(config_args).encode("utf-8"))
                config_file.flush()

                cmd = [self._get_juju_binary(), "bootstrap"]
                cmd.extend(bootstrap_args_without_configs)
                cmd.extend([self.cloud, self.controller])
                cmd.extend(["--config", config_file.name])

                env = os.environ.copy()
                env.update(self.proxy_settings)

                LOG.debug(f"Running command {' '.join(cmd)}")
                LOG.debug(f"Bootstrap configs used: {configs_to_print}")
                process = subprocess.run(
                    cmd, capture_output=True, text=True, check=True, env=env
                )
                LOG.debug(
                    f"Command finished. stdout={process.stdout}, "
                    f"stderr={process.stderr}"
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
        LOG.debug(f"Running command {' '.join(cmd)}")
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
        LOG.debug(f"Running command {' '.join(cmd)}")
        process = subprocess.run(cmd, capture_output=True, text=True, check=True)
        LOG.debug(f"Command finished. stdout={process.stdout}, stderr={process.stderr}")
        return Result(ResultType.COMPLETED)


class DestroyJujuControllerStep(BaseStep, JujuStepHelper):
    def __init__(
        self,
        deployment: Deployment,
        destroy_args: list[str] | None = None,
    ):
        super().__init__("Destroy Juju", "Destroy Juju controller")
        self.deployment = deployment
        self.controller = deployment.juju_controller
        self.destroy_args = destroy_args or []

        home = os.environ.get("SNAP_REAL_HOME")
        os.environ["JUJU_DATA"] = f"{home}/.local/share/juju"

    def is_skip(self, status: Status | None = None) -> Result:
        """Determines if the step should be skipped or not.

        :return: ResultType.SKIPPED if the Step should be skipped,
                 ResultType.COMPLETED or ResultType.FAILED otherwise
        """
        if self.controller and self.controller.is_external:
            LOG.warning("External controller, refusing to destroy")
            return Result(ResultType.SKIPPED)

        try:
            self.get_controller(self.deployment.controller)
        except ControllerNotFoundException:
            return Result(ResultType.SKIPPED)
        return Result(ResultType.COMPLETED)

    def run(self, status: Status | None = None) -> Result:
        """Destroy juju-controller."""
        cmd = [
            self._get_juju_binary(),
            "destroy-controller",
            "--destroy-all-models",
            "--destroy-storage",
            "--no-prompt",
            self.deployment.controller,
        ]
        if self.destroy_args:
            cmd.extend(self.destroy_args)

        try:
            process = subprocess.run(cmd, capture_output=True, text=True, check=True)
        except subprocess.CalledProcessError as e:
            LOG.exception("Error destroying controller")
            return Result(ResultType.FAILED, str(e))
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

    def is_skip(self, status: Status | None = None) -> Result:
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

    def run(self, status: Status | None = None) -> Result:
        """Run the step to completion.

        Invoked when the step is run and returns a ResultType to indicate

        :return:
        """
        try:
            cmd = [self._get_juju_binary(), "add-user", self.username]
            LOG.debug(f"Running command {' '.join(cmd)}")
            process = subprocess.run(cmd, capture_output=True, text=True, check=True)
            LOG.debug(
                f"Command finished. stdout={process.stdout}, stderr={process.stderr}"
            )

            re_groups = re.search(
                self.registration_token_regex, process.stdout, re.MULTILINE
            )
            if re_groups is None:
                return Result(ResultType.FAILED, "Not able to parse registration token")
            token = re_groups.group(1)
            if not token:
                return Result(ResultType.FAILED, "Not able to parse registration token")

            # Grant superuser access to user.
            cmd = [self._get_juju_binary(), "grant", self.username, "superuser"]
            LOG.debug(f"Running command {' '.join(cmd)}")
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
            LOG.debug(f"Running command {' '.join(cmd)}")
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

    def run(self, status: Status | None = None) -> Result:
        """Run the step to completion.

        Invoked when the step is run and returns a ResultType to indicate

        :return:
        """
        try:
            model_with_owner = self.get_model_name_with_owner(self.model)
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
            LOG.debug(f"Running command {' '.join(cmd)}")
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

    def is_skip(self, status: Status | None = None) -> Result:
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

    def run(self, status: Status | None = None) -> Result:
        """Run the step to completion.

        Invoked when the step is run and returns a ResultType to indicate

        :return:
        """
        try:
            cmd = [self._get_juju_binary(), "remove-user", self.username, "--yes"]
            LOG.debug(f"Running command {' '.join(cmd)}")
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

    juju_account: JujuAccount
    registration_token: str | None

    def __init__(
        self,
        client: Client | None,
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

        self.home = os.environ.get("SNAP_REAL_HOME")
        os.environ["JUJU_DATA"] = f"{self.home}/.local/share/juju"

    def is_skip(self, status: Status | None = None) -> Result:
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

        if self.client is None:
            return Result(
                ResultType.FAILED, "Client is not provided, only valid for remote users"
            )
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

    def run(self, status: Status | None = None) -> Result:
        """Run the step to completion.

        Invoked when the step is run and returns a ResultType to indicate

        :return:
        """
        if not self.registration_token:
            return Result(
                ResultType.FAILED, "No registration token found in Cluster database"
            )

        if self.replace:
            # If controller exists with same name, unregister the controller.
            try:
                self.get_controller(self.controller)
                cmd = [
                    self._get_juju_binary(),
                    "unregister",
                    self.controller,
                    "--no-prompt",
                ]
                LOG.debug(f"Running command {' '.join(cmd)}")
                process = subprocess.run(
                    cmd, capture_output=True, text=True, check=True
                )
                LOG.debug(
                    f"Command finished. stdout={process.stdout}, "
                    f"stderr={process.stderr}"
                )
            except ControllerNotFoundException:
                LOG.debug("No controller found to replace, register the juju user")
            except subprocess.CalledProcessError as e:
                return Result(
                    ResultType.FAILED,
                    f"Error in unregistering controller {self.controller}: {str(e)}",
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

        register_args = ["--debug", "register", self.registration_token]
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
                        result = child.before.decode()
                        # If controller already exists, the command keeps on asking
                        # controller name, so change the controller name to dummy.
                        # The command errors out at the next stage that controller
                        # is already registered.
                        if f'Controller "{self.controller}" already exists' in result:
                            child.sendline("dummy")
                        else:
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


class RegisterRemoteJujuUserStep(RegisterJujuUserStep):
    """Register remote user/controller in juju."""

    registration_token: str

    def __init__(
        self,
        token: str,
        controller: str,
        data_location: Path,
        replace: bool = False,
    ):
        # User name not required to register a user. Pass empty string to
        # base class as user name
        super().__init__(None, "", controller, data_location, replace)
        self.registration_token = token
        self.account_file = f"{self.controller}.yaml"

    def is_skip(self, status: Status | None = None) -> Result:
        """Determines if the step should be skipped or not.

        :return: ResultType.SKIPPED if the Step should be skipped,
                 ResultType.COMPLETED or ResultType.FAILED otherwise
        """
        try:
            self.juju_account = JujuAccount.load(self.data_location, self.account_file)
            LOG.debug(f"Local account found for {self.controller}")
        except JujuAccountNotFound:
            password = random_string(12)
            self.juju_account = JujuAccount(user="REPLACE_USER", password=password)
            self.juju_account.write(self.data_location, self.account_file)

        return Result(ResultType.COMPLETED)

    def _get_user_from_local_juju(self, controller: str) -> str | None:
        """Get user name from local juju accounts file."""
        try:
            with open(f"{self.home}/.local/share/juju/accounts.yaml") as f:
                accounts = yaml.safe_load(f)
                user = (
                    accounts.get("controllers", {}).get(self.controller, {}).get("user")
                )
                LOG.debug(f"Found user from accounts.yaml for {controller}: {user}")
        except FileNotFoundError as e:
            LOG.debug(f"Error in retrieving local user: {str(e)}")
            user = None

        return user

    def run(self, status: Status | None = None) -> Result:
        """Run the step to completion.

        Invoked when the step is run and returns a ResultType to indicate

        :return:
        """
        result = super().run()
        if result.result_type != ResultType.COMPLETED:
            # Delete the account file created in skip step
            account_file = self.data_location / self.account_file
            account_file.unlink(missing_ok=True)
            return result

        # Update user name from local juju accounts.yaml
        user = self._get_user_from_local_juju(self.controller)
        if not user:
            return Result(ResultType.FAILED, "User not updated in local juju client")

        if self.juju_account.user != user:
            self.juju_account.user = user
            LOG.debug(f"Updating user in {self.juju_account} file")
            self.juju_account.write(self.data_location, self.account_file)

        return Result(ResultType.COMPLETED)


class UnregisterJujuController(BaseStep, JujuStepHelper):
    """Unregister an external Juju controller."""

    def __init__(self, controller: str, data_location: Path):
        super().__init__(
            "Unregister Juju controller", f"Unregistering juju controller {controller}"
        )
        self.controller = controller
        self.account_file = data_location / f"{self.controller}.yaml"

    def is_skip(self, status: Status | None = None) -> Result:
        """Determines if the step should be skipped or not.

        :return: ResultType.SKIPPED if the Step should be skipped,
                 ResultType.COMPLETED or ResultType.FAILED otherwise
        """
        try:
            self.get_controller(self.controller)
        except ControllerNotFoundException:
            self.account_file.unlink(missing_ok=True)
            LOG.warning(
                f"Controller {self.controller} not found, skipping unregsiter "
                "controller"
            )
            return Result(ResultType.SKIPPED)

        return Result(ResultType.COMPLETED)

    def run(self, status: Status | None = None) -> Result:
        """Run the step to completion.

        Invoked when the step is run and returns a ResultType to indicate

        :return:
        """
        try:
            cmd = [
                self._get_juju_binary(),
                "unregister",
                self.controller,
                "--no-prompt",
            ]
            LOG.debug(f"Running command {' '.join(cmd)}")
            process = subprocess.run(cmd, capture_output=True, text=True, check=True)
            LOG.debug(
                f"Command finished. stdout={process.stdout}, stderr={process.stderr}"
            )
            self.account_file.unlink(missing_ok=True)
        except subprocess.CalledProcessError as e:
            return Result(ResultType.FAILED, str(e))

        return Result(ResultType.COMPLETED)


class AddJujuMachineStep(BaseStep, JujuStepHelper):
    """Add machine in juju."""

    model_with_owner: str

    def __init__(self, ip: str, model: str, jhelper: JujuHelper):
        super().__init__("Add machine", "Adding machine to Juju model")

        self.machine_ip = ip
        self.model = model
        self.jhelper = jhelper

        home = os.environ.get("SNAP_REAL_HOME")
        os.environ["JUJU_DATA"] = f"{home}/.local/share/juju"

    def is_skip(self, status: Status | None = None) -> Result:
        """Determines if the step should be skipped or not.

        :return: ResultType.SKIPPED if the Step should be skipped,
                 ResultType.COMPLETED or ResultType.FAILED otherwise
        """
        try:
            self.model_with_owner = self.get_model_name_with_owner(self.model)
        except ModelNotFoundException as e:
            LOG.debug(str(e))
            return Result(ResultType.FAILED, "Model not found")

        try:
            machines = self._juju_cmd("machines", "-m", self.model_with_owner)
            LOG.debug(f"Found machines: {machines}")
            machines = machines.get("machines", {})

            for machine, details in machines.items():
                if self.machine_ip in details.get("ip-addresses", []):
                    LOG.debug("Machine already exists")
                    return Result(ResultType.SKIPPED, machine)

        except subprocess.CalledProcessError as e:
            LOG.exception("Error retrieving machines list from Juju")
            LOG.warning(e.stderr)
            return Result(ResultType.FAILED, str(e))

        return Result(ResultType.COMPLETED)

    @tenacity.retry(
        wait=tenacity.wait_fixed(5), stop=tenacity.stop_after_attempt(20), reraise=True
    )
    def _wait_for_machine(self, machine_ip: str) -> str:
        """Wait for machine to report it's ip address."""
        machines = self._juju_cmd("machines", "-m", self.model_with_owner)
        LOG.debug(f"Found machines: {machines}")
        machines = machines.get("machines", {})
        for machine, details in machines.items():
            if machine_ip in details.get("ip-addresses", []):
                return machine
        raise ValueError("Machine not found")

    def run(self, status: Status | None = None) -> Result:
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
                ["add-machine", "-m", self.model_with_owner, f"ssh:{self.machine_ip}"],
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
            try:
                machine = self._wait_for_machine(self.machine_ip)
            except ValueError:
                # respond with machine id as -1 if machine is not reflected in juju
                machine = "-1"

            return Result(ResultType.COMPLETED, machine)
        except pexpect.TIMEOUT as e:
            LOG.exception("Error adding machine {self.machine_ip} to Juju")
            LOG.warning(e)
            return Result(ResultType.FAILED, "TIMED OUT to add machine")


class RemoveJujuMachineStep(BaseStep, JujuStepHelper):
    """Remove machine in juju."""

    def __init__(self, client: Client, name: str, jhelper: JujuHelper, model: str):
        super().__init__("Remove machine", f"Removing machine {name} from Juju model")

        self.client = client
        self.jhelper = jhelper
        self.node_name = name
        self.model = model
        self.machine_id = -1
        self.model_with_owner: str | None = None
        home = os.environ.get("SNAP_REAL_HOME")
        os.environ["JUJU_DATA"] = f"{home}/.local/share/juju"

    def is_skip(self, status: Status | None = None) -> Result:
        """Determines if the step should be skipped or not.

        :return: ResultType.SKIPPED if the Step should be skipped,
                 ResultType.COMPLETED or ResultType.FAILED otherwise
        """
        try:
            node = self.client.cluster.get_node_info(self.node_name)
            self.machine_id = node.get("machineid", -1)
        except NodeNotExistInClusterException as e:
            return Result(ResultType.SKIPPED, str(e))

        self.model_with_owner = self.get_model_name_with_owner(self.model)
        try:
            machines = self._juju_cmd("machines", "-m", self.model_with_owner)
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

    def run(self, status: Status | None = None) -> Result:
        """Run the step to completion.

        Invoked when the step is run and returns a ResultType to indicate

        :return:
        """
        if self.model_with_owner is None:
            return Result(ResultType.FAILED, "Model not found")
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
                self.model_with_owner,
                str(self.machine_id),
                "--no-prompt",
            ]
            LOG.debug(f"Running command {' '.join(cmd)}")
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

    def is_skip(self, status: Status | None = None) -> Result:
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

    def run(self, status: Status | None = None) -> Result:
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

    def is_skip(self, status: Status | None = None) -> Result:
        """Determines if the step should be skipped or not.

        :return: ResultType.SKIPPED if the Step should be skipped,
                 ResultType.COMPLETED or ResultType.FAILED otherwise
        """
        try:
            juju_account = JujuAccount.load(self.data_location)
            LOG.debug(f"Local account found: {juju_account.user}")
            if juju_account.user == self.username:
                # TODO(gboutry): make user password updateable ?
                return Result(ResultType.SKIPPED)
        except JujuAccountNotFound:
            LOG.debug("Local account not found")
            pass

        return Result(ResultType.COMPLETED)

    def run(self, status: Status | None = None) -> Result:
        """Run the step to completion.

        Invoked when the step is run and returns a ResultType to indicate

        :return:
        """
        password = random_string(12)

        juju_account = JujuAccount(
            user=self.username,
            password=password,
        )
        juju_account.write(self.data_location)

        return Result(ResultType.COMPLETED)


class SaveJujuRemoteUserLocallyStep(SaveJujuUserLocallyStep):
    """Save remote juju user locally in accounts yaml file."""

    def __init__(self, controller: str, data_location: Path):
        # TODO(hemanth): Replace empty string with username
        super().__init__("", data_location)
        self.controller = controller

    def run(self, status: Status | None = None) -> Result:
        """Run the step to completion.

        Invoked when the step is run and returns a ResultType to indicate

        :return:
        """
        juju_account = JujuAccount.load(self.data_location, f"{self.controller}.yaml")
        juju_account.write(self.data_location)

        return Result(ResultType.COMPLETED)


class SaveJujuAdminUserLocallyStep(BaseStep):
    """Save juju admin user locally in accounts yaml file.

    Save only if the current user is admin.
    """

    def __init__(self, controller: str, data_location: Path):
        super().__init__("Save Admin User", "Saving admin user for local usage")
        self.controller = controller
        self.data_location = data_location
        self.home = os.environ.get("SNAP_REAL_HOME")

    def _get_credentials_from_local_juju(
        self, controller: str
    ) -> tuple[str, str] | None:
        """Get user name from local juju accounts file."""
        try:
            with open(f"{self.home}/.local/share/juju/accounts.yaml") as f:
                accounts = yaml.safe_load(f)
                user = (
                    accounts.get("controllers", {}).get(self.controller, {}).get("user")
                )
                password = (
                    accounts.get("controllers", {})
                    .get(self.controller, {})
                    .get("password")
                )
                LOG.debug(f"Found user from accounts.yaml for {controller}: {user}")
                return user, password
        except FileNotFoundError as e:
            LOG.debug(f"Error in retrieving local user: {str(e)}")
            user = None

        return None

    def run(self, status: Status | None = None) -> Result:
        """Run the step to completion.

        Invoked when the step is run and returns a ResultType to indicate

        :return:
        """
        account = self._get_credentials_from_local_juju(self.controller)
        if account is None:
            return Result(ResultType.FAILED, "Error in retrieving Juju acccount")

        user = account[0]
        if user == "admin":
            juju_account = JujuAccount(
                user=user,
                password=account[1],
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

    def is_skip(self, status: Status | None = None) -> Result:
        """Determines if the step should be skipped or not.

        :return: ResultType.SKIPPED if the Step should be skipped,
                 ResultType.COMPLETED or ResultType.FAILED otherwise
        """
        if self.jhelper.model_exists(self.model):
            return Result(ResultType.COMPLETED)
        LOG.debug(f"Model {self.model} not found")
        return Result(ResultType.SKIPPED)

    def run(self, status: Status | None = None) -> Result:
        """Run the step to completion.

        Invoked when the step is run and returns a ResultType to indicate

        :return:
        """
        try:
            LOG.debug(f"Getting juju status for model {self.model}")
            model_status = self.jhelper.get_model_status(self.model)
            LOG.debug(model_status)

            if not self.file_path.exists():
                self.file_path.touch()
            self.file_path.chmod(0o660)
            with self.file_path.open("w") as file:
                json.dump(model_status, file, ensure_ascii=False, indent=4)
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
        self.model_uuid: str | None = None

    def is_skip(self, status: Status | None = None) -> Result:
        """Determines if the step should be skipped or not.

        :return: ResultType.SKIPPED if the Step should be skipped,
                 ResultType.COMPLETED or ResultType.FAILED otherwise
        """
        try:
            model = self.jhelper.get_model(self.model)
            self.model_uuid = model.get("model-uuid")
            return Result(ResultType.COMPLETED)
        except ModelNotFoundException:
            LOG.debug(f"Model {self.model} not found")
            return Result(ResultType.SKIPPED)

    def run(self, status: Status | None = None) -> Result:
        """Run the step to completion.

        Invoked when the step is run and returns a ResultType to indicate

        :return:
        """
        if not self.model_uuid:
            return Result(ResultType.FAILED, "Model UUID not found")
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

    def is_skip(self, status: Status | None = None) -> Result:
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

    def run(self, status: Status | None = None) -> Result:
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


class AddJujuModelStep(BaseStep):
    """Add model with configs."""

    def __init__(
        self,
        jhelper: JujuHelper,
        model: str,
        cloud: str,
        credential: str | None = None,
        proxy_settings: dict | None = None,
        model_config: dict | None = None,
    ):
        super().__init__(f"Add model {model}", f"Adding model {model}")
        self.jhelper = jhelper
        self.model = model
        self.cloud = cloud
        self.credential = credential
        self.proxy_settings = proxy_settings or {}
        self.model_config = model_config or {}

    def is_skip(self, status: Status | None = None) -> Result:
        """Determines if the step should be skipped or not."""
        if self.jhelper.model_exists(self.model):
            return Result(ResultType.SKIPPED)
        LOG.debug(f"Model {self.model} not found")
        return Result(ResultType.COMPLETED)

    def run(self, status: Status | None = None) -> Result:
        """Add model with configs."""
        try:
            self.model_config.update(
                convert_proxy_to_model_configs(self.proxy_settings)
            )
            LOG.debug(f"Adding model {self.model} with config: {self.model_config}")

            self.jhelper.add_model(
                self.model,
                cloud=self.cloud,
                credential=self.credential,
                config=self.model_config,
            )
            return Result(ResultType.COMPLETED)
        except Exception as e:
            return Result(ResultType.FAILED, str(e))


class DestroyJujuModelStep(BaseStep):
    def __init__(
        self,
        jhelper: JujuHelper,
        model: str,
        timeout: int = 1800,
    ):
        super().__init__(f"Destroy model {model}", f"Destroying model {model}")
        self.jhelper = jhelper
        self.model = model
        self.timeout = timeout

    def is_skip(self, status: Status | None = None) -> Result:
        """Determines if the step should be skipped or not."""
        if self.jhelper.model_exists(self.model):
            return Result(ResultType.COMPLETED)
        LOG.debug(f"Model {self.model} not found")
        return Result(ResultType.SKIPPED)

    def run(self, status: Status | None = None) -> Result:
        """Add model with configs."""
        try:
            self.jhelper.destroy_model(self.model, destroy_storage=True)
            self.jhelper.wait_model_gone(
                self.model,
                timeout=self.timeout,
            )
        except Exception as e:
            LOG.debug(f"Error destroying model {self.model}", exc_info=True)
            return Result(ResultType.FAILED, str(e))

        return Result(ResultType.COMPLETED)


class UpdateJujuModelConfigStep(BaseStep):
    """Update Model Config for the given models."""

    def __init__(self, jhelper: JujuHelper, model: str, model_configs: dict):
        super().__init__("Update Model Config", f"Updating model config for {model}")
        self.jhelper = jhelper
        self.model = model
        self.model_configs = model_configs

    def run(self, status: Status | None = None) -> Result:
        """Run the step to completion.

        Invoked when the step is run and returns a ResultType to indicate

        :return:
        """
        try:
            self.jhelper.set_model_config(self.model, self.model_configs)
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
        self.proxy_settings = proxy_settings or {}

    def is_skip(self, status: Status | None = None) -> Result:
        """Determines if the step should be skipped or not."""
        if not self.proxy_settings:
            return Result(ResultType.SKIPPED)

        return Result(ResultType.COMPLETED)

    def run(self, status: Status | None = None) -> Result:
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
            LOG.debug(f"Running command {' '.join(cmd)}")
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

    def _get_spaces_subnets_mapping(self, model: str) -> dict[str, list[str]]:
        """Return a mapping of all spaces with associated subnets."""
        spaces = self.jhelper.get_spaces(model)
        return {space["name"]: list(space["subnets"].keys()) for space in spaces}

    @tenacity.retry(
        wait=tenacity.wait_fixed(10),
        stop=tenacity.stop_after_delay(300),
        retry=tenacity.retry_if_exception_type(ValueError),
        reraise=True,
    )
    def _wait_for_spaces(self, model: str) -> dict:
        spaces_subnets = self._get_spaces_subnets_mapping(model)
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

    def run(self, status: Status | None = None) -> Result:
        """Run the step to completion.

        Invoked when the step is run and returns a ResultType to indicate

        :return:
        """
        self.update_status(status, "Adding space to model")
        try:
            self.jhelper.add_space(self.model, self.space, self.subnets)
        except JujuException as e:
            message = f"Failed to create space : {str(e)}"
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

    def is_skip(self, status: Status | None = None) -> Result:
        """Determines if the step should be skipped or not."""
        are_integrated = self.jhelper.are_integrated(
            self.model, self.requirer, self.provider, self.relation
        )

        if are_integrated:
            return Result(ResultType.SKIPPED)
        return Result(ResultType.COMPLETED)

    def run(self, status: Status | None) -> Result:
        """Integrate applications."""
        try:
            self.jhelper.integrate(
                self.model, self.requirer, self.provider, self.relation
            )
        except ApplicationNotFoundException as e:
            return Result(ResultType.FAILED, str(e))
        # Juju might take a while to start integrate the applications
        time.sleep(15)
        apps = [self.requirer, self.provider]
        try:
            self.jhelper.wait_until_active(
                self.model,
                apps,
                timeout=1200,
            )
        except (JujuWaitException, TimeoutError) as e:
            LOG.warning(str(e))
            return Result(ResultType.FAILED, str(e))
        return Result(ResultType.COMPLETED)


class UpdateJujuMachineIDStep(BaseStep):
    """Make sure machines IDs have been updated in Juju."""

    nodes_to_update: list[dict[str, typing.Any]]
    hostname_id: dict[str, int]

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

    def is_skip(self, status: Status | None = None) -> Result:
        """Determines if the step should be skipped or not."""
        clusterd_nodes = self.client.cluster.list_nodes()
        if len(clusterd_nodes) == 0:
            return Result(ResultType.SKIPPED)

        model_status = self.jhelper.get_model_status(self.model)
        juju_machines = model_status.machines

        if self.application not in model_status.apps:
            return Result(ResultType.FAILED, "Application not found in model")

        app_status = model_status.apps[self.application]

        machines_ids = []
        for name, unit in app_status.units.items():
            machines_ids.append(unit.machine)
        hostname_id = {}
        for id, machine in juju_machines.items():
            if id in machines_ids:
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
                        and node["systemid"] != ""
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


class SwitchToController(BaseStep, JujuStepHelper):
    """Switch to controller in juju."""

    def __init__(
        self,
        controller: str,
    ):
        super().__init__(
            "Switch to juju controller", f"Switching to juju controller {controller}"
        )
        self.controller = controller

    def run(self, status: Status | None = None) -> Result:
        """Switch to juju controller."""
        try:
            cmd = [self._get_juju_binary(), "switch", self.controller]
            LOG.debug(f"Running command {' '.join(cmd)}")
            process = subprocess.run(cmd, capture_output=True, text=True, check=True)
            LOG.debug(
                f"Command finished. stdout={process.stdout}, stderr={process.stderr}"
            )
        except subprocess.CalledProcessError as e:
            LOG.exception(f"Error in switching the controller to {self.controller}")
            return Result(ResultType.FAILED, str(e))

        return Result(ResultType.COMPLETED)


class SaveControllerStep(BaseStep, JujuStepHelper):
    """Save controller information locally."""

    def __init__(
        self,
        controller: str | None,
        deployment_name: str,
        deployments_config: DeploymentsConfig,
        data_location: Path,
        is_external: bool = False,
        force: bool = False,
    ):
        super().__init__(
            "Save controller information",
            "Saving controller information locally",
        )
        self.deployment_name = deployment_name
        self.deployments_config = deployments_config
        self.deployment = self.deployments_config.get_deployment(self.deployment_name)
        self.controller = controller
        self.data_location = data_location
        self.is_external = is_external
        self.force = force

    def _get_controller(self, name: str) -> JujuController | None:
        try:
            controller = self.get_controller(name)["details"]
        except ControllerNotFoundException as e:
            LOG.debug(str(e))
            return None
        return JujuController(
            name=name,
            api_endpoints=controller["api-endpoints"],
            ca_cert=controller["ca-cert"],
            is_external=self.is_external,
        )

    def is_skip(self, status: Status | None = None) -> Result:
        """Determines if the step should be skipped or not."""
        if self.controller is None:
            # juju controller not yet bootstrapped and is not pointed to external
            return Result(ResultType.COMPLETED)

        self.controller_details = self._get_controller(self.controller)
        if self.controller_details is None:
            return Result(ResultType.FAILED, f"Controller {self.controller} not found")

        if (
            not self.force
            and self.controller_details == self.deployment.juju_controller
        ):
            return Result(ResultType.SKIPPED)

        return Result(ResultType.COMPLETED)

    def run(self, status: Status | None) -> Result:
        """Save controller to deployment information."""
        if self.controller:
            # details can be filled only when controller exists
            self.deployment.juju_controller = self.controller_details

        if self.deployment.juju_account is None:
            if self.is_external:
                self.deployment.juju_account = JujuAccount.load(
                    self.data_location, f"{self.controller}.yaml"
                )
            else:
                password = random_string(32)
                self.deployment.juju_account = JujuAccount(
                    user="admin", password=password
                )

        self.deployments_config.write()
        return Result(ResultType.COMPLETED)


class RemoveSaasApplicationsStep(BaseStep):
    """Removes SAAS offers from given model.

    This is a workaround around:
    https://github.com/juju/terraform-provider-juju/issues/473

    For CMR on same controller, offering_model should be sufficient.
    For CMR across controllers, offering_interfaces are required to
    determine the SAAS Applications.
    Remove saas applications specified in saas_apps_to_delete. If
    saas_apps_to_delete is None, remove all the remote saas applications.
    """

    def __init__(
        self,
        jhelper: JujuHelper,
        model: str,
        offering_model: str | None = None,
        offering_interfaces: list | None = None,
        saas_apps_to_delete: list | None = None,
    ):
        super().__init__(
            f"Purge SAAS Offers: {model}", f"Purging SAAS Offers from {model}"
        )
        self.jhelper = jhelper
        self.model = model
        self.offering_model = offering_model
        self.offering_interfaces = offering_interfaces
        self.saas_apps_to_delete = saas_apps_to_delete
        self._remote_app_to_delete: list[str] = []

    def _get_remote_apps_from_model(
        self,
        remote_applications: dict[str, "jubilant.statustypes.RemoteAppStatus"],
        offering_model: str,
    ) -> list[str]:
        """Get all remote apps connected to offering model."""
        remote_apps = []
        for name, remote_app in remote_applications.items():
            offer = remote_app.url

            LOG.debug("Processing offer: %s", offer)
            model_name = offer.split("/", 1)[1].split(".", 1)[0]
            if model_name == offering_model:
                remote_apps.append(name)

        return remote_apps

    def _get_remote_apps_from_interfaces(
        self,
        remote_applications: dict[str, "jubilant.statustypes.RemoteAppStatus"],
        offering_interfaces: list[str],
    ) -> list[str]:
        """Get all remote apps which has offered given interfaces."""
        remote_apps = []
        for name, remote_app in remote_applications.items():
            if not remote_app:
                continue

            LOG.debug(f"Processing remote app: {remote_app}")
            for endpoint in remote_app.endpoints.values():
                if endpoint.interface in offering_interfaces:
                    remote_apps.append(name)

        return remote_apps

    def is_skip(self, status: Status | None = None) -> Result:
        """Determines if the step should be skipped or not."""
        model_status = self.jhelper.get_model_status(self.model)
        remote_applications = model_status.app_endpoints
        if not remote_applications:
            return Result(ResultType.SKIPPED, "No remote applications found")

        LOG.debug(
            "Remote applications found: %s", ", ".join(remote_applications.keys())
        )

        # Filter remote applications based on parameter self.saas_apps_to_delete
        if self.saas_apps_to_delete is None:
            filtered_remote_applications = remote_applications
        else:
            filtered_remote_applications = {
                name: remote_app
                for name, remote_app in remote_applications.items()
                if name in self.saas_apps_to_delete
            }

        if self.offering_model:
            self._remote_app_to_delete.extend(
                self._get_remote_apps_from_model(
                    filtered_remote_applications, self.offering_model
                )
            )

        if self.offering_interfaces:
            self._remote_app_to_delete.extend(
                self._get_remote_apps_from_interfaces(
                    filtered_remote_applications, self.offering_interfaces
                )
            )

        if len(self._remote_app_to_delete) == 0:
            return Result(ResultType.SKIPPED, "No remote applications to remove")

        return Result(ResultType.COMPLETED)

    def run(self, status: Status | None = None) -> Result:
        """Execute to remove SAAS apps."""
        if not self._remote_app_to_delete:
            return Result(ResultType.COMPLETED)

        LOG.debug(
            "Removing remote applications %s on model %s",
            self._remote_app_to_delete,
            self.model,
        )
        self.jhelper.remove_saas(self.model, *self._remote_app_to_delete)
        return Result(ResultType.COMPLETED)


class MigrateModelStep(BaseStep, JujuStepHelper):
    """Migrate model to another controller in juju."""

    def __init__(
        self,
        model: str,
        from_controller: str,
        to_controller: str,
    ):
        super().__init__(
            "Migrate the model",
            f"Migrating model {model} to juju controller {to_controller}",
        )
        self.model = model
        self.from_controller = from_controller
        self.to_controller = to_controller

    def _switch_controller(self, controller: str):
        cmd = [self._get_juju_binary(), "switch", controller]
        LOG.debug(f"Running command {' '.join(cmd)}")
        process = subprocess.run(cmd, capture_output=True, text=True, check=True)
        LOG.debug(f"Command finished. stdout={process.stdout}, stderr={process.stderr}")

    @tenacity.retry(
        wait=tenacity.wait_fixed(10),
        stop=tenacity.stop_after_delay(960),
        retry=tenacity.retry_if_exception_type(ValueError),
        reraise=True,
    )
    def _wait_for_model(self, model: str, owner: str):
        try:
            self._juju_cmd("status", "--model", f"{owner}/{model}")
        except subprocess.CalledProcessError as e:
            LOG.debug(f"No model {model} found. stdout: {e.stdout}, stderr: {e.stderr}")
            raise ValueError(f"No model {model} found")

    def _get_model_owner(self, model: str) -> str:
        """Determine model owner."""
        try:
            model_info = self._juju_cmd("show-model", model)
        except subprocess.CalledProcessError:
            raise ValueError(f"Model {model} not found")
        LOG.debug(f"Model info: {model_info}")
        if owner := model_info.get(model, {}).get("owner"):
            return owner
        raise ValueError(f"Owner not found for model {model}")

    def is_skip(self, status: Status | None = None) -> Result:
        """Determines if the step should be skipped or not."""
        try:
            self._switch_controller(self.to_controller)
        except subprocess.CalledProcessError as e:
            LOG.exception(f"Error in switching to controller {self.to_controller}")
            return Result(ResultType.FAILED, str(e))

        models = self._juju_cmd("models")
        LOG.debug(f"Models: {models}")
        models = models.get("models", [])
        LOG.debug(f"models: {models} .. looking for {self.model}")
        for model_ in models:
            if model_.get("short-name") == self.model:
                return Result(ResultType.SKIPPED)

        return Result(ResultType.COMPLETED)

    def run(self, status: Status | None = None) -> Result:
        """Model mirgate and switch to new controller."""
        try:
            self._switch_controller(self.from_controller)
        except subprocess.CalledProcessError as e:
            LOG.exception(f"Error in switching to controller {self.from_controller}")
            return Result(ResultType.FAILED, str(e))

        try:
            owner = self._get_model_owner(self.model)
        except ValueError:
            msg = f"Failed to determine owner for model {self.model}"
            LOG.debug(msg)
            return Result(ResultType.FAILED, msg)

        try:
            cmd = [self._get_juju_binary(), "migrate", self.model, self.to_controller]
            LOG.debug(f"Running command {' '.join(cmd)}")
            process = subprocess.run(cmd, capture_output=True, text=True, check=True)
            LOG.debug(
                f"Command finished. stdout={process.stdout}, stderr={process.stderr}"
            )
        except subprocess.CalledProcessError as e:
            LOG.exception(
                f"Error in migrating model {self.model}  from {self.from_controller}"
                f"to controller {self.to_controller}"
            )
            return Result(ResultType.FAILED, str(e))

        try:
            self._switch_controller(self.to_controller)
        except subprocess.CalledProcessError as e:
            LOG.exception(f"Error in switching to controller {self.to_controller}")
            return Result(ResultType.FAILED, str(e))

        try:
            # If the model is visible in to_controller, consider migration is completed.
            self._wait_for_model(self.model, owner)
        except ValueError:
            LOG.debug("Failed to find model after migration", exc_info=True)
            return Result(
                ResultType.FAILED, f"Timed out waiting for model {self.model} to appear"
            )

        return Result(ResultType.COMPLETED)


class CheckJujuReachableStep(BaseStep):
    def __init__(self, juju_controller: JujuController | None):
        super().__init__("Check Juju Reachable", "Checking if Juju is reachable")
        self.juju_controller = juju_controller

    def run(self, status: Status | None = None) -> Result:
        """Check if Juju is reachable."""
        if self.juju_controller is None:
            return Result(ResultType.FAILED, "Juju controller not found")

        all_failed = True
        for address in self.juju_controller.api_endpoints:
            host, port = address.split(":")
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.settimeout(5)
                res = sock.connect_ex((host, int(port)))
                if res == 0:
                    all_failed = False

        if all_failed:
            addresses = ", ".join(self.juju_controller.api_endpoints)
            return Result(
                ResultType.FAILED,
                textwrap.dedent(
                    f"""\
                    Juju Controller is not reachable, please check the following:
                    - `OpenStack APIs IP ranges` is reachable from this host.
                    - Firewall is not blocking the connection.

                    Unreachable API endpoints: {addresses}"""
                ),
            )
        return Result(ResultType.COMPLETED)
