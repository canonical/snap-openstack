# SPDX-FileCopyrightText: 2024 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import logging
import re
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

import click
import pydantic
from croniter import croniter
from packaging.version import Version
from rich import box
from rich.console import Console
from rich.status import Status
from rich.table import Column, Table

from sunbeam.clusterd.client import Client
from sunbeam.core.common import BaseStep, Result, ResultType, run_plan
from sunbeam.core.deployment import Deployment
from sunbeam.core.juju import (
    ActionFailedException,
    ApplicationNotFoundException,
    JujuHelper,
    LeaderNotFoundException,
    UnitNotFoundException,
)
from sunbeam.core.manifest import CharmManifest, FeatureConfig, Manifest, SoftwareConfig
from sunbeam.core.openstack import OPENSTACK_MODEL
from sunbeam.core.terraform import (
    TerraformException,
    TerraformHelper,
    TerraformInitStep,
    TerraformStateLockedException,
)
from sunbeam.feature_manager import FeatureManager
from sunbeam.features.interface.v1.openstack import (
    OpenStackControlPlaneFeature,
    TerraformPlanLocation,
)
from sunbeam.steps.juju import JujuLoginStep
from sunbeam.utils import click_option_show_hints, pass_method_obj
from sunbeam.versions import TEMPEST_CHANNEL

LOG = logging.getLogger(__name__)
console = Console()

FEATURE_VERSION = "0.0.1"
MINIMAL_PERIOD = 15 * 60  # 15 minutes in seconds
TEMPEST_APP_NAME = "tempest"
TEMPEST_CONTAINER_NAME = "tempest"
TEMPEST_VALIDATION_RESULT = "/var/lib/tempest/workspace/tempest-validation.log"
VALIDATION_FEATURE_DEPLOY_TIMEOUT = (
    60 * 60
)  # 60 minutes in seconds, tempest can take some time to initialized
SUPPORTED_TEMPEST_CONFIG = {"schedule"}
SUPPORTED_ROLES = ("compute", "control", "storage", "network")


class Profile(pydantic.BaseModel):
    name: str
    help: str
    params: dict[str, str]


DEFAULT_PROFILE = Profile(
    name="refstack",
    help=(
        "Tests that are part of the RefStack project https://refstack.openstack.org/"
    ),
    params={"test-list": "refstack-2022.11"},
)
PROFILES = {
    p.name: p
    for p in [
        DEFAULT_PROFILE,
        Profile(
            name="quick",
            help="A short list of tests for quick validation",
            params={"test-list": "readonly-quick"},
        ),
        Profile(
            name="smoke",
            help='Tests tagged as "smoke"',
            params={"regex": "smoke"},
        ),
        Profile(
            name="all",
            help="All tests (very large number, not usually recommended)",
            params={"regex": ".*"},
        ),
    ]
}


class Config(pydantic.BaseModel):
    """Represents config updates provided by the user.

    None values mean the user did not provide them.
    """

    schedule: str | None = None

    @pydantic.validator("schedule")
    def validate_schedule(cls, schedule: str) -> str:  # noqa N805
        """Validate the schedule config option.

        Return the valid schedule if valid,
        otherwise Raise a click BadParameter exception.
        """
        # Empty schedule is fine; it means it's disabled in this context.
        if not schedule:
            return ""

        # croniter supports second repeats, but vixie cron does not.
        if len(schedule.split()) == 6:
            raise click.ClickException(
                "This cron does not support seconds in schedule (6 fields)."
                " Exactly 5 columns must be specified for iterator expression."
            )

        # constant base time for consistency
        base = datetime(2004, 3, 5)

        try:
            cron = croniter(schedule, base, max_years_between_matches=1)
        except ValueError as e:
            msg = str(e)
            # croniter supports second repeats, but vixie cron does not,
            # so update the error message here to suit.
            if "Exactly 5 or 6 columns" in msg:
                msg = "Exactly 5 columns must be specified for iterator expression."
            raise click.ClickException(msg)

        # This is a rather naive method for enforcing this,
        # and it may be possible to craft an expression
        # that results in some consecutive runs within 15 minutes,
        # however this is fine, as there is process locking for tempest,
        # and this is more of a sanity check than a security requirement.
        t1 = cron.get_next()
        t2 = cron.get_next()
        if t2 - t1 < MINIMAL_PERIOD:
            raise click.ClickException(
                "Cannot schedule periodic check to run faster than every 15 minutes."
            )

        return schedule


def get_enabled_roles(deployment) -> str:
    """Detect enabled roles in the cluster and return as comma-separated string."""
    client = deployment.get_client()
    roles = []
    for role in SUPPORTED_ROLES:
        nodes = client.cluster.list_nodes_by_role(role)
        if nodes:
            roles.append(role)
    return ",".join(roles)


def parse_config_args(args: list[str]) -> dict[str, str]:
    """Parse key=value args into a valid dictionary of key: values.

    Raise a click bad argument error if errors (only checks syntax here).
    """
    config = {}
    for arg in args:
        split_arg = arg.split("=", 1)
        if len(split_arg) == 1:
            raise click.ClickException("syntax: key=value")
        key, value = split_arg
        if key in config:
            raise click.ClickException(
                f"{key!r} parameter seen multiple times. Only provide it once."
            )
        config[key] = value
    return config


def validated_config_args(args: dict[str, str]) -> Config:
    """Validate config and return validated config if no errors.

    Raise a click bad argument error if errors.
    """
    unsupported_options = set(args.keys()).difference(SUPPORTED_TEMPEST_CONFIG)
    if unsupported_options:
        raise click.ClickException(
            f"{', '.join(unsupported_options)!r} is not a supported config option"
        )
    return Config(**args)


class ConfigureValidationStep(BaseStep):
    """Configure validation feature."""

    def __init__(
        self,
        config_changes: Config,
        client: Client,
        tfhelper: TerraformHelper,
        manifest: Manifest,
        tfvar_config: str,
        deployment: Deployment | None = None,
    ):
        super().__init__(
            "Configure validation feature",
            "Changing the configuration options for tempest",
        )
        self.config_changes = config_changes
        self.client = client
        self.tfhelper = tfhelper
        self.manifest = manifest
        self.tfvar_config = tfvar_config
        self.deployment = deployment

    def run(self, status: Status | None = None) -> Result:
        """Execute step using terraform."""
        try:
            # See ValidationFeature.manifest_attributes_tfvar_map
            charms = self.tfhelper.tfvar_map["charms"]
            tempest_k8s_config_var = charms["tempest-k8s"]["config"]
            roles = get_enabled_roles(self.deployment)
            LOG.info(f"OpenStack roles enabled for Tempest: {roles}")
            override_tfvars: dict[str, Any] = {}
            if self.config_changes.schedule is not None or roles:
                override_tfvars[tempest_k8s_config_var] = {}
                if self.config_changes.schedule is not None:
                    override_tfvars[tempest_k8s_config_var]["schedule"] = (
                        self.config_changes.schedule
                    )
                if roles:
                    override_tfvars[tempest_k8s_config_var]["roles"] = roles
            self.tfhelper.update_tfvars_and_apply_tf(
                self.client,
                self.manifest,
                tfvar_config=self.tfvar_config,
                override_tfvars=override_tfvars,
            )
        except (TerraformException, TerraformStateLockedException) as e:
            LOG.exception("Error configuring validation feature.")
            return Result(ResultType.FAILED, str(e))

        return Result(ResultType.COMPLETED)


class ValidationFeature(OpenStackControlPlaneFeature):
    """Deploy tempest to openstack model."""

    version = Version(FEATURE_VERSION)

    name = "validation"
    tf_plan_location = TerraformPlanLocation.SUNBEAM_TERRAFORM_REPO

    def default_software_overrides(self) -> SoftwareConfig:
        """Feature software configuration."""
        return SoftwareConfig(
            charms={"tempest-k8s": CharmManifest(channel=TEMPEST_CHANNEL)}
        )

    def manifest_attributes_tfvar_map(self) -> dict:
        """Manifest attributes terraformvars map."""
        return {
            self.tfplan: {
                "charms": {
                    "tempest-k8s": {
                        "config": "tempest-config",
                        "channel": "tempest-channel",
                        "revision": "tempest-revision",
                    }
                }
            },
        }

    def set_application_names(self, deployment: Deployment) -> list:
        """Application names handled by the terraform plan."""
        return [TEMPEST_APP_NAME]

    def set_tfvars_on_enable(
        self, deployment: Deployment, config: FeatureConfig
    ) -> dict:
        """Set terraform variables to enable the application."""
        roles = get_enabled_roles(deployment)
        return {
            "enable-validation": True,
            "tempest-config": {"roles": roles},
        }

    def set_tfvars_on_disable(self, deployment: Deployment) -> dict:
        """Set terraform variables to disable the application."""
        return {"enable-validation": False}

    def set_application_timeout_on_enable(self) -> int:
        """Set Application Timeout on enabling the feature.

        The feature plan will timeout if the applications
        are not in active status within in this time.
        """
        return VALIDATION_FEATURE_DEPLOY_TIMEOUT

    def set_application_timeout_on_disable(self) -> int:
        """Set Application Timeout on disabling the feature.

        The feature plan will timeout if the applications
        are not removed within this time.
        """
        return VALIDATION_FEATURE_DEPLOY_TIMEOUT

    def set_tfvars_on_resize(
        self, deployment: Deployment, config: FeatureConfig
    ) -> dict:
        """Set terraform variables to resize the application."""
        return {}

    def _get_tempest_leader_unit(self, deployment: Deployment) -> str:
        """Return the leader unit of tempest application."""
        jhelper = JujuHelper(deployment.juju_controller)
        with console.status(f"Retrieving {TEMPEST_APP_NAME}'s unit name."):
            app = TEMPEST_APP_NAME
            model = OPENSTACK_MODEL
            try:
                unit = jhelper.get_leader_unit(app, model)
            except (ApplicationNotFoundException, LeaderNotFoundException) as e:
                raise click.ClickException(str(e))
            return unit

    def _get_tempest_absolute_model_name(self, deployment: Deployment) -> str:
        """Return the absolute model name where the tempest unit resides."""
        jhelper = JujuHelper(deployment.juju_controller)
        with console.status(
            f"Retrieving the absolute model name for {TEMPEST_APP_NAME}'s unit."
        ):
            try:
                model_name = jhelper.get_model_name_with_owner(OPENSTACK_MODEL)
            except (ApplicationNotFoundException, LeaderNotFoundException) as e:
                raise click.ClickException(str(e))
            return f"{deployment.controller}:{model_name}"

    def _run_action_on_tempest_unit(
        self,
        deployment: Deployment,
        action_name: str,
        action_params: dict | None = None,
        progress_message: str = "",
    ) -> dict[str, Any]:
        """Run the charm's action."""
        unit = self._get_tempest_leader_unit(deployment)
        jhelper = JujuHelper(deployment.juju_controller)
        with console.status(progress_message):
            try:
                action_result = jhelper.run_action(
                    unit,
                    OPENSTACK_MODEL,
                    action_name,
                    action_params or {},
                )
            except (ActionFailedException, UnitNotFoundException) as e:
                LOG.debug(
                    "Error running action %s on %s", action_name, unit, exc_info=True
                )
                raise click.ClickException(str(e))

            if action_result.get("return-code", 0) > 1:
                LOG.debug(
                    "Action %s on %s failed: %s", action_name, unit, action_result
                )
                message = f"Unable to run action: {action_name}"
                raise click.ClickException(message)

            return action_result

    def _check_file_exist_in_tempest_container(
        self, deployment: Deployment, filename: str
    ) -> bool:
        """Check if file exist in tempest container."""
        unit = self._get_tempest_leader_unit(deployment)
        model_name = self._get_tempest_absolute_model_name(deployment)
        # Note: this is a workaround to run command to payload container
        # since python-libjuju does not support such feature. See related
        # bug: https://github.com/juju/python-libjuju/issues/1029
        try:
            subprocess.run(
                [
                    "juju",
                    "ssh",
                    "--model",
                    model_name,
                    "--container",
                    TEMPEST_CONTAINER_NAME,
                    unit,
                    "ls",
                    TEMPEST_VALIDATION_RESULT,
                ],
                check=True,
                timeout=30,  # 30 seconds should be enough for `ls`
                capture_output=True,
            )
        except subprocess.CalledProcessError:
            return False
        except subprocess.TimeoutExpired:
            raise click.ClickException(f"Timed out checking {filename}")
        return True

    def _copy_file_from_tempest_container(
        self, deployment: Deployment, source: str, destination: str
    ) -> None:
        """Copy file from tempest container."""
        unit = self._get_tempest_leader_unit(deployment)
        model_name = self._get_tempest_absolute_model_name(deployment)
        progress_message = (
            f"Copying {source} from "
            f"{TEMPEST_APP_NAME} ({TEMPEST_CONTAINER_NAME}) "
            f"to {destination} ..."
        )
        with console.status(progress_message):
            # Note: this is a workaround to cache the model in the juju client
            try:
                subprocess.run(
                    ["juju", "show-model", model_name],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=True,
                    timeout=30,
                )
            except subprocess.TimeoutExpired:
                raise click.ClickException("Timed out while priming Juju model cache")
            # Note: this is a workaround to run command to payload container
            # since python-libjuju does not support such feature. See related
            # bug: https://github.com/juju/python-libjuju/issues/1029
            if Path(destination).is_dir():
                # juju scp does not allow directory as destination
                destination = str(Path(destination, Path(source).name))
            try:
                subprocess.run(
                    [
                        "juju",
                        "scp",
                        "--model",
                        model_name,
                        "--container",
                        TEMPEST_CONTAINER_NAME,
                        f"{unit}:{source}",
                        destination,
                    ],
                    check=True,
                    timeout=60,  # 60 seconds should be enough for copying a file
                )
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
                raise click.ClickException(str(e))

    def _configure_preflight_check(self, deployment: Deployment) -> bool:
        """Preflight check for configure command."""
        enabled_features = FeatureManager().enabled_features(deployment)
        for feature in enabled_features:
            if (
                group := getattr(feature, "group", None)
            ) and group.name == "observability":
                return True
        return False

    @click.command()
    @click_option_show_hints
    @pass_method_obj
    def enable_cmd(self, deployment: Deployment, show_hints: bool) -> None:
        """Enable OpenStack Integration Test Suite (tempest)."""
        self.enable_feature(deployment, FeatureConfig(), show_hints)

    @click.command()
    @click_option_show_hints
    @pass_method_obj
    def disable_cmd(self, deployment: Deployment, show_hints: bool) -> None:
        """Disable OpenStack Integration Test Suite (tempest)."""
        self.disable_feature(deployment, show_hints)

    @click.command()
    @click.argument("options", nargs=-1)
    @pass_method_obj
    def configure_validation(
        self, deployment: Deployment, options: list[str] | None = None
    ) -> None:
        """Configure validation feature.

        Run without arguments to view available configuration options.

        Run with key=value args to set configuration values.
        For example: sunbeam configure validation schedule="*/30 * * * *"
        """
        if not self._configure_preflight_check(deployment):
            raise click.ClickException(
                "'observability' feature is required for configuring validation"
                " feature."
            )

        if not options:
            console.print(
                "Config options available: \n\n"
                "schedule: set a cron schedule for running periodic tests.  "
                "Empty disables.\n\n"
                "Run with key=value args to set configuration values.\n"
                'For example: sunbeam configure validation schedule="*/30 * * * *"'
            )
            return

        config_changes = validated_config_args(parse_config_args(options))

        tfhelper = deployment.get_tfhelper(self.tfplan)
        run_plan(
            [
                TerraformInitStep(tfhelper),
                ConfigureValidationStep(
                    config_changes,
                    deployment.get_client(),
                    tfhelper,
                    self.manifest,
                    self.get_tfvar_config_key(),
                    deployment,
                ),
            ],
            console,
        )

    @click.command()
    @click.argument(
        "profile",
        default=DEFAULT_PROFILE.name,
        type=click.Choice(list(PROFILES.keys())),
        metavar="[PROFILE]",
    )
    @click.option(
        "-o",
        "--output",
        type=click.Path(),
        default=None,
        help=(
            "Download the full log to output file. "
            "If not provided, the output can be retrieved later "
            "by running `sunbeam validation get-last-result`."
        ),
    )
    @click_option_show_hints
    @pass_method_obj
    def run_validate_action(
        self,
        deployment: Deployment,
        profile: str,
        output: str | None,
        show_hints: bool,
    ) -> None:
        """Run validation tests (default: "refstack" profile).

        Arguments: [PROFILE] The set of tests to run (defaults to "refstack").
        For details of available profiles, run `sunbeam validation profiles`.
        """
        action_name = "validate"
        action_params = PROFILES[profile].params
        progress_message = "Running tempest to validate the sunbeam deployment ..."
        action_result = self._run_action_on_tempest_unit(
            deployment,
            action_name,
            action_params=action_params,
            progress_message=progress_message,
        )

        summary = action_result.get("summary", "").strip()
        console.print(summary)

        failed_match = re.search(r"Failed:\s+(\d+)", summary)
        unexpected_success_match = re.search(r"Unexpected Success:\s+(\d+)", summary)

        failed = int(failed_match.group(1)) if failed_match else 0
        unexpected_success = (
            int(unexpected_success_match.group(1)) if unexpected_success_match else 0
        )

        if output:
            # Due to shelling out to the juju cli (rather than using libjuju),
            # we need to ensure the juju cli is logged in.
            run_plan([JujuLoginStep(deployment.juju_account)], console, show_hints)

            self._copy_file_from_tempest_container(
                deployment, TEMPEST_VALIDATION_RESULT, output
            )

        if failed > 0 or unexpected_success > 0:
            raise click.ClickException(
                f"Validation tests: failed {failed}, unexpected_success: "
                f"{unexpected_success}"
            )

    @click.command()
    def list_profiles(self) -> None:
        """Show details of available test profiles."""
        table = Table(
            Column("Name", no_wrap=True),
            Column("Description"),
            title="Available profiles",
            box=box.SIMPLE,
        )
        for profile in PROFILES.values():
            table.add_row(profile.name, profile.help)
        console.print(table)

    @click.command()
    @click.option(
        "-o",
        "--output",
        type=click.Path(),
        required=True,
        help="Download the last validation check result to output file.",
    )
    @click_option_show_hints
    @pass_method_obj
    def run_get_last_result(
        self, deployment: Deployment, output: str, show_hints: bool
    ) -> None:
        """Get last validation result."""
        # Due to shelling out to the juju cli (rather than using libjuju),
        # we need to ensure the juju cli is logged in.
        run_plan([JujuLoginStep(deployment.juju_account)], console, show_hints)

        if not self._check_file_exist_in_tempest_container(
            deployment, TEMPEST_VALIDATION_RESULT
        ):
            raise click.ClickException(
                (
                    f"Cannot find '{TEMPEST_VALIDATION_RESULT}'. "
                    "Have you run `sunbeam validation run` at least once?"
                )
            )
        self._copy_file_from_tempest_container(
            deployment, TEMPEST_VALIDATION_RESULT, output
        )

    @click.group()
    def validation_group(self):
        """Manage cloud validation functionality."""

    def enabled_commands(self) -> dict[str, list[dict]]:
        """Dict of clickgroup along with commands.

        Return the commands available once the feature is enabled.
        """
        return {
            # sunbeam configure validation ...
            "configure": [{"name": "validation", "command": self.configure_validation}],
            # add the validation subcommand group to the root group:
            # sunbeam validation ...
            "init": [{"name": "validation", "command": self.validation_group}],
            # add the subcommands:
            # sunbeam validation run ... etc.
            "init.validation": [
                {"name": "run", "command": self.run_validate_action},
                {"name": "profiles", "command": self.list_profiles},
                {
                    "name": "get-last-result",
                    "command": self.run_get_last_result,
                },
            ],
        }
