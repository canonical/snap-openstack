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


import inspect
import logging
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Literal, Type, Union

import click
from packaging.requirements import Requirement
from packaging.version import Version
from snaphelpers import Snap

from sunbeam.clusterd.service import ConfigItemNotFoundException
from sunbeam.jobs.common import SunbeamException, read_config, update_config
from sunbeam.jobs.deployment import Deployment
from sunbeam.jobs.manifest import SoftwareConfig
from sunbeam.jobs.plugin import PluginManager
from sunbeam.plugins.interface import utils

LOG = logging.getLogger(__name__)


class PluginError(SunbeamException):
    """Common plugin exception base class."""


class MissingPluginError(PluginError):
    """Exception raised when plugin is not found."""


class MissingVersionInfoError(PluginError):
    """Exception raised when plugin version info is not found."""


class IncompatibleVersionError(PluginError):
    """Exception raised when plugin version is incompatible."""


class InvalidRequirementError(PluginError):
    """Exception raised when plugin requirement is invalid."""


class DisabledPluginError(PluginError):
    """Exception raised when plugin is disabled."""


class NotAutomaticPluginError(PluginError):
    """Exception rased when plugin cannot be automatically enabled."""


class ClickInstantiator:
    """Support invoking click commands on instance methods."""

    def __init__(self, command, klass, client):
        self.command = command
        self.klass = klass
        self.client = client

    def __call__(self, *args, **kwargs):
        """Invoke the click command on the instance method."""
        return self.command(self.klass(self.client), *args, **kwargs)


class BasePlugin(ABC):
    """Base class for Plugin interface."""

    # Version of plugin interface used by Plugin
    interface_version = Version("0.0.1")

    # Version of plugin
    version = Version("0.0.0")

    def __init__(self, name: str, deployment: Deployment) -> None:
        """Constructor for Base plugin.

        :param name: Name of the plugin
        """
        self.name = name
        self.deployment = deployment

    @property
    def plugin_key(self) -> str:
        """Key used to store plugin info in cluster database Config table."""
        return self._get_plugin_key(self.name)

    def _get_plugin_key(self, plugin: str) -> str:
        """Generate plugin key from plugin."""
        return f"Plugin-{plugin}"

    def install_hook(self) -> None:
        """Install hook for the plugin.

        snap-openstack install hook handler invokes this function on all the
        plugins. Plugins should override this function if required.
        """
        pass

    def upgrade_hook(self, upgrade_release: bool = False) -> None:
        """Upgrade hook for the plugin.

        snap-openstack upgrade hook handler invokes this function on all the
        plugins. Plugins should override this function if required.
        """
        pass

    def configure_hook(self) -> None:
        """Configure hook for the plugin.

        snap-openstack configure hook handler invokes this function on all the
        plugins. Plugins should override this function if required.
        """
        pass

    def pre_refresh_hook(self) -> None:
        """Pre refresh hook for the plugin.

        snap-openstack pre-refresh hook handler invokes this function on all the
        plugins. Plugins should override this function if required.
        """
        pass

    def post_refresh_hook(self) -> None:
        """Post refresh hook for the plugin.

        snap-openstack post-refresh hook handler invokes this function on all the
        plugins. Plugins should override this function if required.
        """
        pass

    def remove_hook(self) -> None:
        """Remove hook for the plugin.

        snap-openstack remove hook handler invokes this function on all the
        plugins. Plugins should override this function if required.
        """
        pass

    def get_plugin_info(self) -> dict:
        """Get plugin information from clusterdb.

        :returns: Dictionay with plugin details like version, and any other information
                  uploded by plugin.
        """
        try:
            return read_config(self.deployment.get_client(), self.plugin_key)
        except ConfigItemNotFoundException as e:
            LOG.debug(str(e))
            return {}

    def update_plugin_info(self, info: dict) -> None:
        """Update plugin information in clusterdb.

        Adds version info as well to the info dictionary to update in the cluster db.

        :param info: Plugin specific information as dictionary
        """
        info_from_db = self.get_plugin_info()
        info_from_db.update(info)
        info_from_db.update({"version": str(self.version)})
        update_config(self.deployment.get_client(), self.plugin_key, info_from_db)

    def fetch_plugin_version(self, plugin: str) -> Version:
        """Fetch plugin version stored in database.

        :param plugin: Name of the plugin
        :returns: Version of the plugin
        """
        try:
            config = read_config(
                self.deployment.get_client(), self._get_plugin_key(plugin)
            )
        except ConfigItemNotFoundException as e:
            raise MissingPluginError(f"Plugin {plugin} not found") from e
        version = config.get("version")
        if version is None:
            raise MissingVersionInfoError(f"Version info for plugin {plugin} not found")

        return Version(version)

    def manifest_defaults(self) -> SoftwareConfig:
        """Return manifest part of the plugin.

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
                "<plugin>-plan": {
                    "source": <Path of terraform plan>,
                }
            }
        }

        The plugins that uses terraform plan should override this function.
        """
        return SoftwareConfig()

    def add_manifest_section(self, manifest) -> None:
        """Add manifest section.

        Any new attributes to the manifest introduced by the plugin will be read as
        dict. This function should convert the new attribute to a dataclass if
        required and reassign it to manifest object. This will also help in
        validation of new attributes.
        """
        pass

    def manifest_attributes_tfvar_map(self) -> dict:
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

        The plugins that uses terraform plan should override this function.
        """
        return {}

    def preseed_questions_content(self) -> list:
        """Generate preseed manifest content.

        The returned content will be used in generation of manifest.
        """
        return []

    def update_proxy_model_configs(self) -> None:
        """Update proxy model configs.

        Plugins that creates a new model should override this function
        to update model-configs for the model.
        Plugin can get proxies using get_proxy_settings(self.plugin.deployment)
        """
        pass

    def get_terraform_plans_base_path(self) -> Path:
        """Return Terraform plan base location."""
        return Snap().paths.user_common

    def validate_commands(self) -> bool:
        """Validate the commands dictionary.

        Validate if the dictionary follows the format
        {<group>: [{"name": <command name>, "command": <command function>}]}

        Validates if the command is of type click.Group or click.Command.

        :returns: True if validation is successful, else False.
        """
        LOG.debug(f"Validating commands: {self.commands}")
        for group, commands in self.commands().items():
            for command in commands:
                cmd_name = command.get("name")
                cmd_func = command.get("command")
                if None in (cmd_name, cmd_func):
                    LOG.warning(
                        f"Plugin {self.name}: Commands dictionary is not in "
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
                        f"Plugin {self.name}: {cmd_func} should be either "
                        "click.Group or click.Command"
                    )
                    return False

        LOG.debug("Validation successful")
        return True

    def is_openstack_control_plane(self) -> bool:
        """Is plugin deploys openstack control plane.

        :returns: True if plugin deploys openstack control plane, else False.
                  Defaults to False.
        """
        return False

    def is_cluster_bootstrapped(self) -> bool:
        """Is sunbeam cluster bootstrapped.

        :returns: True if sunbeam cluster is bootstrapped, else False.
        """
        try:
            return self.deployment.get_client().cluster.check_sunbeam_bootstrapped()
        except ValueError:
            return False

    @abstractmethod
    def commands(self) -> dict:
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

    def register(self, cli: click.Group) -> None:
        """Register plugin groups and commands.

        :param cli: Sunbeam main cli group
        """
        LOG.debug(f"Registering plugin {self.name}")
        if not self.validate_commands():
            LOG.warning(f"Not able to register the plugin {self.name}")
            return

        groups = utils.get_all_registered_groups(cli)
        LOG.debug(f"Registered groups: {groups}")
        for group, commands in self.commands().items():
            group_obj = groups.get(group)
            if not group_obj:
                cmd_names = [command.get("name") for command in commands]
                LOG.warning(
                    f"Plugin {self.name}: Not able to register command "
                    f"{cmd_names} in group {group} as group does not exist"
                )
                continue

            for command in commands:
                cmd = command.get("command")
                cmd_name = command.get("name")
                if cmd_name in group_obj.list_commands({}):
                    if isinstance(cmd, click.Command):
                        LOG.warning(
                            f"Plugin {self.name}: Discarding adding command "
                            f"{cmd_name} as it already exists in group {group}"
                        )
                    else:
                        # Should be sub group and already exists
                        LOG.debug(
                            f"Plugin {self.name}: Group {cmd_name} already "
                            f"part of parent group {group}"
                        )
                    continue

                cmd.callback = ClickInstantiator(
                    cmd.callback, type(self), self.deployment
                )
                group_obj.add_command(cmd, cmd_name)
                LOG.debug(
                    f"Plugin {self.name}: Command {cmd_name} registered in "
                    f"group {group}"
                )

                # Add newly created click groups to the registered groups so that
                # commands within the plugin can be registered on group.
                # This allows plugin to create new groups and commands in single place.
                if isinstance(cmd, click.Group):
                    groups[f"{group}.{cmd_name}"] = cmd


class PluginRequirement(Requirement):
    def __init__(self, requirement_string: str, optional: bool = False):
        super().__init__(requirement_string)
        self.optional = optional

    @property
    def klass(self) -> Type["EnableDisablePlugin"]:
        """Return the plugin class for the requirement."""
        klass = PluginManager().resolve_plugin(self.name)
        if klass is None:
            raise InvalidRequirementError(f"Plugin {self.name} not found")
        if not issubclass(klass, EnableDisablePlugin):
            raise InvalidRequirementError(
                f"Plugin {self.name} is not of type EnableDisablePlugin"
            )
        return klass


class EnableDisablePlugin(BasePlugin):
    """Interface for plugins of type on/off.

    Plugins that can be enabled or disabled can use this interface instead
    of BasePlugin.
    """

    interface_version = Version("0.0.1")

    requires: set[PluginRequirement] = set()

    def __init__(self, name: str, deployment: Deployment) -> None:
        """Constructor for plugin interface.

        :param name: Name of the plugin
        """
        super().__init__(name, deployment)
        self.user_manifest = None

    @property
    def enabled(self) -> bool:
        """Plugin is enabled or disabled.

        Retrieves enabled field from the Plugin info saved in
        the database and returns enabled based on the enabled field.

        :returns: True if plugin is enabled, else False.
        """
        info = self.get_plugin_info()
        return info.get("enabled", "false").lower() == "true"

    def check_enabled_requirement_is_compatible(self, requirement: PluginRequirement):
        """Check if an enabled requirement is compatible with current requirer."""
        if len(requirement.specifier) == 0:
            # No version requirement, so no need to check version
            return

        try:
            current_version = self.fetch_plugin_version(requirement.name)
            LOG.debug(f"Plugin {requirement.name} version {current_version} found")
        except MissingVersionInfoError as e:
            LOG.debug(f"Version info for plugin {requirement.name} not found")
            raise PluginError(
                f"{requirement.name} has no version recorded,"
                f" {requirement.specifier} required"
            ) from e

        if not requirement.specifier.contains(current_version):
            raise IncompatibleVersionError(
                f"Plugin {self.name} requires '{requirement.name}"
                f"{requirement.specifier}' but enabled plugin is {current_version}"
            )

    def check_plugin_class_is_compatible(
        self, plugin: "EnableDisablePlugin", requirement: PluginRequirement
    ):
        """Check if actual plugin class is compatible with requirements."""
        if len(requirement.specifier) == 0:
            # No version requirement, so no need to check version
            return

        klass_version_compatible = all(
            (
                plugin.version,
                requirement.specifier,
                requirement.specifier.contains(plugin.version),
            )
        )

        if not klass_version_compatible:
            message = (
                f"Plugin {self.name} requires '{requirement.name}"
                f"{requirement.specifier}' but loaded plugin version"
                f" is {plugin.version}"
            )
            raise IncompatibleVersionError(
                " ".join(
                    (
                        "Plugin has an incompatible version.",
                        "This should not happen.",
                        message,
                    )
                )
            )

    def check_plugin_is_automatically_enableable(self, plugin: "EnableDisablePlugin"):
        """Check whether a plugin can be automatically enabled."""
        spec = inspect.getfullargspec(plugin.enable_plugin)
        if spec.args != ["self"]:
            raise NotAutomaticPluginError(
                f"Plugin {self.name} depends on {plugin.name},"
                f" and {plugin.name} cannot be automatically enabled."
                " Please enable it by running"
                f" 'sunbeam enable {plugin.name} <config options...>'"
            )

    def check_enablement_requirements(
        self,
        state: Union[Literal["enable"], Literal["disable"]] = "enable",
    ):
        """Check whether the plugin can be enabled."""
        plugins = PluginManager().get_all_plugin_classes()
        for klass in plugins:
            if not issubclass(klass, EnableDisablePlugin):
                continue
            plugin = klass(self.deployment)
            if not plugin.enabled:
                continue
            for requirement in plugin.requires:
                if requirement.name != self.name:
                    continue
                if state == "disable":
                    raise PluginError(
                        f"{plugin.name} is enabled and requires {self.name}"
                    )
                message = (
                    f"Plugin {plugin.name} is enabled and "
                    f"requires '{requirement.name}{requirement.specifier}'"
                )
                LOG.debug(message)
                if requirement.specifier.contains(self.version):
                    # we found the plugin, and it's compatible
                    break
                raise IncompatibleVersionError(message)

    def enable_requirements(self):
        """Iterate through requirements, enable plugins if possible."""
        for requirement in self.requires:
            plugin = requirement.klass(self.deployment)

            if plugin.enabled:
                self.check_enabled_requirement_is_compatible(requirement)
                # Plugin is already enabled, and has a compatible version.
                continue

            if requirement.optional:
                # Skip enablement since plugin is optional
                continue
            self.check_plugin_class_is_compatible(plugin, requirement)
            self.check_plugin_is_automatically_enableable(plugin)

            ctx = click.get_current_context()
            ctx.invoke(plugin.enable_plugin)

    def pre_enable(self) -> None:
        """Handler to perform tasks before enabling the plugin."""
        self.check_enablement_requirements()
        self.enable_requirements()

    def post_enable(self) -> None:
        """Handler to perform tasks after the plugin is enabled."""
        pass

    @abstractmethod
    def run_enable_plans(self) -> None:
        """Run plans to enable plugin.

        The plugin implementation is expected to override this function and
        specify the plans to be run to deploy the workload supported by plugin.
        """

    @abstractmethod
    def enable_plugin(self) -> None:
        """Enable plugin command."""
        self.user_manifest = None
        current_click_context = click.get_current_context()
        while current_click_context.parent is not None:
            if (
                current_click_context.parent.command.name == "enable"
                and "manifest" in current_click_context.parent.params  # noqa: W503
            ):
                self.user_manifest = current_click_context.parent.params.get("manifest")
            current_click_context = current_click_context.parent

        self.pre_enable()
        self.run_enable_plans()
        self.post_enable()
        self.update_plugin_info({"enabled": "true"})

    def pre_disable(self) -> None:
        """Handler to perform tasks before disabling the plugin."""
        self.check_enablement_requirements(state="disable")

    def post_disable(self) -> None:
        """Handler to perform tasks after the plugin is disabled."""
        pass

    @abstractmethod
    def run_disable_plans(self) -> None:
        """Run plans to disable plugin.

        The plugin implementation is expected to override this function and
        specify the plans to be run to destroy the workload supported by plugin.
        """

    @abstractmethod
    def disable_plugin(self) -> None:
        """Disable plugin command."""
        self.pre_disable()
        self.run_disable_plans()
        self.post_disable()
        self.update_plugin_info({"enabled": "false"})

    def commands(self) -> dict:
        """Dict of clickgroup along with commands."""
        return {
            "enable": [{"name": self.name, "command": self.enable_plugin}],
            "disable": [{"name": self.name, "command": self.disable_plugin}],
        }
