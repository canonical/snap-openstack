# SPDX-FileCopyrightText: 2023 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

from unittest.mock import Mock, patch

import click
import pydantic
import pytest
from packaging.version import Version

from sunbeam.clusterd.service import ConfigItemNotFoundException
from sunbeam.core.manifest import FeatureManifest, Manifest
from sunbeam.feature_manager import FeatureManager
from sunbeam.features.interface.v1.base import (
    BaseFeature,
    EnableDisableFeature,
    FeatureError,
    FeatureRequirement,
    IncompatibleVersionError,
    MissingFeatureError,
    MissingVersionInfoError,
    NotAutomaticFeatureError,
)


@pytest.fixture()
def deployment():
    yield Mock()


@pytest.fixture()
def utils():
    with patch("sunbeam.features.interface.v1.base.utils") as p:
        yield p


@pytest.fixture()
def clickinstantiator():
    with patch("sunbeam.features.interface.v1.base.ClickInstantiator") as p:
        yield p


@pytest.fixture()
def read_config():
    with patch("sunbeam.features.interface.v1.base.read_config") as p:
        yield p


@pytest.fixture()
def update_config():
    with patch("sunbeam.features.interface.v1.base.update_config") as p:
        yield p


@pytest.fixture(autouse=True)
def base_feature_abc():
    """Disable abstract methods for ease of testing."""
    base_abstract_methods = BaseFeature.__abstractmethods__
    enable_abstract_methods = EnableDisableFeature.__abstractmethods__
    BaseFeature.__abstractmethods__ = frozenset()
    EnableDisableFeature.__abstractmethods__ = frozenset()
    yield
    BaseFeature.__abstractmethods__ = base_abstract_methods
    EnableDisableFeature.__abstractmethods__ = enable_abstract_methods


def feature_classes() -> list[type[EnableDisableFeature]]:
    with (
        patch("snaphelpers.Snap"),
        patch(
            "os.environ",
            {
                "SNAP": "/dev/null",
                "SNAP_COMMON": "/dev/null",
                "SNAP_DATA": "/dev/null",
                "SNAP_USER_COMMON": "/dev/null",
                "SNAP_USER_DATA": "/dev/null",
                "SNAP_REAL_HOME": "/dev/null",
                "SNAP_INSTANCE_NAME": "",
                "SNAP_NAME": "sunbeam",
                "SNAP_REVISION": "2",
                "SNAP_VERSION": "1.2.3",
            },
        ),
    ):
        manager = FeatureManager()
        classes = []
        for klass in manager.get_all_feature_classes():
            if issubclass(klass, EnableDisableFeature):
                classes.append(klass)
        return classes


class TestBaseFeature:
    def test_validate_commands(self, deployment):
        with patch.object(BaseFeature, "commands") as mock_commands:
            feature = BaseFeature()
            feature.name = "test_feature"
            mock_commands.return_value = {
                "group1": [{"name": "cmd1", "command": click.Command("cmd1")}]
            }
            feature = BaseFeature()
            feature.name = "test_feature"
            result = feature.validate_commands()
            assert result is True

    def test_validate_commands_missing_command_function(self, deployment):
        with patch.object(BaseFeature, "commands") as mock_commands:
            mock_commands.return_value = {"group1": [{"name": "cmd1"}]}
            feature = BaseFeature()
            feature.name = "test_feature"
            result = feature.validate_commands()
            assert result is False

    def test_validate_commands_missing_command_name(self, deployment):
        with patch.object(BaseFeature, "commands") as mock_commands:
            mock_commands.return_value = {
                "group1": [{"command": click.Command("cmd1")}]
            }
            feature = BaseFeature()
            feature.name = "test_feature"
            result = feature.validate_commands()
            assert result is False

    def test_validate_commands_empty_command_list(self, deployment):
        with patch.object(BaseFeature, "commands") as mock_commands:
            mock_commands.return_value = {"group1": []}
            feature = BaseFeature()
            feature.name = "test_feature"
            result = feature.validate_commands()
            assert result is True

    def test_validate_commands_subgroup_as_command(self, deployment):
        with patch.object(BaseFeature, "commands") as mock_commands:
            mock_commands.return_value = {
                "group1": [{"name": "subgroup1", "command": click.Group("subgroup1")}]
            }
            feature = BaseFeature()
            feature.name = "test_feature"
            result = feature.validate_commands()
            assert result is True

    def test_register(self, deployment, utils, clickinstantiator):
        with patch.object(BaseFeature, "commands") as mock_commands:
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
            feature = BaseFeature()
            feature.name = "test_feature"
            feature.register(cli)
            clickinstantiator.assert_called_once()
            mock_group_obj.add_command.assert_called_once_with(cmd1_obj, "cmd1")

    def test_register_when_command_already_exists(
        self, deployment, utils, clickinstantiator
    ):
        with patch.object(BaseFeature, "commands") as mock_commands:
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
            feature = BaseFeature()
            feature.name = "test_feature"
            feature.register(cli)
            mock_group_obj.add_command.assert_not_called()
            clickinstantiator.assert_not_called()

    def test_register_when_group_doesnot_exists(
        self, deployment, utils, clickinstantiator
    ):
        with patch.object(BaseFeature, "commands") as mock_commands:
            cli = Mock()
            mock_groups = Mock()
            utils.get_all_registered_groups.return_value = mock_groups
            mock_groups.get.return_value = None

            cmd1_obj = click.Command("cmd1")
            mock_commands.return_value = {
                "group1": [{"name": "cmd1", "command": cmd1_obj}]
            }
            feature = BaseFeature()
            feature.name = "test_feature"
            feature.register(cli)
            clickinstantiator.assert_not_called()

    def test_get_feature_info(self, deployment, read_config):
        mock_info = {"version": "0.0.1"}
        read_config.return_value = mock_info
        feature = BaseFeature()
        feature.name = "test_feature"
        info = feature.get_feature_info(Mock())
        assert info == mock_info

    def test_get_feature_info_no_config_in_db(self, deployment, read_config):
        read_config.side_effect = ConfigItemNotFoundException()
        feature = BaseFeature()
        feature.name = "test_feature"
        info = feature.get_feature_info(Mock())
        assert info == {}

    def test_update_feature_info(self, deployment, read_config, update_config):
        mock_info = {}
        read_config.return_value = mock_info
        feature = BaseFeature()
        feature.name = "test_feature"
        feature.update_feature_info(Mock(), {"test": "test"})
        assert update_config.call_args.args[2] == {"test": "test", "version": "0.0.0"}

    def test_update_feature_info_with_config_in_database(
        self, deployment, read_config, update_config
    ):
        mock_info = {"version": "0.0.1", "enabled": "true"}
        read_config.return_value = mock_info
        feature = BaseFeature()
        feature.name = "test_feature"
        feature.update_feature_info(Mock(), {"test": "test"})
        assert update_config.call_args.args[2] == {
            "enabled": "true",
            "test": "test",
            "version": "0.0.0",
        }

    def test_fetch_feature_version_with_valid_feature(self, deployment, read_config):
        client_instance = Mock()
        deployment.get_client.return_value = client_instance
        feature_key = "test_feature"
        config = {"version": "1.0.0"}
        read_config.return_value = config

        feature = BaseFeature()
        feature.name = "test_feature"
        version = feature.fetch_feature_version(client_instance, feature_key)
        assert version == Version("1.0.0")
        read_config.assert_called_once_with(client_instance, f"Feature-{feature_key}")

    def test_fetch_feature_version_with_missing_feature(self, deployment, read_config):
        client_instance = Mock()
        deployment.get_client.return_value = client_instance
        feature_key = "test_feature"
        read_config.side_effect = ConfigItemNotFoundException
        feature = BaseFeature()
        feature.name = "test_feature"
        with pytest.raises(MissingFeatureError):
            feature.fetch_feature_version(client_instance, feature_key)
        read_config.assert_called_once_with(client_instance, f"Feature-{feature_key}")

    def test_fetch_feature_version_with_missing_version_info(
        self, deployment, read_config
    ):
        client_instance = Mock()
        deployment.get_client.return_value = client_instance
        feature_key = "test_feature"
        config = {}
        read_config.return_value = config
        feature = BaseFeature()
        feature.name = "test_feature"
        with pytest.raises(MissingVersionInfoError):
            feature.fetch_feature_version(client_instance, feature_key)
        read_config.assert_called_once_with(client_instance, f"Feature-{feature_key}")


class DummyFeature(EnableDisableFeature):
    version = Version("0.0.1")
    name = "test_feature"

    def enable_feature(self, deployment, config) -> None:
        pass

    def disable_feature(self, deployment) -> None:
        pass

    def run_enable_plans(self, deployment, config) -> None:
        pass

    def run_disable_plans(self, deployment) -> None:
        pass


def feature_klass(version_: str, enabled: bool = False) -> type[EnableDisableFeature]:
    class CompatibleFeature(EnableDisableFeature):
        version = Version(version_)
        requires = {FeatureRequirement("test_req>=1.0.0")}
        name = "test_req"

        def is_enabled(self, deployment) -> bool:
            return enabled

        def enable_feature(self, *args, **kwargs) -> None:
            pass

        def disable_feature(self, *args, **kwargs) -> None:
            pass

        def run_enable_plans(self, deployment, config) -> None:
            pass

        def run_disable_plans(self, deployment) -> None:
            pass

    return CompatibleFeature


class TestEnableDisableFeature:
    def test_check_enabled_feature_is_compatible_with_compatible_requirement(
        self, deployment, mocker
    ):
        feature = DummyFeature()
        mocker.patch.object(feature, "fetch_feature_version", return_value="1.0.0")
        requirement = FeatureRequirement("test_feature>=1.0.0")
        feature.check_enabled_requirement_is_compatible(deployment, requirement)

    def test_check_enabled_feature_is_compatible_with_missing_version_info(
        self, deployment, mocker
    ):
        feature = DummyFeature()

        mocker.patch.object(
            feature, "fetch_feature_version", side_effect=MissingVersionInfoError
        )
        requirement = FeatureRequirement("test_feature>=1.0.0")
        with pytest.raises(FeatureError):
            feature.check_enabled_requirement_is_compatible(deployment, requirement)

    def test_check_enabled_feature_is_compatible_with_incompatible_requirement(
        self, deployment, mocker
    ):
        feature = DummyFeature()

        mocker.patch.object(feature, "fetch_feature_version", return_value="0.9.0")
        requirement = FeatureRequirement("test_feature>=1.0.0")
        with pytest.raises(IncompatibleVersionError):
            feature.check_enabled_requirement_is_compatible(deployment, requirement)

    def test_check_enabled_feature_is_compatible_with_optional_requirement(
        self, deployment, mocker
    ):
        feature = DummyFeature()

        mocker.patch.object(
            feature, "fetch_feature_version", side_effect=MissingVersionInfoError
        )
        requirement = FeatureRequirement("test_feature>=1.0.0", optional=True)
        with pytest.raises(FeatureError):
            feature.check_enabled_requirement_is_compatible(deployment, requirement)

    def test_check_enabled_feature_is_compatible_with_no_specifier_and_optional_requirement(  # noqa: E501
        self, deployment, mocker
    ):
        feature = DummyFeature()

        mocker.patch.object(
            feature, "fetch_feature_version", side_effect=MissingVersionInfoError
        )
        requirement = FeatureRequirement("test_feature", optional=True)
        feature.check_enabled_requirement_is_compatible(deployment, requirement)

    def test_check_enabled_feature_is_compatible_with_no_specifier_and_required_requirement(  # noqa: E501
        self, deployment, mocker
    ):
        feature = DummyFeature()

        mocker.patch.object(
            feature, "fetch_feature_version", side_effect=MissingVersionInfoError
        )
        requirement = FeatureRequirement("test_feature", optional=False)
        feature.check_enabled_requirement_is_compatible(deployment, requirement)

    def test_check_feature_class_is_compatible_with_compatible_requirement(
        self, deployment
    ):
        feature = DummyFeature()

        requirement = FeatureRequirement("test_feature>=1.0.0")

        klass = feature_klass("1.0.0")
        feature.check_feature_class_is_compatible(klass(), requirement)

    def test_check_feature_class_is_compatible_with_incompatible_requirement(
        self, deployment
    ):
        feature = DummyFeature()

        requirement = FeatureRequirement("test_feature>=2.0.0")

        klass = feature_klass("1.0.0")
        with pytest.raises(IncompatibleVersionError):
            feature.check_feature_class_is_compatible(klass(), requirement)

    def test_check_feature_class_is_compatible_with_core_feature_and_incompatible_version(
        self, deployment
    ):
        feature = DummyFeature()

        requirement = FeatureRequirement("test_feature>=1.0.0")

        klass = feature_klass("0.5.0")
        with pytest.raises(IncompatibleVersionError):
            feature.check_feature_class_is_compatible(klass(), requirement)

    def test_check_feature_class_is_compatible_with_no_specifier(self, deployment):
        feature = DummyFeature()

        requirement = FeatureRequirement("test_feature")

        klass = feature_klass("1.0.0")
        feature.check_feature_class_is_compatible(klass(), requirement)

    def test_check_feature_is_automatically_enableable_with_automatically_enableable_feature(  # noqa: E501
        self, deployment, snap
    ):
        feature = DummyFeature()
        feature.check_feature_is_automatically_enableable(
            feature, Manifest(features={"test_feature": FeatureManifest()})
        )  # noqa: E501

    def test_check_feature_is_automatically_enableable_with_non_automatically_enableable_feature(  # noqa: E501
        self, deployment, snap
    ):
        class DummyConfig(pydantic.BaseModel):
            mandatory: str

        class DummyFeatureInner(DummyFeature):
            def config_type(self) -> type | None:
                return DummyConfig

        required_feature = DummyFeatureInner()

        feature = DummyFeature()
        with pytest.raises(NotAutomaticFeatureError):
            feature.check_feature_is_automatically_enableable(
                required_feature, Manifest(features={"test_feature": FeatureManifest()})
            )

    @pytest.mark.parametrize("klass", feature_classes())
    def test_core_features_requirements(self, deployment, klass):
        feature = klass()

        for requirement in feature.requires:
            feature.check_feature_class_is_compatible(requirement.klass(), requirement)

    def test_check_enablement_requirements_with_enabled_compatible_requirement(
        self, deployment, mocker
    ):
        feature = DummyFeature()
        feature.name = "test_req"
        feature.version = Version("1.0.1")
        klass = feature_klass("0.0.1")
        mocker.patch.object(
            klass,
            "get_feature_info",
            return_value={"version": klass.version, "enabled": "true"},
        )
        mocker.patch(
            "sunbeam.features.interface.v1.base.features",
            Mock(return_value={klass.name: klass}),
        )
        feature.check_enablement_requirements(deployment)

    def test_check_enablement_requirements_with_disabled_compatible_requirement(
        self, deployment, mocker
    ):
        feature = DummyFeature()
        feature.name = "test_req"
        klass = feature_klass("0.0.1", enabled=False)
        mocker.patch.object(
            klass,
            "get_feature_info",
            return_value={"version": klass.version, "enabled": "false"},
        )
        mocker.patch(
            "sunbeam.features.interface.v1.base.features",
            Mock(return_value={klass.name: klass}),
        )
        feature.check_enablement_requirements(deployment)

    def test_check_enablement_requirements_with_enabled_incompatible_requirement(
        self, deployment, mocker
    ):
        feature = DummyFeature()
        feature.name = "test_req"
        klass = feature_klass("0.0.1", True)
        mocker.patch.object(
            klass,
            "get_feature_info",
            return_value={"version": klass.version, "enabled": "true"},
        )
        mocker.patch(
            "sunbeam.features.interface.v1.base.features",
            Mock(return_value={klass.name: klass}),
        )
        with pytest.raises(IncompatibleVersionError):
            feature.check_enablement_requirements(deployment)

    def test_check_enablement_requirements_with_disabled_incompatible_requirement(
        self, deployment, mocker
    ):
        feature = DummyFeature()
        feature.name = "test_req"
        klass = feature_klass("0.0.1")
        mocker.patch.object(
            klass,
            "get_feature_info",
            return_value={"version": klass.version, "enabled": "false"},
        )
        mocker.patch(
            "sunbeam.features.interface.v1.base.features",
            Mock(return_value={klass.name: klass}),
        )
        feature.check_enablement_requirements(deployment)

    def test_check_enablement_requirements_with_enabled_dependant(
        self, deployment, mocker
    ):
        feature = DummyFeature()
        feature.name = "test_req"
        klass = feature_klass("0.0.1", enabled=True)
        mocker.patch.object(
            klass,
            "get_feature_info",
            return_value={"version": klass.version, "enabled": "true"},
        )
        mocker.patch(
            "sunbeam.features.interface.v1.base.features",
            Mock(return_value={klass.name: klass}),
        )
        with pytest.raises(FeatureError):
            feature.check_enablement_requirements(deployment, "disable")

    def test_check_enablement_requirements_with_disabled_dependant(
        self, deployment, mocker
    ):
        feature = DummyFeature()
        feature.name = "test_req"
        klass = feature_klass("0.0.1")
        mocker.patch.object(
            klass,
            "get_feature_info",
            return_value={"version": klass.version, "enabled": "false"},
        )
        mocker.patch(
            "sunbeam.features.interface.v1.base.features",
            Mock(return_value={klass.name: klass}),
        )
        feature.check_enablement_requirements(deployment, "disable")
