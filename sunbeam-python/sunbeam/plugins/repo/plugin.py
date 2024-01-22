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

"""External plugin repo management plugin."""

import logging
import os
import shutil
import sys
from pathlib import Path
from typing import Optional

import click
import jsonschema
import yaml
from git import Repo
from packaging.version import Version
from rich.console import Console
from rich.status import Status
from rich.table import Table
from snaphelpers import Snap

from sunbeam.clusterd.client import Client
from sunbeam.jobs.checks import DaemonGroupCheck, VerifyBootstrappedCheck
from sunbeam.jobs.common import (
    FORMAT_TABLE,
    FORMAT_YAML,
    BaseStep,
    Result,
    ResultType,
    run_plan,
    run_preflight_checks,
)
from sunbeam.jobs.plugin import PLUGIN_YAML, PluginManager
from sunbeam.plugins.interface.v1.base import BasePlugin
from sunbeam.utils import CatchGroup

LOG = logging.getLogger(__name__)
console = Console()


PLUGIN_SCHEMA = {
    "type": "object",
    "properties": {
        "sunbeam-plugins": {
            "type": "object",
            "properties": {
                "plugins": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "description": {"type": "string"},
                            "version": {"type": "string"},
                            "path": {"type": "string"},
                            "supported_architectures": {
                                "type": "array",
                                "items": {
                                    "type": "string",
                                    "enum": ["amd64", "arm64", "s390x", "ppc64le"],
                                },
                            },
                        },
                        "required": [
                            "name",
                            "description",
                            "version",
                            "path",
                            "supported_architectures",
                        ],
                    },
                },
                "description": {"type": "string"},
            },
            "required": ["plugins"],
        }
    },
    "required": ["sunbeam-plugins"],
}


class RepoAlreadyExistsException(Exception):
    pass


class PluginYamlNotFoundException(Exception):
    pass


class PluginYamlFormatException(Exception):
    pass


class PluginClassNotLoadedException(Exception):
    pass


class ExternalRepo:
    """External repo helper functions"""

    def __init__(
        self,
        name: str,
        dest_path: Path,
        git_repo: Optional[str] = None,
        git_branch: Optional[str] = None,
    ) -> None:
        """Constructor for the ExternalRepo.

        :param name: Name of the repo
        :param dest_path: Path where the repo need to be cloned
        :param git_repo: Git URL of the repo
        :param git_branch: Git branch or reference of the repo
        """
        self.name = name
        self.git_repo = git_repo
        self.git_branch = git_branch
        self.dest_path = dest_path
        self.repo = None
        self.snap = Snap()
        os.environ["GIT_EXEC_PATH"] = str(
            self.snap.paths.snap / "usr" / "lib" / "git-core"
        )
        os.environ["GIT_TEMPLATE_DIR"] = str(
            self.snap.paths.snap / "usr" / "share" / "git-core" / "templates"
        )
        os.environ["GIT_CONFIG_NOSYSTEM"] = "1"

    def initialize_local_repo(self) -> None:
        """Initialise the local repo.

        This is used to load the repo in this class which already exists
        in the destination.
        """
        to_path = self.dest_path / self.name
        self.repo = Repo(to_path)

    def clone_repo(self) -> None:
        """Clones the repo to the destination path."""
        to_path = self.dest_path / self.name
        if to_path.exists():
            raise RepoAlreadyExistsException(f"Repo already exists at {to_path}")
        self.repo = Repo.clone_from(self.git_repo, to_path, branch=self.git_branch)

    def validate_repo(self) -> None:
        """Validates the repo.

        Raises Exceptions if the repo validation is failed.
        """
        plugin_file = self.dest_path / self.name / PLUGIN_YAML
        self.validate_plugins_file(plugin_file)

    def load_plugins_yaml(self, repo_plugin_yaml: Path) -> str:
        """Load plugin yaml and check for yaml formatting.

        :returns: content of plugin yaml file
        """
        try:
            with open(repo_plugin_yaml, mode="r") as f:
                return yaml.safe_load(f.read())
        except FileNotFoundError:
            message = (
                f"Missing plugins.yaml in repo {self.git_repo} branch {self.git_branch}"
            )
            raise PluginYamlNotFoundException(message)
        except yaml.YAMLError as err:
            message = (
                f"Yaml format error in plugins.yaml file: {err.context} {err.problem}"
            )
            raise PluginYamlFormatException(message)

    def validate_plugins_file(self, repo_plugin_yaml: Path) -> None:
        """Validates the plugin yaml.

        Validates plugin yaml against plugin schema.
        Verifies if the plugin classes can be loaded properly or not.
        Raises Exceptions if validation is failed.
        """
        LOG.debug(f"Validating {str(repo_plugin_yaml)}")
        contents = self.load_plugins_yaml(repo_plugin_yaml)
        try:
            jsonschema.validate(contents, schema=PLUGIN_SCHEMA)
        except jsonschema.ValidationError as err:
            message = f"Invalid plugins.yaml file: {err.message}"
            raise PluginYamlFormatException(message)

        try:
            plugin_repo_path = str(repo_plugin_yaml.parent)
            if plugin_repo_path not in sys.path:
                sys.path.append(plugin_repo_path)

            PluginManager.get_plugin_classes(repo_plugin_yaml, raise_exception=True)
        except (ModuleNotFoundError, AttributeError) as e:
            raise PluginClassNotLoadedException(str(e))


class RepoPlugin(BasePlugin):
    """Plugin to manage external repos.

    Commands exposed by plugin to the user:
    sunbeam repo add --name <name> --repo <git url> --branch <git branch>
    sunbeam repo list --plugins --include-core
    sunbeam repo update --name <name>
    sunbeam repo remove --name <name>
    """

    version = Version("0.0.1")

    def __init__(self, client: Client) -> None:
        self.name = "repo"
        super().__init__(self.name, client)

    def commands(self) -> dict:
        return {
            "init": [{"name": self.name, "command": self.repo}],
            "repo": [
                {"name": "add", "command": self.add},
                {"name": "remove", "command": self.remove},
                {"name": "list", "command": self.list},
                {"name": "update", "command": self.update},
            ],
        }

    @click.group("repo", cls=CatchGroup)
    def repo(self):
        """Manage external plugin repos."""

    @click.command()
    @click.option(
        "-b", "--branch", type=str, default="main", help="Repo branch or reference"
    )
    @click.option(
        "-r", "--repo", type=str, prompt=True, help="Github link to the plugin repo"
    )
    @click.option("-n", "--name", type=str, prompt=True, help="Name of the repo")
    def add(self, name: str, repo: str, branch: str) -> None:
        """Add external plugin repo."""
        preflight_checks = []
        preflight_checks.append(DaemonGroupCheck())
        preflight_checks.append(VerifyBootstrappedCheck(self.client))
        run_preflight_checks(preflight_checks, console)

        if name.lower() == "core":
            click.echo(
                f"ERROR: {name} is reserved for Core plugins, use different name."
            )
            return

        external_plugins_dir = PluginManager.get_external_plugins_base_path()
        if not external_plugins_dir.exists():
            external_plugins_dir.mkdir(mode=0o775, exist_ok=True)
        repo = ExternalRepo(name, external_plugins_dir, repo, branch)

        plan = []
        plan.append(AddPluginRepoStep(repo, self))
        run_plan(plan, console)
        click.echo(f"External plugin repo {name} added.")

    @click.command("list")
    @click.option("-n", "--name", type=str, prompt=True, help="Name of the repo")
    def remove(self, name: str) -> None:
        """Remove external plugin repo."""
        preflight_checks = []
        preflight_checks.append(DaemonGroupCheck())
        preflight_checks.append(VerifyBootstrappedCheck(self.client))
        run_preflight_checks(preflight_checks, console)

        external_plugins_dir = PluginManager.get_external_plugins_base_path()

        plan = []
        plan.append(RemovePluginRepoStep(name, external_plugins_dir, self))
        run_plan(plan, console)
        click.echo(f"External plugin repo {name} removed.")

    @click.command()
    @click.option(
        "-f",
        "--format",
        type=click.Choice([FORMAT_TABLE, FORMAT_YAML]),
        default=FORMAT_TABLE,
        help="Output format, defaults to table.",
    )
    @click.option("-p", "--plugins", help="Include plugins in the output", is_flag=True)
    @click.option("-c", "--include-core", help="Include core plugins", is_flag=True)
    def list(self, format: str, plugins: bool, include_core: bool) -> None:
        """List external plugin repo."""
        preflight_checks = []
        preflight_checks.append(DaemonGroupCheck())
        preflight_checks.append(VerifyBootstrappedCheck(self.client))
        run_preflight_checks(preflight_checks, console)
        repos = PluginManager.get_all_external_repos(self.client, detail=True)
        if format == FORMAT_TABLE:
            table = Table()
            table.add_column("Name", justify="center")
            table.add_column("Repo path", justify="center")
            table.add_column("Repo Branch", justify="center")
            for repo in repos:
                table.add_row(
                    repo.get("name"), repo.get("git_repo"), repo.get("git_branch")
                )
            click.echo("List of repos:")
            console.print(table)

            if not plugins:
                return

            # Handle --plugins and --include-core
            click.echo("")
            repo_names = PluginManager.get_all_external_repos(self.client)
            if include_core:
                click.echo("Core plugins:")
                plugins = PluginManager.get_plugins(["core"])
                self._print_plugins_table(plugins.get("core"))

            for repo in repo_names:
                click.echo(f"Plugins in repo {repo}:")
                plugins = PluginManager.get_plugins([repo])
                self._print_plugins_table(plugins.get(repo))

        elif format == FORMAT_YAML:
            # Add plugins to the repos list
            if plugins:
                plugins = PluginManager.get_plugins()
                if include_core:
                    repos.append({"name": "core"})

                for repo in repos:
                    repo_name = repo.get("name")
                    repo["plugins"] = [
                        {"name": plugin[0], "description": plugin[1]}
                        for plugin in plugins.get(repo_name, {})
                    ]

            click.echo(yaml.dump(repos, sort_keys=True))

    @click.command()
    @click.option("-n", "--name", type=str, prompt=True, help="Name of the repo")
    def update(self, name: str) -> None:
        """Update external plugin repo."""
        preflight_checks = []
        preflight_checks.append(DaemonGroupCheck())
        preflight_checks.append(VerifyBootstrappedCheck(self.client))
        run_preflight_checks(preflight_checks, console)

        external_plugins_dir = PluginManager.get_external_plugins_base_path()
        external_repo = ExternalRepo(name, external_plugins_dir)
        external_repo.initialize_local_repo()

        plan = []
        plan.append(UpdatePluginRepoStep(self.client, external_repo, self))
        run_plan(plan, console)
        click.echo(f"External plugin repo {name} updated.")

    def _print_plugins_table(self, plugins: list) -> None:
        table = Table()
        table.add_column("Name", justify="center")
        table.add_column("Description", justify="left")
        for plugin in plugins:
            table.add_row(plugin[0], plugin[1])
        console.print(table)


class AddPluginRepoStep(BaseStep):
    """Add plugin repo."""

    def __init__(self, repo: ExternalRepo, plugin: RepoPlugin) -> None:
        super().__init__("Add external plugin repo", "Adding External plugin repo")
        self.repo = repo
        self.plugin = plugin

    def run(self, status: Optional[Status] = None) -> Result:
        """Clone and validate the repo"""
        try:
            LOG.debug(f"Cloning the repo {self.repo.git_repo}")
            self.repo.clone_repo()
            self.repo.validate_repo()
        except Exception as e:
            return Result(ResultType.FAILED, str(e))

        repo_info = {
            "name": self.repo.name,
            "git_repo": self.repo.git_repo,
            "git_branch": self.repo.git_branch,
        }
        plugin_info = self.plugin.get_plugin_info()
        LOG.debug(f"Plugin info from database: {plugin_info}")
        plugin_info.setdefault("repos", []).append(repo_info)
        self.plugin.update_plugin_info(plugin_info)
        LOG.debug(f"Updated plugin info to database: {plugin_info}")

        return Result(ResultType.COMPLETED)


class RemovePluginRepoStep(BaseStep):
    """Remove plugin repo."""

    def __init__(self, repo_name: str, repo_dir: Path, plugin: RepoPlugin) -> None:
        super().__init__("Remove external plugin repo", "Removing External plugin repo")
        self.repo_name = repo_name
        self.repo_dir = repo_dir
        self.plugin = plugin

    def run(self, status: Optional[Status] = None) -> Result:
        """Remove the repo if no plugins are enabled"""

        enabled_plugins = PluginManager.enabled_plugins([self.repo_name])
        if enabled_plugins:
            message = (
                f"ERROR: Cannot remove {self.repo_name} as following "
                f"plugins are enabled: {enabled_plugins}"
            )
            return Result(ResultType.FAILED, message)

        plugin_repo_dir = self.repo_dir / self.repo_name
        if plugin_repo_dir.exists():
            LOG.debug(f"Removing plugin directory {str(plugin_repo_dir)}")
            shutil.rmtree(str(plugin_repo_dir))

        plugin_info = self.plugin.get_plugin_info()
        LOG.debug(f"Plugin info from database: {plugin_info}")
        for repo in plugin_info.setdefault("repos", []):
            if repo.get("name") == self.repo_name:
                plugin_info["repos"].remove(repo)
                self.plugin.update_plugin_info(plugin_info)
                LOG.debug(f"Updated plugin info to database: {plugin_info}")
                return Result(ResultType.COMPLETED)

        return Result(
            ResultType.FAILED,
            f"Repo {self.repo_name} not exist in cluster database",
        )


class UpdatePluginRepoStep(BaseStep):
    """Update plugin repo."""

    def __init__(self, client: Client, repo: ExternalRepo, plugin: RepoPlugin) -> None:
        super().__init__("Update external plugin repo", "Updating External plugin repo")
        self.client = client
        self.repo = repo
        self.plugin = plugin

    def run(self, status: Optional[Status] = None) -> Result:
        """Update and validate the repo.

        Pull the repo from git url and branch and validate the repo.
        If validation is fine, run the upgrade hooks for the plugins
        that are enabled.
        """
        if self.repo.name not in PluginManager.get_all_external_repos(self.client):
            message = f"Repo {self.repo.name} not found in clusterdb"
            return Result(ResultType.FAILED, message)

        current_commit = None
        try:
            LOG.debug(f"Pulling the repo {self.repo.name}")
            current_commit = self.repo.repo.git.rev_parse("HEAD")
            LOG.debug(
                f"Current commit of existing repo {self.repo.name}: {current_commit}"
            )
            self.repo.repo.git.pull()
            self.repo.validate_repo()
        except Exception as e:
            if current_commit is not None:
                LOG.debug(f"Rolling back {self.repo.name} to commit {current_commit}")
                self.repo.repo.git.reset(current_commit, hard=True)
            return Result(ResultType.FAILED, str(e))

        PluginManager.update_plugins([self.repo.name])
        LOG.debug("Update Plugin info to change version")
        self.plugin.update_plugin_info({})

        return Result(ResultType.COMPLETED)
