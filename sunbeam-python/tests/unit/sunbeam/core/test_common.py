# SPDX-FileCopyrightText: 2023 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import functools
from unittest.mock import Mock, patch

import click
import pytest

from sunbeam.clusterd.service import ClusterServiceUnavailableException
from sunbeam.core.common import Role, validate_roles
from sunbeam.core.deployment import Deployment


@pytest.fixture()
def read_config():
    with patch("sunbeam.core.deployment.read_config") as p:
        yield p


@pytest.fixture()
def deployment():
    with patch("sunbeam.core.deployment.Deployment") as p:
        dep = p(name="", url="", type="")
        dep.get_proxy_settings.side_effect = functools.partial(
            Deployment.get_proxy_settings, dep
        )
        yield dep


class TestRoles:
    def test_is_control(self):
        assert Role.CONTROL.is_control_node()
        assert not Role.COMPUTE.is_control_node()
        assert not Role.STORAGE.is_control_node()

    def test_is_compute(self):
        assert not Role.CONTROL.is_compute_node()
        assert Role.COMPUTE.is_compute_node()
        assert not Role.STORAGE.is_control_node()

    def test_is_storage(self):
        assert not Role.CONTROL.is_storage_node()
        assert not Role.COMPUTE.is_storage_node()
        assert Role.STORAGE.is_storage_node()


class TestProxy:
    @pytest.mark.parametrize(
        "test_input,expected_proxy",
        [
            ({"proxy": {}}, {}),
            ({"proxy": {"proxy_required": False}}, {}),
            (
                {
                    "proxy": {
                        "proxy_required": False,
                        "http_proxy": "http://squid.internal:3128",
                    }
                },
                {},
            ),
            (
                {
                    "proxy": {
                        "proxy_required": False,
                        "http_proxy": "http://squid.internal:3128",
                        "no_proxy": ".example.com",
                    }
                },
                {},
            ),
            ({"proxy": {"proxy_required": True}}, {}),
            (
                {
                    "proxy": {
                        "proxy_required": True,
                        "http_proxy": "http://squid.internal:3128",
                    }
                },
                {"HTTP_PROXY": "http://squid.internal:3128"},
            ),
            (
                {
                    "proxy": {
                        "proxy_required": True,
                        "http_proxy": "http://squid.internal:3128",
                        "no_proxy": ".example.com",
                    }
                },
                {
                    "HTTP_PROXY": "http://squid.internal:3128",
                    "NO_PROXY": (
                        "127.0.0.1,10.1.0.0/16,.example.com,.svc,localhost,10.152.183.0/24"  # noqa: E501
                    ),
                },
            ),
        ],
    )
    def test_get_proxy_settings(
        self, read_config, deployment, test_input, expected_proxy
    ):
        read_config.return_value = test_input
        proxy = deployment.get_proxy_settings()
        assert expected_proxy.get("HTTP_PROXY") == proxy.get("HTTP_PROXY")
        assert expected_proxy.get("HTTPS_PROXY") == proxy.get("HTTPS_PROXY")
        expected_no_proxy_list = ",".split(expected_proxy.get("NO_PROXY"))
        no_proxy_list = ",".split(proxy.get("NO_PROXY"))
        assert expected_no_proxy_list == no_proxy_list

    def test_get_proxy_settings_no_connection_to_clusterdb(
        self, read_config, deployment
    ):
        read_config.side_effect = ClusterServiceUnavailableException(
            "Cluster unavailable.."
        )
        deployment.get_default_proxy_settings.return_value = {}
        proxy = deployment.get_proxy_settings()
        assert proxy == {}

    def test_get_proxy_settings_no_connection_to_clusterdb_and_with_default_proxy(
        self, read_config, deployment
    ):
        read_config.side_effect = ClusterServiceUnavailableException(
            "Cluster unavailable.."
        )
        deployment.get_default_proxy_settings.return_value = {
            "HTTP_PROXY": "http://squid.internal:3128",
            "NO_PROXY": ".example.com",
        }
        proxy = deployment.get_proxy_settings()
        expected_proxy = {
            "HTTP_PROXY": "http://squid.internal:3128",
            "NO_PROXY": (
                "127.0.0.1,10.1.0.0/16,.example.com,.svc,localhost,10.152.183.0/24"
            ),
        }
        expected_no_proxy_list = ",".split(expected_proxy.get("NO_PROXY"))
        no_proxy_list = ",".split(proxy.get("NO_PROXY"))
        assert expected_no_proxy_list == no_proxy_list


def test_validate_roles():
    all_roles = {Role.CONTROL, Role.COMPUTE, Role.STORAGE}
    # Test valid roles
    valid_roles = ("control", "compute", "storage")
    result = validate_roles(Mock(), Mock(), valid_roles)
    assert not set(result) ^ all_roles

    # Test invalid role
    invalid_role = ("invalid",)
    with pytest.raises(click.BadParameter):
        validate_roles(Mock(), Mock(), invalid_role)

    # Test case-insensitive roles
    case_insensitive_roles = ("Control", "COMPUTE", "StOrAgE")
    result = validate_roles(Mock(), Mock(), case_insensitive_roles)
    assert not set(result) ^ all_roles

    # test comma separated roles
    comma_separated_roles = ("control,compute,storage",)
    result = validate_roles(Mock(), Mock(), comma_separated_roles)
    assert not set(result) ^ all_roles

    # test multiple roles with comma separated
    multiple_roles = ("control,compute", "storage")
    result = validate_roles(Mock(), Mock(), multiple_roles)
    assert not set(result) ^ all_roles

    # test mutiple comma separated roles
    multiple_comma_separated_roles = ("control,compute", "storage,compute")
    result = validate_roles(Mock(), Mock(), multiple_comma_separated_roles)
    assert not set(result) ^ all_roles


def test_validate_roles_gated():
    """Test that gated roles require feature gate to be enabled."""
    # region_controller role requires feature.multi-region gate
    with patch("sunbeam.core.common._is_role_enabled") as mock_is_enabled:
        # Mock: region_controller is NOT enabled
        def is_enabled_side_effect(role):
            return role != Role.REGION_CONTROLLER

        mock_is_enabled.side_effect = is_enabled_side_effect

        # Should raise error for gated role
        with pytest.raises(click.BadParameter, match="not enabled"):
            validate_roles(Mock(), Mock(), ("region_controller",))

        # Should work for non-gated roles
        result = validate_roles(Mock(), Mock(), ("control", "compute"))
        assert set(result) == {Role.CONTROL, Role.COMPUTE}

    # Test with gate enabled
    with patch("sunbeam.core.common._is_role_enabled", return_value=True):
        result = validate_roles(Mock(), Mock(), ("region_controller",))
        assert result == [Role.REGION_CONTROLLER]
