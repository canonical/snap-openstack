# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import configparser
import os
import typing

import pydantic

from sunbeam.core.manifest import (
    FeatureConfig,
)

_NETCONF_CONF_OPTS = {
    "driver",
    "switch_id",
    "switch_info",
    "physical_networks",
    "manage_vlans",
    "network_instance",
    "port_id_re_sub",
    "disabled_properties",
    "manage_lacp_aggregates",
    "link_aggregate_prefix",
    "link_aggregate_range",
    "host",
    "username",
    "port",
    "password",
    "key_filename",
    "hostkey_verify",
    "device_params",
    "allow_agent",
    "look_for_keys",
}

_GENERIC_CONF_OPTS = {
    "device_type",
    "ip",
    "port",
    "username",
    "password",
    "use_keys",
    "key_file",
    "secret",
    # NGS internal opts.
    # https://github.com/openstack/networking-generic-switch/blob/stable/2025.1/networking_generic_switch/devices/__init__.py#L28
    "ngs_mac_address",
    "ngs_trunk_ports",
    "ngs_port_default_vlan",
    "ngs_physical_networks",
    "ngs_ssh_disabled_algorithms",
    "ngs_ssh_connect_timeout",
    "ngs_ssh_connect_interval",
    "ngs_max_connections",
    "ngs_switchport_mode",
    "ngs_disable_inactive_ports",
    "ngs_network_name_format",
    "ngs_manage_vlans",
    "ngs_save_configuration",
    "ngs_batch_requests",
    "ngs_fake_sleep_min_s",
    "ngs_fake_sleep_max_s",
    "ngs_allowed_vlans",
    "ngs_allowed_ports",
}

_CONF_OPTS = {
    "netconf": _NETCONF_CONF_OPTS,
    "generic": _GENERIC_CONF_OPTS,
}

_ADDITIONAL_FILE_CONF_OPT = {
    "netconf": "key_filename",
    "generic": "key_file",
}

_NEUTRON_SSHKEYS_PATH = "/etc/neutron/sshkeys"


class _Config(pydantic.BaseModel):
    configfile: str
    additional_files: dict[str, str] = pydantic.Field(
        alias="additional-files",
        default={},
    )


class _SwitchConfigs(pydantic.BaseModel):
    netconf: dict[str, _Config] = pydantic.Field(default={})
    generic: dict[str, _Config] = pydantic.Field(default={})

    @pydantic.field_validator("netconf")
    @classmethod
    def validate_netconf(cls, v: dict[str, _Config]):
        """Validate netconf."""
        _validate_configs(v, "netconf")
        return v

    @pydantic.field_validator("generic")
    @classmethod
    def validate_generic(cls, v: dict[str, _Config]):
        """Validate generic."""
        sections = _validate_configs(v, "generic")
        for section_name, section in sections.items():
            if "device_type" not in section:
                raise ValueError(
                    f"generic: device_type missing from section {section_name}."
                )
        return v

    @classmethod
    def read_switch_config(
        cls,
        name: str,
        protocol: str,
        configfile: typing.TextIO,
        additional_files: list[tuple[str, typing.TextIO]],
    ):
        names = [name for name, _ in additional_files]
        if len(names) != len(set(names)):
            raise ValueError("Duplicate additional files.")

        additional_files_dict = {}
        for filename, file in additional_files:
            additional_files_dict[filename] = file.read()

        config_obj = _Config(configfile=configfile.read())
        config_obj.additional_files = additional_files_dict

        config = {protocol: {name: config_obj}}
        return _SwitchConfigs(**config)


class BaremetalFeatureConfig(FeatureConfig):
    shards: list[str] = pydantic.Field(examples=["foo", "bar"], default=[])

    conductor_groups: list[str] = pydantic.Field(
        examples=["foo", "bar"],
        alias="conductor-groups",
        validation_alias="conductor_groups",
        default=[],
    )

    switchconfigs: _SwitchConfigs | None = pydantic.Field(default=None)

    @pydantic.field_validator("shards")
    @classmethod
    def validate_shards(cls, v: list):
        """Validate shards."""
        if len(v) == 0:
            return v

        if len(v) != len(set(v)):
            raise ValueError("Shards must be unique.")

        return v

    @pydantic.field_validator("conductor_groups")
    @classmethod
    def validate_conductor_groups(cls, v: list):
        """Validate conductor_groups."""
        if len(v) == 0:
            return v

        if len(v) != len(set(v)):
            raise ValueError("Conductor groups must be unique.")

        return v


def _validate_configs(configs: dict[str, _Config], config_type: str):
    sections = {}
    additional_files = []
    for config in configs.values():
        parser = _validate_config(config, config_type)
        for section_name in parser.sections():
            if section_name in sections:
                raise ValueError(
                    f"{config_type}: {section_name} section is duplicated."
                )
            sections[section_name] = parser[section_name]

        for additional_file in config.additional_files:
            if additional_file in additional_files:
                raise ValueError(f"{config_type}: {additional_file} is duplicated.")
            additional_files.append(additional_file)

    return sections


def _validate_config(config: _Config, config_type: str):
    configfile = config.configfile
    additional_files = config.additional_files
    if not configfile.strip():
        raise ValueError(f"{config_type}: configfile must be non-empty.")

    try:
        parser = configparser.ConfigParser()
        parser.read_string(configfile)
    except configparser.Error as ex:
        raise ValueError(f"{config_type}: configfile must be a valid INI: {ex}")

    valid_opts = _CONF_OPTS[config_type]
    additional_file_opt = _ADDITIONAL_FILE_CONF_OPT[config_type]
    for section_name in parser.sections():
        section = parser[section_name]
        for key in section.keys():
            if key not in valid_opts:
                raise ValueError(f"{config_type}: unrecognised field: {key}")

        additional_file = section.get(additional_file_opt)
        if not additional_file:
            continue

        path, additional_file = os.path.split(additional_file)
        if path != _NEUTRON_SSHKEYS_PATH:
            raise ValueError(
                f"{config_type}: expected {additional_file_opt} base path to "
                f"be {_NEUTRON_SSHKEYS_PATH}"
            )
        if additional_file not in additional_files:
            raise ValueError(
                f"{config_type}: {additional_file} referenced in configfile, "
                "but no additional-file was found with that name."
            )

    return parser
