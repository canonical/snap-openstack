# Copyright 2023 Canonical Ltd.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from unittest.mock import Mock, patch

import click
import pytest
from packaging.version import Version

from sunbeam.clusterd.service import ConfigItemNotFoundException
from sunbeam.jobs.plugin import PLUGIN_YAML, PluginManager
from sunbeam.plugins.interface.v1.base import (
    BasePlugin,
    EnableDisablePlugin,
    IncompatibleVersionError,
    MissingPluginError,
    MissingVersionInfoError,
    NotAutomaticPluginError,
    PluginError,
    PluginRequirement,
)


@pytest.fixture()
def cclient():
    yield Mock()


@pytest.fixture()
def utils():
    with patch("sunbeam.plugins.interface.v1.base.utils") as p:
        yield p


@pytest.fixture()
def clickinstantiator():
    with patch("sunbeam.plugins.interface.v1.base.ClickInstantiator") as p:
        yield p


@pytest.fixture()
def read_config():
    with patch("sunbeam.plugins.interface.v1.base.read_config") as p:
        yield p


@pytest.fixture()
def update_config():
    with patch("sunbeam.plugins.interface.v1.base.update_config") as p:
        yield p


def plugin_classes() -> list[type[EnableDisablePlugin]]:
    with patch("snaphelpers.Snap"):
        manager = PluginManager()
        yaml_file = manager.get_core_plugins_path() / PLUGIN_YAML
        classes = []
        for klass in manager.get_plugin_classes(yaml_file):
            if issubclass(klass, EnableDisablePlugin):
                classes.append(klass)
        return classes


class TestBasePlugin:
    def test_validate_commands(self, cclient):
        BasePlugin.__abstractmethods__ = set()
        with patch.object(BasePlugin, "commands") as mock_commands:
            plugin = BasePlugin("test", cclient)
            mock_commands.return_value = {
                "group1": [{"name": "cmd1", "command": click.Command("cmd1")}]
            }
            plugin = BasePlugin("test", cclient)
            result = plugin.validate_commands()
            assert result is True

    def test_validate_commands_missing_command_function(self, cclient):
        BasePlugin.__abstractmethods__ = set()
        with patch.object(BasePlugin, "commands") as mock_commands:
            mock_commands.return_value = {"group1": [{"name": "cmd1"}]}
            plugin = BasePlugin("test", cclient)
            result = plugin.validate_commands()
            assert result is False

    def test_validate_commands_missing_command_name(self, cclient):
        BasePlugin.__abstractmethods__ = set()
        with patch.object(BasePlugin, "commands") as mock_commands:
            mock_commands.return_value = {
                "group1": [{"command": click.Command("cmd1")}]
            }
            plugin = BasePlugin("test", cclient)
            result = plugin.validate_commands()
            assert result is False

    def test_validate_commands_empty_command_list(self, cclient):
        BasePlugin.__abstractmethods__ = set()
        with patch.object(BasePlugin, "commands") as mock_commands:
            mock_commands.return_value = {"group1": []}
            plugin = BasePlugin("test", cclient)
            result = plugin.validate_commands()
            assert result is True

    def test_validate_commands_subgroup_as_command(self, cclient):
        BasePlugin.__abstractmethods__ = set()
        with patch.object(BasePlugin, "commands") as mock_commands:
            mock_commands.return_value = {
                "group1": [{"name": "subgroup1", "command": click.Group("subgroup1")}]
            }
            plugin = BasePlugin("test", cclient)
            result = plugin.validate_commands()
            assert result is True

    def test_register(self, cclient, utils, clickinstantiator):
        with patch.object(BasePlugin, "commands") as mock_commands:
            cli = Mock()
            mock_groups = Mock()
            mock_group_obj = Mock()
            utils.get_all_registered_groups.return_value = mock_groups
            mock_groups.get.return_value = mock_group_obj
            mock_group_obj.list_commands.return_value = []

            cmd1_obj = click.Command("cmd1")
            mock_commands.return_value = {
                "group1": [{"name": "cmd1", "command": cmd1_obj}]
            }
            plugin = BasePlugin("test", cclient)
            plugin.register(cli)
            clickinstantiator.assert_called_once()
            mock_group_obj.add_command.assert_called_once_with(cmd1_obj, "cmd1")

    def test_register_when_command_already_exists(
        self, cclient, utils, clickinstantiator
    ):
        with patch.object(BasePlugin, "commands") as mock_commands:
            cli = Mock()
            mock_groups = Mock()
            mock_group_obj = Mock()
            utils.get_all_registered_groups.return_value = mock_groups
            mock_groups.get.return_value = mock_group_obj
            mock_group_obj.list_commands.return_value = ["cmd1"]

            cmd1_obj = click.Command("cmd1")
            mock_commands.return_value = {
                "group1": [{"name": "cmd1", "command": cmd1_obj}]
            }
            plugin = BasePlugin("test", cclient)
            plugin.register(cli)
            mock_group_obj.add_command.assert_not_called()
            clickinstantiator.assert_not_called()

    def test_register_when_group_doesnot_exists(
        self, cclient, utils, clickinstantiator
    ):
        with patch.object(BasePlugin, "commands") as mock_commands:
            cli = Mock()
            mock_groups = Mock()
            utils.get_all_registered_groups.return_value = mock_groups
            mock_groups.get.return_value = None

            cmd1_obj = click.Command("cmd1")
            mock_commands.return_value = {
                "group1": [{"name": "cmd1", "command": cmd1_obj}]
            }
            plugin = BasePlugin("test", cclient)
            plugin.register(cli)
            clickinstantiator.assert_not_called()

    def test_get_plugin_info(self, cclient, read_config):
        mock_info = {"version": "0.0.1"}
        read_config.return_value = mock_info
        plugin = BasePlugin("test", cclient)
        info = plugin.get_plugin_info()
        assert info == mock_info

    def test_get_plugin_info_no_config_in_db(self, cclient, read_config):
        read_config.side_effect = ConfigItemNotFoundException()
        plugin = BasePlugin("test", cclient)
        info = plugin.get_plugin_info()
        assert info == {}

    def test_update_plugin_info(self, cclient, read_config, update_config):
        mock_info = {}
        read_config.return_value = mock_info
        plugin = BasePlugin("test", cclient)
        plugin.update_plugin_info({"test": "test"})
        assert update_config.call_args.args[2] == {"test": "test", "version": "0.0.0"}

    def test_update_plugin_info_with_config_in_database(
        self, cclient, read_config, update_config
    ):
        mock_info = {"version": "0.0.1", "enabled": "true"}
        read_config.return_value = mock_info
        plugin = BasePlugin("test", cclient)
        plugin.update_plugin_info({"test": "test"})
        assert update_config.call_args.args[2] == {
            "enabled": "true",
            "test": "test",
            "version": "0.0.0",
        }

    def test_fetch_plugin_version_with_valid_plugin(self, cclient, read_config):
        client_instance = cclient()
        plugin_key = "test_plugin"
        config = {"version": "1.0.0"}
        read_config.return_value = config

        plugin = BasePlugin("test", cclient)
        plugin.client = client_instance
        version = plugin.fetch_plugin_version(plugin_key)
        assert version == Version("1.0.0")
        read_config.assert_called_once_with(client_instance, f"Plugin-{plugin_key}")

    def test_fetch_plugin_version_with_missing_plugin(self, cclient, read_config):
        client_instance = cclient()
        plugin_key = "test_plugin"
        read_config.side_effect = ConfigItemNotFoundException
        plugin = BasePlugin("test", cclient)
        plugin.client = client_instance
        with pytest.raises(MissingPluginError):
            plugin.fetch_plugin_version(plugin_key)
        read_config.assert_called_once_with(client_instance, f"Plugin-{plugin_key}")

    def test_fetch_plugin_version_with_missing_version_info(self, cclient, read_config):
        client_instance = cclient()
        plugin_key = "test_plugin"
        config = {}
        read_config.return_value = config
        plugin = BasePlugin("test", cclient)
        plugin.client = client_instance
        with pytest.raises(MissingVersionInfoError):
            plugin.fetch_plugin_version(plugin_key)
        read_config.assert_called_once_with(client_instance, f"Plugin-{plugin_key}")


class DummyPlugin(EnableDisablePlugin):
    version = Version("0.0.1")

    def __init__(self, name: str, client):
        self.name = name
        self.client = client

    def enable_plugin(self) -> None:
        pass

    def disable_plugin(self) -> None:
        pass

    def run_enable_plans(self) -> None:
        pass

    def run_disable_plans(self) -> None:
        pass


def plugin_klass(version_: str) -> type[EnableDisablePlugin]:
    class CompatiblePlugin(EnableDisablePlugin):
        name = "compatible"
        version = Version(version_)

        requires = {PluginRequirement("test_req>=1.0.0")}

        def __init__(self, client):
            super().__init__(self.name, client)

        def enable_plugin(self, *args, **kwargs) -> None:
            pass

        def disable_plugin(self, *args, **kwargs) -> None:
            pass

        def run_enable_plans(self) -> None:
            pass

        def run_disable_plans(self) -> None:
            pass

    return CompatiblePlugin


class TestEnableDisablePlugin:
    def test_check_enabled_plugin_is_compatible_with_compatible_requirement(
        self, cclient, mocker
    ):
        plugin = DummyPlugin("test_plugin", cclient)
        mocker.patch.object(plugin, "fetch_plugin_version", return_value="1.0.0")
        requirement = PluginRequirement("test_repo.test_plugin>=1.0.0")
        plugin.check_enabled_requirement_is_compatible(requirement)

    def test_check_enabled_plugin_is_compatible_with_missing_version_info(
        self, cclient, mocker
    ):
        plugin = DummyPlugin("test_plugin", cclient)

        mocker.patch.object(
            plugin, "fetch_plugin_version", side_effect=MissingVersionInfoError
        )
        requirement = PluginRequirement("test_repo.test_plugin>=1.0.0")
        with pytest.raises(PluginError):
            plugin.check_enabled_requirement_is_compatible(requirement)

    def test_check_enabled_plugin_is_compatible_with_incompatible_requirement(
        self, cclient, mocker
    ):
        plugin = DummyPlugin("test_plugin", cclient)

        mocker.patch.object(plugin, "fetch_plugin_version", return_value="0.9.0")
        requirement = PluginRequirement("test_repo.test_plugin>=1.0.0")
        with pytest.raises(IncompatibleVersionError):
            plugin.check_enabled_requirement_is_compatible(requirement)

    def test_check_enabled_plugin_is_compatible_with_optional_requirement(
        self, cclient, mocker
    ):
        plugin = DummyPlugin("test_plugin", cclient)

        mocker.patch.object(
            plugin, "fetch_plugin_version", side_effect=MissingVersionInfoError
        )
        requirement = PluginRequirement("test_repo.test_plugin>=1.0.0", optional=True)
        with pytest.raises(PluginError):
            plugin.check_enabled_requirement_is_compatible(requirement)

    def test_check_enabled_plugin_is_compatible_with_no_specifier_and_optional_requirement(  # noqa: E501
        self, cclient, mocker
    ):
        plugin = DummyPlugin("test_plugin", cclient)

        mocker.patch.object(
            plugin, "fetch_plugin_version", side_effect=MissingVersionInfoError
        )
        requirement = PluginRequirement("test_repo.test_plugin", optional=True)
        plugin.check_enabled_requirement_is_compatible(requirement)

    def test_check_enabled_plugin_is_compatible_with_no_specifier_and_required_requirement(  # noqa: E501
        self, cclient, mocker
    ):
        plugin = DummyPlugin("test_plugin", cclient)

        mocker.patch.object(
            plugin, "fetch_plugin_version", side_effect=MissingVersionInfoError
        )
        requirement = PluginRequirement("test_repo.test_plugin", optional=False)
        plugin.check_enabled_requirement_is_compatible(requirement)

    def test_check_plugin_class_is_compatible_with_compatible_requirement(
        self, cclient
    ):
        plugin = DummyPlugin("test_plugin", cclient)

        requirement = PluginRequirement("test_repo.test_plugin>=1.0.0")

        klass = plugin_klass("1.0.0")
        plugin.check_plugin_class_is_compatible(klass(cclient), requirement)

    def test_check_plugin_class_is_compatible_with_incompatible_requirement(
        self, cclient
    ):
        plugin = DummyPlugin("test_plugin", cclient)

        requirement = PluginRequirement("test_repo.test_plugin>=2.0.0")

        klass = plugin_klass("1.0.0")
        with pytest.raises(IncompatibleVersionError):
            plugin.check_plugin_class_is_compatible(klass(cclient), requirement)

    def test_check_plugin_class_is_compatible_with_core_plugin_and_incompatible_version(
        self, cclient
    ):
        plugin = DummyPlugin("test_plugin", cclient)

        requirement = PluginRequirement("core.test_plugin>=1.0.0")

        klass = plugin_klass("0.5.0")
        with pytest.raises(IncompatibleVersionError):
            plugin.check_plugin_class_is_compatible(klass(cclient), requirement)

    def test_check_plugin_class_is_compatible_with_no_specifier(self, cclient):
        plugin = DummyPlugin("test_plugin", cclient)

        requirement = PluginRequirement("test_repo.test_plugin")

        klass = plugin_klass("1.0.0")
        plugin.check_plugin_class_is_compatible(klass(cclient), requirement)

    def test_check_plugin_is_automatically_enableable_with_automatically_enableable_plugin(  # noqa: E501
        self, cclient
    ):
        plugin = DummyPlugin("test_plugin", cclient)
        plugin.check_plugin_is_automatically_enableable(plugin)  # type: ignore

    def test_check_plugin_is_automatically_enableable_with_non_automatically_enableable_plugin(  # noqa: E501
        self, cclient
    ):
        BasePlugin.__abstractmethods__ = frozenset()
        EnableDisablePlugin.__abstractmethods__ = frozenset()

        class DummyPlugin_(DummyPlugin):
            def enable_plugin(self, necessary_arg) -> None:
                return super().enable_plugin()

        required_plugin = DummyPlugin_(name="test_plugin", client=cclient())

        plugin = DummyPlugin("test_plugin", cclient)
        with pytest.raises(NotAutomaticPluginError):
            plugin.check_plugin_is_automatically_enableable(required_plugin)  # type: ignore # noqa: E501

    @pytest.mark.parametrize("klass", plugin_classes())
    def test_core_plugins_requirements(self, cclient, klass):
        plugin = klass(client=cclient)

        for requirement in plugin.requires:
            plugin.check_plugin_class_is_compatible(
                requirement.klass(client=cclient), requirement
            )

    def test_check_enablement_requirements_with_enabled_compatible_requirement(
        self, cclient, mocker
    ):
        plugin = DummyPlugin(client=cclient, name="test_req")
        plugin.version = Version("1.0.1")
        klass = plugin_klass("0.0.1")
        mocker.patch.object(
            klass,
            "get_plugin_info",
            return_value={"version": klass.version, "enabled": "true"},
        )
        mocker.patch.object(
            PluginManager, "get_all_plugin_classes", return_value=[klass]
        )
        plugin.check_enablement_requirements()

    def test_check_enablement_requirements_with_disabled_compatible_requirement(
        self, cclient, mocker
    ):
        client = cclient()
        plugin = DummyPlugin(client=client, name="test_req")
        klass = plugin_klass("0.0.1")
        mocker.patch.object(
            klass,
            "get_plugin_info",
            return_value={"version": klass.version, "enabled": "false"},
        )
        mocker.patch.object(
            PluginManager, "get_all_plugin_classes", return_value=[klass]
        )
        plugin.check_enablement_requirements()

    def test_check_enablement_requirements_with_enabled_incompatible_requirement(
        self, cclient, mocker
    ):
        client = cclient()
        plugin = DummyPlugin(client=client, name="test_req")
        klass = plugin_klass("0.0.1")
        mocker.patch.object(
            klass,
            "get_plugin_info",
            return_value={"version": klass.version, "enabled": "true"},
        )
        mocker.patch.object(
            PluginManager, "get_all_plugin_classes", return_value=[klass]
        )
        with pytest.raises(IncompatibleVersionError):
            plugin.check_enablement_requirements()

    def test_check_enablement_requirements_with_disabled_incompatible_requirement(
        self, cclient, mocker
    ):
        client = cclient()
        plugin = DummyPlugin(client=client, name="test_req")
        klass = plugin_klass("0.0.1")
        mocker.patch.object(
            klass,
            "get_plugin_info",
            return_value={"version": klass.version, "enabled": "false"},
        )
        mocker.patch.object(
            PluginManager, "get_all_plugin_classes", return_value=[klass]
        )
        plugin.check_enablement_requirements()

    def test_check_enablement_requirements_with_enabled_dependant(
        self, cclient, mocker
    ):
        client = cclient()
        plugin = DummyPlugin(client=client, name="test_req")
        klass = plugin_klass("0.0.1")
        mocker.patch.object(
            klass,
            "get_plugin_info",
            return_value={"version": klass.version, "enabled": "true"},
        )
        mocker.patch.object(
            PluginManager, "get_all_plugin_classes", return_value=[klass]
        )
        with pytest.raises(PluginError):
            plugin.check_enablement_requirements("disable")

    def test_check_enablement_requirements_with_disabled_dependant(
        self, cclient, mocker
    ):
        client = cclient()
        plugin = DummyPlugin(client=client, name="test_req")
        klass = plugin_klass("0.0.1")
        mocker.patch.object(
            klass,
            "get_plugin_info",
            return_value={"version": klass.version, "enabled": "false"},
        )
        mocker.patch.object(
            PluginManager, "get_all_plugin_classes", return_value=[klass]
        )
        plugin.check_enablement_requirements("disable")
