# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

from unittest.mock import Mock

import pydantic
import pytest

from sunbeam.features.baremetal import feature_config

_NETCONF_SAMPLE_CONFIG = """[%(name)s]
driver = netconf-openconfig
device_params = name:nexus
switch_info = nexus
switch_id = 00:53:00:0a:0a:0a
host = nexus.example.net
username = user
"""

_GENERIC_SAMPLE_CONFIG = """[genericswitch:%(name)s-hostname]
device_type = %(device_type)s
ngs_mac_address = 00:53:00:0a:0a:0a
ip = 10.20.30.40
username = admin
"""


def _get_netconf_sample_config(name: str, with_key=True) -> str:
    config = _NETCONF_SAMPLE_CONFIG % {"name": name}
    if with_key:
        config = config + f"\nkey_filename = /etc/neutron/sshkeys/{name}-key"

    return config


def _get_generic_sample_config(name: str, device_type: str, with_key=True) -> str:
    config = _GENERIC_SAMPLE_CONFIG % {"name": name, "device_type": device_type}
    if with_key:
        config = config + f"\nkey_file = /etc/neutron/sshkeys/{name}-key"

    return config


class TestBaremetalFeatureConfig:
    def test_validate_shards(self):
        # must be list.
        with pytest.raises(pydantic.ValidationError):
            feature_config.BaremetalFeatureConfig(shards="foo")

        # must be strings.
        with pytest.raises(pydantic.ValidationError):
            feature_config.BaremetalFeatureConfig(shards=[5])

        # no duplicates.
        with pytest.raises(pydantic.ValidationError):
            feature_config.BaremetalFeatureConfig(shards=["foo", "foo"])

        # valid examples.
        feature_config.BaremetalFeatureConfig()
        feature_config.BaremetalFeatureConfig(shards=[])
        feature_config.BaremetalFeatureConfig(shards=["foo", "lish"])

    def test_validate_conductor_groups(self):
        # must be list.
        with pytest.raises(pydantic.ValidationError):
            feature_config.BaremetalFeatureConfig(conductor_groups="foo")

        # must be strings.
        with pytest.raises(pydantic.ValidationError):
            feature_config.BaremetalFeatureConfig(conductor_groups=[5])

        # no duplicates.
        with pytest.raises(pydantic.ValidationError):
            feature_config.BaremetalFeatureConfig(conductor_groups=["foo", "foo"])

        # valid examples.
        feature_config.BaremetalFeatureConfig()
        feature_config.BaremetalFeatureConfig(conductor_groups=[])
        feature_config.BaremetalFeatureConfig(conductor_groups=["foo", "lish"])

    def test_validate_netconf_config(self):
        # valid config.
        configfile_data = _get_netconf_sample_config("foo")
        additional_files = {"foo-key": "lish"}
        valid_netconf = feature_config._Config(
            configfile=configfile_data,
        )
        valid_netconf.additional_files = additional_files
        feature_config._SwitchConfigs(netconf={"foo": valid_netconf})

        # missing configfile.
        with pytest.raises(pydantic.ValidationError):
            feature_config._Config()

        # empty configfile.
        with pytest.raises(pydantic.ValidationError):
            netconf = feature_config._Config(configfile="\n\n")
            feature_config._SwitchConfigs(netconf={"foo": netconf})

        # invalid toml.
        with pytest.raises(pydantic.ValidationError):
            netconf = feature_config._Config(configfile="foo")
            feature_config._SwitchConfigs(netconf={"foo": netconf})

        # unknown field.
        with pytest.raises(pydantic.ValidationError):
            data = "[switch]\nfoo = lish"
            netconf = feature_config._Config(configfile=data)
            feature_config._SwitchConfigs(netconf={"foo": netconf})

        # invalid additional file path.
        with pytest.raises(pydantic.ValidationError):
            data = "[switch]\nkey_filename = /opt/data/foo-key"
            netconf = feature_config._Config(
                configfile=data,
                additional_files=additional_files,
            )
            feature_config._SwitchConfigs(netconf={"foo": netconf})

        # missing additional file.
        with pytest.raises(pydantic.ValidationError):
            netconf = feature_config._Config(configfile=configfile_data)
            feature_config._SwitchConfigs(netconf={"foo": netconf})

        # duplicate section.
        with pytest.raises(pydantic.ValidationError):
            netconf_dict = {
                "foo": valid_netconf,
                "lish": valid_netconf,
            }
            feature_config._SwitchConfigs(
                netconf=netconf_dict,
                additional_files=additional_files,
            )

        # duplicate additional file.
        with pytest.raises(pydantic.ValidationError):
            data = _get_netconf_sample_config("lish", False)
            data = data + "\nkey_file = /etc/neutron/sshkeys/foo-key"
            other_netconf = feature_config._Config(
                configfile=data,
                additional_files=additional_files,
            )
            netconf_dict = {
                "foo": valid_netconf,
                "lish": other_netconf,
            }
            feature_config._SwitchConfigs(
                netconf=netconf_dict,
                additional_files=additional_files,
            )

    def test_validate_generic_config(self):
        # valid config.
        configfile_data = _get_generic_sample_config("foo", "netmiko_arista_eos")
        additional_files = {"foo-key": "lish"}
        valid_generic = feature_config._Config(
            configfile=configfile_data,
        )
        valid_generic.additional_files = additional_files
        feature_config._SwitchConfigs(generic={"foo": valid_generic})

        # section must start with genericswitch:.
        with pytest.raises(pydantic.ValidationError):
            data = "[switch]\ndevice_type = some_type"
            generic = feature_config._Config(configfile=data)
            feature_config._SwitchConfigs(generic={"foo": generic})

        # missing device_type field.
        with pytest.raises(pydantic.ValidationError):
            data = "[genericswitch:switch]\nfoo = lish"
            generic = feature_config._Config(configfile=data)
            feature_config._SwitchConfigs(generic={"foo": generic})

        # unknown field.
        with pytest.raises(pydantic.ValidationError):
            data = "[genericswitch:switch]\ndevice_type = some_type\nfoo = lish"
            generic = feature_config._Config(configfile=data)
            feature_config._SwitchConfigs(generic={"foo": generic})

    def test_read_switch_config_duplicate(self):
        with pytest.raises(ValueError):
            feature_config._SwitchConfigs.read_switch_config(
                "foo",
                "netconf",
                configfile=Mock(),
                additional_files=[("foo", Mock()), ("foo", Mock())],
            )

    def test_read_switch_config(self):
        netconf = _get_netconf_sample_config("foo")
        configfile = Mock()
        configfile.read.return_value = netconf
        additional_file = Mock()
        additional_file.read.return_value = "some-cool-key-here"

        switch_config = feature_config._SwitchConfigs.read_switch_config(
            "foo",
            "netconf",
            configfile=configfile,
            additional_files=[("foo-key", additional_file)],
        )

        assert switch_config is not None
        assert "foo" in switch_config.netconf
        assert switch_config.netconf["foo"].configfile == netconf
        assert switch_config.netconf["foo"].additional_files == {
            "foo-key": "some-cool-key-here"
        }
