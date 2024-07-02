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

import importlib
import logging
from pathlib import Path
from typing import Dict, List, Optional

import click
import yaml

from sunbeam import utils
from sunbeam.jobs.common import SunbeamException
from sunbeam.jobs.deployment import Deployment
from sunbeam.jobs.manifest import SoftwareConfig

LOG = logging.getLogger(__name__)
PLUGIN_YAML = "plugins.yaml"


class PluginManager:
    """Class to expose functions to interact with plugins.

    Implement the functions required either by sunbeam
    cli or any other cluster operations that need to be
    triggered on all or some of the plugins.
    """

    @classmethod
    def get_core_plugins_path(cls) -> Path:
        """Returns the path where the core plugins are defined."""
        return Path(__file__).parent.parent / "plugins"

    @classmethod
    def get_plugins_map(
        cls,
        plugin_file: Path,
        raise_exception: bool = False,
    ) -> Dict[str, type]:
        """Return dict of {plugin name: plugin class} from plugin yaml.

        :param plugin_file: Plugin yaml file
        :param raise_exception: If set to true, raises an exception in case
                                plugin class is not loaded. By default, ignores
                                by logging the error message.

        :returns: Dict of plugin classes
        :raises: ModuleNotFoundError or AttributeError
        """
        plugins_yaml = {}
        with plugin_file.open() as file:
            plugins_yaml = yaml.safe_load(file)

        plugins = plugins_yaml.get("sunbeam-plugins", {}).get("plugins", [])
        plugin_classes = {}

        for plugin in plugins:
            module = None
            plugin_class = plugin.get("path")
            if plugin_class is None:
                continue
            module_class_ = plugin_class.rsplit(".", 1)
            try:
                module = importlib.import_module(module_class_[0])
                plugin_class = getattr(module, module_class_[1])
                plugin_classes[plugin["name"]] = plugin_class
            # Catching Exception instead of specific errors as plugins
            # can raise any exception based on implementation.
            except Exception as e:
                # Exceptions observed so far
                # ModuleNotFoundError, AttributeError, NameError
                LOG.debug(str(e))
                LOG.warning(f"Ignored loading plugin: {plugin_class}")
                if raise_exception:
                    raise e

                continue

        LOG.debug(f"Plugin classes: {plugin_classes}")
        return plugin_classes

    @classmethod
    def get_plugin_classes(
        cls, plugin_file: Path, raise_exception: bool = False
    ) -> List[type]:
        """Return a list of plugin classes from plugin yaml.

        :param plugin_file: Plugin yaml file
        :param raise_exception: If set to true, raises an exception in case
                                plugin class is not loaded. By default, ignores
                                by logging the error message.

        :returns: List of plugin classes
        :raises: ModuleNotFoundError or AttributeError
        """
        return list(cls.get_plugins_map(plugin_file, raise_exception).values())

    @classmethod
    def get_all_plugin_classes(cls) -> List[type]:
        """Return a list of plugin classes."""
        core_plugin_file = cls.get_core_plugins_path() / PLUGIN_YAML
        plugins = cls.get_plugin_classes(core_plugin_file)
        return plugins

    @classmethod
    def get_plugins(cls, deployment: Deployment) -> list:
        """Returns list of plugin name and description.

        :param deployment: Deployment instance.
        :returns: List of plugin name and description

        Sample output:
        [
            ("pro", "Ubuntu pro management plugin"),
        ]
        """
        plugin_file = cls.get_core_plugins_path() / PLUGIN_YAML

        plugins_yaml = {}
        with plugin_file.open() as file:
            plugins_yaml = yaml.safe_load(file)

        plugins_list = plugins_yaml.get("sunbeam-plugins", {}).get("plugins", {})
        return [
            (plugin.get("name"), plugin.get("description")) for plugin in plugins_list
        ]

    @classmethod
    def enabled_plugins(cls, deployment: Deployment) -> list:
        """Returns plugin names that are enabled.

        :param deployment: Deployment instance.
        :returns: List of enabled plugins
        """
        enabled_plugins = []
        for plugin in cls.get_all_plugin_classes():
            p = plugin(deployment)
            if hasattr(plugin, "enabled") and p.enabled:
                enabled_plugins.append(p.name)

        LOG.debug(f"Enabled plugins: {enabled_plugins}")
        return enabled_plugins

    @classmethod
    def get_all_plugin_manifests(
        cls, deployment: Deployment
    ) -> dict[str, SoftwareConfig]:
        """Return a dict of all plugin manifest defaults."""
        manifests = {}
        plugins = cls.get_all_plugin_classes()
        for klass in plugins:
            plugin = klass(deployment)
            manifests[plugin.name] = plugin.manifest_defaults()

        return manifests

    @classmethod
    def get_all_plugin_manifest_tfvar_map(cls, deployment: Deployment) -> dict:
        """Return a dict of all plugin manifest attributes terraformvars map."""
        tfvar_map = {}
        plugins = cls.get_all_plugin_classes()
        for klass in plugins:
            plugin = klass(deployment)
            m_dict = plugin.manifest_attributes_tfvar_map()
            utils.merge_dict(tfvar_map, m_dict)

        return tfvar_map

    @classmethod
    def add_manifest_section(
        cls, deployment: Deployment, software_config: SoftwareConfig
    ) -> None:
        """Allow every plugin to augment the manifest."""
        plugins = cls.get_all_plugin_classes()
        for klass in plugins:
            plugin = klass(deployment)
            plugin.add_manifest_section(software_config)

    @classmethod
    def get_preseed_questions_content(cls, deployment: Deployment) -> list:
        """Allow every plugin to add preseed questions to the preseed file."""
        content = []
        plugins = cls.get_all_plugin_classes()
        for klass in plugins:
            plugin = klass(deployment)
            content.extend(plugin.preseed_questions_content())

        return content

    @classmethod
    def get_all_charms_in_openstack_plan(cls, deployment: Deployment) -> list:
        """Return all charms in openstack-plan from all plugins."""
        charms = []
        plugins = cls.get_all_plugin_classes()
        for klass in plugins:
            plugin = klass(deployment)
            m_dict = plugin.manifest_attributes_tfvar_map()
            charms_from_plugin = list(
                m_dict.get("openstack-plan", {}).get("charms", {}).keys()
            )
            charms.extend(charms_from_plugin)

        return charms

    @classmethod
    def update_proxy_model_configs(cls, deployment: Deployment) -> None:
        """Make all plugins update proxy model configs."""
        plugins = cls.get_all_plugin_classes()
        for klass in plugins:
            plugin = klass(deployment)
            plugin.update_proxy_model_configs()

    @classmethod
    def register(
        cls,
        deployment: Deployment,
        cli: click.Group,
    ) -> None:
        """Register the plugins.

        Register the plugins. Once registeted, all the commands/groups defined by
        plugins will be shown as part of sunbeam cli.

        :param deployment: Deployment instance.
        :param cli: Main click group for sunbeam cli.
        """
        LOG.debug("Registering core plugins")
        for plugin in cls.get_all_plugin_classes():
            try:
                plugin(deployment).register(cli)
            except (ValueError, SunbeamException) as e:
                LOG.debug("Failed to register plugin: %r", str(plugin))
                if "Clusterd address" in str(e) or "Insufficient permissions" in str(e):
                    LOG.debug("Sunbeam not bootstrapped. Ignoring plugin registration.")
                    continue
                raise e

    @classmethod
    def resolve_plugin(cls, plugin: str) -> Optional[type]:
        """Resolve a plugin name to a class.

        Lookup plugins to find a plugin with the given name.
        """
        plugin_file = cls.get_core_plugins_path() / PLUGIN_YAML
        plugins = cls.get_plugins_map(plugin_file)
        return plugins.get(plugin)

    @classmethod
    def is_plugin_version_changed(cls, plugin) -> bool:
        """Check if plugin version is changed.

        Compare the plugin version in the database and the newly loaded one
        from plugins.yaml. Return true if versions are different.

        :param plugin: Plugin object
        :returns: True if versions are different.
        """
        LOG.debug("In plugin version changed check")
        if not hasattr(plugin, "get_plugin_info") or not hasattr(plugin, "version"):
            raise AttributeError("Plugin is not a valid plugin class")
        return not plugin.get_plugin_info().get("version", "0.0.0") == str(
            plugin.version
        )

    @classmethod
    def update_plugins(
        cls,
        deployment: Deployment,
        upgrade_release: bool = False,
    ) -> None:
        """Call plugin upgrade hooks.

        Get all the plugins and call the corresponding plugin upgrade hooks
        if the plugin is enabled and version is changed.

        :param deployment: Deployment instance.
        :param upgrade_release: Upgrade release flag.
        """
        for plugin in cls.get_all_plugin_classes():
            p = plugin(deployment)
            LOG.debug(f"Object created {p.name}")
            if (
                hasattr(plugin, "enabled")
                and p.enabled  # noqa W503
                and hasattr(plugin, "upgrade_hook")  # noqa W503
            ):
                LOG.debug(f"Upgrading plugin {p.name}")
                try:
                    p.upgrade_hook(upgrade_release=upgrade_release)
                except TypeError:
                    LOG.debug(
                        (
                            f"Plugin {p.name} does not support upgrades "
                            "between channels"
                        )
                    )
                    p.upgrade_hook()
