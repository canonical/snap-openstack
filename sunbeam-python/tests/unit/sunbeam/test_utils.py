# SPDX-FileCopyrightText: 2023 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import base64
import json
import textwrap
from unittest.mock import mock_open, patch

import pytest

import sunbeam.utils as utils

IFADDRESSES = {
    "eth1": {
        17: [{"addr": "00:16:3e:07:ba:1e", "broadcast": "ff:ff:ff:ff:ff:ff"}],
        2: [
            {
                "addr": "10.177.200.93",
                "netmask": "255.255.255.0",
                "broadcast": "10.177.200.255",
            }
        ],
        10: [
            {
                "addr": "fe80::216:3eff:fe07:ba1e%enp5s0",
                "netmask": "ffff:ffff:ffff:ffff::/64",
            }
        ],
    },
    "bond1": {
        17: [{"addr": "00:16:3e:07:ba:1e", "broadcast": "ff:ff:ff:ff:ff:ff"}],
        10: [
            {
                "addr": "fe80::216:3eff:fe07:ba1e%bond1",
                "netmask": "ffff:ffff:ffff:ffff::/64",
            }
        ],
    },
}


@pytest.fixture()
def ifaddresses():
    with patch("sunbeam.utils.netifaces.ifaddresses") as p:
        p.side_effect = lambda nic: IFADDRESSES.get(nic)
        yield p


class TestUtils:
    def test_get_fqdn(self, mocker):
        gethostname = mocker.patch("sunbeam.utils.socket.gethostname")
        gethostname.return_value = "myhost"
        getaddrinfo = mocker.patch("sunbeam.utils.socket.getaddrinfo")
        getaddrinfo.return_value = [(2, 1, 6, "myhost.local", ("10.5.3.44", 0))]
        assert utils.get_fqdn() == "myhost.local"

    def test_get_fqdn_when_gethostname_has_dot(self, mocker):
        gethostname = mocker.patch("sunbeam.utils.socket.gethostname")
        gethostname.return_value = "myhost.local"
        assert utils.get_fqdn() == "myhost.local"

    def test_get_fqdn_when_getaddrinfo_has_localhost_as_fqdn(self, mocker):
        gethostname = mocker.patch("sunbeam.utils.socket.gethostname")
        gethostname.return_value = "myhost"
        getaddrinfo = mocker.patch("sunbeam.utils.socket.getaddrinfo")
        getaddrinfo.return_value = [(2, 1, 6, "localhost", ("10.5.3.44", 0))]
        local_ip = mocker.patch("sunbeam.utils.get_local_ip_by_default_route")
        local_ip.return_value = "127.0.0.1"
        getfqdn = mocker.patch("sunbeam.utils.socket.getfqdn")
        getfqdn.return_value = "myhost.local"
        assert utils.get_fqdn() == "myhost.local"

    def test_get_fqdn_when_getfqdn_returns_localhost(self, mocker):
        gethostname = mocker.patch("sunbeam.utils.socket.gethostname")
        gethostname.return_value = "myhost"
        getaddrinfo = mocker.patch("sunbeam.utils.socket.getaddrinfo")
        getaddrinfo.return_value = [(2, 1, 6, "localhost", ("10.5.3.44", 0))]
        local_ip = mocker.patch("sunbeam.utils.get_local_ip_by_default_route")
        local_ip.return_value = "127.0.0.1"
        getfqdn = mocker.patch("sunbeam.utils.socket.getfqdn")
        getfqdn.return_value = "localhost"
        assert utils.get_fqdn() == "myhost"

    def test_get_local_ip_by_default_route(self, mocker, ifaddresses):
        gateways = mocker.patch("sunbeam.utils.netifaces.gateways")
        gateways.return_value = {"default": {2: ("10.177.200.1", "eth1")}}
        assert utils.get_local_ip_by_default_route() == "10.177.200.93"

    def test_get_ifaddresses_by_default_route(self, mocker, ifaddresses):
        gateways = mocker.patch("sunbeam.utils.netifaces.gateways")
        fallback = mocker.patch("sunbeam.utils._get_default_gw_iface_fallback")
        gateways.return_value = {"default": {2: ("10.177.200.93", "eth1")}}
        fallback.return_value = "eth1"
        assert utils.get_ifaddresses_by_default_route() == IFADDRESSES["eth1"][2][0]

    def test_get_ifaddresses_by_default_route_no_default(self, mocker, ifaddresses):
        gateways = mocker.patch("sunbeam.utils.netifaces.gateways")
        fallback = mocker.patch("sunbeam.utils._get_default_gw_iface_fallback")
        gateways.return_value = {"default": {}}
        fallback.return_value = "eth1"
        assert utils.get_ifaddresses_by_default_route() == IFADDRESSES["eth1"][2][0]

    def test__get_default_gw_iface_fallback(self):
        proc_net_route = textwrap.dedent(
            """
        Iface	Destination	Gateway 	Flags	RefCnt	Use	Metric	Mask		MTU	Window	IRTT
        ens10f0	00000000	020A010A	0003	0	0	0	00000000	0	0	0
        ens10f3	000A010A	00000000	0001	0	0	0	00FEFFFF	0	0	0
        ens10f2	000A010A	00000000	0001	0	0	0	00FEFFFF	0	0	0
        ens10f0	000A010A	00000000	0001	0	0	0	00FEFFFF	0	0	0
        ens4f0	0018010A	00000000	0001	0	0	0	00FCFFFF	0	0	0
        ens10f1	0080F50A	00000000	0001	0	0	0	00F8FFFF	0	0	0
        """
        )
        with patch("builtins.open", mock_open(read_data=proc_net_route)):
            assert utils._get_default_gw_iface_fallback() == "ens10f0"

    def test__get_default_gw_iface_fallback_no_0_dest(self):
        """Tests route has 000 mask but no 000 dest, then returns None"""
        proc_net_route = textwrap.dedent(
            """
        Iface	Destination	Gateway 	Flags	RefCnt	Use	Metric	Mask		MTU	Window	IRTT
        ens10f0	00000001	020A010A	0003	0	0	0	00000000	0	0	0
        """
        )
        with patch("builtins.open", mock_open(read_data=proc_net_route)):
            assert utils._get_default_gw_iface_fallback() is None

    def test__get_default_gw_iface_fallback_no_0_mask(self):
        """Tests route has a 000 dest but no 000 mask, then returns None"""
        proc_net_route = textwrap.dedent(
            """
        Iface	Destination	Gateway 	Flags	RefCnt	Use	Metric	Mask		MTU	Window	IRTT
        ens10f0	00000000	020A010A	0003	0	0	0	0000000F	0	0	0
        """
        )
        with patch("builtins.open", mock_open(read_data=proc_net_route)):
            assert utils._get_default_gw_iface_fallback() is None

    def test__get_default_gw_iface_fallback_not_up(self):
        """Tests route is a gateway but not up, then returns None"""
        proc_net_route = textwrap.dedent(
            """
        Iface	Destination	Gateway 	Flags	RefCnt	Use	Metric	Mask		MTU	Window	IRTT
        ens10f0	00000000	020A010A	0002	0	0	0	00000000	0	0	0
        """
        )
        with patch("builtins.open", mock_open(read_data=proc_net_route)):
            assert utils._get_default_gw_iface_fallback() is None

    def test__get_default_gw_iface_fallback_up_but_not_gateway(self):
        """Tests route is up but not a gateway, then returns None"""
        proc_net_route = textwrap.dedent(
            """
        Iface	Destination	Gateway 	Flags	RefCnt	Use	Metric	Mask		MTU	Window	IRTT
        ens10f0	00000000	020A010A	0001	0	0	0	00000000	0	0	0
        """
        )
        with patch("builtins.open", mock_open(read_data=proc_net_route)):
            assert utils._get_default_gw_iface_fallback() is None

    def test_generate_password(self, mocker):
        generate_password = mocker.patch("sunbeam.utils.generate_password")
        generate_password.return_value = "abcdefghijkl"
        assert utils.generate_password() == "abcdefghijkl"

    def test_get_local_cidr_matching_token_success(self, mocker):
        """Test successful CIDR resolution from join token."""
        mock_get_local_cidr = mocker.patch(
            "sunbeam.utils.get_local_cidr_from_ip_address"
        )
        mock_get_local_cidr.return_value = "192.168.1.0/24"

        token_data = {"join_addresses": ["192.168.1.100:8080", "10.0.0.100:8080"]}
        token_json = json.dumps(token_data)
        token_b64 = base64.b64encode(token_json.encode()).decode()

        result = utils.get_local_cidr_matching_token(token_b64)

        assert result == "192.168.1.0/24"
        mock_get_local_cidr.assert_called_once_with("192.168.1.100")

    def test_get_local_cidr_matching_token_fallback_to_second_address(self, mocker):
        """Test fallback to second address when first fails."""
        mock_get_local_cidr = mocker.patch(
            "sunbeam.utils.get_local_cidr_from_ip_address"
        )
        mock_get_local_cidr.side_effect = [ValueError("No match"), "10.0.0.0/24"]

        token_data = {"join_addresses": ["192.168.1.100:8080", "10.0.0.100:8080"]}
        token_json = json.dumps(token_data)
        token_b64 = base64.b64encode(token_json.encode()).decode()

        result = utils.get_local_cidr_matching_token(token_b64)

        assert result == "10.0.0.0/24"
        assert mock_get_local_cidr.call_count == 2
        mock_get_local_cidr.assert_any_call("192.168.1.100")
        mock_get_local_cidr.assert_any_call("10.0.0.100")

    def test_get_local_cidr_matching_token_ipv6_address(self, mocker):
        """Test with IPv6 addresses in join token."""
        mock_get_local_cidr = mocker.patch(
            "sunbeam.utils.get_local_cidr_from_ip_address"
        )
        mock_get_local_cidr.return_value = "2001:db8::/64"

        token_data = {"join_addresses": ["[2001:db8::1]:8080", "192.168.1.100:8080"]}
        token_json = json.dumps(token_data)
        token_b64 = base64.b64encode(token_json.encode()).decode()

        result = utils.get_local_cidr_matching_token(token_b64)

        assert result == "2001:db8::/64"
        mock_get_local_cidr.assert_called_once_with("[2001:db8::1]")

    def test_get_local_cidr_matching_token_no_port_in_address(self, mocker):
        """Test with address that has no port."""
        mock_get_local_cidr = mocker.patch(
            "sunbeam.utils.get_local_cidr_from_ip_address"
        )
        mock_get_local_cidr.return_value = "192.168.1.0/24"

        token_data = {"join_addresses": ["192.168.1.100", "10.0.0.100:8080"]}
        token_json = json.dumps(token_data)
        token_b64 = base64.b64encode(token_json.encode()).decode()

        result = utils.get_local_cidr_matching_token(token_b64)

        assert result == "192.168.1.0/24"
        mock_get_local_cidr.assert_called_once_with("192.168.1.100")

    def test_get_local_cidr_matching_token_no_matching_networks(self, mocker):
        """Test when no local networks match any join addresses."""
        mock_get_local_cidr = mocker.patch(
            "sunbeam.utils.get_local_cidr_from_ip_address"
        )
        mock_get_local_cidr.side_effect = ValueError("No local CIDR found")

        token_data = {"join_addresses": ["192.168.1.100:8080", "10.0.0.100:8080"]}
        token_json = json.dumps(token_data)
        token_b64 = base64.b64encode(token_json.encode()).decode()

        with pytest.raises(
            ValueError, match="No local networks found matching join token addresses"
        ):
            utils.get_local_cidr_matching_token(token_b64)

        assert mock_get_local_cidr.call_count == 2
        mock_get_local_cidr.assert_any_call("192.168.1.100")
        mock_get_local_cidr.assert_any_call("10.0.0.100")

    def test_get_local_cidr_matching_token_empty_join_addresses(self):
        """Test with empty join_addresses list."""
        token_data = {"join_addresses": []}
        token_json = json.dumps(token_data)
        token_b64 = base64.b64encode(token_json.encode()).decode()

        with pytest.raises(
            ValueError, match="No local networks found matching join token addresses"
        ):
            utils.get_local_cidr_matching_token(token_b64)

    def test_get_local_cidr_matching_token_invalid_base64(self):
        """Test with invalid base64 token."""
        invalid_token = "invalid-base64!"

        with pytest.raises(Exception):
            utils.get_local_cidr_matching_token(invalid_token)

    def test_get_local_cidr_matching_token_invalid_json(self):
        """Test with invalid JSON in token."""
        invalid_json = "not-json"
        token_b64 = base64.b64encode(invalid_json.encode()).decode()

        with pytest.raises(json.JSONDecodeError):
            utils.get_local_cidr_matching_token(token_b64)

    def test_get_local_cidr_matching_token_missing_join_addresses_key(self):
        """Test with token missing join_addresses key."""
        token_data = {"other_key": "value"}
        token_json = json.dumps(token_data)
        token_b64 = base64.b64encode(token_json.encode()).decode()

        with pytest.raises(KeyError):
            utils.get_local_cidr_matching_token(token_b64)

    def test_hostnames_match_same_name_forms(self):
        """Test hostnames_match accepts the short and FQDN forms of a name."""
        assert utils.hostnames_match("node1", "node1")
        assert utils.hostnames_match("node1", "node1.example.com")
        assert utils.hostnames_match("node1.example.com", "node1")
        assert utils.hostnames_match("node1.maas", "node1")

    def test_hostnames_match_different_machines(self):
        """Test hostnames_match rejects different machines.

        Sharing the first DNS label (or both being dotted strings such as
        IPs) must not make two names match, see LP#2129793.
        """
        assert not utils.hostnames_match("node1.maas", "node1.other.com")
        assert not utils.hostnames_match("node1", "node2")
        assert not utils.hostnames_match("node1", "node10.example.com")
        assert not utils.hostnames_match("10.0.0.5", "10.1.1.1")
        assert not utils.hostnames_match("node1", "")
