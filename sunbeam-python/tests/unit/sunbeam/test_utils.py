# SPDX-FileCopyrightText: 2023 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

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
