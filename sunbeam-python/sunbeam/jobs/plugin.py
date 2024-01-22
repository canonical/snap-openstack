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
import sys
from pathlib import Path
from typing import Dict, List, Optional

import click
import yaml
from snaphelpers import Snap

from sunbeam.clusterd.client import Client
from sunbeam.clusterd.service import (
    ClusterServiceUnavailableException,
    ConfigItemNotFoundException,
)
from sunbeam.jobs.common import read_config

LOG = logging.getLogger(__name__)
PLUGIN_YAML = "plugins.yaml"
# Plugin-<repo plugin name>
EXTERNAL_REPO_PLUGIN_KEY = "Plugin-repo"


class PluginManager:
    """Class to expose functions to interact with plugins.

    Implement the functions required either by sunbeam
    cli or any other cluster operations that need to be
    triggered on all or some of the plugins.
    """

    @classmethod
    def get_external_plugins_base_path(cls) -> Path:
        """Returns the path in snap where external repos are cloned."""
        return Snap().paths.user_data / "plugins"

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
        """Return a lsit of plugin classes from all repositories."""
        core_plugin_file = cls.get_core_plugins_path() / PLUGIN_YAML
        plugins = cls.get_plugin_classes(core_plugin_file)
        for path in cls.get_external_plugins_base_path().glob("*"):
            if not path.is_dir():
                continue
            plugins.extend(cls.get_plugin_classes(path / PLUGIN_YAML))
        return plugins

    @classmethod
    def get_all_external_repos(cls, client: Client, detail: bool = False) -> list:
        """Return all external repos stored in DB.

        Returns just names by default, the format will be
        [<repo1>, <repo2>,...]
        If details is True, returns names and git repo, branch details.
        The format will be
        [
            {"name": <repo1>, "git_repo": <repo url>, "git_branch": <repo branch>},
            {"name": <repo2>, "git_repo": <repo url>, "git_branch": <repo branch>},
            ...
        ]

        :param detail: If true, includes repo path and branch as well.
        :returns: List of repos.
        """
        try:
            config = read_config(client, EXTERNAL_REPO_PLUGIN_KEY)
            if detail:
                return config.get("repos", [])
            else:
                repos = [
                    repo.get("name")
                    for repo in config.get("repos", [])
                    if "name" in repo
                ]
                return repos
        except (ConfigItemNotFoundException, ClusterServiceUnavailableException) as e:
            LOG.debug(str(e))
            return []

    @classmethod
    def get_plugins(cls, repos: Optional[list] = []) -> dict:
        """Returns list of plugin name and description.

        Get all plugins information for each repo specified in repos.
        If repos is None or empty list, get plugins for all the repos
        including the internal plugins in snap-openstack repo. Repo name
        core is reserved for internal plugins in snap-openstack repo.

        :param repos: List of repos
        :returns: Dictionary of repo with plugin name and description

        Sample output:
        {
            "core": {
                [
                    ("pro", "Ubuntu pro management plugin"),
                    ("repo", "External plugin repo management"
                ]
            }
        }
        """
        if not repos:
            repos.append("core")
            repos.extend(cls.get_all_external_repos())

        plugins = {}
        for repo in repos:
            if repo == "core":
                plugin_file = cls.get_core_plugins_path() / PLUGIN_YAML
            else:
                plugin_file = cls.get_external_plugins_base_path() / repo / PLUGIN_YAML

            plugins_yaml = {}
            with plugin_file.open() as file:
                plugins_yaml = yaml.safe_load(file)

            plugins_list = plugins_yaml.get("sunbeam-plugins", {}).get("plugins", {})
            plugins[repo] = []
            plugins[repo].extend(
                [
                    (plugin.get("name"), plugin.get("description"))
                    for plugin in plugins_list
                ]
            )

        return plugins

    @classmethod
    def enabled_plugins(cls, repos: Optional[list] = []) -> list:
        """Returns plugin names that are enabled.

        Get all plugins from the list of repos and return plugins that have enabled
        as True.
        Repo name core is reserved for internal plugins in snap-openstack repo.
        If repos is None or empty list, get plugins from all repos defined in
        cluster db including the internal plugins.

        :param repos: List of repos
        :returns: List of enabled plugins
        """
        enabled_plugins = []
        if not repos:
            repos.append("core")
            repos.extend(cls.get_all_external_repos())

        for repo in repos:
            if repo == "core":
                plugin_file = cls.get_core_plugins_path() / PLUGIN_YAML
            else:
                plugin_file = cls.get_external_plugins_base_path() / repo / PLUGIN_YAML
                plugin_repo_path = str(plugin_file.parent)
                if plugin_repo_path not in sys.path:
                    sys.path.append(plugin_repo_path)

            # If the repo folder is already deleted
            if not plugin_file.exists():
                LOG.debug(
                    f"Discarding loading plugins for repo {repo} as Plugin "
                    "yaml does not exist"
                )
                continue

            for plugin in cls.get_plugin_classes(plugin_file):
                p = plugin()
                if hasattr(plugin, "enabled") and p.enabled:
                    enabled_plugins.append(p.name)

        LOG.debug(f"Enabledplugins in repos {repos}: {enabled_plugins}")
        return enabled_plugins

    @classmethod
    def register(cls, cli: click.Group, client: Client) -> None:
        """Register the plugins.

        Register both the core plugins in snap-openstack repo and the plugins
        in the external repos added to sunbeam by the user. Once registeted,
        all the commands/groups defined by plugins will be shown as part of
        sunbeam cli.

        :param cli: Main click group for sunbeam cli.
        :param client: Clusterd client object.
        """
        LOG.debug("Registering core plugins")
        core_plugin_file = cls.get_core_plugins_path() / PLUGIN_YAML
        for plugin in cls.get_plugin_classes(core_plugin_file):
            plugin(client).register(cli)

        repos = cls.get_all_external_repos(client)
        LOG.debug(f"Registering external repo plugins {repos}")
        for repo in repos:
            plugin_file = cls.get_external_plugins_base_path() / repo / PLUGIN_YAML
            plugin_repo_path = str(plugin_file.parent)
            if plugin_repo_path not in sys.path:
                sys.path.append(plugin_repo_path)

            # If the repo folder is already deleted
            if not plugin_file.exists():
                continue

            for plugin in cls.get_plugin_classes(plugin_file):
                plugin().register(cli)

    @classmethod
    def resolve_plugin(cls, repo: str, plugin: str) -> Optional[type]:
        """Resolve a plugin name to a class.

        Lookup core and external plugins to find a plugin with the given name.
        """
        if repo == "core":
            plugin_file = cls.get_core_plugins_path() / PLUGIN_YAML
        else:
            plugin_file = cls.get_external_plugins_base_path() / repo / PLUGIN_YAML
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
        cls, repos: Optional[list] = [], upgrade_release: bool = False
    ) -> None:
        """Call plugin upgrade hooks.

        Get all the plugins defined in repos and call the corresponding plugin
        upgrade hooks if the plugin is enabled and version is changed. Do not
        run any upgrade hooks if repos is empty list.

        :param repos: List of repos
        """
        if not repos:
            return

        for repo in repos:
            LOG.debug(f"Upgrading plugins for repo {repo}")
            if repo == "core":
                plugin_file = cls.get_core_plugins_path() / PLUGIN_YAML
            else:
                plugin_file = cls.get_external_plugins_base_path() / repo / PLUGIN_YAML
                plugin_repo_path = str(plugin_file.parent)
                if plugin_repo_path not in sys.path:
                    sys.path.append(plugin_repo_path)

            # If the repo folder is already deleted
            if not plugin_file.exists():
                continue

            for plugin in cls.get_plugin_classes(plugin_file):
                p = plugin()
                LOG.debug(f"Object created {p.name}")
                if hasattr(plugin, "enabled"):
                    LOG.debug(f"enabled - {p.enabled}")
                if (
                    hasattr(plugin, "enabled")
                    and p.enabled  # noqa W503
                    and hasattr(plugin, "upgrade_hook")  # noqa W503
                ):
                    LOG.debug(f"Upgrading plugin {p.name} defined in repo {repo}")
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
