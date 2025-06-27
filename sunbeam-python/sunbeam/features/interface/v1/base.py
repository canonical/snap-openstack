# SPDX-FileCopyrightText: 2023 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import logging
import typing
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Generic, Literal, Type

import click
from packaging.requirements import Requirement
from packaging.version import Version
from snaphelpers import Snap

from sunbeam.clusterd.client import Client
from sunbeam.clusterd.service import ConfigItemNotFoundException
from sunbeam.core.common import RiskLevel, SunbeamException, read_config, update_config
from sunbeam.core.deployment import Deployment
from sunbeam.core.manifest import FeatureConfig, Manifest, SoftwareConfig
from sunbeam.features.interface import utils
from sunbeam.provider.maas.deployment import MAAS_TYPE
from sunbeam.versions import VarMap

LOG = logging.getLogger(__name__)
_GROUPS: dict[str, Type["BaseFeatureGroup"]] = {}
_FEATURES: dict[str, Type["BaseFeature"]] = {}


def features() -> typing.Mapping[str, Type["BaseFeature"]]:
    return _FEATURES


def groups() -> typing.Mapping[str, Type["BaseFeatureGroup"]]:
    return _GROUPS


class FeatureError(SunbeamException):
    """Common feature exception base class."""


class MissingFeatureError(FeatureError):
    """Exception raised when feature is not found."""


class MissingVersionInfoError(FeatureError):
    """Exception raised when feature version info is not found."""


class IncompatibleVersionError(FeatureError):
    """Exception raised when feature version is incompatible."""


class InvalidRequirementError(FeatureError):
    """Exception raised when feature requirement is invalid."""


class DisabledFeatureError(FeatureError):
    """Exception raised when feature is disabled."""


class NotAutomaticFeatureError(FeatureError):
    """Exception raised when feature cannot be automatically enabled."""


class HasRequirersFeaturesError(FeatureError):
    """Exception raised when feature has requirers features."""


class ClickInstantiator:
    """Support invoking click commands on instance methods."""

    def __init__(self, command: click.Command, registerable: "BaseRegisterable"):
        self.command = command
        self.registerable = registerable

    def __call__(self, *args, **kwargs):
        """Invoke the click command on the instance method."""
        return self.command(self.registerable, *args, **kwargs)


class BaseRegisterable(ABC):
    name: str

    def validate_commands(
        self, conditions: typing.Mapping[str, str | bool] = {}
    ) -> bool:
        """Validate the commands dictionary.

        Validate if the dictionary follows the format
        {<group>: [{"name": <command name>, "command": <command function>}]}

        Validates if the command is of type click.Group or click.Command.

        :returns: True if validation is successful, else False.
        """
        self_commands = self.commands(conditions)
        LOG.debug(f"Validating commands: {self_commands}")
        for group, commands in self_commands.items():
            for command in commands:
                cmd_name = command.get("name")
                cmd_func = command.get("command")
                if None in (cmd_name, cmd_func):
                    LOG.warning(
                        f"Feature {self.name}: Commands dictionary is not in "
                        "required format"
                    )
                    return False

                if not any(
                    [
                        isinstance(cmd_func, click.Group),
                        isinstance(cmd_func, click.Command),
                    ]
                ):
                    LOG.warning(
                        f"Feature {self.name}: {cmd_func} should be either "
                        "click.Group or click.Command"
                    )
                    return False

        LOG.debug("Validation successful")
        return True

    @abstractmethod
    def commands(
        self, conditions: typing.Mapping[str, str | bool] = {}
    ) -> dict[str, list[dict]]:
        """Dict of clickgroup along with commands.

        Should be of form
        {<group>: [{"name": <command name>, "command": <command function>}]}

        command can be click.Group or click.Command.

        Example:
        {
            "enable": [
                {
                    "name": "subcmd",
                    "command": self.enable_subcmd,
                },
            ],
            "disable": [
                {
                    "name": "subcmd",
                    "command": self.disable_subcmd,
                },
            ],
            "init": [
                {
                    "name": "subgroup",
                    "command": self.trobuleshoot,
                },
            ],
            "subgroup": [
                {
                    "name": "subcmd",
                    "command": self.troubleshoot_subcmd,
                },
            ],
        }

        Based on above example, expected the subclass to define following functions:

        @click.command()
        def enable_subcmd(self):
            pass

        @click.command()
        def disable_subcmd(self):
            pass

        @click.group()
        def troublshoot(self):
            pass

        @click.command()
        def troubleshoot_subcmd(self):
            pass

        Example of one function that requires options:

        @click.command()
        @click.option(
            "-t",
            "--token",
            help="Ubuntu Pro token to use for subscription attachment",
            prompt=True,
        )
        def enable_subcmd(self, token: str):
            pass

        The user can invoke the above commands like:

        sunbeam enable subcmd
        sunbeam disable subcmd
        sunbeam troubleshoot subcmd
        """

    def register(
        self, cli: click.Group, conditions: typing.Mapping[str, str | bool] = {}
    ) -> None:
        """Register feature groups and commands.

        :param cli: Sunbeam main cli group
        """
        LOG.debug(f"Registering feature {self.name}")
        if not self.validate_commands(conditions):
            LOG.warning(f"Not able to register the feature {self.name}")
            return

        groups = utils.get_all_registered_groups(cli)
        LOG.debug(f"Registered groups: {groups}")
        for group, commands in self.commands(conditions).items():
            group_obj = groups.get(group)
            if not group_obj:
                cmd_names = [command.get("name") for command in commands]
                LOG.warning(
                    f"Feature {self.name}: Not able to register command "
                    f"{cmd_names} in group {group} as group does not exist"
                )
                continue

            for command in commands:
                cmd = command.get("command")
                if not cmd:
                    LOG.warning(
                        f"Feature {self.name}: Not able to register command "
                        f"{command.get('name')} in group {group} as command is None"
                    )
                    continue
                cmd_name = command.get("name")
                if cmd_name in group_obj.list_commands({}):
                    if isinstance(cmd, click.Command):
                        LOG.warning(
                            f"Feature {self.name}: Discarding adding command "
                            f"{cmd_name} as it already exists in group {group}"
                        )
                    else:
                        # Should be sub group and already exists
                        LOG.debug(
                            f"Feature {self.name}: Group {cmd_name} already "
                            f"part of parent group {group}"
                        )
                    continue

                cmd.callback = ClickInstantiator(cmd.callback, self)
                group_obj.add_command(cmd, cmd_name)
                LOG.debug(
                    f"Feature {self.name}: Command {cmd_name} registered in "
                    f"group {group}"
                )

                # Add newly created click groups to the registered groups so that
                # commands within the feature can be registered on group.
                # This allows feature to create new groups and commands in single place.
                if isinstance(cmd, click.Group):
                    groups[f"{group}.{cmd_name}"] = cmd


class BaseFeatureGroup(BaseRegisterable):
    """A feature group assumes enable/disable."""

    name: str

    enable_group: click.Group
    disable_group: click.Group

    def __init_subclass__(cls, **kwargs):
        """Register inherited feature group classes."""
        super().__init_subclass__(**kwargs)
        if name := getattr(cls, "name", None):
            _GROUPS[name] = cls

    def commands(
        self, conditions: typing.Mapping[str, str | bool] = {}
    ) -> dict[str, list[dict]]:
        """Define commands for the group."""
        return {
            "enable": [{"name": self.name, "command": self.enable_group}],
            "disable": [{"name": self.name, "command": self.disable_group}],
        }


ConfigType = typing.TypeVar("ConfigType", bound=FeatureConfig)


class BaseFeature(BaseRegisterable, Generic[ConfigType]):
    """Base class for Feature interface."""

    # Version of feature interface used by Feature
    interface_version = Version("0.0.1")

    # Version of feature
    version = Version("0.0.0")

    # Name of feature
    name: str
    group: type[BaseFeatureGroup] | None = None

    # Risk level of the feature
    risk_availability: RiskLevel = RiskLevel.STABLE

    def __init_subclass__(cls, **kwargs):
        """Register inherited features classes."""
        super().__init_subclass__(**kwargs)
        if name := getattr(cls, "name", None):
            _FEATURES[name] = cls

    @property
    def display_name(self) -> str:
        """Return the display name of the feature."""
        if not self.group:
            return self.name
        return " ".join(part.capitalize() for part in self.name.split("."))

    @property
    def feature_key(self) -> str:
        """Key used to store feature info in cluster database Config table."""
        return self._get_feature_key(self.name)

    def _get_feature_key(self, feature: str) -> str:
        """Generate feature key from feature."""
        return f"Feature-{feature}"

    def config_type(self) -> Type[ConfigType] | None:
        """Return the config type for the feature."""
        return None

    def install_hook(self) -> None:
        """Install hook for the feature.

        snap-openstack install hook handler invokes this function on all the
        features. Features should override this function if required.
        """
        pass

    def upgrade_hook(
        self, deployment: Deployment, upgrade_release: bool = False
    ) -> None:
        """Upgrade hook for the feature.

        snap-openstack upgrade hook handler invokes this function on all the
        features. Features should override this function if required.
        """
        pass

    def configure_hook(
        self,
        deployment: Deployment,
    ) -> None:
        """Configure hook for the feature.

        snap-openstack configure hook handler invokes this function on all the
        features. Features should override this function if required.
        """
        pass

    def pre_refresh_hook(
        self,
        deployment: Deployment,
    ) -> None:
        """Pre refresh hook for the feature.

        snap-openstack pre-refresh hook handler invokes this function on all the
        features. Features should override this function if required.
        """
        pass

    def post_refresh_hook(
        self,
        deployment: Deployment,
    ) -> None:
        """Post refresh hook for the feature.

        snap-openstack post-refresh hook handler invokes this function on all the
        features. Features should override this function if required.
        """
        pass

    def remove_hook(
        self,
        deployment: Deployment,
    ) -> None:
        """Remove hook for the feature.

        snap-openstack remove hook handler invokes this function on all the
        features. Features should override this function if required.
        """
        pass

    def get_feature_info(self, client: Client) -> dict:
        """Get feature information from clusterdb.

        :returns: Dictionay with feature details like version, and any other information
                  uploded by feature.
        """
        try:
            return read_config(client, self.feature_key)
        except ConfigItemNotFoundException:
            return {}

    def update_feature_info(self, client: Client, info: dict) -> None:
        """Update feature information in clusterdb.

        Adds version info as well to the info dictionary to update in the cluster db.

        :param info: Feature specific information as dictionary
        """
        info_from_db = self.get_feature_info(client)
        info_from_db.update(info)
        info_from_db.update({"version": str(self.version)})
        update_config(client, self.feature_key, info_from_db)

    def fetch_feature_version(self, client: Client, feature: str) -> Version:
        """Fetch feature version stored in database.

        :param feature: Name of the feature
        :returns: Version of the feature
        """
        try:
            config = read_config(client, self._get_feature_key(feature))
        except ConfigItemNotFoundException as e:
            raise MissingFeatureError(f"Feature {feature} not found") from e
        version = config.get("version")
        if version is None:
            raise MissingVersionInfoError(
                f"Version info for feature {feature} not found"
            )

        return Version(version)

    def default_software_overrides(self) -> SoftwareConfig:
        """Return manifest pa of the feature.

        Define manifest charms involved and default values for charm attributes
        and terraform plan.
        Sample manifest:
        {
            "charms": {
                "heat-k8s": {
                    "channel": <>.
                    "revision": <>,
                    "config": <>,
                }
            },
            "terraform": {
                "<feature>-plan": {
                    "source": <Path of terraform plan>,
                }
            }
        }

        The features that uses terraform plan should override this function.
        """
        return SoftwareConfig()

    def manifest_attributes_tfvar_map(self) -> dict[str, VarMap]:
        """Return terraform var map for the manifest attributes.

        Map terraform variable for each manifest attribute.
        Sample return value:
        {
            <tf plan1>: {
                "charms": {
                    "heat-k8s": {
                        "channel": <tfvar for heat channel>,
                        "revision": <tfvar for heat revision>,
                        "config": <tfvar for heat config>,
                    }
                }
            },
            <tfplan2>: {
                "caas-config": {
                    "image-url": <tfvar map for caas image url>
                }
            }
        }

        The features that uses terraform plan should override this function.
        """
        return {}

    def preseed_questions_content(self) -> list:
        """Generate preseed manifest content.

        The returned content will be used in generation of manifest.
        """
        return []

    def update_proxy_model_configs(
        self, deployment: Deployment, show_hints: bool
    ) -> None:
        """Update proxy model configs.

        Features that creates a new model should override this function
        to update model-configs for the model.
        Feature can get proxies using get_proxy_settings(self.feature.deployment)
        """
        pass

    def get_terraform_plans_base_path(self) -> Path:
        """Return Terraform plan base location."""
        return Snap().paths.user_common

    def is_openstack_control_plane(self) -> bool:
        """Is feature deploys openstack control plane.

        :returns: True if feature deploys openstack control plane, else False.
                  Defaults to False.
        """
        return False

    def is_cluster_bootstrapped(self, client: Client) -> bool:
        """Is sunbeam cluster bootstrapped.

        :returns: True if sunbeam cluster is bootstrapped, else False.
        """
        try:
            return client.cluster.check_sunbeam_bootstrapped()
        except ValueError:
            return False


class FeatureRequirement(Requirement):
    def __init__(self, requirement_string: str, optional: bool = False):
        super().__init__(requirement_string)
        self.optional = optional

    @property
    def klass(self) -> Type["EnableDisableFeature"]:
        """Return the feature class for the requirement."""
        klass = features().get(self.name)
        if klass is None:
            raise InvalidRequirementError(f"Feature {self.name} not found")
        if not issubclass(klass, EnableDisableFeature):
            raise InvalidRequirementError(
                f"Feature {self.name} is not of type EnableDisableFeature"
            )
        return klass


@typing.runtime_checkable
class NamedEnabledDisableFeatureProtocol(typing.Protocol):
    # def __init__(self) -> None:
    #     pass

    def is_enabled(self, client: Client) -> bool:
        """Is the feature enabled."""
        ...


class EnableDisableFeature(BaseFeature, Generic[ConfigType]):
    """Interface for features of type on/off.

    Features that can be enabled or disabled can use this interface instead
    of BaseFeature.
    """

    interface_version = Version("0.0.1")

    requires: set[FeatureRequirement] = set()

    enable_cmd: click.Command
    disable_cmd: click.Command

    def __init__(self) -> None:
        """Constructor for feature interface."""
        self.user_manifest: Path | None = None

    def is_enabled(self, client: Client) -> bool:
        """Feature is enabled or disabled.

        Retrieves enabled field from the Feature info saved in
        the database and returns enabled based on the enabled field.

        :returns: True if feature is enabled, else False.
        """
        info = self.get_feature_info(client)
        return info.get("enabled", "false").lower() == "true"

    def check_enabled_requirement_is_compatible(
        self, deployment: Deployment, requirement: FeatureRequirement
    ):
        """Check if an enabled requirement is compatible with current requirer."""
        if len(requirement.specifier) == 0:
            # No version requirement, so no need to check version
            return

        try:
            current_version = self.fetch_feature_version(
                deployment.get_client(), requirement.name
            )
            LOG.debug(f"Feature {requirement.name} version {current_version} found")
        except MissingVersionInfoError as e:
            LOG.debug(f"Version info for feature {requirement.name} not found")
            raise FeatureError(
                f"{requirement.name} has no version recorded,"
                f" {requirement.specifier} required"
            ) from e

        if not requirement.specifier.contains(current_version):
            raise IncompatibleVersionError(
                f"Feature {self.name} requires '{requirement.name}"
                f"{requirement.specifier}' but enabled feature is {current_version}"
            )

    def check_feature_class_is_compatible(
        self, feature: "EnableDisableFeature", requirement: FeatureRequirement
    ):
        """Check if actual feature class is compatible with requirements."""
        if len(requirement.specifier) == 0:
            # No version requirement, so no need to check version
            return

        klass_version_compatible = all(
            (
                feature.version,
                requirement.specifier,
                requirement.specifier.contains(feature.version),
            )
        )

        if not klass_version_compatible:
            message = (
                f"Feature {self.name} requires '{requirement.name}"
                f"{requirement.specifier}' but loaded feature version"
                f" is {feature.version}"
            )
            raise IncompatibleVersionError(
                " ".join(
                    (
                        "Feature has an incompatible version.",
                        "This should not happen.",
                        message,
                    )
                )
            )

    def check_feature_is_automatically_enableable(
        self, feature: "EnableDisableFeature", manifest: Manifest
    ):
        """Check whether a feature can be automatically enabled."""
        feature_manifest = manifest.get_feature(feature.name)
        if not feature_manifest:
            raise ValueError(f"Unknown feature {feature.name}")
        if feature_manifest.config is not None:
            return
        config_type = feature.config_type()
        if config_type is None:
            return
        try:
            config_type()
        except ValueError:
            raise NotAutomaticFeatureError(
                f"Feature {self.name} depends on {feature.name},"
                f" and {feature.name} cannot be automatically enabled."
                " Please enable it by running"
                f" 'sunbeam enable {feature.name} <config options...>'"
            )

    def check_enablement_requirements(
        self,
        deployment: Deployment,
        state: Literal["enable"] | Literal["disable"] = "enable",
    ):
        """Check whether the feature can be enabled."""
        feature_classes = features().values()
        for klass in feature_classes:
            if not issubclass(klass, EnableDisableFeature):
                LOG.debug(f"Skipping {klass} as it is not of type EnableDisableFeature")
                continue
            feature = klass()
            if not feature.is_enabled(deployment.get_client()):
                continue
            for requirement in feature.requires:
                if requirement.name != self.name:
                    continue
                if state == "disable":
                    raise HasRequirersFeaturesError(
                        f"{feature.name} is enabled and requires {self.name}"
                    )
                message = (
                    f"Feature {feature.name} is enabled and "
                    f"requires '{requirement.name}{requirement.specifier}'"
                )
                LOG.debug(message)
                if requirement.specifier.contains(self.version):
                    # we found the feature, and it's compatible
                    break
                raise IncompatibleVersionError(message)

    def enable_requirements(self, deployment: Deployment, show_hints: bool):
        """Iterate through requirements, enable features if possible."""
        for requirement in self.requires:
            if not issubclass(requirement.klass, EnableDisableFeature):
                LOG.debug(
                    f"Skipping {requirement.klass} as it is not of type"
                    " EnableDisableFeature"
                )
                continue
            feature = requirement.klass()

            if feature.is_enabled(deployment.get_client()):
                self.check_enabled_requirement_is_compatible(deployment, requirement)
                # Feature is already enabled, and has a compatible version.
                continue

            if requirement.optional:
                # Skip enablement since feature is optional
                continue
            self.check_feature_class_is_compatible(feature, requirement)
            self.check_feature_is_automatically_enableable(
                feature, deployment.get_manifest()
            )

            manifest = deployment.get_manifest()
            feature_manifest = manifest.get_feature(feature.name)
            if not feature_manifest:
                raise ValueError(f"Unknown feature {feature.name}")
            if feature_manifest.config is not None:
                config = feature_manifest.config
            else:
                config_type = feature.config_type()
                if config_type is None:
                    config = FeatureConfig()
                else:
                    config = config_type()

            # Enable feature if it is not yet enabled
            if not feature.is_enabled(deployment.get_client()):
                feature.enable_feature(deployment, config, show_hints)

    def pre_enable(
        self, deployment: Deployment, config: ConfigType, show_hints: bool
    ) -> None:
        """Handler to perform tasks before enabling the feature."""
        self.check_enablement_requirements(deployment)
        self.enable_requirements(deployment, show_hints)

    def post_enable(
        self, deployment: Deployment, config: ConfigType, show_hints: bool
    ) -> None:
        """Handler to perform tasks after the feature is enabled."""
        pass

    @abstractmethod
    def run_enable_plans(
        self, deployment: Deployment, config: ConfigType, show_hints: bool
    ) -> None:
        """Run plans to enable feature.

        The feature implementation is expected to override this function and
        specify the plans to be run to deploy the workload supported by feature.
        """

    def enable_feature(
        self, deployment: Deployment, config: ConfigType, show_hints: bool
    ) -> None:
        """Enable feature command."""
        self.user_manifest = None
        current_click_context = click.get_current_context()
        while current_click_context.parent is not None:
            if (
                current_click_context.parent.command.name == "enable"
                and "manifest" in current_click_context.parent.params
            ):
                if manifest := current_click_context.parent.params.get("manifest"):
                    self.user_manifest = Path(manifest)
                    deployment.get_manifest(self.user_manifest)
            current_click_context = current_click_context.parent

        self.pre_enable(deployment, config, show_hints)
        self.run_enable_plans(deployment, config, show_hints)
        self.post_enable(deployment, config, show_hints)
        self.update_feature_info(deployment.get_client(), {"enabled": "true"})

    def pre_disable(self, deployment: Deployment, show_hints: bool) -> None:
        """Handler to perform tasks before disabling the feature."""
        self.check_enablement_requirements(deployment, state="disable")

    def post_disable(self, deployment: Deployment, show_hints: bool) -> None:
        """Handler to perform tasks after the feature is disabled."""
        pass

    @abstractmethod
    def run_disable_plans(self, deployment: Deployment, show_hints: bool) -> None:
        """Run plans to disable feature.

        The feature implementation is expected to override this function and
        specify the plans to be run to destroy the workload supported by feature.
        """

    def disable_feature(self, deployment: Deployment, show_hints: bool) -> None:
        """Disable feature command."""
        self.pre_disable(deployment, show_hints)
        self.run_disable_plans(deployment, show_hints)
        self.post_disable(deployment, show_hints)
        self.update_feature_info(deployment.get_client(), {"enabled": "false"})

    def commands(
        self, conditions: typing.Mapping[str, str | bool] = {}
    ) -> dict[str, list[dict]]:
        """Dict of clickgroup along with commands."""
        enable_cmd, disable_cmd = self.toggle_commands()
        command_name = self.name
        enable_group = "enable"
        disable_group = "disable"
        if group := self.group:
            enable_group += f".{group.name}"
            disable_group += f".{group.name}"
            if command_name.startswith(group.name + "."):
                command_name = command_name[len(group.name) + 1 :]
        commands = {
            enable_group: [{"name": command_name, "command": enable_cmd}],
            disable_group: [{"name": command_name, "command": disable_cmd}],
        }
        if conditions.get("enabled", False):
            commands.update(self.enabled_commands())
        return commands

    def toggle_commands(self) -> tuple[click.BaseCommand, click.BaseCommand]:
        """Return enable and disable commands."""
        return self.enable_cmd, self.disable_cmd

    def enabled_commands(self) -> dict[str, list[dict]]:
        """Dict of clickgroup along with commands.

        Return the commands available once the feature is enabled.
        """
        return {}


def is_maas_deployment(deployment: Deployment) -> bool:
    """Check if deployment type is maas."""
    if deployment.type == MAAS_TYPE:
        return True

    return False
